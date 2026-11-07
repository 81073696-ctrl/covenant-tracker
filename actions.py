"""动作 reducer：买/卖/刷/升/移动/过回合。

每个 handler 都只改传入的状态（已经是 clone），失败抛 :class:`ActionError`。
入口在 :mod:`covenant.state.reducer` 的 ``apply``，这里只负责注册表。
"""

from __future__ import annotations

from typing import Any, Callable

from ..calc.effects import band_effect, refresh_details
from ..core.types import (
    KIND_CHAR,
    KIND_ITEM,
    ActionType,
    ShopOffer,
    Unit,
    ZONE_BENCH,
    ZONE_BOARD,
    ZONE_TEMP,
)
from ..data.pack import Pack
from .errors import ActionError
from .state import GameState, empty_shop, new_state

ZONES = (ZONE_BOARD, ZONE_BENCH, ZONE_TEMP)


# ---------------------------------------------------------------------------
# 动作实现
# ---------------------------------------------------------------------------


def _zone(st: GameState, name: Any) -> list:
    """取区域；未知区域名转成 ActionError（避免 KeyError 变成 500）。"""
    if name not in ZONES:
        raise ActionError(f"未知区域：{name!r}")
    return st.zone(name)


def _buy(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    slot = _as_int(args.get("slot", -1), "slot")
    if not 0 <= slot < len(st.shop):
        raise ActionError(f"商店格 {slot} 不存在")
    offer = st.shop[slot]
    if not offer.op_id:
        raise ActionError("这一格没有可购买的干员")
    if len(st.bench) >= pack.economy.bench_max:
        raise ActionError(f"整备区已满（{pack.economy.bench_max} 张），不能购买")
    op = pack.op(offer.op_id)
    tier = offer.tier or (op.tier if op else 1)
    from ..calc.effects import purchase_price
    price = purchase_price(pack, st, offer.op_id, tier)
    if st.gold < price:
        raise ActionError(f"资金不足：需要 {price}，现有 {st.gold}")

    st.gold -= price
    st.bench.append(Unit(offer.op_id, 1))
    name = op.name if op else offer.op_id
    offer.op_id = None
    offer.known = False
    offer.bought = True
    offer.tier = 0
    st.touch("shop", "action", 1.0, f"买入 {name}")
    st.add_log("buy", f"买入 {name}（-{price}）→ 资金 {st.gold}")
    _merge_all(st, pack)


def _sell(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    zone = args.get("zone", ZONE_BENCH)
    slot = _as_int(args.get("slot", -1), "slot")
    units = _zone(st, zone)
    if not 0 <= slot < len(units):
        raise ActionError(f"{zone}[{slot}] 不存在")
    unit = units.pop(slot)
    op = pack.op(unit.op_id)
    gain = pack.economy.sell_price
    st.gold += gain
    st.touch(zone, "action", 1.0, "出售")
    st.add_log(
        "sell",
        f"出售 {op.name if op else unit.op_id}（+{gain}）→ 资金 {st.gold}",
    )


def _refresh(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    try:
        details = refresh_details(pack, st, args)
    except ValueError as exc:
        raise ActionError(str(exc)) from exc
    cost = int(details["cost"])
    if st.gold < cost:
        raise ActionError(f"资金不足：刷新需要 {cost}，现有 {st.gold}")
    st.gold -= cost
    st.shop = empty_shop(len(st.shop))
    st.frozen = False
    count = int(st.rules.get("refresh_count") or 0)
    if st.rules.get("refresh_count_round") != st.round_no:
        count = 0
    st.rules["refresh_count_round"] = st.round_no
    st.rules["refresh_count"] = count + 1
    if details["refresh_type"] == "special" and int(st.rules.get("special_refresh_charges") or 0) > 0:
        st.rules["special_refresh_charges"] = int(st.rules["special_refresh_charges"]) - 1
    st.touch("shop", "action", 1.0, "刷新")
    label = {"normal": "普通", "free": "免费", "special": "特殊"}[details["refresh_type"]]
    st.add_log("refresh", f"{label}刷新商店（-{cost}）→ 资金 {st.gold}")


def _level_up(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    mode = pack.mode(st.mode_id)
    eco = pack.economy
    if st.shop_level >= eco.max_shop_level:
        raise ActionError("调度中心已是最高等级")
    rule = mode.rule(st.shop_level)
    if rule is None:
        raise ActionError(f"模式 {mode.id} 缺少商店等级 {st.shop_level} 的规则")
    cost = max(0, rule.upgrade_price - st.upgrade_decay)
    if st.gold < cost:
        raise ActionError(f"资金不足：升级需要 {cost}，现有 {st.gold}")
    st.gold -= cost
    st.shop_level += 1
    st.upgrade_decay = 0
    refresh_rule = (band_effect(pack, st).get("refresh") or {})
    if st.shop_level in (refresh_rule.get("on_levels") or []):
        st.rules["special_refresh_charges"] = int(st.rules.get("special_refresh_charges") or 0) + int(refresh_rule.get("charges") or 1)
        st.add_log("effect", f"策略授予 {refresh_rule.get('charges') or 1} 次特殊刷新")
    _resize_shop(st, mode)
    st.touch("shop_level", "action", 1.0, "升级")
    st.add_log("level_up", f"调度中心 → {st.shop_level} 级（-{cost}）→ 资金 {st.gold}")


def _set_shop(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    offers = args.get("offers")
    if offers is None and "slot" in args:
        # 只改一格：{"slot": 0, "op_id": "..."} —— 界面点一下就能改
        slot = _as_int(args["slot"], "slot")
        current: list[Any] = [o.op_id for o in st.shop]
        while len(current) <= slot:
            current.append(None)
        current[slot] = args.get("op_id")
        offers = current
    offers = offers or []
    mode = pack.mode(st.mode_id)
    n = max(len(offers), mode.char_slots(st.shop_level))
    shop: list[ShopOffer] = []
    for i in range(n):
        raw = offers[i] if i < len(offers) else None
        if raw is None:
            shop.append(ShopOffer(slot=i, known=False))
            continue
        if isinstance(raw, str):
            op = pack.op(raw)
            shop.append(
                ShopOffer(
                    slot=i,
                    op_id=raw,
                    tier=op.tier if op else 0,
                    kind=KIND_CHAR,
                    known=True,
                )
            )
            continue
        if isinstance(raw, dict):
            op_id = raw.get("op_id")
            kind = raw.get("kind", KIND_CHAR)
            if kind == KIND_ITEM:
                item_id = raw.get("item_id") or op_id
                shop.append(ShopOffer(slot=i, op_id=item_id, tier=0, kind=KIND_ITEM, known=True))
                continue
            op = pack.op(op_id) if op_id else None
            shop.append(
                ShopOffer(
                    slot=i,
                    op_id=op_id,
                    tier=int(raw.get("tier") or (op.tier if op else 0)),
                    kind=KIND_CHAR,
                    known=bool(op_id),
                )
            )
            continue
        raise ActionError(f"无法理解的商店格：{raw!r}")
    st.shop = shop
    st.touch("shop", "manual", 1.0, "人工录入")
    st.add_log("set_shop", f"人工录入商店：{sum(1 for o in shop if o.op_id)}/{len(shop)} 格")


def _move(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    fz = args.get("from_zone", ZONE_BENCH)
    tz = args.get("to_zone", ZONE_BOARD)
    fs = _as_int(args.get("from_slot", -1), "from_slot")
    ts = args.get("to_slot")
    src = _zone(st, fz)
    if not 0 <= fs < len(src):
        raise ActionError(f"{fz}[{fs}] 不存在")
    unit = src.pop(fs)
    dst = _zone(st, tz)
    if ts is None:
        dst.append(unit)
    else:
        target = _as_int(ts, "to_slot")
        dst.insert(max(0, target), unit)
        unit.slot = max(0, target)
    if tz == ZONE_BOARD and len(dst) > pack.economy.board_max:
        src.insert(fs, unit)  # 回滚
        raise ActionError(f"战场最多 {pack.economy.board_max} 张")
    st.add_log("move", f"{fz}[{fs}] → {tz}[{ts}]")


def _add_unit(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    zone = args.get("zone", ZONE_BENCH)
    if zone not in ZONES:
        raise ActionError(f"未知区域：{zone}")
    op_id = str(args.get("op_id", ""))
    if not op_id:
        raise ActionError("缺少 op_id")
    star = _as_int(args.get("star", 1), "star")
    if not 1 <= star <= 3:
        raise ActionError("星级必须在 1..3")
    if pack.op(op_id) is None:
        raise ActionError(f"未知干员：{op_id}")
    units = _zone(st, zone)
    if zone == ZONE_BOARD and len(units) >= pack.economy.board_max:
        raise ActionError(f"战场最多 {pack.economy.board_max} 张")
    if zone == ZONE_BENCH and len(units) >= pack.economy.bench_max:
        raise ActionError(f"整备区最多 {pack.economy.bench_max} 张")
    units.append(Unit(op_id, star, slot=args.get("slot")))
    st.touch(zone, "manual", 1.0, "人工摆放")
    op = pack.op(op_id)
    st.add_log("add_unit", f"放置 {op.name if op else op_id}（{star}★）到 {zone}")
    _merge_all(st, pack)


def _remove_unit(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    zone = args.get("zone", ZONE_BENCH)
    slot = _as_int(args.get("slot", -1), "slot")
    units = _zone(st, zone)
    if not 0 <= slot < len(units):
        raise ActionError(f"{zone}[{slot}] 不存在")
    unit = units.pop(slot)
    op = pack.op(unit.op_id)
    st.add_log("remove_unit", f"移除 {op.name if op else unit.op_id}（{zone}）")


def _merge_all(st: GameState, pack: Pack) -> None:
    """自动三合一（同名 3 张 → 1 张精锐）。风丸等例外看 merge_num。"""
    while True:
        groups: dict[str, list[tuple[str, int, Unit]]] = {}
        for zone in ZONES:
            for i, u in enumerate(st.zone(zone)):
                if u.star == 1:
                    groups.setdefault(u.op_id, []).append((zone, i, u))
        hit = None
        for op_id, lst in groups.items():
            op = pack.op(op_id)
            need = op.merge_num if op and op.merge_num else 3
            if len(lst) >= need:
                hit = (op_id, lst[:need])
                break
        if hit is None:
            return
        op_id, use = hit
        use.sort(key=lambda t: 0 if t[0] == ZONE_BOARD else 1)
        keep_zone = use[0][0]
        pool_copies = sum(unit.copies for _zone, _idx, unit in use)
        for zone, idx, _unit in sorted(use, key=lambda t: (t[0], -t[1])):
            del st.zone(zone)[idx]
        st.zone(keep_zone).append(Unit(op_id, star=2, pool_copies=pool_copies))
        op = pack.op(op_id)
        st.touch("board", "action", 1.0, "三合一")
        st.add_log("merge", f"{op.name if op else op_id} 三合一 → 精锐")


def _next_round(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    eco = pack.economy
    mode = pack.mode(st.mode_id)
    if not args.get("_replay") and mode.rounds and st.round_no >= mode.rounds:
        raise ActionError(f"已到最后一回合（{mode.rounds}），请先记录本局或开新局")
    st.push_timeline()  # 先记下刚结束的这一回合
    st.round_no += 1
    from ..calc.effects import gold_carry_over, round_income
    before = st.gold
    gain = round_income(pack, st, st.round_no, remaining_gold=before)
    carry = gold_carry_over(pack, st)
    st.gold = before + gain if carry else gain
    st.upgrade_decay += eco.upgrade_decay_per_round
    st.temp.clear()
    st.rules.pop("result_hint", None)  # 结算提示只对刚结束的这一回合有效
    if not st.frozen:
        st.shop = empty_shop(len(st.shop))
    st.frozen = False
    st.phase = "prep"
    st.touch("round_no", "action", 1.0, "过回合")
    st.touch("gold", "action", 1.0, "回合收入")
    st.add_log(
        "next_round",
        f"第 {st.round_no} 回合开始：收入 +{gain}"
        + ("" if carry else "（未花完资金已清空）")
        + f" → 资金 {st.gold}",
    )
    if mode.rounds and st.round_no > mode.rounds:
        st.add_log("warn", f"已超过该模式回合数 {mode.rounds}")


def _as_int(value: Any, label: str = "数值") -> int:
    """把 API 传来的值转成 int；失败统一转成 ActionError（而不是 500）。"""
    if isinstance(value, bool):
        raise ActionError(f"{label}必须是整数")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ActionError(f"{label}必须是整数") from None


def _set(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    path = str(args.get("path", "")).strip()
    value = args.get("value")
    if not path:
        raise ActionError("缺少 path")
    parts = path.split(".")
    head = parts[0]
    eco = pack.economy
    if head in ("gold", "round_no", "shop_level", "hp", "upgrade_decay"):
        value = _as_int(value, head)
        if head == "gold" and value < 0:
            raise ActionError("资金不能为负")
        if head == "shop_level" and not 1 <= value <= eco.max_shop_level:
            raise ActionError(f"商店等级必须在 1..{eco.max_shop_level}")
        if head == "round_no" and not args.get("_replay"):
            max_rounds = pack.mode(st.mode_id).rounds
            if value < 1 or (max_rounds and value > max_rounds):
                limit = f"1..{max_rounds}" if max_rounds else "不小于 1"
                raise ActionError(f"回合数必须在 {limit}")
        if head == "upgrade_decay" and value < 0:
            raise ActionError("降价点数不能为负")
        setattr(st, head, value)
        st.touch(head, "manual", 1.0)
    elif head in ("phase", "mode_id", "band_id", "frozen"):
        if head == "frozen":
            flag = value
            if isinstance(flag, str):
                flag = flag.strip().lower() in ("1", "true", "yes", "on")
            setattr(st, head, bool(flag))
        else:
            setattr(st, head, None if value is None else str(value))
        st.touch(head, "manual", 1.0)
    elif head == "bond_layers" and len(parts) == 2:
        st.bond_layers[parts[1]] = _as_int(value, "盟约层数")
        st.touch("bond_layers", "manual", 1.0)
    elif head == "choices" and len(parts) == 2:
        key = parts[1]
        if key == "bond_group" and value is not None:
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise ActionError("核心盟约组必须是整数下标") from None
            groups = getattr(pack, "core_bond_groups", []) or []
            if not 0 <= value < len(groups):
                raise ActionError(f"核心盟约组只有 {len(groups)} 组")
        st.choices[key] = value
        st.touch("choices", "manual", 1.0)
    elif head == "rules" and len(parts) == 2:
        st.rules[parts[1]] = value
        st.touch("rules", "manual", 1.0)
    else:
        raise ActionError(f"不允许直接设置的字段：{path}")
    st.add_log("set", f"人工设置 {path} = {value!r}")


def _freeze(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    st.frozen = bool(args.get("value", not st.frozen))
    st.add_log("freeze", "锁定商店" if st.frozen else "取消锁定")


def _reset(st: GameState, args: dict[str, Any], pack: Pack) -> GameState:
    mode = pack.mode(args.get("mode_id") or st.mode_id)
    band_id = args.get("band_id", st.band_id)
    hp = args.get("hp")
    st2 = new_state(
        mode_id=mode.id,
        gold=pack.economy.income(1),
        shop_slots=mode.char_slots(1),
        band_id=band_id,
        hp=hp,
    )
    # Kimi / DeepSeek：新局不得继承旧局的经济覆盖、刷新计数或里程碑领取标记。
    session_keys = {
        "gold_carry_over", "result_hint", "refresh_count", "refresh_count_round",
        "special_refresh_charges", "effect_milestones",
    }
    st2.rules = {k: v for k, v in st.rules.items() if k not in session_keys}
    group = args.get("bond_group")
    if group is not None:
        groups = getattr(pack, "core_bond_groups", []) or []
        try:
            index = int(group)
        except (TypeError, ValueError):
            raise ActionError("核心盟约组必须是整数下标") from None
        if not 0 <= index < len(groups):
            raise ActionError(f"核心盟约组只有 {len(groups)} 组")
        st2.choices["bond_group"] = index
    st2.add_log("reset", "开新局")
    return st2


def _merge_action(st: GameState, args: dict[str, Any], pack: Pack) -> None:
    before = len(list(st.all_units()))
    _merge_all(st, pack)
    if len(list(st.all_units())) == before:
        st.add_log("merge", "没有可合成的干员")


ACTION_HANDLERS: dict[str, Callable[[GameState, dict[str, Any], Pack], Any]] = {
    ActionType.BUY: _buy,
    ActionType.SELL: _sell,
    ActionType.REFRESH: _refresh,
    ActionType.LEVEL_UP: _level_up,
    ActionType.SET_SHOP: _set_shop,
    ActionType.MOVE: _move,
    ActionType.ADD_UNIT: _add_unit,
    ActionType.REMOVE_UNIT: _remove_unit,
    ActionType.NEXT_ROUND: _next_round,
    ActionType.SET: _set,
    ActionType.FREEZE: _freeze,
    ActionType.RESET: _reset,
    ActionType.MERGE: _merge_action,
}


def _resize_shop(st: GameState, mode) -> None:
    n = mode.char_slots(st.shop_level)
    if len(st.shop) < n:
        st.shop.extend(empty_shop(n)[len(st.shop):])
    elif len(st.shop) > n:
        del st.shop[n:]
