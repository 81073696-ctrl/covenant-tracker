"""把逆向出来的原始数据，转换成程序用的数据包。

用法::

    python scripts/build_pack.py                 # 读 _refs/stronghold，写 packs/act2autochess
    python scripts/build_pack.py --refs D:/x --out packs/act2autochess-2026-11

原始数据来源（都是公开资料，不含任何客户端内存读取）：
* sganggs/Stronghold-Protocol —— 对 ``activity_table.json`` (act2autochess) 的逆向整理
* 官方公告、PRTS、NGA/B站社区实测

如果 11/8 上线后数值变了，改这里的映射或直接改生成出来的 JSON 都行；
程序只认 schema，不认具体数字。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REFS = ROOT.parent / "_refs" / "stronghold"
DEFAULT_OUT = ROOT / "packs" / "act2autochess"

TAG_RE = re.compile(r"<[^>]{1,60}>")
THRESHOLD_RE = re.compile(r"在场\s*(\d+)\s*名不同")


def clean(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    text = TAG_RE.sub("", text)
    text = text.replace("\\n", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# 各表转换
# ---------------------------------------------------------------------------


def build_operators(d3: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in d3.get("chess", []):
        if c.get("isGolden"):
            continue
        op = {
            "id": c["chessId"],
            "name": c.get("name") or c["chessId"],
            "tier": int(c.get("tier", 1)),
            "bonds": list(c.get("bondIds") or []),
            "hidden": bool(c.get("isHidden", False)),
            "kind": c.get("chessType", "NORMAL"),
            "golden_id": c.get("goldenChessId"),
            "profession": c.get("profession", ""),
            "sub": c.get("subProfessionName", ""),
            "rarity": int(c.get("rarity", 0)),
            "price": int(c.get("purchasePrice", 0) or 0),
            "sell": int(c.get("chessSoldPrice", 1) or 1),
            "merge_num": int(c.get("upgradeNum", 3) or 3),
        }
        if not op["price"]:
            op["price"] = {1: 2, 2: 3, 3: 3, 4: 3, 5: 4, 6: 4}.get(op["tier"], 0)
        out.append(op)
    out.sort(key=lambda o: (o["tier"], o["name"]))
    return out


def build_bonds(d2: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in d2.get("bonds", []):
        desc = clean(b.get("effectDescResolved") or b.get("effectDesc") or b.get("desc"))
        thresholds = {int(x) for x in THRESHOLD_RE.findall(desc)}
        for p in b.get("activeParamList") or []:
            if str(p).isdigit():
                thresholds.add(int(p))
        activate = int(b.get("activeCount", 2) or 2)
        thresholds = {t for t in thresholds if 0 < t <= 12}
        out.append(
            {
                "id": b["bondId"],
                "name": b.get("name", b["bondId"]),
                "category": b.get("category", "ADD_ON"),
                "activate_at": activate,
                "in_deck": bool(b.get("isActiveInDeck", False)),
                "power": bool(b.get("isPower", False)),
                "desc": desc,
                "thresholds": sorted(t for t in thresholds if t > activate),
            }
        )
    out.sort(key=lambda b: (b["category"] != "CORE", b["name"]))
    return out


def build_bands(d1: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for b in d1.get("bands", []):
        out.append(
            {
                "id": b["bandId"],
                "name": b.get("name", b["bandId"]),
                "hp": int(b.get("totalHp", 0) or 0),
                "effect_name": b.get("effectName", ""),
                "desc": clean(b.get("desc")),
                "modes": list(b.get("modeTypeList") or []),
                "victor_count": int(b.get("victorCount", 0) or 0),
            }
        )
    out.sort(key=lambda x: (x["hp"], x["name"]))
    return out


def build_modes(d1: dict[str, Any]) -> list[dict[str, Any]]:
    modes: list[dict[str, Any]] = []
    shop_levels = d1.get("shopLevels", {})
    rounds = d1.get("rounds", {})
    for mid, m in d1.get("modes", {}).items():
        rows = []
        for r in shop_levels.get(mid, []):
            rows.append(
                {
                    "level": int(r["shopLevel"]),
                    "upgrade_price": int(r.get("initialUpgradePrice", 99)),
                    "char_slots": int(r.get("charChessCount", 3)),
                    "item_slots": int(r.get("itemCount", 0)),
                }
            )
        modes.append(
            {
                "id": mid,
                "name": m.get("name", mid),
                "code": m.get("code", ""),
                "mode_type": m.get("modeType", "SINGLE"),
                "difficulty": m.get("modeDifficulty", "FUNNY"),
                "rounds": len(rounds.get(mid, [])),
                "shop_levels": rows,
                "active_bonds": list(m.get("activeBondIdList") or []),
                "inactive_bonds": list(m.get("inactiveBondIdList") or []),
            }
        )
    modes.sort(key=lambda x: (x["mode_type"], x["id"]))
    return modes


def build_pack_meta(
    d1: dict[str, Any],
    *,
    pack_id: str,
    season: str,
    updated: str,
) -> dict[str, Any]:
    const = d1.get("constData", {}) or {}
    income = d1.get("incomePerRound", {}) or {}
    price = {
        int(k): int((v[0].get("purchasePrice") if isinstance(v, list) and v else 0) or 0)
        for k, v in (d1.get("charPriceByTier") or {}).items()
        if str(k).isdigit()
    }
    pool_copies = {
        k: int(v)
        for k, v in (d1.get("poolCopiesPerOperatorByTier") or {}).items()
        if str(k).isdigit()
    }
    return {
        "schema": 1,
        "id": pack_id,
        "name": "卫戍协议：盟约",
        "season": season,
        "updated": updated,
        "default_mode": "mode_single_funny",
        "source": [
            {
                "name": "Stronghold-Protocol（同人复刻 + 数据逆向）",
                "url": "https://github.com/sganggs/Stronghold-Protocol",
                "license": "GPL-3.0",
                "note": "act2autochess 活动表的结构化整理；数值以客户端数据表为准",
            },
            {
                "name": "官方公告《卫戍协议：盟约》",
                "url": "https://ak.hypergryph.com/news/5114",
                "license": "© 鹰角网络",
                "note": "玩法介绍与官方截图",
            },
            {
                "name": "PRTS / NGA / B站社区实测",
                "url": "https://prts.wiki/",
                "license": "CC BY-NC-SA",
                "note": "卡池份数、收入曲线等未在客户端明示的数值",
            },
        ],
        "confidence": {
            "modes": "data",
            "shop.rules": "data",
            "operators": "data",
            "bonds": "data",
            "bands": "data",
            "economy.refresh_cost": "data",
            "economy.upgrade_price": "data",
            "economy.board_max": "data",
            "economy.income": "community",
            "pool.copies_by_tier": "community",
            "economy.gold_carry_over": "assumed",
            "shop.odds_model": "assumed",
        },
        "economy": {
            "refresh_cost": int(const.get("shopRefreshPrice", 1) or 1),
            "sell_price": 1,
            "income_base": int(income.get("R1", 4)) - 1,
            "income_cap": int(income.get("CAP", 10) or 10),
            "income_per_round_bonus": 0,
            "upgrade_decay_per_round": int(
                (d1.get("upgradeRules") or {}).get("upgradePriceDecreasePerRound", 1) or 1
            ),
            "board_max": int(const.get("maxBattleChessCnt", 8) or 8),
            "bench_max": int(const.get("maxDeckChessCnt", 10) or 10),
            "temp_max": 5,
            "max_shop_level": 6,
            "gold_carry_over": True,
            "price_by_tier": {str(k): v for k, v in sorted(price.items())},
        },
        "pool": {
            "copies_by_tier": {str(k): v for k, v in sorted(pool_copies.items())},
            "golden_copies": int((d1.get("merge") or {}).get("copiesToGolden", 3) or 3),
        },
        "modes": build_modes(d1),
        "_notes": [
            "收入公式含推测成分：R1..R3 有证据，之后每回合 +1 与上限 10 属社区假设。",
            "抽卡模型（每格按剩余份数均匀）为社区假设，官方未公布。",
            "资金是否跨回合保留存在歧义，默认保留；可在界面里切换。",
            "11/8 新赛季上线后，请用新的 activity_table 数据重新生成本包。",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成卫戍协议数据包")
    ap.add_argument("--refs", type=Path, default=DEFAULT_REFS)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--id", default="act2autochess-2026-03")
    ap.add_argument("--season", default="act2autochess（2026-03 赛季）")
    ap.add_argument("--updated", default="2026-03-14")
    args = ap.parse_args(argv)

    refs = args.refs
    need = ["01-core-data.json", "02-bonds.json", "03-operators.json"]
    missing = [n for n in need if not (refs / n).exists()]
    if missing:
        print(f"缺少原始数据：{missing}（目录 {refs}）", file=sys.stderr)
        return 2

    d1 = read_json(refs / "01-core-data.json")
    d2 = read_json(refs / "02-bonds.json")
    d3 = read_json(refs / "03-operators.json")

    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    meta = build_pack_meta(d1, pack_id=args.id, season=args.season, updated=args.updated)
    operators = build_operators(d3)
    bonds = build_bonds(d2)
    bands = build_bands(d1)

    (out / "pack.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out / "operators.json").write_text(
        json.dumps({"items": operators}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out / "bonds.json").write_text(
        json.dumps({"items": bonds}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (out / "bands.json").write_text(
        json.dumps({"items": bands}, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    print(f"已生成数据包 → {out}")
    print(f"  干员 {len(operators)}  盟约 {len(bonds)}  策略 {len(bands)}  模式 {len(meta['modes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
