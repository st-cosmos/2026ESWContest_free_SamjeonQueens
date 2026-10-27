#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""스캔 정확도 집계 — services/scan_log.py 가 남긴 JSONL 을 세션 단위로 묶어 지표를 낸다.

    cd web
    uv run python tools/scan_eval.py data/scan_logs/*.jsonl --manifest <labels_manifest.csv> [--csv out.csv] [--strict]

세션 = 같은 session_id 의 이벤트 묶음 (앱이 병 하나를 스캔하기 시작해 결정이 날 때까지).
정답은 세션의 label_id 를 manifest 의 파일 번호(예: "12" → 12.methanol_kr.png) 또는 name_ko 와 맞춰 찾는다.

지표
- 초회 성공률: 서버의 첫 제안(no_match 가 아닌 첫 응답)이 정답이고, 거절 없이 자동 확정 또는 확인으로 끝난 세션 비율
- 자동 확정 정확도 / 오확정률: source=auto 로 끝난 세션 중 정답·오답 비율 (오확정은 가장 치명적인 실패)
- 최종 정답률: 최종 결정(auto/confirm/llm/manual)이 정답인 비율, 수동 선택 비율, LLM 사용 비율
- 인식 소요 시간: session_start(없으면 첫 match 요청) → 최종 결정까지, 평균·중앙값·p90
- 오제안 수: 세션 중 정답이 아닌 이름을 제안한 응답 수
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Optional

DECISION_SOURCES = ("auto", "confirm", "llm", "manual")


def normalize(s: Optional[str]) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())


# ───────────────────────── 정답표 ─────────────────────────
class GroundTruth:
    """labels_manifest.csv (file, name_ko, ...) → label_id 로 기대 이름을 찾는다."""

    def __init__(self, path: Optional[Path]):
        self.by_number: dict[str, str] = {}
        self.names: set[str] = set()
        if not path:
            return
        with path.open(encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                name = (row.get("name_ko") or "").strip()
                if not name:
                    continue
                self.names.add(name)
                m = re.match(r"(\d+)", row.get("file") or "")
                if m:
                    self.by_number[str(int(m.group(1)))] = name

    def expected(self, label_id: Optional[str]) -> Optional[str]:
        if not label_id:
            return None
        key = label_id.strip()
        if key.lstrip("0").isdigit() or key.isdigit():
            return self.by_number.get(str(int(key)))
        return key  # 라벨 번호 대신 이름을 직접 적은 경우


def is_correct(expected: Optional[str], actual: Optional[str], strict: bool = False) -> Optional[bool]:
    """기대 이름과 확정 이름 비교. 느슨 모드에서는 '에탄올' ↔ '에탄올 95%' 처럼 한쪽이 다른 쪽의 접두면 정답."""
    if expected is None or actual is None:
        return None
    e, a = normalize(expected), normalize(actual)
    if strict:
        return e == a
    return e == a or a.startswith(e) or e.startswith(a)


# ───────────────────────── 세션 구성 ─────────────────────────
def load_events(paths: Iterable[Path]) -> list[dict]:
    rows: list[dict] = []
    for p in paths:
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    rows.sort(key=lambda r: r.get("t_ms", 0))
    return rows


def group_sessions(rows: list[dict]) -> dict[str, list[dict]]:
    sessions: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        sid = r.get("session_id")
        if sid:
            sessions[sid].append(r)
    return sessions


def suggestions(events: list[dict]) -> list[tuple[int, dict]]:
    """서버가 이름을 제안한 응답들 (match / match_llm 중 status != no_match)."""
    out = []
    for e in events:
        if e.get("event") in ("match", "match_llm"):
            res = e.get("result") or {}
            if res.get("status") in ("matched", "needs_confirmation") and res.get("chemical_name"):
                out.append((e["t_ms"], res))
    return out


def evaluate_session(sid: str, events: list[dict], gt: GroundTruth, strict: bool) -> dict:
    label_id = next((e.get("label_id") for e in events if e.get("label_id")), None)
    expected = gt.expected(label_id)
    mode = next((e.get("mode") for e in events if e.get("mode")), None)
    t_start = next((e["t_ms"] for e in events if e.get("event") == "session_start"), None)
    if t_start is None:
        t_start = next((e["t_ms"] for e in events if e.get("event") == "match"), events[0]["t_ms"])

    decisions = [e for e in events if e.get("event") == "decision" and e.get("source") in DECISION_SOURCES]
    declines = [e for e in events if e.get("event") == "decision" and e.get("source") == "decline"]
    final = decisions[-1] if decisions else None
    final_name = final.get("chemical_name") if final else None
    final_ok = is_correct(expected, final_name, strict)

    sugg = suggestions(events)
    first = sugg[0][1] if sugg else None
    first_ok = is_correct(expected, first.get("chemical_name") if first else None, strict)
    wrong_suggestions = sum(1 for _, r in sugg if is_correct(expected, r.get("chemical_name"), strict) is False)

    first_attempt = bool(final and first_ok and final_ok and not declines and final.get("source") in ("auto", "confirm"))
    return {
        "session_id": sid, "label_id": label_id, "expected": expected, "mode": mode,
        "n_match": sum(1 for e in events if e.get("event") == "match"),
        "used_llm": any(e.get("event") == "match_llm" for e in events),
        "first_suggestion": first.get("chemical_name") if first else None,
        "first_method": first.get("method") if first else None,
        "first_ok": first_ok,
        "wrong_suggestions": wrong_suggestions,
        "declines": len(declines),
        "final_source": final.get("source") if final else None,
        "final_name": final_name,
        "final_ok": final_ok,
        "first_attempt_success": first_attempt,
        "time_s": round((final["t_ms"] - t_start) / 1000, 2) if final else None,
    }


# ───────────────────────── 집계 ─────────────────────────
def pct(n: int, d: int) -> str:
    return f"{100 * n / d:5.1f}% ({n}/{d})" if d else "   -   (0/0)"


def summarize(results: list[dict]) -> dict:
    scored = [r for r in results if r["expected"] is not None]
    resolved = [r for r in scored if r["final_source"]]
    auto = [r for r in resolved if r["final_source"] == "auto"]
    times = [r["time_s"] for r in resolved if r["time_s"] is not None and r["final_ok"]]
    s = {
        "sessions": len(results), "scored": len(scored), "resolved": len(resolved),
        "first_attempt": (sum(r["first_attempt_success"] for r in scored), len(scored)),
        "final_correct": (sum(bool(r["final_ok"]) for r in resolved), len(resolved)),
        "auto_sessions": (len(auto), len(resolved)),
        "auto_correct": (sum(bool(r["final_ok"]) for r in auto), len(auto)),
        "auto_wrong": (sum(r["final_ok"] is False for r in auto), len(auto)),
        "manual": (sum(r["final_source"] == "manual" for r in resolved), len(resolved)),
        "llm_used": (sum(r["used_llm"] for r in scored), len(scored)),
        "wrong_suggestions": sum(r["wrong_suggestions"] for r in scored),
        "unresolved": len(scored) - len(resolved),
    }
    if times:
        times_sorted = sorted(times)
        s["time_mean"] = round(statistics.mean(times), 2)
        s["time_median"] = round(statistics.median(times), 2)
        s["time_p90"] = round(times_sorted[min(len(times_sorted) - 1, int(0.9 * len(times_sorted)))], 2)
    return s


def print_report(results: list[dict], s: dict, title: str) -> None:
    print(f"\n=== {title}")
    print(f"세션 {s['sessions']}개 (정답 있는 세션 {s['scored']}, 결정까지 간 세션 {s['resolved']}, 미결 {s['unresolved']})")
    print(f"초회 성공률        {pct(*s['first_attempt'])}")
    print(f"최종 정답률        {pct(*s['final_correct'])}")
    print(f"자동 확정 비율     {pct(*s['auto_sessions'])}   자동 확정 정답 {pct(*s['auto_correct'])}   오확정 {pct(*s['auto_wrong'])}")
    print(f"수동 선택 비율     {pct(*s['manual'])}   LLM 사용 {pct(*s['llm_used'])}   오제안 응답 {s['wrong_suggestions']}건")
    if "time_mean" in s:
        print(f"인식 소요(정답 세션) 평균 {s['time_mean']}s · 중앙값 {s['time_median']}s · p90 {s['time_p90']}s")
    print("\nlabel  기대이름        첫제안(방법)                 결과(출처)            초회  시간")
    for r in sorted(results, key=lambda r: (r["label_id"] or "", r["session_id"])):
        mark = "O" if r["first_attempt_success"] else ("x" if r["expected"] else "?")
        fin = f"{(r['final_name'] or '-')[:14]:14s}({r['final_source'] or '-'})"
        fst = f"{(r['first_suggestion'] or '-')[:14]:14s}({r['first_method'] or '-'})"
        print(f"{(r['label_id'] or '-'):5s}  {(r['expected'] or '-')[:14]:14s}  {fst:28s}  {fin:22s}  {mark:^4s}  {r['time_s'] if r['time_s'] is not None else '-'}")


def write_csv(results: list[dict], path: Path) -> None:
    cols = list(results[0].keys()) if results else []
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(results)


def main() -> None:
    ap = argparse.ArgumentParser(description="스캔 로그 집계")
    ap.add_argument("logs", nargs="+", help="scan-*.jsonl")
    ap.add_argument("--manifest", type=Path, help="labels_manifest.csv (정답표)")
    ap.add_argument("--csv", type=Path, help="세션별 결과 CSV 저장")
    ap.add_argument("--strict", action="store_true", help="이름 완전 일치만 정답으로 인정")
    a = ap.parse_args()

    gt = GroundTruth(a.manifest)
    sessions = group_sessions(load_events(Path(p) for p in a.logs))
    if not sessions:
        sys.exit("세션이 없습니다 — session_id 가 있는 로그인지 확인하세요.")
    results = [evaluate_session(sid, ev, gt, a.strict) for sid, ev in sessions.items()]
    print_report(results, summarize(results), "측정 결과 (실측 로그)")
    if a.csv:
        write_csv(results, a.csv)
        print(f"\nCSV: {a.csv}")


if __name__ == "__main__":
    main()
