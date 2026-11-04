import os
import json
import logging
import re
import threading
import urllib.request
from typing import Any, Dict

logger = logging.getLogger("llm_safety")

# ==============================================================================
# Rule-based Fallback Dictionary (시약 혼재 금지 기본 안전 룰셋)
# LLM API 키가 설정되지 않거나 통신 오류/타임아웃 발생 시 안전하게 자동 적용됩니다.
# ==============================================================================
FALLBACK_RULES = [
    {
        "keywords": ["에탄올", "메탄올", "아세톤", "헥산", "톨루엔", "알코올", "유기용매", "인화성"],
        "incompatible": ["질산", "황산", "과산화수소", "산화제", "질산나트륨", "과망간산칼륨"],
        "reason": "인화성 유기용매는 강산화제 및 강산 물질과 혼재 시 화재 및 폭발 위험이 있습니다."
    },
    {
        "keywords": ["염산", "황산", "질산", "아세트산", "초산", "산성"],
        "incompatible": ["수산화나트륨", "수산화칼륨", "암모니아", "시안화칼륨", "에탄올", "아세톤", "염기성"],
        "reason": "산성 물질은 강염기성 물질 및 시안화물/유기용매와 혼재 시 중화 발열, 유독가스 및 폭발 위험이 있습니다."
    },
    {
        "keywords": ["수산화나트륨", "수산화칼륨", "암모니아", "가성소다", "염기성", "알칼리"],
        "incompatible": ["염산", "황산", "질산", "아세트산", "초산", "산성", "유기용매"],
        "reason": "강염기성 물질은 산성 물질과 혼재 시 격렬한 중화 반응 및 발열/비산 위험이 있습니다."
    },
    {
        "keywords": ["시안화칼륨", "시안화나트륨", "시안화물"],
        "incompatible": ["염산", "황산", "질산", "아세트산", "산성"],
        "reason": "시안화물은 산성 물질과 혼재 시 치명적인 맹독성 시안화수소(HCN) 가스를 발생시킵니다."
    },
    {
        # 금속 나트륨·칼륨만 — "나트륨"/"칼륨" 단독 키워드는 수산화나트륨·과망간산칼륨·
        # 시안화칼륨 이름 안에서도 맞아 엉뚱한 금수성 목록(물·에탄올·자기 자신)을 만들었다
        "keywords": ["금속 나트륨", "나트륨 금속", "금속 칼륨", "칼륨 금속", "수소화나트륨", "금수성"],
        "incompatible": ["물", "염산", "황산", "질산", "에탄올", "수산화나트륨"],
        "reason": "금수성 물질은 물 또는 산/알코올과 상호작용 시 가연성 수소가스를 격렬히 발생시켜 폭발 위험이 있습니다."
    },
    {
        "keywords": ["과산화수소", "과망간산칼륨", "질산나트륨", "산화제"],
        "incompatible": ["에탄올", "메탄올", "아세톤", "황산", "유기용매"],
        "reason": "강산화제는 유기용매 및 환원성 물질과 혼재 시 자연 발화 및 폭발 위험이 있습니다."
    }
]


# LLM 이 "강산", "강염기성 물질" 같은 카테고리명으로 답하면 실제 병 이름과
# 부분 문자열이 겹치지 않아 혼재 매칭이 빗나간다. 카테고리명 → 해당하는
# 대표 시약 이름 키워드로 확장해 매칭한다. (검사 순서 중요:
# "강산화제"가 "강산"으로 오인되지 않도록 산화제를 먼저 검사한다)
_OXIDIZERS = ["과산화수소", "과망간산칼륨", "질산", "과염소산", "질산나트륨", "크롬산", "차아염소산"]
# 강산은 진짜 강산만 — 아세트산(초산) 같은 약산을 넣어 두면 LLM 이 아세트산 목록에 쓴
# "강산(질산·황산)"이 다른 아세트산 병이나 묽은 염산까지 가리키게 된다
_STRONG_ACIDS = ["염산", "질산", "황산", "과염소산"]
_ORGANIC_ACIDS = ["아세트산", "초산", "포름산", "옥살산", "시트르산", "젖산"]
_ACIDS = _STRONG_ACIDS + _ORGANIC_ACIDS  # "산성 물질" — 염기·시안화물 목록이 쓰는 넓은 범주
_BASES = ["수산화나트륨", "수산화칼륨", "암모니아", "가성소다"]
_FLAMMABLES = ["에탄올", "메탄올", "아세톤", "톨루엔", "헥산", "이소프로판올",
               "자일렌", "벤젠", "에틸아세테이트", "포름알데히드"]
_CYANIDES = ["시안화칼륨", "시안화나트륨"]

# 목록 항목 문구 안에서 '실제 시약 이름'을 찾을 때 쓰는 전체 키워드
_ALL_KEYWORDS = sorted(set(_OXIDIZERS + _ACIDS + _BASES + _FLAMMABLES + _CYANIDES), key=len, reverse=True)
# 산 병의 목록에 있는 이 범주들은 다른 산에 적용하지 않는다 (아래 incompatible_with 참고)
_ACID_CLASS_HINTS = ("강산", "산성", "유기산")


def _category_keywords(item: str) -> list:
    if "산화제" in item:
        return _OXIDIZERS
    if "유기산" in item:
        return _ORGANIC_ACIDS
    if "강산" in item:
        return _STRONG_ACIDS
    if "산성" in item:
        return _ACIDS
    if "염기" in item or "알칼리" in item:
        return _BASES
    if "가연" in item or "인화" in item or "유기용" in item or "환원제" in item:
        return _FLAMMABLES
    if "시안" in item:
        return _CYANIDES
    return []


def _mentions(item: str, name: str) -> bool:
    """목록 항목이 시약 이름을 직접 지목하는가.
    부분 문자열("질산" ↔ "질산 65%")뿐 아니라 "강산 (질산, 황산 등)"처럼 항목 문구 안에
    실제 시약 이름이 들어 있는 경우도 직접 지목으로 본다."""
    if item in name or name in item:
        return True
    return any(kw in item and kw in name for kw in _ALL_KEYWORDS)


def is_acid(name: str) -> bool:
    return any(kw in name for kw in _ACIDS)


def same_substance(name_a: str, name_b: str) -> bool:
    """농도·규격 표기만 다른 같은 물질인가 ("염산" ↔ "염산 1M", "에탄올 95%" ↔ "에탄올").
    같은 물질의 병끼리는 혼재 판정 대상이 아니다."""
    strip = lambda s: re.sub(r"[\d.]+\s*(mol/l|mol|m|n|%|v/v|w/w)?", "", (s or "").lower())
    a = re.sub(r"\s+", "", strip(name_a))
    b = re.sub(r"\s+", "", strip(name_b))
    return bool(a) and a == b


def incompatible_with(owner_name: str, items, other_name: str) -> bool:
    """owner 의 혼재 금지 목록(items)이 other 를 가리키는가.

    - 이름을 직접 지목한 항목은 항상 인정한다 ("질산" ↔ "질산 65%", "강산(질산…)" ↔ 질산).
    - 카테고리 항목("강산", "강염기성 물질" 등)은 대표 시약으로 확장해 매칭한다.
    - 단, 산끼리는 "강산/산성/유기산" 류 카테고리를 적용하지 않는다. 아세트산 목록의
      "강산"은 산화성 강산과의 혼합 위험을 뜻하는 것이지 묽은 염산이나 다른 산 전반을
      뜻하지 않는다 — 산 사이의 위험 조합(아세트산 + 질산 등)은 이름을 직접 지목했을 때만 인정한다.
    """
    if not owner_name or not other_name:
        return False
    both_acids = is_acid(owner_name) and is_acid(other_name)
    for item in items or []:
        if not item:
            continue
        if _mentions(item, other_name):
            return True
        if both_acids and any(h in item for h in _ACID_CLASS_HINTS) and "산화제" not in item:
            continue
        if any(kw in other_name for kw in _category_keywords(item)):
            return True
    return False


def names_match(item: str, name: str) -> bool:
    """단일 항목 ↔ 시약 이름 매칭 (직접 지목 + 카테고리 확장). 산끼리 제외 규칙은
    쌍 전체를 보는 incompatible_with 에서 적용한다."""
    if not item or not name:
        return False
    return _mentions(item, name) or any(kw in name for kw in _category_keywords(item))


# ==============================================================================
# 데모 시약 고정 혼재표
# 데모에 쓰는 네 시약끼리는 LLM 답이 흔들려도 판정이 바뀌지 않도록 표로 고정한다.
# 네 시약 사이의 혼재 금지 조합은 아래 두 개뿐이고, 나머지 네 조합은 경고하지 않는다.
#   수산화나트륨 + 묽은 염산 / 수산화나트륨 + 아세트산
# 데모 시약은 Gemini 를 부르지 않는다. 표에 없는 시약이 끼면 기존대로 각 병의
# 목록(LLM·대체 규칙)을 서로 대조한다.
# ==============================================================================
# 표준명 → 이름 표기들. 공백·농도·괄호 표기를 지운 뒤 '정확히' 같아야 한다 —
# 부분 일치로 하면 아세트산에틸·아세트산나트륨·염산염 같은 다른 물질까지 걸린다.
_DEMO_NAMES = {
    "염화나트륨": {"염화나트륨", "염화나트륨용액", "염화나트륨수용액", "식염수", "생리식염수",
                "nacl", "sodiumchloride", "sodiumchloridesolution"},
    "아세트산": {"아세트산", "초산", "빙초산", "빙아세트산", "아세트산용액",
              "aceticacid", "glacialaceticacid"},
    "수산화나트륨": {"수산화나트륨", "수산화나트륨용액", "수산화나트륨수용액", "가성소다",
                 "naoh", "sodiumhydroxide"},
    # 이 시스템의 표준명은 "염산 1M" — 농도 표기를 지우면 "염산"
    "묽은 염산": {"염산", "묽은염산", "희염산", "염산용액", "염산수용액", "hcl", "hydrochloricacid"},
}
_DEMO_PAIR_REASONS = {
    frozenset(("수산화나트륨", "묽은 염산")):
        "강염기인 수산화나트륨과 염산이 섞이면 중화 반응으로 열이 나고, 용액이 끓어 튈 수 있습니다.",
    frozenset(("수산화나트륨", "아세트산")):
        "강염기인 수산화나트륨과 아세트산이 섞이면 중화 반응으로 열이 나고, 진한 아세트산이면 격렬하게 끓어 튈 수 있습니다.",
}
# 병 기록에 저장할 목록·사유 (웹 재고 화면에 그대로 보인다)
_DEMO_INFO = {
    "염화나트륨": ([], "고정 혼재표: 아세트산·수산화나트륨·묽은 염산과 함께 두어도 위험한 반응이 없습니다."),
    "아세트산": (["수산화나트륨"], "고정 혼재표: " + _DEMO_PAIR_REASONS[frozenset(("수산화나트륨", "아세트산"))]),
    "수산화나트륨": (["묽은 염산", "아세트산"],
                 "고정 혼재표: 묽은 염산·아세트산과 섞이면 중화 반응으로 열이 나고, 용액이 끓어 튈 수 있습니다."),
    "묽은 염산": (["수산화나트륨"], "고정 혼재표: " + _DEMO_PAIR_REASONS[frozenset(("수산화나트륨", "묽은 염산"))]),
}


def _demo_norm(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"\([^)]*\)|\[[^\]]*\]", "", s)  # 괄호 속 부가 표기
    # 농도 표기 (1M, 0.1 N, 0.9 w/v%, 99.5%) — 단위 뒤에 영문이 이어지면 단위가 아니다 ("2 NaOH")
    s = re.sub(r"[\d.]+\s*(?:(?:mol/l|mol|m|n|w/v|v/v|w/w)(?![a-z]))?\s*%?", "", s)
    return re.sub(r"[\s·,_/%-]+", "", s)


def demo_key(name: str):
    """데모 시약이면 표의 표준명("염화나트륨"·"아세트산"·"수산화나트륨"·"묽은 염산"), 아니면 None."""
    n = _demo_norm(name)
    for key, names in _DEMO_NAMES.items():
        if n in names:
            return key
    return None


def demo_info(name: str):
    """데모 시약이면 (저장할 혼재 금지 목록, 사유), 아니면 None."""
    key = demo_key(name)
    return _DEMO_INFO[key] if key else None


def demo_pair_verdict(name_a: str, name_b: str):
    """둘 다 데모 시약이면 고정 표의 판정 (혼재 여부, 사유), 하나라도 아니면 None.
    저장된 목록이나 쌍 판정이 무엇이든 표가 정한 두 조합만 경고한다."""
    ka, kb = demo_key(name_a), demo_key(name_b)
    if not (ka and kb):
        return None
    reason = None if ka == kb else _DEMO_PAIR_REASONS.get(frozenset((ka, kb)))
    return reason is not None, reason


def list_incompatible(name_a: str, items_a, name_b: str, items_b) -> bool:
    """병마다 받아 둔 혼재 금지 목록끼리 대조 — 쌍 판정이 없을 때의 대체 규칙.
    상대 목록이 이 병을 지목해도 위험으로 본다."""
    return incompatible_with(name_a, items_a, name_b) or incompatible_with(name_b, items_b, name_a)


def pair_incompatible(name_a: str, items_a, name_b: str, items_b):
    """고정 표 + 목록 대조 판정 → (혼재 여부, 고정 표의 사유 또는 None).
    경고 계산과 안전 위치 추천은 저장된 쌍 판정을 먼저 보는 services/costorage.judge 를 쓴다."""
    rule = demo_pair_verdict(name_a, name_b)
    if rule is not None:
        return rule
    return list_incompatible(name_a, items_a, name_b, items_b), None


def get_fallback_incompatibility(chemical_name: str) -> Dict[str, Any]:
    """LLM 미사용 또는 오류 시 작동하는 룰 기반 대체 분석기"""
    name_clean = chemical_name.strip()
    matched_incompatible = set()
    reasons = []

    for rule in FALLBACK_RULES:
        if any(kw in name_clean for kw in rule["keywords"]):
            for item in rule["incompatible"]:
                matched_incompatible.add(item)
            if rule["reason"] not in reasons:
                reasons.append(rule["reason"])

    if not matched_incompatible:
        matched_incompatible = {"강산성 물질", "강염기성 물질", "강산화제"}
        reasons.append("일반 화학 시약으로서 반응성 위험 물질과의 분리 보관이 권장됩니다.")

    return {
        "incompatible_chemicals": list(matched_incompatible),
        "reason": " ".join(reasons)
    }


def fetch_incompatible_chemicals_from_llm(chemical_name: str) -> Dict[str, Any]:
    """Google Gemini API를 호출하여 시약의 혼재 금지 목록을 받아옵니다."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info(f"GEMINI_API_KEY가 설정되지 않아 Fallback 룰을 적용합니다: '{chemical_name}'")
        return get_fallback_incompatibility(chemical_name)

    prompt = f"""
    너는 실무 화학물질 안전보건(MSDS) 전문 연구원이야.
    시약명 '{chemical_name}'이(가) 최초 반입되었어.

    이 시약과 같은 선반이나 인접한 수납칸에 '절대 함께 두면 안 되는(혼재 금지)' 대표적인 시약 이름이나 물질 종류(한국어) 목록을 조사해줘.

    포함 기준 — 오직 '치명적 조합'만:
    - 혼합·접촉 시 폭발, 발화, 격렬한 발열 반응이 일어나는 조합 (예: 강산화제 + 인화성 유기용매)
    - 맹독성 가스가 즉시 발생하는 조합 (예: 시안화물 + 산, 차아염소산염 + 산)
    - 금수성 물질처럼 소량 접촉만으로 급격히 반응하는 조합

    제외 기준 — 다음은 목록에 넣지 마:
    - 보관 등급 분리가 '권장'될 뿐 접촉해도 급성 위험이 없는 조합 (예: 케톤 ↔ 강염기, 인화성 ↔ 부식성 일반론)
    - 장기 보관·품질 저하 수준의 상성 문제

    목록은 위험이 가장 큰 것부터 최대 7개까지만.
    반드시 다음 요구사항에 맞는 유효한 JSON 형식으로만 응답해야 해. 마크다운 태그(```json 등)는 제외하고 순수 JSON만 출력해줘.

    JSON 포맷:
    {{
      "incompatible_chemicals": ["질산", "황산", "과산화수소", "강염기성 물질"],
      "reason": "해당 시약들과 혼재 시 열 발생, 유독가스 분출, 폭발 등의 구체적 반응 위험 원인 설명"
    }}
    """

    # 반입 처리 경로를 막지 않도록 응답이 빠른 lite 를 기본으로 쓴다 (GEMINI_MODEL 로 변경 가능)
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.2
        }
    }

    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8")
            res_json = json.loads(body)
            raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]

            data = json.loads(raw_text)
            if isinstance(data, dict) and "incompatible_chemicals" in data:
                return {
                    "incompatible_chemicals": data.get("incompatible_chemicals", []),
                    "reason": data.get("reason", "LLM 안전성 분석 결과")
                }
    except Exception as e:
        logger.warning(f"Gemini API 호출 중 오류 발생 ({e}), Fallback 룰 적용: '{chemical_name}'")

    return get_fallback_incompatibility(chemical_name)


_ensuring_lock = threading.Lock()
_ensuring: set = set()  # 지금 목록을 묻고 있는 병 id


def ensure_chemical_incompatibility_info(chem, db):
    """시약 객체 chem에 혼재 금지 정보가 없을 경우 LLM/Fallback으로 채우고 저장합니다.

    데모 시약은 Gemini 를 부르지 않고 고정 혼재표 값으로 맞춘다 — 이전에 LLM·대체 규칙으로
    저장된 목록도 다음 조회 때 표 값으로 바뀐다."""
    fixed = demo_info(chem.name)
    if fixed is not None:
        items_json = json.dumps(fixed[0], ensure_ascii=False)
        if chem.incompatible_chemicals != items_json or chem.incompatible_reason != fixed[1]:
            chem.incompatible_chemicals = items_json
            chem.incompatible_reason = fixed[1]
            db.commit()
            db.refresh(chem)
        return chem
    if not chem.incompatible_chemicals:
        # 경고·재고 조회가 동시에 여러 번 들어와도 같은 병은 한 번만 묻는다
        # (같은 병을 동시에 여러 번 물으면 Gemini 요청 한도에 걸린다)
        with _ensuring_lock:
            if chem.id in _ensuring:
                return chem
            _ensuring.add(chem.id)
        try:
            info = fetch_incompatible_chemicals_from_llm(chem.name)
            # 자기 자신을 가리키는 항목은 뺀다 — 같은 시약 병끼리 혼재로 뜨는 원인
            items = [x for x in info["incompatible_chemicals"]
                     if x and not (x in chem.name or chem.name in x)]
            chem.incompatible_chemicals = json.dumps(items, ensure_ascii=False)
            chem.incompatible_reason = info["reason"]
            db.commit()
            db.refresh(chem)
        finally:
            with _ensuring_lock:
                _ensuring.discard(chem.id)
    return chem


def find_recommended_safe_shelf(chem, db):
    """chem과 반응 위험이 없는 안전한 대체 수납칸(shelf_id, desc)을 찾아 반환합니다.
    판정은 경고와 같은 시약 쌍 판정(services/costorage)을 쓴다."""
    import models
    import services.costorage as costorage  # costorage 가 이 모듈을 import 하므로 지연 import
    verdicts = costorage.load_verdicts(db)
    shelves = db.query(models.Shelf).all()
    incomp_list = json.loads(chem.incompatible_chemicals) if chem.incompatible_chemicals else []
    all_chems = db.query(models.Chemical).filter(models.Chemical.current_status == "비치중").all()

    for s in shelves:
        if s.id == chem.shelf_id:
            continue
        is_safe = True
        for other in all_chems:
            if other.id == chem.id or not other.shelf_id or same_substance(other.name, chem.name):
                continue
            other_shelf = db.query(models.Shelf).filter(models.Shelf.id == other.shelf_id).first()
            if not other_shelf:
                continue
            if s.parent_shelf and other_shelf.parent_shelf and s.parent_shelf == other_shelf.parent_shelf:
                # 혼재 판정 범위와 동일: 같은 선반의 같은 행에 혼재 금지 시약이 없어야 안전
                # (양방향 — 상대 목록이 이 시약을 지목하는 경우도 위험)
                if (s.row or 1) == (other_shelf.row or 1):
                    other_list = json.loads(other.incompatible_chemicals) if other.incompatible_chemicals else []
                    # 시약 쌍 판정 (데모 고정 표 → 저장된 판정 → 보류·대체 규칙)
                    if costorage.judge(verdicts, chem.name, incomp_list, other.name, other_list)[0]:
                        is_safe = False
                        break
        if is_safe:
            parent = s.parent_shelf or "선반"
            desc = f"선반 {parent} · {s.row}행 {s.col}열"
            return s.id, desc

    return None, "선반 C · 분리 보관 전용 구역"
