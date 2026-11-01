"""시약병 용량(가득 찼을 때 총 무게, kg) 추정 — 잔량 % 의 분모.

밀도·용기 무게를 따지는 복잡한 계산 대신 단순한 규칙으로 정한다.
 1) 스캔한 라벨 글자(OCR)에 내용량 표기(500 mL, 25 g, 1 L 등)가 있으면 그 값을 가득
    무게로 본다. 부피는 1 mL = 1 g 으로 바꾼다.
 2) 글자에 없으면 라벨 사진을 Gemini 에 보여 내용량 표기와 병 크기만 묻는다.
    사진에서 내용량을 읽으면 1)과 같이 쓰고, 못 읽으면 작은 병은 0.12 kg,
    그 밖의 병은 0.52 kg 기본값을 쓴다.
 3) 사진이 없거나 Gemini 가 응답하지 않으면 0.52 kg 기본값.

반입 때 실측 무게가 추정치보다 크면 실측으로 올라간다 (routes/shelves.py). 밀도가 큰
액체나 무거운 유리병처럼 1 mL = 1 g 환산이 작게 잡는 경우는 가득 찬 병이 처음 놓일 때
바로잡힌다. 내용량 표기 없이 정한 기본값은 처음 등록하는 병에만 쓴다 — 이미 용량이 있는
병에 쓰면, 올라간 값은 다시 내려오지 않으므로 작은 병이 0.52 kg 으로 굳어 버린다.
"""
import json
import logging
import os
import re
import time
import urllib.request
from typing import Optional

logger = logging.getLogger("capacity")

# 내용량 표기가 없을 때의 기본 가득 무게 (kg)
DEFAULT_CAPACITY_KG = 0.52  # 일반 시약병 (500 mL 안팎)
SMALL_CAPACITY_KG = 0.12    # 작은 병 (100 mL 안팎)

# 라벨 내용량으로 인정할 범위 (kg, 부피는 1 mL = 1 g) — 벗어나면 오독으로 보고 버린다
MIN_CONTENT_KG = 0.005
MAX_CONTENT_KG = 20.0

# 라벨 내용량 표기: "500 mL", "1L", "500g", "2.5 kg", "500 그램" 등.
# 숫자 앞에 다른 숫자가 붙은 것(카탈로그 번호 등)과, 단위 뒤에 글자나 '/' 가 이어지는
# 것(분자량 g/mol, 농도 g/L·mg/mL, 밀도 g/mL)은 내용량이 아니므로 제외한다.
CONTENT_RE = re.compile(
    r"(?<![0-9.,])(\d{1,4}(?:[.,]\d{1,2})?)\s*"
    r"(㎖|ml|밀리리터|㎗|l|ℓ|리터|liters?|litres?|㎏|kg|킬로그램|kilograms?|grams?|g|그램)"
    r"(?![a-z가-힣/])",
    re.IGNORECASE,
)


def parse_label_content(text: str) -> Optional[tuple]:
    """라벨 글자에서 내용량 표기를 찾는다. 반환: (가득 무게 kg, 원문 표기) 또는 None.

    부피는 1 mL = 1 g 으로 바꾼다. 후보가 여럿이면 가장 큰 값을 고른다 — 내용량은
    라벨에서 가장 큰 수치 표기인 경우가 대부분이다.
    """
    best = None
    for m in CONTENT_RE.finditer(text or ""):
        value = float(m.group(1).replace(",", "."))
        unit = m.group(2).lower()
        if unit in ("㎖", "ml", "밀리리터"):
            kg = value / 1000.0
        elif unit == "㎗":
            kg = value / 10.0
        elif unit in ("l", "ℓ", "리터") or unit.startswith("lit"):
            kg = value
        elif unit in ("㎏", "kg", "킬로그램") or unit.startswith("kilo"):
            kg = value
        else:  # g, gram(s), 그램
            kg = value / 1000.0
        if not (MIN_CONTENT_KG <= kg <= MAX_CONTENT_KG):
            continue
        if best is None or kg > best[0]:
            best = (kg, " ".join(m.group(0).split()))
    return best


def _ask_photo(chemical_name: str, ocr_text: str, images_b64: list) -> Optional[dict]:
    """라벨 사진으로 내용량 표기와 병 크기만 묻는다.

    반환: {"net_content": 표기 문자열 또는 None, "size": "small" | "standard"}.
    사진이 없거나 키가 없거나 호출이 실패하면 None (호출 측이 기본값을 쓴다).
    """
    api_key = os.getenv("GEMINI_API_KEY")
    images = [img for img in (images_b64 or [])[:3] if img]
    if not api_key or not images:
        return None

    prompt = f"""너는 실험실 시약병 사진을 보고 두 가지만 판단하는 도우미야. 시약은 '{chemical_name}' 이야.

1. net_content: 라벨에 인쇄된 내용량 표기(예: "500 mL", "25 g", "1 L", "2.5 kg")가 사진에서 보이면
   숫자와 단위를 그대로 적어. 없거나 읽을 수 없으면 null.
   분자량(g/mol), 농도(g/L, mg/mL, %), 밀도(g/mL)는 내용량이 아니다.
2. size: 병 크기. 일반 시약병(500 mL 안팎)보다 확실히 작은 병(약 250 mL 이하, 예: 100 mL 병)이면
   "small", 그 밖이면 "standard". 병이 잘 보이지 않아 판단하기 어려우면 "standard".

참고용 라벨 OCR 글자:
{(ocr_text or "")[:800]}

마크다운 없이 순수 JSON만 출력해: {{"net_content": "500 mL", "size": "standard"}}"""

    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    parts = [{"text": prompt}] + [{"inline_data": {"mime_type": "image/jpeg", "data": img}} for img in images]
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1},
    }
    try:
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
        data = json.loads(res_json["candidates"][0]["content"]["parts"][0]["text"])
        if not isinstance(data, dict):
            return None
        net = data.get("net_content")
        size = "small" if str(data.get("size") or "").strip().lower() == "small" else "standard"
        return {"net_content": net.strip() if isinstance(net, str) and net.strip() else None, "size": size}
    except Exception as e:
        logger.warning(f"용량 사진 판단 실패 ({e}) — 기본값 사용: '{chemical_name}'")
        return None


def _result(kg: float, source: str, detail: str, is_default: bool, chemical_name: str) -> dict:
    cap = round(kg, 3)
    logger.info(f"용량 추정 '{chemical_name}': {cap}kg ({source}, {detail})")
    return {"capacity_kg": cap, "source": source, "detail": detail, "is_default": is_default}


def estimate_capacity(chemical_name: str, ocr_text: str,
                      images_b64: Optional[list] = None) -> dict:
    """가득 총 무게 추정. 항상 결과를 돌려준다.

    반환: {"capacity_kg": float, "source": str, "detail": str, "is_default": bool}
      source — "label": 스캔 글자의 내용량, "photo_label": 사진 속 라벨의 내용량,
               "small" / "standard": 내용량 표기 없이 사진으로 본 병 크기의 기본값,
               "default": 사진 판단도 없는 기본값
      is_default — 내용량 표기 없이 기본값을 쓴 경우 True. 처음 등록하는 병에만 적용된다.
    """
    found = parse_label_content(ocr_text)
    if found:
        return _result(found[0], "label", f"스캔 글자 '{found[1]}'", False, chemical_name)

    photo = _ask_photo(chemical_name, ocr_text, images_b64 or [])
    if photo:
        found = parse_label_content(photo["net_content"] or "")
        if found:
            return _result(found[0], "photo_label", f"사진 라벨 '{found[1]}'", False, chemical_name)
        if photo["size"] == "small":
            return _result(SMALL_CAPACITY_KG, "small", "내용량 표기 없음 · 작은 병 기본값", True, chemical_name)
        return _result(DEFAULT_CAPACITY_KG, "standard", "내용량 표기 없음 · 일반 병 기본값", True, chemical_name)

    return _result(DEFAULT_CAPACITY_KG, "default", "내용량 표기 없음 · 기본값", True, chemical_name)


# ---- 반입이 추정보다 먼저 끝난 새 병 ------------------------------------------------
# 앱은 반입 스캔 직후 추정을 요청하지만, 병이 이미 올려져 있었거나 사진 판단이 늦으면
# 반입이 먼저 끝난다. 그때 새로 등록된 병을 표시해 두면, 늦게 온 기본값을 그 병에만
# 적용할 수 있다 (이미 용량이 있던 병은 표시되지 않으므로 기본값으로 덮이지 않는다).
_NEW_WITHOUT_ESTIMATE_TTL_S = 180.0
_new_without_estimate: dict = {}  # chemical_id -> 표시 시각


def mark_new_without_estimate(chem_id: str):
    _new_without_estimate[chem_id] = time.time()


def take_new_without_estimate(chem_id: str) -> bool:
    """이 병이 추정 없이 막 새로 등록된 병인가 (한 번만 True)."""
    now = time.time()
    for k in [k for k, t in _new_without_estimate.items() if now - t > _NEW_WITHOUT_ESTIMATE_TTL_S]:
        _new_without_estimate.pop(k, None)
    return _new_without_estimate.pop(chem_id, None) is not None
