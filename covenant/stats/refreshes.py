"""从过程事件生成刷新历史与 D 牌实测；不读取或伪造终局摘要。"""

from __future__ import annotations

from collections import Counter
from typing import Any, Iterable

from ..state.events import MatchEvent


def _action(event: MatchEvent) -> dict[str, Any]:
    raw = event.payload.get("action") or {}
    return raw if isinstance(raw, dict) else {}


def _shop_ids(payload: dict[str, Any]) -> list[str | None]:
    offers = payload.get("shop_after") or []
    if not isinstance(offers, list):
        return []
    return [
        (str(item.get("op_id")) if isinstance(item, dict) and item.get("op_id") else None)
        for item in offers
    ]


def refresh_history(events: Iterable[MatchEvent]) -> list[dict[str, Any]]:
    """配对刷新与商店证据；未归因变化保留审计，但不进入命中率。"""
    history: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    for event in sorted(events, key=lambda e: e.seq):
        if event.type in ("refresh", "refresh_observed", "shop_change_observed"):
            payload = event.payload
            automatic = event.type in ("refresh_observed", "shop_change_observed")
            action_args = _action(event).get("args") or {}
            pending = {
                "seq": event.seq,
                "round": event.round_no,
                "shop_level": payload.get("shop_level"),
                "gold_before": payload.get("gold_before"),
                "gold_after": payload.get("gold_after"),
                "confirmed": automatic,
                "shop": _shop_ids(payload) if automatic else [],
                "source": _action(event).get("source", "manual"),
                "refresh_type": payload.get("refresh_type") or action_args.get("refresh_type") or "normal",
                "refresh_source": payload.get("refresh_source") or action_args.get("refresh_source") or "standard",
                "attribution": payload.get("attribution") or ("observed" if automatic else "manual"),
            }
            history.append(pending)
            if automatic:
                pending = None
            continue
        if event.type in ("next_round", "match_start", "match_end"):
            pending = None
            continue
        if event.type == "set_shop" and pending is not None and not pending["confirmed"]:
            if _action(event).get("source", "manual") != "manual":
                continue
            pending["shop"] = _shop_ids(event.payload)
            pending["confirmed"] = True
            pending["confirmed_seq"] = event.seq
            pending = None
    return history


def summarize_refreshes(
    events: Iterable[MatchEvent],
    *,
    shop_level: int | None = None,
    min_sample: int = 5,
) -> dict[str, Any]:
    """汇总可归因刷新；同一干员同刷命中只计一次，出现格数另计。"""
    all_history = refresh_history(events)
    history = [
        item for item in all_history
        if shop_level is None or item.get("shop_level") == shop_level
    ]
    confirmed = [item for item in history if item["confirmed"] and item["refresh_type"] != "unknown"]
    unattributed = [item for item in history if item["confirmed"] and item["refresh_type"] == "unknown"]
    hits: Counter[str] = Counter()
    appearances: Counter[str] = Counter()
    costs: Counter[str] = Counter()
    by_type: Counter[str] = Counter(item["refresh_type"] for item in confirmed)
    for item in confirmed:
        ids = [op_id for op_id in item["shop"] if op_id]
        try:
            cost = max(0, int(item["gold_before"]) - int(item["gold_after"]))
        except (TypeError, ValueError):
            cost = 0
        for op_id in set(ids):
            hits[op_id] += 1
            costs[op_id] += cost
        appearances.update(ids)

    total = len(confirmed)
    operators: list[dict[str, Any]] = []
    for op_id in sorted(set(hits) | set(appearances)):
        n = hits[op_id]
        operators.append({
            "op_id": op_id,
            "refreshes": total,
            "hits": n,
            "appearances": appearances[op_id],
            "hit_rate": n / total if total else 0.0,
            "avg_cost_per_hit": costs[op_id] / n if n else None,
            "sample_ok": total >= max(1, min_sample),
        })
    operators.sort(key=lambda row: (-row["hit_rate"], -row["hits"], row["op_id"]))
    by_op = {row["op_id"]: row for row in operators}
    notes = ["实测包含人工确认及高置信度自动同步；未归因商店变化不进入命中率。"]
    if total < max(1, min_sample):
        notes.append(f"样本不足：当前 {total} 次确认刷新，建议至少 {max(1, min_sample)} 次。")
    return {
        "source": "process_events",
        "filters": {"shop_level": shop_level, "min_sample": max(1, min_sample)},
        "totals": {
            "refreshes": total,
            "unconfirmed": len([item for item in history if not item["confirmed"]]),
            "unattributed": len(unattributed),
            "by_type": dict(sorted(by_type.items())),
            "cost": sum(
                max(0, int(item["gold_before"]) - int(item["gold_after"]))
                for item in confirmed
                if isinstance(item.get("gold_before"), int) and isinstance(item.get("gold_after"), int)
            ),
        },
        "operators": operators,
        "by_op": by_op,
        "history": history,
        "notes": notes,
    }
