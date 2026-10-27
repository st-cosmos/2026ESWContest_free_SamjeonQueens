#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""오프라인 재생 — 보관된 스캔 로그의 OCR 텍스트·바코드 열을 현재 matching.py 에 다시 넣어
알고리즘 변경 전후를 같은 촬영 자료로 비교한다 (재촬영 불필요).

    cd web
    uv run python tools/scan_replay.py data/scan_logs/*.jsonl --manifest <labels_manifest.csv> \
        [--inventory inventory.txt] [--learn] [--csv out.csv] [--strict]

- inventory.txt: 실험 당시 DB 에 등록돼 있던 시약 한 줄에 하나 ("이름" 또는 "이름,CAS").
  생략하면 정답표의 이름 전부를 등록한 것으로 본다.
- 앱 정책을 흉내 낸다: matched → 자동 확정, needs_confirmation → 정답이면 확인·아니면 거절(12초 쿨다운),
  no_match 지속 → 로그에 남은 LLM 응답을 그 시점에 그대로 사용(LLM 은 재호출하지 않는다).
- --learn: 세션이 정답으로 끝나면 /match/confirm 처럼 별칭·바코드를 학습해 다음 세션에 반영한다
  (실험 때 사용자가 확인한 순서 그대로 학습 효과를 재현).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE.parent
sys.path.insert(0, str(WEB))
sys.path.insert(0, str(HERE))
os.environ.setdefault("DATABASE_URL", "sqlite://")

import database  # noqa: E402
import matching  # noqa: E402
import models  # noqa: E402
import scan_eval  # noqa: E402

DECLINE_COOLDOWN_MS = 12000


def build_db(inventory: list[tuple[str, str | None]]):
    models.Base.metadata.create_all(database.engine)
    db = database.SessionLocal()
    matching.ensure_alias_seed(db)
    for i, (name, cas) in enumerate(inventory):
        db.add(models.Chemical(id=f"replay_{i}", name=name, cas_no=cas))
    db.commit()
    return db


def read_inventory(path: Path | None, gt: scan_eval.GroundTruth) -> list[tuple[str, str | None]]:
    if not path:
        return [(n.split("/")[0], None) for n in sorted(gt.names)]
    items = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, cas = line.partition(",")
        items.append((name.strip(), cas.strip() or None))
    return items


def replay_session(db, sid: str, events: list[dict], gt: scan_eval.GroundTruth, strict: bool, learn: bool) -> dict:
    label_id = next((e.get("label_id") for e in events if e.get("label_id")), None)
    expected = gt.expected(label_id)
    t0 = next((e["t_ms"] for e in events if e.get("event") == "session_start"), None)
    requests = [e for e in events if e.get("event") in ("match", "match_llm")]
    if t0 is None and requests:
        t0 = requests[0]["t_ms"]

    declined: dict[str, int] = {}
    first_sugg = None
    wrong = 0
    final = None  # (name, source, method, t_ms)
    for e in requests:
        t = e["t_ms"]
        if e["event"] == "match_llm":
            res = e.get("result") or {}          # 로그의 LLM 응답을 재사용
        else:
            res = matching.match(db, e.get("ocr_text") or "", e.get("barcode"))
        name = res.get("chemical_name")
        status = res.get("status")
        if status not in ("matched", "needs_confirmation") or not name:
            continue
        if name in declined and t - declined[name] < DECLINE_COOLDOWN_MS:
            continue
        ok = scan_eval.is_correct(expected, name, strict)
        if first_sugg is None:
            first_sugg = (name, res.get("method"), ok)
        if ok is False:
            wrong += 1
        if status == "matched":
            final = (name, "auto", res.get("method"), t)
            break
        if ok:                                   # 확인 모달 → 사용자가 "맞아요"
            final = (name, "llm" if res.get("method") == "llm" else "confirm", res.get("method"), t)
            if learn:
                matching.confirm(db, name, e.get("barcode"), res.get("matched_token"))
            break
        declined[name] = t                       # "아니요" → 쿨다운 후 계속

    final_ok = scan_eval.is_correct(expected, final[0], strict) if final else None
    return {
        "session_id": sid, "label_id": label_id, "expected": expected, "mode": None,
        "n_match": sum(1 for e in requests if e["event"] == "match"),
        "used_llm": any(e["event"] == "match_llm" for e in requests),
        "first_suggestion": first_sugg[0] if first_sugg else None,
        "first_method": first_sugg[1] if first_sugg else None,
        "first_ok": first_sugg[2] if first_sugg else None,
        "wrong_suggestions": wrong,
        "declines": len(declined),
        "final_source": final[1] if final else None,
        "final_name": final[0] if final else None,
        "final_ok": final_ok,
        "first_attempt_success": bool(final and first_sugg and first_sugg[2] and final_ok and not declined
                                      and final[1] in ("auto", "confirm")),
        "time_s": round((final[3] - t0) / 1000, 2) if final and t0 is not None else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="스캔 로그 오프라인 재생")
    ap.add_argument("logs", nargs="+")
    ap.add_argument("--manifest", type=Path, required=True)
    ap.add_argument("--inventory", type=Path)
    ap.add_argument("--learn", action="store_true")
    ap.add_argument("--csv", type=Path)
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    gt = scan_eval.GroundTruth(a.manifest)
    db = build_db(read_inventory(a.inventory, gt))
    sessions = scan_eval.group_sessions(scan_eval.load_events(Path(p) for p in a.logs))
    if not sessions:
        sys.exit("세션이 없습니다.")
    results = [replay_session(db, sid, ev, gt, a.strict, a.learn) for sid, ev in sessions.items()]
    scan_eval.print_report(results, scan_eval.summarize(results), "재생 결과 (현재 matching.py)")
    if a.csv:
        scan_eval.write_csv(results, a.csv)
        print(f"\nCSV: {a.csv}")


if __name__ == "__main__":
    main()
