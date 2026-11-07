"""商店概率：下一刷出现某张牌的概率、需要刷几次。

模型（重要，可能被官方推翻）
---------------------------
官方从未公布抽卡细节。当前实现采用社区逆向文档的假设：

    每个空位独立地从「所有未隐藏、等阶 ≤ 调度中心等级、且池中仍有剩余份数」
    的副本里均匀抽 1 张。

也就是说：**按份数加权**，不是按干员数平均。这带来两个可验证的推论——
低费干员因为份数多（12 份）比高费（5 份）更容易出现；某张牌被买光后，
同阶其它牌的概率会上升。

如果 11/8 之后发现真实规则是「先按等阶 Roll 权重、再在等阶内均匀」，
只需要实现一个新的 :class:`OddsModel` 并在 ``pack.json`` 里切换，UI 不用改。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import TYPE_CHECKING, Any

from ..data.pack import Mode, Pack

if TYPE_CHECKING:  # 避免 calc ←→ state 循环导入
    from ..state.state import GameState

MODEL_UNIFORM_COPIES = "uniform_copies"
MODEL_TIER_WEIGHTS = "tier_weights"


def prob_at_least_one(total: int, copies: int, slots: int) -> float:
    """按独立格抽样估计，至少出现一张指定干员；允许同店重复。"""
    if slots <= 0 or copies <= 0 or total <= 0:
        return 0.0
    p = max(0.0, min(1.0, copies / total))
    return max(0.0, min(1.0, 1.0 - (1.0 - p) ** slots))


def refreshes_for_confidence(p: float, confidence: float) -> int | None:
    """达到目标把握所需的估计刷新次数；零概率时无解。"""
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence 必须在 0 与 1 之间")
    if p <= 0.0:
        return None
    if p >= 1.0:
        return 1
    return max(1, math.ceil(math.log1p(-confidence) / math.log1p(-p)))


@dataclass
class OpOdds:
    op_id: str
    name: str
    tier: int
    remaining: int
    total: int
    slot_prob: float          # 单个商店格出现它的概率估计
    at_least_one: float       # 本次 n 格商店至少出现一次的概率估计
    expected_refreshes: float  # 含命中那一刷的几何分布期望
    refreshes_50: int | None = None
    refreshes_80: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "op_id": self.op_id,
            "name": self.name,
            "tier": self.tier,
            "remaining": self.remaining,
            "total": self.total,
            "slot_prob": round(self.slot_prob, 5),
            "at_least_one": round(self.at_least_one, 5),
            "expected_refreshes": (
                None if self.expected_refreshes == float("inf") else round(self.expected_refreshes, 2)
            ),
            "refreshes_50": self.refreshes_50,
            "refreshes_80": self.refreshes_80,
            "estimated": True,
        }


@dataclass
class TierOdds:
    tier: int
    remaining: int
    operators: int
    slot_prob: float
    at_least_one: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "remaining": self.remaining,
            "operators": self.operators,
            "slot_prob": round(self.slot_prob, 5),
            "at_least_one": round(self.at_least_one, 5),
        }


@dataclass
class OddsResult:
    model: str
    shop_level: int
    slots: int
    eligible_operators: int
    eligible_copies: int
    per_op: dict[str, OpOdds] = field(default_factory=dict)
    per_tier: dict[int, TierOdds] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def odds_of(self, op_id: str) -> OpOdds | None:
        return self.per_op.get(op_id)

    def top(self, n: int = 10, *, tier: int | None = None) -> list[OpOdds]:
        pool = [o for o in self.per_op.values() if tier is None or o.tier == tier]
        pool.sort(key=lambda o: o.slot_prob, reverse=True)
        return pool[:n]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "estimated": True,
            "shop_level": self.shop_level,
            "slots": self.slots,
            "eligible_operators": self.eligible_operators,
            "eligible_copies": self.eligible_copies,
            "per_tier": {str(k): v.to_dict() for k, v in sorted(self.per_tier.items())},
            "per_op": {k: v.to_dict() for k, v in self.per_op.items()},
            "notes": self.notes,
        }


def shop_odds(
    pack: Pack,
    state: GameState,
    *,
    mode: Mode | None = None,
    level: int | None = None,
    copies_by_op: dict[str, int] | None = None,
    slots: int | None = None,
    pool: Any = None,
) -> OddsResult:
    """计算当前商店等级下的出现概率。

    ``copies_by_op`` 可传入自定义剩余份数（用于「如果我再买两张会怎样」的
    推演），默认取当前卡池快照。``pool`` 可以直接传已经算好的
    :class:`~covenant.calc.pool.PoolSnapshot`，避免 snapshot 里重复建一次池。
    """
    from .pool import pool_snapshot, state_extra_inactive  # 局部导入，避免循环

    mode = mode or pack.mode(state.mode_id)
    level = level or state.shop_level
    ops = pack.available_operators(mode, extra_inactive=state_extra_inactive(state, pack))
    snap = pool or pool_snapshot(pack, state, mode=mode, ops=ops)

    n = slots if slots is not None else mode.char_slots(level)
    result = OddsResult(
        model=MODEL_UNIFORM_COPIES,
        shop_level=level,
        slots=n,
        eligible_operators=0,
        eligible_copies=0,
        notes=["各格独立、按剩余份数加权；允许同店重复。官方概率未公布，结果均为估计。"],
    )

    tier_remaining: dict[int, int] = {}
    tier_ops: dict[int, int] = {}
    per_op_remaining: dict[str, int] = {}

    for op in ops:
        if op.tier > level:
            continue
        entry = snap.entries.get(op.id)
        if entry is None:
            continue
        remaining = (
            copies_by_op.get(op.id, entry.remaining)
            if copies_by_op
            else entry.remaining
        )
        if remaining <= 0:
            continue
        per_op_remaining[op.id] = remaining
        tier_remaining[op.tier] = tier_remaining.get(op.tier, 0) + remaining
        tier_ops[op.tier] = tier_ops.get(op.tier, 0) + 1

    total = sum(per_op_remaining.values())
    result.eligible_copies = total
    result.eligible_operators = len(per_op_remaining)

    if total <= 0:
        result.notes.append("池中已没有可选干员（全部买光或被模式排除）")
        return result

    for op_id, remaining in per_op_remaining.items():
        op = pack.op(op_id)
        slot_prob = remaining / total
        p_any = prob_at_least_one(total, remaining, n) if n > 1 else slot_prob
        expected = float("inf") if p_any <= 0 else 1.0 / p_any
        result.per_op[op_id] = OpOdds(
            op_id=op_id,
            name=op.name if op else op_id,
            tier=op.tier if op else 0,
            remaining=remaining,
            total=pack.copies_for(op_id),
            slot_prob=slot_prob,
            at_least_one=p_any,
            expected_refreshes=expected,
            refreshes_50=refreshes_for_confidence(p_any, 0.5),
            refreshes_80=refreshes_for_confidence(p_any, 0.8),
        )

    for tier, remaining in sorted(tier_remaining.items()):
        slot_prob = remaining / total
        p_any = prob_at_least_one(total, remaining, n) if n > 1 else slot_prob
        result.per_tier[tier] = TierOdds(
            tier=tier,
            remaining=remaining,
            operators=tier_ops.get(tier, 0),
            slot_prob=slot_prob,
            at_least_one=p_any,
        )

    return result
