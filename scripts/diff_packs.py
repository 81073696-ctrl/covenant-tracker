"""对比两个数据包，输出新增/删除/改动。

明天（11/8）官方更新后：
    py -3 scripts/diff_packs.py packs/act2autochess packs/act2autochess-20261108

或者先跑 `python -m covenant pack --refs ...` 重新生成，再 diff 旧包与备份。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from covenant.data.pack import Pack  # noqa: E402


def _ops(pack: Pack) -> dict:
    return {
        op_id: {
            "name": op.name,
            "tier": op.tier,
            "price": op.price,
            "bonds": tuple(sorted(op.bonds)),
            "hidden": op.hidden,
        }
        for op_id, op in pack.operators.items()
    }


def _bonds(pack: Pack) -> dict:
    return {
        bid: {
            "name": bond.name,
            "thresholds": tuple(bond.thresholds or ()),
            "in_deck": bool(bond.in_deck),
        }
        for bid, bond in pack.bonds.items()
    }


def _modes(pack: Pack) -> dict:
    out = {}
    for mode_id, mode in pack.modes.items():
        out[mode_id] = {
            "name": mode.name,
            "rounds": mode.rounds,
            "shop": [
                (lvl.level, lvl.upgrade_price, lvl.char_slots, lvl.item_slots)
                for lvl in (mode.shop_levels or [])
            ],
        }
    return out


def diff(old: Pack, new: Pack) -> list[str]:
    lines: list[str] = []

    def section(title: str) -> None:
        lines.append("")
        lines.append(f"## {title}")

    ao, an = _ops(old), _ops(new)
    added, removed = sorted(set(an) - set(ao)), sorted(set(ao) - set(an))
    changed = [
        oid
        for oid in sorted(set(ao) & set(an))
        if ao[oid] != an[oid]
    ]
    section(f"干员：{len(ao)} → {len(an)}（+{len(added)} / -{len(removed)} / ~{len(changed)}）")
    for oid in added[:40]:
        o = an[oid]
        lines.append(f"+ {o['name']}  T{o['tier']}  {o['price']}金  {','.join(o['bonds'])}")
    if len(added) > 40:
        lines.append(f"  …… 还有 {len(added) - 40} 个新增")
    for oid in removed[:40]:
        o = ao[oid]
        lines.append(f"- {o['name']}（{oid}）")
    for oid in changed[:60]:
        a, b = ao[oid], an[oid]
        fields = []
        for key in ("name", "tier", "price", "bonds", "hidden"):
            if a[key] != b[key]:
                fields.append(f"{key}: {a[key]} → {b[key]}")
        lines.append(f"~ {a['name']}（{oid}）{'；'.join(fields)}")
    if len(changed) > 60:
        lines.append(f"  …… 还有 {len(changed) - 60} 个改动")

    bo, bn = _bonds(old), _bonds(new)
    section(f"盟约：{len(bo)} → {len(bn)}")
    for bid in sorted(set(bn) - set(bo)):
        lines.append(f"+ {bn[bid]['name']}（{bid}）阈值 {bn[bid]['thresholds']}")

    for bid in sorted(set(bo) & set(bn)):
        if bo[bid] != bn[bid]:
            a, b = bo[bid], bn[bid]
            lines.append(
                f"~ {a['name']}：阈值 {a['thresholds']} → {b['thresholds']}"
                f"，in_deck {a['in_deck']} → {b['in_deck']}"
            )

    mo, mn = _modes(old), _modes(new)
    section("模式 / 商店表")
    for mid in sorted(set(mn) - set(mo)):
        lines.append(f"+ {mn[mid]['name']}（{mid}），{mn[mid]['rounds']} 回合")
    for mid in sorted(set(mo) & set(mn)):
        if mo[mid] != mn[mid]:
            lines.append(f"~ {mn[mid]['name']}（{mid}）")
            if mo[mid]["rounds"] != mn[mid]["rounds"]:
                lines.append(f"    回合 {mo[mid]['rounds']} → {mn[mid]['rounds']}")
            if mo[mid]["shop"] != mn[mid]["shop"]:
                lines.append(f"    商店表 {mo[mid]['shop']} → {mn[mid]['shop']}")

    eo, en = old.economy, new.economy
    section("经济")
    for key in (
        "refresh_cost",
        "sell_price",
        "income_base",
        "income_cap",
        "upgrade_decay_per_round",
        "board_max",
        "bench_max",
        "gold_carry_over",
    ):
        a, b = getattr(eo, key, None), getattr(en, key, None)
        if a != b:
            lines.append(f"~ {key}: {a} → {b}")
    if old.pool_copies != new.pool_copies:
        lines.append(f"~ 池份数: {old.pool_copies} → {new.pool_copies}")
    if list(getattr(old, "core_bond_groups", []) or []) != list(getattr(new, "core_bond_groups", []) or []):
        lines.append(f"~ 核心盟约组: {getattr(old, 'core_bond_groups', [])} → {getattr(new, 'core_bond_groups', [])}")

    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="对比两个卫戍协议数据包")
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--json", action="store_true", help="输出机器可读摘要")
    args = ap.parse_args(argv)

    old, new = Pack.load(args.old), Pack.load(args.new)
    if args.json:
        payload = {
            "old": {"id": old.id, "season": old.season, "operators": len(old.operators)},
            "new": {"id": new.id, "season": new.season, "operators": len(new.operators)},
            "added": sorted(set(new.operators) - set(old.operators)),
            "removed": sorted(set(old.operators) - set(new.operators)),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"旧：{old.id} / {old.season} / {len(old.operators)} 人 / {len(old.bonds)} 盟约")
    print(f"新：{new.id} / {new.season} / {len(new.operators)} 人 / {len(new.bonds)} 盟约")
    print("\n".join(diff(old, new)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
