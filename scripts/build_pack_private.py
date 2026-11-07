"""从 Stronghold-Protocol 私服数据生成数据包。

与 ``build_pack.py``（GitHub 研究文档）的区别：这里直接读私服客户端的
``data/config.json``、``chess.json``、``bonds.json``、``bands.json``，
字段更全（含 charId、stats、thresholds、countMode），并且商店表、收入、
卡池、禁用盟约都来自同一份 config。

用法::

    python scripts/build_pack_private.py
    python scripts/build_pack_private.py --refs _refs/private --out packs/act2autochess-private
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
TAG_RE = re.compile(r"<[^<>]{1,40}>")


def clean(text: Any) -> str:
    """去掉客户端富文本标签，保留可读文本。"""
    s = str(text or "")
    s = TAG_RE.sub("", s)
    return s.replace("<@ba.vup>", "").replace("</>", "").strip()


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def build_operators(chess: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, c in chess.items():
        if c.get("isGolden"):
            continue
        out.append(
            {
                "id": key,
                "name": c.get("name") or key,
                "tier": int(c.get("tier") or 1),
                "bonds": list(c.get("bonds") or []),
                "hidden": bool(c.get("isHidden")),
                "kind": c.get("chessType") or "NORMAL",
                "golden_id": c.get("goldenId"),
                "profession": c.get("profession") or "",
                "sub": c.get("subProfessionName") or c.get("subProfessionId") or "",
                "rarity": int(c.get("rarity") or 0),
                "price": int(c.get("price") or 0),
                "sell": int(c.get("sellPrice") or 1),
                "merge_num": int(c.get("upgradeNum") or 3),
                "char_id": c.get("charId") or "",
                "shop_sort": int(c.get("shopSortId") or 0),
            }
        )
    out.sort(key=lambda o: (o["tier"], o["shop_sort"], o["name"]))
    return out


def build_bonds(bonds: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, b in bonds.items():
        out.append(
            {
                "id": key,
                "name": b.get("name") or key,
                "category": "CORE" if b.get("isCore") else "ADD_ON",
                "activate_at": int(b.get("activeCount") or 2),
                "in_deck": bool(b.get("countsHand") or b.get("isActiveInDeck")),
                "power": bool(b.get("isCore")),
                "desc": clean(b.get("desc")),
                "thresholds": [int(x) for x in (b.get("thresholds") or [])],
                "count_mode": b.get("countMode") or "BOARD",
                "weight": b.get("weight"),
            }
        )
    out.sort(key=lambda x: (x["category"], x["id"]))
    return out


def build_bands(bands: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for key, b in bands.items():
        out.append(
            {
                "id": key,
                "name": b.get("name") or key,
                "hp": int(b.get("totalHp") or 0),
                "effect_name": b.get("effectName") or "",
                "desc": clean(b.get("desc")),
                "modes": list(b.get("modeTypeList") or []),
                "victor_count": b.get("victorCount"),
            }
        )
    out.sort(key=lambda x: (x["modes"], x["name"]))
    return out


def build_modes(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mode_id, m in (cfg.get("modes") or {}).items():
        prices = list(m.get("upgradePrices") or [])
        slots = m.get("shopSlots") or {}
        levels: list[dict[str, Any]] = []
        for lv in range(1, 7):
            s = slots.get(str(lv)) or {}
            price = prices[lv - 1] if lv - 1 < len(prices) else 99
            levels.append(
                {
                    "level": lv,
                    "upgrade_price": int(price),
                    "char_slots": int(s.get("chess") or 3),
                    "item_slots": int(s.get("item") or 0),
                }
            )
        out.append(
            {
                "id": mode_id,
                "name": m.get("name") or mode_id,
                "code": m.get("code") or "",
                "mode_type": m.get("type") or "SINGLE",
                "difficulty": m.get("difficulty") or "FUNNY",
                "rounds": int(m.get("lastRound") or 0),
                "shop_levels": levels,
                "active_bonds": list(m.get("activeBondIds") or []),
                "inactive_bonds": list(m.get("inactiveBondIds") or []),
            }
        )
    out.sort(key=lambda x: (x["mode_type"], x["id"]))
    return out


def build_economy(cfg: dict[str, Any]) -> dict[str, Any]:
    eco = cfg.get("economy") or {}
    prices = eco.get("chessPrice") or {}
    price_by_tier = {
        str(k): int(((v or {}).get("normal") if isinstance(v, dict) else v) or 0)
        for k, v in prices.items()
        if str(k).isdigit()
    }
    return {
        "refresh_cost": int(eco.get("refreshPrice") or 1),
        "sell_price": 1,
        "income_base": 3,
        "income_cap": int(eco.get("incomeCap") or 12),
        "income_per_round_bonus": 0,
        "upgrade_decay_per_round": 1,
        "upgrade_reset_on_upgrade": True,
        "board_max": int(eco.get("deployCap") or 8),
        "bench_max": int(eco.get("benchSize") or 10),
        "temp_max": int(eco.get("tempSize") or 5),
        "max_shop_level": 6,
        "gold_carry_over": not bool(eco.get("leftoverFundsLost", True)),
        "price_by_tier": price_by_tier,
    }


def build_pool(cfg: dict[str, Any]) -> dict[str, Any]:
    eco = cfg.get("economy") or {}
    copies = eco.get("poolCopies") or {}
    return {
        "copies_by_tier": {str(k): int(v) for k, v in copies.items()},
        "golden_copies": int(eco.get("goldenCopies") or 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--refs", default="_refs/private")
    ap.add_argument("--out", default="packs/act2autochess-private")
    ap.add_argument("--id", default="act2autochess")
    ap.add_argument("--season", default="2026-03-priv")
    ap.add_argument("--updated", default=date.today().isoformat())
    args = ap.parse_args()

    refs = Path(args.refs)
    if not refs.is_absolute():
        refs = ROOT / refs
    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)

    cfg = load(refs / "config.json")
    chess = load(refs / "chess.json")
    bonds = load(refs / "bonds.json")
    bands = load(refs / "bands.json")

    from covenant.version import PACK_SCHEMA

    meta = {
        "schema": PACK_SCHEMA,
        "id": args.id,
        "name": "卫戍协议：盟约（私服数据包）",
        "season": args.season,
        "updated": args.updated,
        "default_mode": "mode_single_funny",
        "source": [
            {
                "name": "Stronghold-Protocol 私服 data/*.json",
                "url": "https://github.com/sganggs/Stronghold-Protocol",
                "note": "config / chess / bonds / bands，字段与私服服务端同源",
                "confidence": "data",
            },
            {
                "name": "私服 server/match/pool.js",
                "note": "独立格、按份数加权、允许同店重复、展示不占池",
                "confidence": "data",
            },
        ],
        "confidence": {
            "operators": "data",
            "bonds": "data",
            "shop_levels": "data",
            "upgrade_price": "data",
            "income": "data",
            "pool": "data",
            "odds_model": "data",
            "bans": "data",
            "merge": "data",
        },
        "economy": build_economy(cfg),
        "pool": build_pool(cfg),
        "modes": build_modes(cfg),
        "notes": [
            "私服为旧版本复刻；正式服 11/8 更新后需重新核对 modes / economy / bans。",
            "商店抽卡依据 server/match/pool.js：每格独立、按剩余份数加权、允许同店重复。",
        ],
    }

    (out / "pack.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "operators.json").write_text(
        json.dumps({"items": build_operators(chess)}, ensure_ascii=False),
        encoding="utf-8",
    )
    (out / "bonds.json").write_text(
        json.dumps({"items": build_bonds(bonds)}, ensure_ascii=False), encoding="utf-8"
    )
    (out / "bands.json").write_text(
        json.dumps({"items": build_bands(bands)}, ensure_ascii=False), encoding="utf-8"
    )

    ops = build_operators(chess)
    tier_count: dict[int, int] = {}
    for o in ops:
        tier_count[o["tier"]] = tier_count.get(o["tier"], 0) + 1
    print(f"写入 {out}")
    print(f"  干员 {len(ops)}（按阶 {dict(sorted(tier_count.items()))}）")
    print(f"  盟约 {len(build_bonds(bonds))}，策略 {len(build_bands(bands))}，模式 {len(meta['modes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
