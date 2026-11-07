"""卡池余量。

规则（来自客户端数据表 + 社区实测）
------------------------------------
* 每种干员在每个等阶有固定份数（I..VI = 12/14/18/16/8/5，以数据包为准）；
* **精锐占 3 份**（三张合一），所以一张 2 星牌等价于消耗了 3 份；
* 商店里「展示中」的副本**不占池**，只有买下来（进整备区/战场）才占；
* 多人模式下卡池由全队共享，别人的购买这里默认看不到 —— 通过
  ``pool_extra`` 手工补记，见 :func:`pool_extra`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..data.pack import Mode, Pack

if TYPE_CHECKING:  # 避免 calc ←→ state 循环导入
    from ..state.state import GameState


@dataclass
class PoolEntry:
    op_id: str
    name: str
    tier: int
    total: int
    consumed: int

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.consumed)

    @property
    def depleted(self) -> bool:
        return self.total > 0 and self.remaining <= 0


@dataclass
class PoolSnapshot:
    entries: dict[str, PoolEntry] = field(default_factory=dict)
    by_tier: dict[int, dict[str, int]] = field(default_factory=dict)
    total: int = 0
    consumed: int = 0
    model_note: str = ""

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.consumed)

    def remaining_of(self, op_id: str) -> int:
        e = self.entries.get(op_id)
        return e.remaining if e else 0

    def tier_remaining(self, tier: int) -> int:
        return int(self.by_tier.get(tier, {}).get("remaining", 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "consumed": self.consumed,
            "remaining": self.remaining,
            "by_tier": {str(k): v for k, v in sorted(self.by_tier.items())},
            "entries": {
                k: {
                    "name": e.name,
                    "tier": e.tier,
                    "total": e.total,
                    "consumed": e.consumed,
                    "remaining": e.remaining,
                }
                for k, e in self.entries.items()
            },
            "model_note": self.model_note,
        }


def pool_extra(state: GameState) -> dict[str, int]:
    """手工补记的「别人已经拿走」的份数，存在 ``rules.pool_extra`` 里。"""
    raw = state.rules.get("pool_extra") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = max(0, int(v))
        except (TypeError, ValueError):
            continue
    return out



def state_extra_inactive(state, pack) -> tuple[str, ...]:
    """本局开局「核心盟约二选一」带来的额外禁用盟约。

    选了第 i 组，则其它组的盟约在本次模拟中不出现；没选或数据包没配
    这个机制时返回空元组，行为与旧版本完全一致。
    """
    groups = getattr(pack, "core_bond_groups", None) or []
    if len(groups) < 2:
        return ()
    choice = state.choices.get("bond_group") if hasattr(state, "choices") else None
    if not isinstance(choice, int) or not (0 <= choice < len(groups)):
        return ()
    others: set[str] = set()
    chosen = {str(x) for x in groups[choice]}
    for index, group in enumerate(groups):
        if index != choice:
            others |= {str(x) for x in group}
    # 两组若有重叠，重叠项在「选中的组」里仍然有效，不能因为出现在另一组就被禁
    return tuple(sorted(others - chosen))


def pool_snapshot(
    pack: Pack,
    state: GameState,
    *,
    mode: Mode | None = None,
    ops: list | None = None,
) -> PoolSnapshot:
    """计算当前卡池：总数、已消耗、剩余，按干员与等阶各切一份。

    ``ops`` 可传入已经过滤好的干员列表，避免与 :func:`covenant.calc.odds.shop_odds`
    重复算一遍「本局可用干员」。
    """
    mode = mode or pack.mode(state.mode_id)
    owned = state.owned_copies()
    extra = pool_extra(state)

    snap = PoolSnapshot(
        model_note="商店展示不占池；精锐按 3 份计；已含 rules.pool_extra 的手工补记"
    )
    by_tier: dict[int, dict[str, int]] = {}

    if ops is None:
        ops = pack.available_operators(mode, extra_inactive=state_extra_inactive(state, pack))

    for op in ops:
        total = pack.copies_for(op.id)
        if total <= 0:
            continue
        consumed = min(total, owned.get(op.id, 0) + extra.get(op.id, 0))
        entry = PoolEntry(op.id, op.name, op.tier, total, consumed)
        snap.entries[op.id] = entry
        snap.total += total
        snap.consumed += consumed
        bucket = by_tier.setdefault(op.tier, {"total": 0, "consumed": 0, "remaining": 0, "operators": 0})
        bucket["total"] += total
        bucket["consumed"] += consumed
        bucket["remaining"] += entry.remaining
        bucket["operators"] += 1

    snap.by_tier = dict(sorted(by_tier.items()))
    return snap
