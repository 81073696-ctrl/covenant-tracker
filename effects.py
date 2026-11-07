"""数据包驱动的对局效果；只实现已有明确规则来源的效果。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..data.pack import Pack

if TYPE_CHECKING:
    from ..state.state import GameState


def band_effect(pack: Pack, state: GameState) -> dict[str, Any]:
    """当前策略的结构化效果；旧数据包没有该字段时安全返回空。"""
    raw = pack.bands.get(state.band_id or "") or {}
    effects = raw.get("effects") or {}
    return effects if isinstance(effects, dict) else {}


def _active_bond_effects(pack: Pack, state: GameState) -> dict[str, dict[str, Any]]:
    """只有实际激活的盟约才能触发奖励；永久解锁另由里程碑保留。"""
    from .bonds import bond_status

    return {
        status.id: pack.bonds[status.id].effects
        for status in bond_status(pack, state)
        if status.active and pack.bonds[status.id].effects
    }


def gold_carry_over(pack: Pack, state: GameState) -> bool:
    """人工覆盖优先，其次读取策略能力，最后使用模式经济默认值。"""
    if "gold_carry_over" in state.rules:
        return bool(state.rules["gold_carry_over"])
    economy = band_effect(pack, state).get("economy") or {}
    return bool(economy.get("gold_carry_over", pack.economy.gold_carry_over))


def round_income(pack: Pack, state: GameState, round_no: int, *, remaining_gold: int = 0) -> int:
    """回合基础收入加条件奖励；额外收入只看回合结束时的剩余资金。"""
    economy = band_effect(pack, state).get("economy") or {}
    threshold = int(economy.get("remaining_gold_threshold") or 0)
    bonus = int(economy.get("income_bonus") or 0) if threshold and remaining_gold >= threshold else 0
    return pack.economy.income(round_no, bonus=bonus)


def purchase_price(pack: Pack, state: GameState, op_id: str, tier: int = 1) -> int:
    """永久折扣以解锁标记为准；扩展到全商店是范围变化，不重复叠加。"""
    op = pack.op(op_id)
    base = op.price if op and op.price else pack.economy.buy_price(op.tier if op else tier)
    milestones = state.rules.get("effect_milestones") or {}
    if not isinstance(milestones, dict):
        milestones = {}
    active = _active_bond_effects(pack, state)
    discounts: list[int] = []
    for bond_id, bond in pack.bonds.items():
        effects = bond.effects or {}
        for raw in effects.get("purchase_discounts") or []:
            threshold = int(raw.get("threshold") or 0)
            key = f"{bond_id}:{threshold}"
            unlocked = bool(milestones.get(key)) or (
                bond_id in active and int(state.bond_layers.get(bond_id) or 0) >= threshold
            )
            scope = raw.get("scope")
            if unlocked and (scope == "all" or (scope == "bond" and bond_id in (op.bonds if op else ()))):
                discounts.append(int(raw.get("amount") or 0))
    return max(0, base - max(discounts, default=0))


def free_refresh_chance_percent(pack: Pack, state: GameState) -> float | None:
    """只计算并展示概率，不在本地替游戏掷随机数。"""
    for bond_id, effects in _active_bond_effects(pack, state).items():
        raw = effects.get("free_refresh_chance") or {}
        if raw:
            layers = max(0, int(state.bond_layers.get(bond_id) or 0))
            return float(raw.get("base_percent") or 0) + float(raw.get("per_layer_percent") or 0) * layers
    return None


def settle_effects(pack: Pack, state: GameState) -> bool:
    """结算已达但尚未领取的层数奖励和永久折扣，可跨档补发且重放幂等。"""
    milestones = state.rules.get("effect_milestones")
    if not isinstance(milestones, dict):
        milestones = {}
    changed = False
    for bond_id, effects in _active_bond_effects(pack, state).items():
        layers = max(0, int(state.bond_layers.get(bond_id) or 0))
        reward = effects.get("layer_rewards") or {}
        every = int(reward.get("every") or 0)
        gold = int(reward.get("gold") or 0)
        if every > 0 and gold:
            for threshold in range(every, layers + 1, every):
                key = f"{bond_id}:{threshold}"
                if milestones.get(key):
                    continue
                milestones[key] = True
                state.gold += gold
                state.add_log("effect", f"{pack.bonds[bond_id].name} {threshold} 层：资金 +{gold}")
                changed = True
        for raw in effects.get("purchase_discounts") or []:
            threshold = int(raw.get("threshold") or 0)
            key = f"{bond_id}:{threshold}"
            if threshold and layers >= threshold and not milestones.get(key):
                milestones[key] = True
                scope = "全商店" if raw.get("scope") == "all" else f"{pack.bonds[bond_id].name}干员"
                state.add_log("effect", f"{pack.bonds[bond_id].name} {threshold} 层：{scope}购买价永久 -{int(raw.get('amount') or 0)}")
                changed = True
    if changed:
        # DeepSeek 协作：领取标记属于本局状态；新局由 reset 清除，不能依赖当前层数反推。
        state.rules["effect_milestones"] = milestones
        state.touch("gold", "effect", 1.0, "层数奖励或永久折扣")
    return changed


def refresh_details(pack: Pack, state: GameState, args: dict[str, Any] | None = None) -> dict[str, Any]:
    """返回一次主动刷新的类型、来源和费用。

    免费刷新只能由明确的手动操作指定；奇迹的概率不会在本地掷随机数。
    特殊刷新由策略规则推断，推断标签与画面证据一起写入审计事件。
    """
    args = args or {}
    requested = str(args.get("refresh_type") or "")
    if requested not in ("", "normal", "free", "special"):
        raise ValueError(f"未知刷新类型：{requested}")
    if requested == "free":
        return {"refresh_type": "free", "refresh_source": "manual", "cost": 0, "attribution": "manual"}

    effect = band_effect(pack, state).get("refresh") or {}
    source = state.band_id or "manual"
    special = requested == "special"
    if effect.get("scope") == "all":
        special = True
    per_round = int(effect.get("per_round") or 0)
    count = int(state.rules.get("refresh_count") or 0)
    if state.rules.get("refresh_count_round") != state.round_no:
        count = 0
    if per_round and count < per_round:
        special = True
    if int(state.rules.get("special_refresh_charges") or 0) > 0:
        special = True
    if special:
        return {
            "refresh_type": "special",
            "refresh_source": source if effect else "manual",
            "cost": pack.economy.refresh_cost,
            "attribution": "strategy_inferred" if effect else "manual",
        }
    return {
        "refresh_type": "normal",
        "refresh_source": "standard",
        "cost": pack.economy.refresh_cost,
        "attribution": "manual" if args else "gold_and_shop",
    }


def observed_refresh_details(pack: Pack, state: GameState, gold_drop: int) -> dict[str, Any]:
    """只归类证据足够的刷新；付费但来源不明时也保留为未归因变化。"""
    effect = band_effect(pack, state).get("refresh") or {}
    source = state.band_id or "unknown"
    if gold_drop == 0:
        return {"refresh_type": "unknown", "refresh_source": "unknown", "attribution": "unattributed"}
    if effect.get("scope") == "all":
        return {"refresh_type": "special", "refresh_source": source, "attribution": "strategy_inferred"}
    count = int(state.rules.get("refresh_count") or 0)
    if state.rules.get("refresh_count_round") != state.round_no:
        count = 0
    per_round = int(effect.get("per_round") or 0)
    # DeepSeek 协作：观测不消费手动计数；额度内或佩佩次数只能靠猜时，不进入命中率。
    if (per_round and count < per_round) or (
        effect.get("on_levels") and int(state.rules.get("special_refresh_charges") or 0) > 0
    ):
        return {"refresh_type": "unknown", "refresh_source": source, "attribution": "insufficient_observation"}
    return {"refresh_type": "normal", "refresh_source": "standard", "attribution": "gold_and_shop"}
