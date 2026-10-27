"""OCR 텍스트 → 시약명 LLM 폴백 식별기.

사전 매칭(matching.py)이 몇 초간 계속 실패할 때 앱이 1회 호출한다.
결과는 항상 사용자 확인(needs_confirmation)을 거치며, 사용자가 "맞아요"를
누르면 기존 /match/confirm 학습 경로로 라벨 문구가 별칭 사전에 등록되어
다음 스캔부터는 LLM 없이 즉시 인식된다.
"""
import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Optional

logger = logging.getLogger("llm_match")


def identify_chemical_from_ocr(ocr_text: str, known_names: list,
                               image_b64: Optional[str] = None) -> Optional[dict]:
    """OCR 텍스트(+선택적 라벨 사진)에서 시약명을 식별한다. 실패/키 미설정 시 None.

    image_b64(JPEG)가 있으면 비전 입력으로 함께 보낸다 — ML Kit OCR 이 깨뜨린
    글자를 모델이 원본 이미지에서 직접 읽어 정확도가 크게 오른다.
    반환: {"name": 표준 시약명, "label_text": 라벨 원문 문구(별칭 학습용)}
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        logger.info("GEMINI_API_KEY 미설정 — LLM 시약 식별 폴백 비활성")
        return None

    image_note = (
        "라벨 사진도 첨부했어. OCR 텍스트와 사진이 다르면 사진에 인쇄된 글자를 우선 신뢰해.\n"
        if image_b64 else ""
    )
    prompt = f"""너는 화학 시약 라벨 판독 전문가야.
아래는 시약병 라벨을 카메라 OCR로 읽은 텍스트야. 오인식된 글자가 섞여 있을 수 있어.
{image_note}
--- OCR 텍스트 ---
{ocr_text[:1500]}
------------------

이 병이 어떤 화학 시약인지 식별해줘.

규칙:
1. 아래 '연구실 보유 시약 목록'의 시약에 해당하면 반드시 목록의 표기를 그대로 name 으로 사용해.
   농도·형태만 다른 같은 물질(예: 목록 '염화나트륨', 라벨 '염화나트륨용액 10%')도 목록 표기를 써.
2. 목록에 없는 시약이면 라벨에 인쇄된 이름을 근거로 널리 쓰이는 한국어 시약명을 name 으로 써.
   (예: 'Sodium silicate solution' → '규산나트륨', 'Phenolphthalein' → '페놀프탈레인', 'Iodine' → '아이오딘')
   라벨에 한글 시약명이 읽히면 그 이름을 그대로 쓰면 된다. 오타가 섞인 글자는 가장 가까운 시약명으로 교정해.
3. label_text 에는 OCR 텍스트에서 시약명을 나타내는 원문 문구를 그대로 담아줘. (별칭 학습용)
4. 시약명을 전혀 짐작할 수 없을 때만 name 을 null 로 해. 라벨에 시약 이름으로 보이는 단어가 있으면 답해.
   결과는 사용자가 확인하므로 확신이 60% 정도여도 답하는 편이 낫다.
5. 메탄올/에탄올처럼 한 글자 차이 이름은 특히 주의해서 구분해. (methanol=메탄올, ethanol=에탄올)
6. 성분표(예: 'with 15% Methanol', 'COMPONENTS')에 적힌 물질은 이 병의 이름이 아니다. 제목처럼 크게
   적힌 이름이 병의 시약명이다.

연구실 보유 시약 목록: {json.dumps(known_names, ensure_ascii=False)}

마크다운 없이 순수 JSON만 출력해: {{"name": "...", "label_text": "..."}}"""

    # flash 계열은 기본 thinking 때문에 15~20초씩 걸린다 — 스캔 폴백에는
    # 1초대에 답하는 lite 가 적합 (실측: 3.1-flash-lite 1.0s vs 3.6-flash 21s)
    model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    parts = [{"text": prompt}]
    if image_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.1,
        },
    }

    body = json.dumps(payload).encode("utf-8")
    # 503(일시 과부하)은 실측에서 드물지 않게 나온다 — 한 번은 짧게 쉬고 다시 시도한다
    for attempt in range(2):
        try:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}, method="POST",
            )
            # 앱은 이 호출을 스캔과 병행해 백그라운드로 기다리므로 여유 있게 잡는다
            with urllib.request.urlopen(req, timeout=25) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                raw_text = res_json["candidates"][0]["content"]["parts"][0]["text"]
                data = json.loads(raw_text)
                name = (data.get("name") or "").strip() if isinstance(data, dict) else ""
                if name:
                    return {
                        "name": name,
                        "label_text": (data.get("label_text") or "").strip() or None,
                    }
                return None
        except urllib.error.HTTPError as e:
            logger.warning(f"LLM 시약 식별 호출 실패: {e}")
            if e.code >= 500 and attempt == 0:
                time.sleep(1.0)
                continue
            return None
        except Exception as e:
            logger.warning(f"LLM 시약 식별 호출 실패: {e}")
            return None

    return None
