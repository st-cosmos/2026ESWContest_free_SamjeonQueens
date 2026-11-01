"""OCR/바코드 → 시약 표준명 매칭 엔진.

우선순위: 바코드(학습된 것) > CAS 번호 > 별칭 정확 일치 > 별칭 부분 일치 > 유사도(fuzzy).
결과는 자동 확정(matched) / 사용자 확인 필요(needs_confirmation) / 실패(no_match)
3단계로 나뉘며, 확신이 없을 때는 앱이 사용자에게 되묻도록 needs_confirmation 을 반환한다.
사용자가 확인/직접 선택한 결과는 confirm() 으로 별칭·바코드 테이블에 학습된다.
"""
import re
from difflib import SequenceMatcher
from typing import Optional

from sqlalchemy.orm import Session

import models

# 이 점수 이상이면 확인 없이 자동 확정
AUTO_ACCEPT = 0.9
# 이 점수 이상이면 후보로 제시하고 사용자 확인을 받음 (미만이면 no_match)
CONFIRM_MIN = 0.65
# fuzzy 후보로 인정할 최소 유사도
FUZZY_MIN = 0.70

# 별칭 테이블이 비어 있을 때 시드로 넣는 기본 별칭 (구 CHEMICAL_NAME_MAPPING 대체)
DEFAULT_ALIASES = {
    "ethanol": "에탄올 95%",
    "에탄올": "에탄올 95%",
    "acetone": "아세톤",
    "아세톤": "아세톤",
    "cyanide": "시안화칼륨",
    "potassium cyanide": "시안화칼륨",
    "시안화칼륨": "시안화칼륨",
    "silver nitrate": "질산은 표준액",
    "질산은": "질산은 표준액",
    "methanol": "메탄올",
    "메탄올": "메탄올",
    "sulfuric acid": "황산 0.1M",
    "sulfuric": "황산 0.1M",
    "h2so4": "황산 0.1M",
    "황산": "황산 0.1M",
    "hydrochloric acid": "염산 1M",
    "hydrochloric": "염산 1M",
    "hcl": "염산 1M",
    "염산": "염산 1M",
    "nitric acid": "질산 65%",
    "nitric": "질산 65%",
    "hno3": "질산 65%",
    "질산": "질산 65%",
    "acetic acid": "아세트산",
    "acetic": "아세트산",
    "ch3cooh": "아세트산",
    "아세트산": "아세트산",
    "초산": "아세트산",
    "빙초산": "아세트산",
    "sodium hydroxide": "수산화나트륨",
    "naoh": "수산화나트륨",
    "수산화나트륨": "수산화나트륨",
    "hydrogen peroxide": "과산화수소",
    "과산화수소": "과산화수소",
    "isopropanol": "이소프로판올",
    "ipa": "이소프로판올",
    "isopropyl alcohol": "이소프로판올",
    "이소프로필알코올": "이소프로판올",
    "2propanol": "이소프로판올",
    "이소프로판올": "이소프로판올",
    "toluene": "톨루엔",
    "톨루엔": "톨루엔",
    # 영문/한글 동의어 — methyl↔ethyl 하이재킹 방지를 위해 반드시 전체 구절로 등록
    "methyl alcohol": "메탄올",
    "메틸알코올": "메탄올",
    "메틸알콜": "메탄올",
    "ethyl alcohol": "에탄올 95%",
    "에틸알코올": "에탄올 95%",
    "에틸알콜": "에탄올 95%",
    # 라벨의 화학식/실험실 약어 표기 (fuzzy 가 ch3cooh 등 비슷한 식을 오인하는 것 방지)
    "ch3oh": "메탄올",
    "meoh": "메탄올",
    "c2h5oh": "에탄올 95%",
    "etoh": "에탄올 95%",
    # 실험실 상용 시약 어휘 확장
    "benzene": "벤젠",
    "벤젠": "벤젠",
    "chloroform": "클로로포름",
    "chcl3": "클로로포름",
    "클로로포름": "클로로포름",
    "acetonitrile": "아세토니트릴",
    "acn": "아세토니트릴",
    "mecn": "아세토니트릴",
    "아세토니트릴": "아세토니트릴",
    "dichloromethane": "디클로로메탄",
    "dcm": "디클로로메탄",
    "디클로로메탄": "디클로로메탄",
    "ethyl acetate": "에틸아세테이트",
    "etoac": "에틸아세테이트",
    "에틸아세테이트": "에틸아세테이트",
    "hexane": "헥산",
    "nhexane": "헥산",
    "헥산": "헥산",
    "xylene": "자일렌",
    "자일렌": "자일렌",
    "ammonia": "암모니아수",
    "nh4oh": "암모니아수",
    "암모니아": "암모니아수",
    "암모니아수": "암모니아수",
    "potassium permanganate": "과망간산칼륨",
    "kmno4": "과망간산칼륨",
    "과망간산칼륨": "과망간산칼륨",
    "formaldehyde": "포름알데히드",
    "formalin": "포름알데히드",
    "포르말린": "포름알데히드",
    "포름알데히드": "포름알데히드",
    "glycerol": "글리세롤",
    "glycerin": "글리세롤",
    "글리세린": "글리세롤",
    "글리세롤": "글리세롤",
    "phenol": "페놀",
    "페놀": "페놀",
    "dimethyl sulfoxide": "DMSO",
    "dmso": "DMSO",
    "dimethylformamide": "DMF",
    "dmf": "DMF",
    "tetrahydrofuran": "THF",
    "thf": "THF",
    # 염화나트륨 (Sodium Chloride Solution) — 라벨의 영문/한글/화학식/식염수 표기.
    # 표준명은 다른 시약처럼 물질명으로 두고, "용액" 등 형태 표기는 별칭으로 흡수한다.
    "sodium chloride solution": "염화나트륨",
    "sodium chloride": "염화나트륨",
    "nacl": "염화나트륨",
    "염화나트륨": "염화나트륨",
    "염화나트륨용액": "염화나트륨",
    "식염수": "염화나트륨",
    "생리식염수": "염화나트륨",
}

# OCR 이 유난히 자주 뒤바꾸는 시약 쌍. 한글 '메'/'에'는 한 획 차이라 한 프레임만
# 잘못 읽혀도 별칭 정확 일치(0.95)로 즉시 오확정된다. 누적 텍스트 전체에서 각
# 시약의 고유 마커 출현 횟수를 세어 증거가 많은 쪽으로 정정하고, 증거가 없거나
# 갈리면 자동 확정을 막아 사용자 확인을 거치게 한다. (마커는 normalize() 된
# 문자열 기준 — 소문자, 한글/영문/숫자만. "(?<![a-z])eth"는 methanol 속 ethan 오탐 방지)
CONFUSABLE_GROUPS = [
    {
        "메탄올": [r"메탄", r"메틸", r"(?<![a-z])meth", r"ch3oh", r"(?<![a-z])meoh"],
        "에탄올 95%": [r"에탄", r"에틸", r"(?<![a-z])ethan", r"(?<![a-z])ethyl",
                     r"c2h5oh", r"(?<![a-z])etoh"],
    },
]


# 단독으로는 시약을 특정하지 못하는 일반 단어 (정규화 기준). 이 토큰은
#  - "토큰이 별칭의 일부" 규칙과 퍼지 매칭의 근거가 될 수 없고 ("Sodium" → 수산화나트륨 오제안 방지)
#  - 사용자 확인으로도 별칭으로 학습되지 않는다 ("hydroxide" 가 학습돼 수산화칼슘을 수산화나트륨으로
#    자동 확정한 실측 사례 방지)
GENERIC_WORDS = {
    "sodium", "potassium", "calcium", "magnesium", "ammonium", "lithium", "zinc", "copper", "iron",
    "hydroxide", "hydrogen", "chloride", "bromide", "iodide", "fluoride", "oxide", "peroxide",
    "carbonate", "bicarbonate", "nitrate", "nitrite", "sulfate", "sulphate", "sulfide", "phosphate",
    "acetate", "citrate", "silicate", "acid", "alcohol", "solution", "soln", "ketone", "ether",
    "ester", "amine", "glycol", "ethyl", "methyl", "propyl", "butyl", "phenyl", "benzyl",
    "anhydrous", "reagent", "reagents", "grade", "chemical", "chemicals", "laboratory",
    "나트륨", "칼륨", "칼슘", "마그네슘", "암모늄", "용액", "수용액", "표준액", "시약", "무수",
    "산", "염", "알코올", "알콜", "에틸", "메틸",
}

# 별칭이 더 긴 토큰의 접두일 때 허용하는 나머지 — "탄산수소나트륨용액" 의 "용액",
# "에탄올95" 의 "95" 처럼 같은 물질의 형태·농도 표기만 인정한다.
# "phenolphthalein"(phenol+phthalein), "ethanolamine"(ethanol+amine) 처럼 다른 물질이 되는
# 접미는 거부한다.
PREFIX_SUFFIX_OK = {"", "용액", "수용액", "표준액", "액", "solution", "soln", "sol", "anhydrous", "무수",
                    "시약", "reagent"}

# 성분표 표기로 보는 퍼센트 상한 — "with 15% Methanol", "METHANOL 5%" 는 주성분이 아니다.
# (37% Formaldehyde, 35% 염산처럼 주성분 농도는 이보다 크다)
INGREDIENT_PCT_MAX = 30.0
_PCT_RE = re.compile(r"(\d{1,2}(?:\.\d+)?)\s*%")
# 토큰 끝의 형태 접미 — "수산화나트륨용액" 은 수산화나트륨과 같은 물질 표기로 본다
FORM_SUFFIXES = ("수용액", "용액", "표준액", "solution", "soln")


def _is_generic(norm_token: str) -> bool:
    """일반 단어이거나, OCR 이 끝을 잘라 읽은 일반 단어("solutio", "chemica")인가."""
    if norm_token in GENERIC_WORDS:
        return True
    return len(norm_token) >= 4 and any(len(g) > len(norm_token) and g.startswith(norm_token)
                                        for g in GENERIC_WORDS)


_ALIAS_SPLIT_RE = re.compile(r"[\n/()\[\],;:|]+")
_SCRIPT_BOUNDARY_RE = re.compile(r"(?<=[가-힣])\s*(?=[A-Za-z])|(?<=[A-Za-z])\s*(?=[가-힣])")
ALIAS_MAX_LEN = 30


def learnable_aliases(label_text: str) -> list:
    """확인된 라벨 문구에서 학습할 별칭들. LLM 이 돌려주는 label_text 는 여러 줄·괄호 병기
    ("Sodium hydrogen carbonate Solution
탄산수소나트륨용액 (중탄산나트륨용액)") 라서 통째로
    학습하면 어떤 라벨과도 정확히 일치하지 않고, 잘린 토큰("Solutio")이 그 안에 포함돼 엉뚱한
    라벨에 부분 일치로 걸린다. 구분자로 나눈 구절마다 따로 학습하되, 형태 접미("용액")는 떼고
    일반 단어만으로 된 구절·너무 짧거나 긴 구절은 버린다."""
    out = []
    # 한글과 영문이 붙어 있으면("Sodium Chloride Solution 염화나트륨용액") 문자 체계 경계에서도 나눈다
    text = _SCRIPT_BOUNDARY_RE.sub("\n", label_text or "")
    for phrase in _ALIAS_SPLIT_RE.split(text):
        alias = normalize(phrase)
        alias = _strip_form_suffix(alias)
        if not alias or alias.isdigit() or len(alias) > ALIAS_MAX_LEN:
            continue
        if not (_usable_short(alias) or len(alias) >= 5):
            continue
        if _is_generic(alias):
            continue
        if alias not in out:
            out.append(alias)
    return out


def _prefix_remainder_ok(rem: str) -> bool:
    if rem in PREFIX_SUFFIX_OK or rem.isdigit():
        return True
    # "95용액", "35pct" 처럼 농도 + 형태
    m = re.match(r"^\d+(.*)$", rem)
    return bool(m) and m.group(1) in PREFIX_SUFFIX_OK


def _strip_form_suffix(nt: str) -> str:
    for suf in FORM_SUFFIXES:
        if nt.endswith(suf) and len(nt) > len(suf):
            return nt[:-len(suf)]
    return nt


def _name_in_tokens(name: str, toks: list) -> bool:
    """이름이 토큰 경계에 맞춰 나타나는가 — 'ethanol' 이 'methanol' 안에서 잡히지 않도록.
    인접 토큰을 이어 붙인 것(최대 4개)과 통째로 같거나, 형태·농도 접미만 더 붙은 경우만 인정."""
    for i in range(len(toks)):
        acc = ""
        for j in range(i, min(i + 4, len(toks))):
            if not toks[j]:
                break
            acc += toks[j]
            if acc == name or (acc.startswith(name) and _prefix_remainder_ok(acc[len(name):])):
                return True
            if len(acc) > len(name) + 12:
                break
    return False


def _ingredient_demote(scores: dict, ocr_text: str, aliases: list):
    """낮은 퍼센트(≤30%)와 같은 줄에 적힌 이름은 성분표 항목으로 보고 확인 수준 이하로 낮춘다.
    (포름알데히드 라벨의 'with 15% Methanol', 변성 에탄올의 'METHANOL 5%')
    모든 후보가 성분표 의심이면(라벨이 '과산화수소 3%' 하나뿐인 경우) 아무것도 낮추지 않는다."""
    low_lines = []
    for line in (ocr_text or "").splitlines():
        pcts = [float(p) for p in _PCT_RE.findall(line)]
        if pcts and min(pcts) <= INGREDIENT_PCT_MAX:
            low_lines.append([normalize(t) for t in tokenize(line)])
    if not low_lines:
        return
    suspects = set()
    for std, (score, method, token) in scores.items():
        names = {normalize(token or "")} | {na for na, s in aliases if s == std}
        names.discard("")
        if any(_name_in_tokens(n, toks) for n in names for toks in low_lines):
            suspects.add(std)
    if suspects and len(suspects) < len(scores):
        for std in suspects:
            score, method, token = scores[std]
            if score > 0.6:
                scores[std] = (0.6, "ingredient", token)


def _apply_confusable_guard(scores: dict, spaced_text: str):
    """혼동 쌍이 매칭 결과에 끼어 있으면 텍스트 증거로 판정을 정정한다.
    spaced_text 는 토큰을 공백으로 이은 정규화 문자열 — 단어 경계 lookbehind 가 동작하도록."""
    for group in CONFUSABLE_GROUPS:
        if not any(m in scores for m in group):
            continue
        hits = {
            m: sum(len(re.findall(p, spaced_text)) for p in patterns)
            for m, patterns in group.items()
        }
        best = max(group, key=lambda m: hits[m])
        rivals = [m for m in group if m != best]
        if hits[best] > 0 and all(hits[best] > hits[r] for r in rivals):
            # 증거 우세 — 열세 후보 제거, 우세 후보는 최소 확인 수준으로 보장
            for r in rivals:
                scores.pop(r, None)
            if best not in scores:
                scores[best] = (0.85, "alias", best)
        else:
            # 증거 없음/동률 — 어느 쪽도 자동 확정하지 않는다 (사용자 확인 강제)
            for m in group:
                if m in scores and scores[m][0] > 0.85:
                    scores[m] = (0.85,) + scores[m][1:]


# CAS 는 "64-17-5" 꼴. OCR 이 하이픈 하나를 자주 삼키므로("64-175", "6417-5") 하이픈이
# 하나만 남은 꼴도 후보로 보되, 앞뒤에 숫자가 더 붙지 않아야 하고 체크디짓을 통과해야 한다.
CAS_RE = re.compile(r"(?<![\d-])(\d{2,7})-(\d{2})-(\d)(?![\d-])")
CAS_LOOSE_RE = re.compile(r"(?<![\d-])(\d{2,7})-(\d{2})(\d)(?![\d-])|(?<![\d-])(\d{2,7})(\d{2})-(\d)(?![\d-])")
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣%.]+")
KOREAN_ONLY_RE = re.compile(r"^[가-힣]+$")


def _usable_short(s: str) -> bool:
    """2자여도 순수 한글이면 유효 토큰/별칭으로 인정한다.
    '황산'·'질산'·'염산' 등 2자 한글 시약명이 3자 미만 노이즈 필터에
    걸려 원천적으로 매칭 불가능해지는 문제를 막는다. (영문 2자는 여전히 제외)"""
    return len(s) >= 2 and bool(KOREAN_ONLY_RE.match(s))


def normalize(s: str) -> str:
    """소문자화 후 한글/영문/숫자만 남긴다. (공백·특수문자·%가 OCR마다 달라지는 문제 흡수)"""
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


def tokenize(text: str) -> list:
    return TOKEN_RE.findall(text or "")


def cas_checksum_ok(cas: str) -> bool:
    """CAS 번호 마지막 자리는 체크디짓 — OCR 오인식 검증에 사용."""
    digits = cas.replace("-", "")
    if len(digits) < 5:
        return False
    body, check = digits[:-1], int(digits[-1])
    total = sum(int(d) * (i + 1) for i, d in enumerate(reversed(body)))
    return total % 10 == check


def extract_cas_numbers(text: str) -> list:
    """체크디짓을 통과한 CAS 번호들. 정상 표기를 먼저, 하이픈 하나가 빠진 표기는 보정해서 뒤에."""
    text = text or ""
    found = [m.group(0) for m in CAS_RE.finditer(text) if cas_checksum_ok(m.group(0))]
    for m in CAS_LOOSE_RE.finditer(text):
        g = [x for x in m.groups() if x is not None]
        if len(g) != 3:
            continue
        cas = f"{g[0]}-{g[1]}-{g[2]}"
        if cas not in found and cas_checksum_ok(cas):
            found.append(cas)
    return found


def _load_aliases(db: Session) -> list:
    """[(정규화 별칭, 표준명)] — 긴 별칭 우선(부분 일치 오탐 최소화).
    일반 단어 하나짜리 별칭(과거에 학습된 "hydroxide" 등)은 무시한다."""
    return _load_aliases_and_registered(db)[0]


def _load_aliases_and_registered(db: Session) -> tuple:
    rows = db.query(models.ChemicalAlias).all()
    pairs = [(r.alias, r.standard_name) for r in rows if r.alias and not _is_generic(r.alias)]

    # 추가: 실제 등록된 시약 이름들도 자동 별칭으로 포함하여 OCR 인식을 지원한다.
    registered = set()
    for chem in db.query(models.Chemical).all():
        if chem.name:
            registered.add(chem.name)
            norm = normalize(chem.name)
            if norm:
                pairs.append((norm, chem.name))

    pairs = list(set(pairs))
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs, registered


def _same_substance(a: str, b: str) -> bool:
    """'에탄올' 과 '에탄올 95%' 처럼 농도·형태 표기만 다른 같은 물질인가."""
    na, nb = normalize(a), normalize(b)
    if na == nb:
        return True
    short, long_ = (na, nb) if len(na) <= len(nb) else (nb, na)
    return long_.startswith(short) and _prefix_remainder_ok(long_[len(short):])


def match(db: Session, ocr_text: str, barcode: Optional[str] = None) -> dict:
    """OCR 텍스트/바코드를 시약 표준명으로 매칭한다."""
    # 1. 바코드 — 학습된 매핑이 있으면 최우선 확정
    if barcode:
        row = db.query(models.BarcodeMap).filter(models.BarcodeMap.barcode == barcode).first()
        if row:
            return {
                "status": "matched", "method": "barcode",
                "chemical_name": row.chemical_name, "confidence": 1.0,
                "candidates": [{"name": row.chemical_name, "score": 1.0}],
                "matched_token": None,
            }

    # 2. CAS 번호 — 체크디짓 검증 통과분만 DB 대조
    for cas in extract_cas_numbers(ocr_text):
        chem = db.query(models.Chemical).filter(models.Chemical.cas_no == cas).first()
        if chem:
            return {
                "status": "matched", "method": "cas",
                "chemical_name": chem.name, "confidence": 0.98,
                "candidates": [{"name": chem.name, "score": 0.98}],
                "matched_token": cas,
            }

    aliases, registered = _load_aliases_and_registered(db)
    tokens = tokenize(ocr_text)
    norm_tokens = [normalize(t) for t in tokens]
    spaced_text = " ".join(t for t in norm_tokens if t)   # 단어 경계가 살아 있는 정규화 텍스트

    # 표준명별 최고 점수 집계: {std_name: (score, method, matched_token)}
    scores = {}

    def put(std, score, method, token):
        if std not in scores or score > scores[std][0]:
            scores[std] = (score, method, token)

    exact_names = set()
    for token, nt in zip(tokens, norm_tokens):
        if len(nt) < 3 and not _usable_short(nt):  # 짧은 노이즈 토큰 제외 (2자 한글은 허용)
            continue
        generic = _is_generic(nt)
        # "토큰이 별칭의 일부" 규칙용: 이 토큰을 포함하는 별칭들의 표준명 집합.
        # 여러 표준명에 걸치면("acid" ⊂ 염산·질산·황산·아세트산) 정보가 없는
        # 조각이므로 해당 규칙을 적용하지 않는다 — boric acid → 염산 오탐 방지.
        # 일반 단어("sodium", "hydrogen")는 표준명이 하나뿐이어도 근거가 못 된다.
        contained_stds = (
            {std for na, std in aliases if nt != na and nt in na}
            if len(nt) >= 4 and not generic else set()
        )
        nt_core = _strip_form_suffix(nt)
        for na, std in aliases:
            if nt == na or nt_core == na:
                # 토큰이 별칭과 정확히 일치("수산화나트륨용액" 도 수산화나트륨) → 자동 확정 후보
                put(std, 0.95, "alias", token)
                exact_names.add(std)
            elif nt.startswith(na) and _prefix_remainder_ok(nt[len(na):]):
                # 별칭 + 형태/농도 접미("탄산수소나트륨용액", "에탄올95") → 확인 필요.
                # 다른 물질이 되는 접미("phenolphthalein", "에탄올아민")는 제안하지 않는다.
                put(std, 0.7, "alias", token)
            elif len(nt) >= 4 and nt in na and len(contained_stds) == 1:
                # 토큰이 별칭의 일부분일 때 (예: "hydrochloric" -> "hydrochloric acid")
                put(std, 0.8, "alias", token)
            elif len(na) >= 3 and len(nt) >= 3 and not generic:
                # 길이 차이가 너무 큰 조합의 오탐 방지 (예: 노이즈 3자 vs 별칭 8자)
                if abs(len(na) - len(nt)) <= max(len(na), len(nt)) // 2:
                    ratio = SequenceMatcher(None, nt, na).ratio()
                    if ratio >= FUZZY_MIN:
                        # 유사도 매칭은 자동 확정 금지 (최대 0.85)
                        put(std, round(ratio * 0.85, 3), "fuzzy", token)

    # 여러 토큰에 걸친 별칭("에탄올 95%", "sulfuric acid", "황 산")은 인접 토큰을 이어 붙인
    # 문자열과 통째로 비교한다. 전체 문자열 부분 일치는 쓰지 않는다 — "phenolphthalein" 속
    # "phenol", "중탄산나트륨용액" 속 "탄산나트륨" 처럼 단어 중간에서 잡히는 오탐 방지.
    runs = set()
    for i in range(len(norm_tokens)):
        if not norm_tokens[i]:
            continue
        acc = ""
        for j in range(i, min(i + 4, len(norm_tokens))):
            if not norm_tokens[j]:
                break
            acc += norm_tokens[j]
            if j > i:
                runs.add(acc)
                # 마지막 토큰이 형태 접미("용액")면 그 앞까지도 후보
                for suf in ("용액", "수용액", "solution"):
                    if acc.endswith(suf) and len(acc) > len(suf):
                        runs.add(acc[:-len(suf)])
    full_hits = [(na, std) for na, std in aliases if (len(na) >= 4 or _usable_short(na)) and na in runs]
    for na, std in full_hits:
        put(std, max(scores.get(std, (0,))[0], 0.85), "alias", na)

    _ingredient_demote(scores, ocr_text, aliases)
    _apply_confusable_guard(scores, spaced_text)

    if not scores:
        return {
            "status": "no_match", "method": None, "chemical_name": None,
            "confidence": 0.0, "candidates": [], "matched_token": None,
        }

    # 점수 동률이면 등록된 재고 이름을 시드 표준명보다 앞세운다 ("에탄올" 등록 시 "에탄올 95%" 보다 우선)
    ranked = sorted(scores.items(), key=lambda kv: (kv[1][0], kv[0] in registered), reverse=True)
    candidates = [{"name": std, "score": sc[0]} for std, sc in ranked[:3]]
    best_name, (best_score, best_method, best_token) = ranked[0]

    # 서로 다른 물질이 동시에 0.85 이상이면(라벨에 두 이름) 자동 확정하지 않음 — 성분표가
    # 있는 라벨은 사용자 확인을 거친다. 농도·형태 표기만 다른 같은 물질("에탄올"/"에탄올 95%")은
    # 하나로 센다. (혼동 가드·성분표 규칙이 낮춘 후보는 세지 않는다)
    strong = [n for n, s in scores.items() if s[0] >= 0.85]
    distinct = []
    for n in strong:
        if not any(_same_substance(n, m) for m in distinct):
            distinct.append(n)
    ambiguous = len(distinct) > 1

    if best_score >= AUTO_ACCEPT and not ambiguous:
        status = "matched"
    elif best_score >= CONFIRM_MIN:
        status = "needs_confirmation"
    else:
        return {
            "status": "no_match", "method": None, "chemical_name": None,
            "confidence": 0.0, "candidates": candidates, "matched_token": None,
        }

    return {
        "status": status, "method": best_method,
        "chemical_name": best_name, "confidence": best_score,
        "candidates": candidates, "matched_token": best_token,
    }


def confirm(db: Session, chemical_name: str, barcode: Optional[str] = None,
            matched_token: Optional[str] = None) -> dict:
    """사용자 확인/직접 선택 결과를 학습한다. 다음 스캔부터 즉시 인식된다."""
    learned = {"barcode": False, "alias": False}

    if barcode:
        row = db.query(models.BarcodeMap).filter(models.BarcodeMap.barcode == barcode).first()
        if row:
            row.chemical_name = chemical_name
        else:
            db.add(models.BarcodeMap(barcode=barcode, chemical_name=chemical_name))
        learned["barcode"] = True

    if matched_token:
        # 짧은 영문/숫자 조각("eth", "thy")과 일반 단어("hydroxide", "sodium")는 부분 일치 오탐의
        # 씨앗이 되므로 학습하지 않는다 — learnable_aliases 가 구절 단위로 거른다.
        for alias in learnable_aliases(matched_token):
            row = db.query(models.ChemicalAlias).filter(models.ChemicalAlias.alias == alias).first()
            if not row:
                db.add(models.ChemicalAlias(alias=alias, standard_name=chemical_name))
                learned["alias"] = True

    db.commit()
    return learned


def known_names(db: Session) -> list:
    """직접 선택 UI 용 — 별칭 표준명 ∪ 보유 시약명."""
    names = {r.standard_name for r in db.query(models.ChemicalAlias).all()}
    names.update(c.name for c in db.query(models.Chemical).all())
    return sorted(names)


def ensure_alias_seed(db: Session):
    """DEFAULT_ALIASES 중 DB에 없는 항목을 보충 시드한다.

    과거에는 테이블이 비어 있을 때만 시드해서, 사전에 나중에 추가된
    별칭(질산·염산 등)이 기존 DB에 영영 반영되지 않는 문제가 있었다.
    학습으로 쌓인 별칭은 건드리지 않고 누락분만 추가한다."""
    existing = {r.alias for r in db.query(models.ChemicalAlias).all()}
    added = False
    for raw, std in DEFAULT_ALIASES.items():
        alias = normalize(raw)
        if alias and alias not in existing:
            db.add(models.ChemicalAlias(alias=alias, standard_name=std))
            existing.add(alias)
            added = True
    if added:
        db.commit()
