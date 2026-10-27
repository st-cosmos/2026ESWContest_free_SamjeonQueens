"""스캔 정확도 측정용 이벤트 로거.

앱의 인식 시도(/match, /match/llm), 사용자 결정(/match/confirm, /match/event)을
스캔 세션(session_id) 단위로 JSONL 에 남기고, 실험 모드에서 앱이 보내는 라벨 프레임을
JPEG 로 보관한다. 보관된 OCR 텍스트·프레임은 tools/scan_replay.py 로 알고리즘을
오프라인 재평가하는 입력이 된다.

- 위치: SCAN_LOG_DIR (기본 web/data/scan_logs) — git 에 올리지 않는다 (.gitignore).
- 끄기: SCAN_LOG=0
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_ENABLED = os.getenv("SCAN_LOG", "1") not in ("0", "false", "no")
_DIR = Path(os.getenv("SCAN_LOG_DIR", str(Path(__file__).resolve().parent.parent / "data" / "scan_logs")))
_LOCK = threading.Lock()


def enabled() -> bool:
    return _ENABLED


def log_dir() -> Path:
    return _DIR


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def record(event: str, session_id: Optional[str], label_id: Optional[str] = None, **fields: Any) -> None:
    """이벤트 한 줄을 오늘 날짜 파일에 추가한다. 실패해도 서비스 동작에는 영향이 없다."""
    if not _ENABLED:
        return
    row = {"ts": _now_iso(), "t_ms": int(time.time() * 1000), "event": event,
           "session_id": session_id, "label_id": label_id}
    row.update(fields)
    try:
        _DIR.mkdir(parents=True, exist_ok=True)
        path = _DIR / f"scan-{datetime.now().strftime('%Y%m%d')}.jsonl"
        line = json.dumps(row, ensure_ascii=False) + "\n"
        with _LOCK, path.open("a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


def save_frame(session_id: Optional[str], frame_b64: Optional[str]) -> Optional[str]:
    """실험 모드 프레임(JPEG base64)을 frames/<session>/<t_ms>.jpg 로 저장하고 상대 경로를 돌려준다."""
    if not _ENABLED or not frame_b64 or not session_id:
        return None
    try:
        data = base64.b64decode(frame_b64)
        safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64] or "unknown"
        rel = Path("frames") / safe / f"{int(time.time() * 1000)}.jpg"
        path = _DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return rel.as_posix()
    except (OSError, ValueError):
        return None


def summarize_result(result: dict) -> dict:
    """매칭 응답에서 로그에 남길 핵심만 추린다 (후보는 이름·점수 상위 3개)."""
    cands = []
    for c in (result.get("candidates") or [])[:3]:
        if isinstance(c, dict):
            cands.append({"name": c.get("name"), "score": c.get("score", c.get("confidence"))})
    return {
        "status": result.get("status"),
        "method": result.get("method"),
        "chemical_name": result.get("chemical_name"),
        "confidence": result.get("confidence"),
        "matched_token": result.get("matched_token"),
        "candidates": cands,
    }
