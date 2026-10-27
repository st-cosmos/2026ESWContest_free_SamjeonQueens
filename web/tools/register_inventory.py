#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""실험용 재고 등록 — 앱의 반입 절차(선반 무게 감지) 없이 시약을 DB 에 바로 넣는다.

    cd web
    uv run python tools/register_inventory.py --manifest <labels_manifest.csv>      # 정답표의 name_ko + CAS
    uv run python tools/register_inventory.py --inventory inventory.txt             # "이름" 또는 "이름,CAS" 한 줄에 하나
    uv run python tools/register_inventory.py --manifest ... --reset-learned        # 학습된 별칭·바코드를 지우고 시드 상태로
    uv run python tools/register_inventory.py --list                                # 현재 등록 재고 출력

DATABASE_URL 은 서버와 같은 .env 를 읽는다. 같은 이름이 이미 있으면 건너뛰고 CAS 만 보충한다.
--reset-learned 는 v1/v2 측정을 같은 출발점(시드 사전만 있는 상태)에서 시작하기 위한 옵션이다.
"""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(WEB / ".env")

import database  # noqa: E402
import matching  # noqa: E402
import models  # noqa: E402


def read_manifest(path: Path) -> list[tuple[str, str | None]]:
    items: dict[str, str | None] = {}
    with path.open(encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            name = (row.get("name_ko") or "").strip()
            cas = (row.get("cas_on_label") or "").strip() or None
            if name and name not in items:
                items[name] = cas
            elif name and cas and not items[name]:
                items[name] = cas
    return list(items.items())


def read_inventory(path: Path) -> list[tuple[str, str | None]]:
    items = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, cas = line.partition(",")
        items.append((name.strip(), cas.strip() or None))
    return items


def main() -> None:
    ap = argparse.ArgumentParser(description="실험용 재고 등록")
    ap.add_argument("--manifest", type=Path)
    ap.add_argument("--inventory", type=Path)
    ap.add_argument("--reset-learned", action="store_true", help="학습된 별칭·바코드 매핑 삭제 (시드는 유지)")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    models.Base.metadata.create_all(database.engine)
    db = database.SessionLocal()
    matching.ensure_alias_seed(db)

    if a.reset_learned:
        seed_aliases = {matching.normalize(k) for k in matching.DEFAULT_ALIASES}
        removed = 0
        for al in db.query(models.ChemicalAlias).all():
            if matching.normalize(al.alias) not in seed_aliases:
                db.delete(al)
                removed += 1
        codes = db.query(models.BarcodeMap).delete()
        db.commit()
        print(f"학습 초기화: 별칭 {removed}개, 바코드 {codes}개 삭제")

    items: list[tuple[str, str | None]] = []
    if a.manifest:
        items += read_manifest(a.manifest)
    if a.inventory:
        items += read_inventory(a.inventory)

    added = updated = skipped = 0
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for i, (name, cas) in enumerate(items):
        chem = db.query(models.Chemical).filter(models.Chemical.name == name).first()
        if chem:
            if cas and not chem.cas_no:
                chem.cas_no = cas
                updated += 1
            else:
                skipped += 1
            continue
        db.add(models.Chemical(
            id=f"inv_{datetime.now().strftime('%Y%m%d')}_{i:03d}", name=name, cas_no=cas,
            current_status="비치중", time_in=now, weight=0.5, capacity_kg=0.5,
        ))
        added += 1
    db.commit()
    if items:
        print(f"등록 {added}개, CAS 보충 {updated}개, 이미 있음 {skipped}개")

    if a.list or not items:
        print("\n현재 재고:")
        for c in db.query(models.Chemical).order_by(models.Chemical.name).all():
            print(f"  {c.name:20s} CAS {c.cas_no or '-':12s} {c.current_status}")


if __name__ == "__main__":
    main()
