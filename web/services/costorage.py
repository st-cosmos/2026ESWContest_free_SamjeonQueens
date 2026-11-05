"""혼재 판정 — 두 시약을 같은 행에 둬도 되는지 '시약 이름 쌍'마다 판정해 저장하고 다시 쓴다.

예전에는 병마다 Gemini 에게 일반적인 혼재 금지 목록을 받아 두고, 경고를 계산할 때마다 그
목록과 이웃 병 이름을 글자로 대조했다. Gemini 는 이웃을 모르는 채 '가연성 물질' 같은 넓은
범주를 섞어 답했고, 범주를 넓혀 대조하는 과정에서 요오드 + 에탄올 같은 오경보가 났다.

이제는 반입 스캔과 위치 이동 요청 때 그 시약과 지금 선반에 놓인 시약들을 한 번에 보내
'같이 둬도 되는가'를 쌍마다 묻고, 판정을 co_storage_pairs 표에 저장한다.
 - 경고 조회와 안전 위치 추천은 이 표만 읽는다. Gemini 를 부르지 않는다.
 - 같은 쌍은 처음 받은 판정을 계속 쓴다. 판정이 틀리면 표의 행을 고친다.
 - 데모 시약 네 가지끼리는 고정 혼재표(llm_safety)가 우선한다.
 - 판정이 없는 쌍(예전에 들어온 병, Gemini 실패)은 배경에서 다시 묻는다. 묻는 동안에는
   경고를 보류하고, 실패한 쌍은 다음 시도 전까지 병별 목록 대조(대체 규칙)로 판정한다.

반입 스캔·위치 이동 요청은 FastAPI 스레드풀에서 처리되므로 여기서 Gemini 를 기다려도 서버가
멈추지 않는다. 무게 보고 처리(비동기 루프)에서는 저장된 판정만 읽는다.

쌍의 이름은 공백을 정리하고 소문자로 맞춘 시약 표준명이다. 농도 표기(1M, 95%)는 위험도에
영향을 주므로 지우지 않는다.
"""
import json
import logging
import os
import threading
import time
import urllib.request
from typing import Optional

from sqlalchemy.exc import IntegrityError

import models
import services.llm_safety as llm_safety
from database import SessionLocal

logger = logging.getLogger("costorage")

RETRY_AFTER_S = 60.0          # 판정 요청이 실패한 쌍을 다시 묻기까지
CHUNK = 25                    # 한 번에 묻는 상대 시약 수
SYNC_TIMEOUT_S = 6.0          # 반입 스캔·위치 이동 요청 안에서 기다리는 최대 시간
BACKGROUND_TIMEOUT_S = 15.0   # 배경에서 다시 물을 때의 제한 시간

_lock = threading.Lock()
_failed_at: dict = {}    # 쌍 -> 마지막으로 묻다 실패한 시각
_inflight: set = set()   # 묻는 중이거나 물을 차례인 쌍
_queue: dict = {}        # 기준 시약 이름 -> 함께 물을 상대 이름들 (배경 판정 대기열)
_worker: Optional[threading.Thread] = None


def key(name: str) -> str:
    return " ".join((name or "").split()).lower()


def pair_of(a: str, b: str) -> tuple:
    ka, kb = key(a), key(b)
    return (ka, kb) if ka <= kb else (kb, ka)


def load_verdicts(db) -> dict:
    """저장된 판정 전체 {(이름a, 이름b): (혼재 여부, 사유)}. 경고 계산 한 번에 한 번 읽는다."""
    return {(r.name_a, r.name_b): (bool(r.dangerous), r.reason)
            for r in db.query(models.CoStoragePair).all()}


def _needs_verdict(verdicts: dict, a: str, b: str) -> bool:
    if not a or not b or llm_safety.same_substance(a, b):
        return False
    if llm_safety.demo_pair_verdict(a, b) is not None:
        return False
    return pair_of(a, b) not in verdicts


# ---- 판정 ---------------------------------------------------------------------------
def judge(verdicts: dict, name_a: str, items_a, name_b: str, items_b) -> tuple:
    """두 병을 같은 행에 두면 안 되는가 → (혼재 여부, 사유 또는 None).

    순서: 같은 물질 → 데모 고정 혼재표 → 저장된 쌍 판정 → 판정 없음.
    판정이 없으면 배경에서 묻도록 예약한다. 묻는 중이면 보류(혼재 아님으로 둠)하고,
    최근에 묻다 실패했으면 병별 목록 대조(대체 규칙)로 판정한다.
    """
    if llm_safety.same_substance(name_a, name_b):
        return False, None
    fixed = llm_safety.demo_pair_verdict(name_a, name_b)
    if fixed is not None:
        return fixed
    p = pair_of(name_a, name_b)
    v = verdicts.get(p)
    if v is not None:
        return v[0], (v[1] or None)
    with _lock:
        failed = p in _failed_at
    _schedule(name_a, name_b)
    if failed:
        return llm_safety.list_incompatible(name_a, items_a, name_b, items_b), None
    return False, None


# ---- Gemini 에 묻기 -------------------------------------------------------------------
def _ask(anchor: str, others: list, timeout_s: float) -> Optional[dict]:
    """anchor 와 others 각각을 같은 행에 둬도 되는지 Gemini 에 한 번에 묻는다.

    반환: {상대 이름: (혼재 여부, 사유)}. 답이 빠진 이름은 들어 있지 않다. 호출 실패 시 None.
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key or not others:
        return None
    listing = "\n".join(f"{i}. {name}" for i, name in enumerate(others, 1))
    prompt = f"""너는 실무 화학물질 안전보건(MSDS) 전문 연구원이야.
시약 '{anchor}'을(를) 아래 시약들과 같은 선반의 같은 행에 함께 보관하려고 해.
번호마다 '{anchor}'와(과) 함께 두면 위험한지 판단해줘.

위험(dangerous: true) — 오직 '치명적 조합'만:
- 섞이거나 닿으면 폭발, 발화, 격렬한 발열 반응이 일어나는 조합 (예: 강산화제와 인화성 유기용매, 강산과 강염기)
- 맹독성 가스가 즉시 발생하는 조합 (예: 시안화물과 산, 차아염소산염과 산)
- 금수성 물질처럼 소량 접촉만으로 급격히 반응하는 조합

위험 아님(dangerous: false):
- 보관 등급 분리가 '권장'될 뿐 닿아도 급성 위험이 없는 조합 (예: 케톤과 강염기, 인화성과 부식성 일반론)
- 실험실에서 서로 섞어 쓰는 조합 (예: 요오드를 에탄올에 녹인 요오드 팅크)
- 장기 보관이나 품질 저하 수준의 문제

시약 이름에 적힌 농도(묽은, 1M, 95% 등)를 반영해 판단해.

판단할 시약:
{listing}

모든 번호에 빠짐없이 답해. 마크다운 없이 순수 JSON만 출력해:
{{"results": [{{"no": 1, "dangerous": false, "reason": "판단 이유 한 문장"}}]}}"""

    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        # 같은 질문에 같은 답이 나오도록 온도 0. 그래도 흔들릴 수 있어 첫 판정을 저장해 다시 쓴다
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0},
    }
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
        data = json.loads(res_json["candidates"][0]["content"]["parts"][0]["text"])
        rows = data.get("results") if isinstance(data, dict) else data
        out = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            try:
                no = int(row.get("no"))
            except (TypeError, ValueError):
                continue
            danger = row.get("dangerous")
            if not (1 <= no <= len(others)) or not isinstance(danger, bool):
                continue
            reason = str(row.get("reason") or "").strip()[:300] or None
            out[others[no - 1]] = (danger, reason)
        return out
    except Exception as e:
        logger.warning(f"혼재 쌍 판정 요청 실패 ({e}): '{anchor}' 와 {len(others)}개")
        return None


def _store(db, anchor: str, other: str, dangerous: bool, reason: Optional[str]):
    a, b = pair_of(anchor, other)
    exists = db.query(models.CoStoragePair).filter(
        models.CoStoragePair.name_a == a, models.CoStoragePair.name_b == b
    ).first()
    if exists:
        return  # 같은 쌍은 처음 받은 판정을 유지한다
    db.add(models.CoStoragePair(name_a=a, name_b=b, dangerous=dangerous, reason=reason, source="llm"))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # 다른 요청이 같은 쌍을 먼저 저장했다


def _resolve(db, anchor: str, others: list, timeout_s: float):
    """anchor 와 others 의 판정을 묻고 저장한다. 답을 못 받은 쌍은 실패로 표시한다."""
    for i in range(0, len(others), CHUNK):
        chunk = others[i:i + CHUNK]
        answers = _ask(anchor, chunk, timeout_s) or {}
        now = time.time()
        for other in chunk:
            p = pair_of(anchor, other)
            if other in answers:
                _store(db, anchor, other, *answers[other])
            with _lock:
                if other in answers:
                    _failed_at.pop(p, None)
                else:
                    _failed_at[p] = now
                _inflight.discard(p)


def resolve_for(db, name: str):
    """반입 스캔·위치 이동 때: name 과 지금 선반에 놓인 모든 시약 사이의 판정 중 없는 것을 묻는다.

    선반 전체를 묻기 때문에 경고뿐 아니라 안전 위치 추천에 필요한 판정도 함께 채워진다.
    첫 묶음은 이 요청 안에서 기다리고(최대 SYNC_TIMEOUT_S), 나머지는 배경에서 묻는다.
    """
    stored = {c.name for c in db.query(models.Chemical).filter(
        models.Chemical.current_status == "비치중", models.Chemical.shelf_id.isnot(None)
    ).all()}
    verdicts = load_verdicts(db)
    todo = sorted(o for o in stored if _needs_verdict(verdicts, name, o))
    if not todo:
        return
    first, rest = todo[:CHUNK], todo[CHUNK:]
    with _lock:
        for o in first:
            _inflight.add(pair_of(name, o))
    _resolve(db, name, first, SYNC_TIMEOUT_S)
    for o in rest:
        _schedule(name, o)


# ---- 배경 판정 ------------------------------------------------------------------------
def _schedule(name_a: str, name_b: str):
    """판정이 없는 쌍을 배경에서 묻도록 예약한다. 경고 조회 중에 부르므로 기다리지 않는다."""
    global _worker
    p = pair_of(name_a, name_b)
    now = time.time()
    with _lock:
        if p in _inflight:
            return
        t = _failed_at.get(p)
        if t is not None and now - t < RETRY_AFTER_S:
            return
        _inflight.add(p)
        _queue.setdefault(name_a, set()).add(name_b)
        if _worker is None:
            _worker = threading.Thread(target=_drain, name="costorage-resolver", daemon=True)
            _worker.start()


def _drain():
    global _worker
    while True:
        with _lock:
            if not _queue:
                _worker = None
                return
            anchor, others = _queue.popitem()
        db = SessionLocal()
        try:
            _resolve(db, anchor, sorted(others), BACKGROUND_TIMEOUT_S)
        except Exception as e:
            logger.warning(f"혼재 쌍 배경 판정 실패 ({e}): '{anchor}'")
            now = time.time()
            with _lock:
                for o in others:
                    p = pair_of(anchor, o)
                    _failed_at[p] = now
                    _inflight.discard(p)
        finally:
            db.close()
