"""把一堆战绩聚合成 pick 率 / 通关率（胜率）。

定义先说清楚
------------
* **出场率（pick rate）**：在筛选出的对局里，该干员出现在**终局战场**的比例。
  不放整备区，是因为备战时买来又卖掉的过渡牌会把出场率灌水。
* **胜率（win rate）**：带这个干员通关的比例 = 通关场次 / 出场场次。
  卫戍协议是 PvE 合作，没有「名次」，所以「胜」= 通关（结算界面明确通过）。
* **Δ**：该干员胜率减去全体通关率（百分点）。原始胜率会被玩家水平整体抬高或压低，
  看 Δ 比看绝对值更有意义。
* **样本**：出场次数。低于 ``min_sample`` 时前端标灰，不做结论 —— 3 场 2 胜的
  「66.7% 胜率」没有信息量。区间用 Wilson 95%，小样本会自动给得很宽。

偏差提示：这是**自愿上报**的样本（装了这个工具、还愿意点「记录本局」的人），
不是全体玩家随机抽样；强干员会被高手优先拿，所以胜率天然带选择偏差。
看趋势、看 Δ、看样本量，别把小数点后一位当真理。
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from typing import Any, Iterable

from .model import MatchRecord

# 低于这个样本数只展示、不下结论
DEFAULT_MIN_SAMPLE = 5


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """二项比例的 Wilson 置信区间；n=0 时返回 (0, 0)。"""
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - margin), min(1.0, center + margin))


def _rate(k: int, n: int) -> float:
    return (k / n) if n else 0.0


def eligible_for_winrate(record: MatchRecord) -> bool:
    """第一回合未完成就退出的记录保留原始数据，但不进官方胜率口径。"""
    return not (record.result == "quit" and record.rounds <= 1)


def summarize(
    records: Iterable[MatchRecord],
    pack=None,
    *,
    mode_id: str | None = None,
    band_id: str | None = None,
    patch: str | None = None,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    top: int = 300,
) -> dict[str, Any]:
    """聚合记录。``pack`` 只用来补干员/盟约的中文名，可以为 None。"""
    all_records = list(records)
    filtered = [
        r
        for r in all_records
        if (not mode_id or r.mode_id == mode_id)
        and (not band_id or r.band_id == band_id)
        and (not patch or r.patch == patch)
    ]
    rows = [r for r in filtered if eligible_for_winrate(r)]
    matches = len(rows)
    clears = sum(1 for r in rows if r.won)
    baseline = _rate(clears, matches)

    def op_name(op_id: str) -> tuple[str, int]:
        op = pack.operators.get(op_id) if pack is not None else None
        return ((getattr(op, "name", "") or op_id), int(getattr(op, "tier", 0) or 0))

    def bond_name(bid: str) -> str:
        bond = pack.bonds.get(bid) if pack is not None else None
        return getattr(bond, "name", "") or bid

    # -- 干员 -------------------------------------------------------------
    picks: Counter[str] = Counter()
    op_clears: Counter[str] = Counter()
    star_sum: Counter[str] = Counter()
    for r in rows:
        ids = {u.get("op_id") for u in r.board if u.get("op_id")}
        for op_id in ids:  # 同一局同一个干员只算一次
            picks[op_id] += 1
            if r.won:
                op_clears[op_id] += 1
        for u in r.board:
            if u.get("op_id"):
                star_sum[u["op_id"]] += max(1, min(3, int(u.get("star") or 1)))

    operators: list[dict[str, Any]] = []
    for op_id, n in picks.items():
        k = op_clears[op_id]
        name, tier = op_name(op_id)
        lo, hi = wilson_interval(k, n)
        operators.append(
            {
                "op_id": op_id,
                "name": name,
                "tier": tier,
                "picks": n,
                "pick_rate": _rate(n, matches),
                "clears": k,
                "win_rate": _rate(k, n),
                "delta": (_rate(k, n) - baseline) if n else 0.0,
                "ci_low": lo,
                "ci_high": hi,
                "avg_star": (star_sum[op_id] / n) if n else 1.0,
                "sample_ok": n >= min_sample,
            }
        )
    operators.sort(key=lambda x: (-x["pick_rate"], -x["win_rate"], x["tier"], x["name"]))
    operators = operators[:top]

    # -- 盟约 -------------------------------------------------------------
    bond_picks: Counter[str] = Counter()
    bond_clears: Counter[str] = Counter()
    for r in rows:
        for bid in r.bonds:
            bond_picks[bid] += 1
            if r.won:
                bond_clears[bid] += 1
    bonds = [
        {
            "id": bid,
            "name": bond_name(bid),
            "picks": n,
            "pick_rate": _rate(n, matches),
            "clears": bond_clears[bid],
            "win_rate": _rate(bond_clears[bid], n),
            "delta": (_rate(bond_clears[bid], n) - baseline) if n else 0.0,
            "sample_ok": n >= min_sample,
        }
        for bid, n in bond_picks.items()
    ]
    bonds.sort(key=lambda x: (-x["pick_rate"], -x["win_rate"]))

    # -- 策略（开局分队） -------------------------------------------------
    band_rows: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = r.band_id or ""
        item = band_rows.setdefault(
            key,
            {
                "id": key,
                "name": r.band_name or ("未选策略" if not key else key),
                "matches": 0,
                "clears": 0,
            },
        )
        item["matches"] += 1
        if r.won:
            item["clears"] += 1
    bands = [
        {**it, "win_rate": _rate(it["clears"], it["matches"]), "sample_ok": it["matches"] >= min_sample}
        for it in band_rows.values()
    ]
    bands.sort(key=lambda x: (-x["matches"], -x["win_rate"]))

    # -- 模式 -------------------------------------------------------------
    mode_rows: dict[str, dict[str, Any]] = {}
    for r in rows:
        item = mode_rows.setdefault(
            r.mode_id,
            {"id": r.mode_id, "name": r.mode_name or r.mode_id, "matches": 0, "clears": 0},
        )
        item["matches"] += 1
        if r.won:
            item["clears"] += 1
    modes = [{**it, "win_rate": _rate(it["clears"], it["matches"])} for it in mode_rows.values()]
    modes.sort(key=lambda x: -x["matches"])

    # -- 阵容（终局盟约组合） ---------------------------------------------
    comp_rows: dict[tuple[str, ...], dict[str, int]] = defaultdict(lambda: {"matches": 0, "clears": 0})
    for r in rows:
        key = tuple(sorted(r.bonds.keys()))
        if len(key) < 2:
            continue
        comp_rows[key]["matches"] += 1
        if r.won:
            comp_rows[key]["clears"] += 1
    comps = [
        {
            "bonds": list(key),
            "name": " + ".join(bond_name(b) for b in key),
            "matches": it["matches"],
            "clears": it["clears"],
            "win_rate": _rate(it["clears"], it["matches"]),
            "sample_ok": it["matches"] >= min_sample,
        }
        for key, it in comp_rows.items()
    ]
    comps.sort(key=lambda x: (-x["matches"], -x["win_rate"]))
    comps = comps[:24]

    # -- 按回合（打到该回合的人里，谁在场） -------------------------------
    round_buckets: dict[int, dict[str, Any]] = {}
    for r in rows:
        for snap in r.timeline:
            try:
                rd = int(snap.get("round") or 0)
            except (TypeError, ValueError):
                continue
            if rd <= 0:
                continue
            b = round_buckets.setdefault(
                rd,
                {"round": rd, "records": 0, "levels": [], "golds": [], "boards": [], "ops": Counter()},
            )
            b["records"] += 1
            lvl = snap.get("shop_level")
            gold = snap.get("gold")
            if isinstance(lvl, (int, float)):
                b["levels"].append(int(lvl))
            if isinstance(gold, (int, float)):
                b["golds"].append(int(gold))
            ids = {str(x) for x in (snap.get("board") or []) if x}
            b["boards"].append(len(ids))
            b["ops"].update(ids)

    by_round: list[dict[str, Any]] = []
    for rd in sorted(round_buckets):
        b = round_buckets[rd]
        n = b["records"]
        ops = []
        for op_id, k in b["ops"].most_common(24):
            name, tier = op_name(op_id)
            ops.append(
                {
                    "op_id": op_id,
                    "name": name,
                    "tier": tier,
                    "picks": k,
                    "pick_rate": _rate(k, n),
                    "sample_ok": k >= min_sample,
                }
            )
        by_round.append(
            {
                "round": rd,
                "records": n,
                "avg_shop_level": (sum(b["levels"]) / len(b["levels"])) if b["levels"] else None,
                "avg_gold": (sum(b["golds"]) / len(b["golds"])) if b["golds"] else None,
                "avg_board": (sum(b["boards"]) / n) if n else 0.0,
                "ops": ops,
            }
        )

    # -- 汇总 -------------------------------------------------------------
    round_vals = [r.rounds for r in rows if r.rounds > 0]
    hp_vals = [r.hp_left for r in rows if r.hp_left is not None]
    cup_vals = [r.cups for r in rows if r.cups is not None]

    return {
        "ok": True,
        "generated_at": time.time(),
        "filters": {
            "mode_id": mode_id or "",
            "band_id": band_id or "",
            "patch": patch or "",
            "min_sample": min_sample,
        },
        "patches": sorted({r.patch for r in all_records if r.patch}, reverse=True),
        "modes_available": sorted({(r.mode_id, r.mode_name) for r in all_records if r.mode_id}),
        "totals": {
            "matches": matches,
            "raw_matches": len(filtered),
            "excluded_early_quit": len(filtered) - matches,
            "clears": clears,
            "clear_rate": baseline,
            "avg_rounds": (sum(round_vals) / len(round_vals)) if round_vals else 0.0,
            "avg_hp": (sum(hp_vals) / len(hp_vals)) if hp_vals else None,
            "avg_cups": (sum(cup_vals) / len(cup_vals)) if cup_vals else None,
            "records_total": len(all_records),
        },
        "operators": operators,
        "bonds": bonds,
        "bands": bands,
        "modes": modes,
        "comps": comps,
        "by_round": by_round,
        "notes": [
            "出场率 = 终局战场出现该干员的对局占比；胜率 = 其中通关的比例。",
            f"样本少于 {min_sample} 场的数据只作参考，灰色显示。",
            "按回合的在场率只算「打到该回合」的对局，不是全部对局。",
            "第一回合未完成的退出不进入官方胜率口径，原始记录仍保留。",
            "数据来自本机记录（或导入的社区文件），非全体玩家随机样本。",
        ],
    }
