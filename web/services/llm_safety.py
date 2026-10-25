import os
import json
import logging
import urllib.request
import urllib.error
from typing import Dict, List, Any

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
        "keywords": ["나트륨", "칼륨", "수소화나트륨", "금수성"],
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
_OXIDIZERS = ["과산화수소", "과망간산칼륨", "질산", "과염소산", "질산나트륨"]
_ACIDS = ["염산", "질산", "황산", "아세트산", "초산", "과염소산"]
_BASES = ["수산화나트륨", "수산화칼륨", "암모니아", "가성소다"]
_FLAMMABLES = ["에탄올", "메탄올", "아세톤", "톨루엔", "헥산", "이소프로판올",
               "자일렌", "벤젠", "에틸아세테이트", "포름알데히드"]
_CYANIDES = ["시안화칼륨", "시안화나트륨"]


def _category_keywords(item: str) -> list:
    if "산화제" in item:
        return _OXIDIZERS
    if "강산" in item or "산성" in item or "유기산" in item:
        return _ACIDS
    if "염기" in item or "알칼리" in item:
        return _BASES
    if "가연" in item or "인화" in item or "유기용" in item or "환원제" in item:
        return _FLAMMABLES
    if "시안" in item:
        return _CYANIDES
    return []


def names_match(item: str, name: str) -> bool:
    """혼재 금지 목록 항목 ↔ 시약 이름 매칭.
    직접 부분 문자열 + 카테고리명 확장("강산" → 염산/질산/황산…)."""
    if not item or not name:
        return False
    if item in name or name in item:
        return True
    return any(kw in name for kw in _category_keywords(item))


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
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
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


def ensure_chemical_incompatibility_info(chem, db):
    """시약 객체 chem에 혼재 금지 정보가 없을 경우 LLM/Fallback으로 채우고 저장합니다."""
    if not chem.incompatible_chemicals:
        info = fetch_incompatible_chemicals_from_llm(chem.name)
        chem.incompatible_chemicals = json.dumps(info["incompatible_chemicals"], ensure_ascii=False)
        chem.incompatible_reason = info["reason"]
        db.commit()
        db.refresh(chem)
    return chem


def find_recommended_safe_shelf(chem, db):
    """chem과 반응 위험이 없는 안전한 대체 수납칸(shelf_id, desc)을 찾아 반환합니다."""
    import models
    shelves = db.query(models.Shelf).all()
    incomp_list = json.loads(chem.incompatible_chemicals) if chem.incompatible_chemicals else []
    all_chems = db.query(models.Chemical).filter(models.Chemical.current_status == "비치중").all()

    for s in shelves:
        if s.id == chem.shelf_id:
            continue
        is_safe = True
        for other in all_chems:
            if other.id == chem.id or not other.shelf_id:
                continue
            other_shelf = db.query(models.Shelf).filter(models.Shelf.id == other.shelf_id).first()
            if not other_shelf:
                continue
            if s.parent_shelf and other_shelf.parent_shelf and s.parent_shelf == other_shelf.parent_shelf:
                # 혼재 판정 범위와 동일: 같은 선반의 같은 행에 혼재 금지 시약이 없어야 안전
                if (s.row or 1) == (other_shelf.row or 1):
                    if any(names_match(item, other.name) for item in incomp_list):
                        is_safe = False
                        break
        if is_safe:
            parent = s.parent_shelf or "선반"
            desc = f"선반 {parent} · {s.row}행 {s.col}열"
            return s.id, desc

    return None, "선반 C · 분리 보관 전용 구역"
