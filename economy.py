"""经济：收入曲线、升级成本、攒钱回合数。

几个容易被忽略的细节，这里都显式建模了：

* **收入随回合线性增长**：``income(r) = min(base + r, cap)``，所以「早一回合
  升级」和「晚一回合升级」差的不是 1 块钱，而是那 1 块钱在后续每回合的复利；
* **升级价每回合 -1**：本回合的升级价 = 初始价 - 已等待回合数（不低于 0），
  升级后重置为新等级的初始价 —— 所以「再等一回合」确实更便宜；
* **资金是否跨回合保留**是一个规则开关（``economy.gold_carry_over``），
  因为官方说明对这点有歧义；UI 里可以手动切。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..data.pack import Economy, Mode, Pack

if TYPE_CHECKING:  # 避免 calc ←→ state 循环导入
    from ..state.state import GameState


@dataclass
class LevelPlan:
    level: int                 # 目标等级
    initial_price: int         # 该等级升级的初始价
    cost_now: int              # 现在升上去要多少
    cost_next_round: int       # 等到下回合再升要多少
    cumulative_cost: int       # 从当前等级连续升到该等级的总价（估算）
    affordable_now: bool
    rounds_to_afford: int | None   # 假设一分钱不花，还要攒几回合
    can_afford_this_round_gain: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "initial_price": self.initial_price,
            "cost_now": self.cost_now,
            "cost_next_round": self.cost_next_round,
            "cumulative_cost": self.cumulative_cost,
            "affordable_now": self.affordable_now,
            "rounds_to_afford": self.rounds_to_afford,
            "can_afford_this_round_gain": self.can_afford_this_round_gain,
        }


@dataclass
class EconomyView:
    gold: int
    round_no: int
    shop_level: int
    income_now: int
    income_next: int
    income_curve: list[dict[str, int]] = field(default_factory=list)
    plans: list[LevelPlan] = field(default_factory=list)
    level_up_now: LevelPlan | None = None
    spendable: int = 0
    notes: list[str] = field(default_factory=list)
    gold_carry_over: bool = False
    refresh_cost: int = 1
    free_refresh_chance_percent: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gold": self.gold,
            "round_no": self.round_no,
            "shop_level": self.shop_level,
            "income_now": self.income_now,
            "income_next": self.income_next,
            "income_curve": self.income_curve,
            "plans": [p.to_dict() for p in self.plans],
            "level_up_now": self.level_up_now.to_dict() if self.level_up_now else None,
            "spendable": self.spendable,
            "notes": self.notes,
            "gold_carry_over": self.gold_carry_over,
            "refresh_cost": self.refresh_cost,
            "free_refresh_chance_percent": self.free_refresh_chance_percent,
        }


def income_of(eco: Economy, round_no: int) -> int:
    return eco.income(round_no)


def rounds_to_afford(
    eco: Economy,
    gold: int,
    cost: int,
    round_no: int,
    *,
    cap: int = 20,
    carry: bool | None = None,
) -> int | None:
    """按实际结转规则估计还要几回合能支付 ``cost``。"""
    if gold >= cost:
        return 0
    keep_gold = eco.gold_carry_over if carry is None else bool(carry)
    have = gold
    for i in range(1, cap + 1):
        r = round_no + i
        have = have + eco.income(r) if keep_gold else eco.income(r)
        if have >= cost:
            return i
    return None


def economy_view(
    pack: Pack,
    state: GameState,
    *,
    mode: Mode | None = None,
    horizon: int = 6,
) -> EconomyView:
    from .effects import free_refresh_chance_percent, gold_carry_over, refresh_details, round_income

    eco = pack.economy
    mode = mode or pack.mode(state.mode_id)
    carry = gold_carry_over(pack, state)
    chance = free_refresh_chance_percent(pack, state)
    view = EconomyView(
        gold=state.gold,
        round_no=state.round_no,
        shop_level=state.shop_level,
        income_now=eco.income(state.round_no),
        income_next=round_income(pack, state, state.round_no + 1, remaining_gold=state.gold),
        spendable=state.gold,
        gold_carry_over=carry,
        refresh_cost=int(refresh_details(pack, state)["cost"]),
        free_refresh_chance_percent=chance,
    )

    for i in range(horizon):
        r = state.round_no + i
        income = eco.income(r) if i == 0 else round_income(pack, state, r, remaining_gold=state.gold)
        view.income_curve.append({"round": r, "income": income})

    cumulative = 0
    for target in range(state.shop_level + 1, eco.max_shop_level + 1):
        rule = mode.rule(target - 1)  # 从 target-1 升到 target 的价格
        if rule is None:
            view.notes.append(f"模式 {mode.id} 缺少商店等级 {target - 1} 的规则")
            continue
        initial = rule.upgrade_price
        # 只有第一次立即升级享受当前已等待的降价；升级后衰减会重置。
        current_decay = state.upgrade_decay if target == state.shop_level + 1 else 0
        cost_now = max(0, initial - current_decay)
        cost_next = max(0, initial - (current_decay + eco.upgrade_decay_per_round))
        cumulative += cost_now
        plan = LevelPlan(
            level=target,
            initial_price=initial,
            cost_now=cost_now,
            cost_next_round=cost_next,
            cumulative_cost=cumulative,
            affordable_now=state.gold >= cost_now,
            rounds_to_afford=rounds_to_afford(
                eco, state.gold, cost_now, state.round_no, carry=carry
            ),
            can_afford_this_round_gain=(
                state.gold + view.income_next if carry else view.income_next
            ) >= cost_now,
        )
        view.plans.append(plan)
        if target == state.shop_level + 1:
            view.level_up_now = plan

    if not carry:
        view.notes.append("本规则下资金不跨回合保留：回合结束前必须花完")
    if chance is not None:
        view.notes.append(f"奇迹：当前每层模型推算免费刷新概率 {chance:.1f}%；仅展示，不代替游戏判定")
    if state.shop_level >= eco.max_shop_level:
        view.notes.append("调度中心已满级")

    return view
