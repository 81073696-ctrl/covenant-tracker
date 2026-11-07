"""视觉观测 -> 状态。

从动作 reducer 里拆出来：这里只有 :func:`dispatch` 和各类观测处理器，
不负责买/卖/过回合。分开的原因是两者的失败模式不同——动作错了是玩家
点错，观测错了是识别不准，调试时想看的是不同的日志。
"""

from __future__ import annotations

from typing import Any, Callable

from ..core.types import (
    KIND_CHAR,
    Observation,
    ObservationKind,
    ShopOffer,
    Unit,
    ZONE_BENCH,
    ZONE_BOARD,
)
from ..data.pack import Pack
from .merge import merge_shop, merge_units
from .state import GameState


def dispatch(st: GameState, obs: Observation, pack: Pack, *, replay: bool = False) -> bool:
    kind = obs.kind
    fn = _OBS_HANDLERS.get(kind)
    if fn is None:
        st.add_log("obs", f"未处理的观测类型：{kind}")
        return False
    try:
        if kind == ObservationKind.ROUND:
            return bool(fn(st, obs, pack, replay=replay))
        return bool(fn(st, obs, pack))
    except (TypeError, ValueError, AttributeError) as exc:
        # 视觉输入是脏的：处理器自己该校验的已经校验了，这里兜底，
        # 保证一条畸形观测不会把整个引擎打挂（界面上表现为 500）。
        st.add_log("obs", f"{kind} 观测处理失败：{type(exc).__name__}: {exc}")
        return False


def _accept(st: GameState, field_name: str, obs: Observation) -> bool:
    """手填优先：同回合内人工设置过的字段，拒绝低置信度的视觉覆盖。"""
    prev = st.provenance.get(field_name) or {}
    if (
        prev.get("source") == "manual"
        and prev.get("round") == st.round_no
        and obs.source != "manual"
        and obs.confidence < 0.95
    ):
        st.add_log(
            "obs_conflict",
            f"{field_name}：已有手填值，忽略视觉观测（{obs.confidence:.2f}）",
        )
        return False
    return True


def _obs_scalar(field_name: str, caster: Callable[[Any], Any], validator: Callable[[Any], bool] | None = None):
    def handler(st: GameState, obs: Observation, pack: Pack, *, replay: bool = False) -> bool:
        try:
            value = caster(obs.value)
        except (TypeError, ValueError):
            st.add_log("obs", f"{field_name} 值无法解析：{obs.value!r}")
            return False
        if validator and not validator(value):
            st.add_log("obs", f"{field_name} 值越界：{value!r}")
            return False
        if field_name == "round_no" and not replay:
            max_rounds = pack.mode(st.mode_id).rounds
            if max_rounds and value > max_rounds:
                st.add_log("obs", f"round_no 值越界：{value!r}（当前模式最多 {max_rounds}）")
                return False
        if getattr(st, field_name) == value:
            return False
        if not _accept(st, field_name, obs):
            return False
        if field_name == "round_no" and value > st.round_no:
            st.push_timeline()
        setattr(st, field_name, value)
        st.touch(field_name, obs.source, obs.confidence, obs.note)
        return True

    return handler


def _obs_shop(st: GameState, obs: Observation, pack: Pack) -> bool:
    if not _accept(st, "shop", obs):
        return False
    mode = pack.mode(st.mode_id)
    shop, changed = merge_shop(st.shop, obs.value, mode.char_slots(st.shop_level))
    if not changed:
        return False
    st.shop = shop
    st.touch("shop", obs.source, obs.confidence, obs.note)
    return True


def _obs_units(zone: str):
    def handler(st: GameState, obs: Observation, pack: Pack) -> bool:
        if not _accept(st, zone, obs):
            return False
        units, changed = merge_units(list(st.zone(zone)), obs.value)
        if not changed:
            return False
        st.zone(zone)[:] = units
        st.touch(zone, obs.source, obs.confidence, obs.note)
        return True

    return handler


def _obs_bonds(st: GameState, obs: Observation, pack: Pack) -> bool:
    if not _accept(st, "bond_layers", obs):
        return False
    raw = obs.value or {}
    if not isinstance(raw, dict):
        return False
    changed = False
    for bond_id, layer in raw.items():
        try:
            value = max(0, int(layer))
        except (TypeError, ValueError):
            continue
        if st.bond_layers.get(bond_id) != value:
            st.bond_layers[bond_id] = value
            changed = True
    if changed:
        st.touch("bond_layers", obs.source, obs.confidence, obs.note)
    return changed


def _obs_scene(st: GameState, obs: Observation, pack: Pack) -> bool:
    scene = str(obs.value or "")
    if st.rules.get("scene") == scene:
        return False
    st.rules["scene"] = scene
    st.touch("rules", obs.source, obs.confidence, "scene")
    return True


def _obs_result(st: GameState, obs: Observation, pack: Pack) -> bool:
    """结算画面识别：只挂一个「待确认」提示，不自动落记录。

    自动落记录的风险是同一张结算画面被连续几帧识别到，落出好几条重复战绩；
    而且「通关/失败」判错一次会永久污染统计。这里让前端弹一条横幅，玩家点一下
    才写进 matches.jsonl —— 少一次误记，比多一次自动化重要。
    """
    raw = obs.value or {}
    result = str(raw.get("result") if isinstance(raw, dict) else raw or "")
    if result not in ("clear", "fail"):
        return False
    hint = {
        "result": result,
        "confidence": round(float(obs.confidence), 3),
        "ts": obs.ts,
        "round": int(st.round_no),
    }
    prev = st.rules.get("result_hint")
    if isinstance(prev, dict) and prev.get("result") == result and prev.get("round") == hint["round"]:
        return False
    st.rules["result_hint"] = hint
    st.touch("rules", obs.source, obs.confidence, "result_hint")
    st.add_log("result", f"识别到结算画面：{'通关' if result == 'clear' else '失败'}（{obs.confidence:.2f}）")
    return True


_OBS_HANDLERS: dict[str, Callable[[GameState, Observation, Pack], bool]] = {
    ObservationKind.ROUND: _obs_scalar("round_no", int, lambda v: 1 <= v <= 99),
    ObservationKind.GOLD: _obs_scalar("gold", int, lambda v: v >= 0),
    ObservationKind.HP: _obs_scalar("hp", int, lambda v: v >= 0),
    ObservationKind.SHOP_LEVEL: _obs_scalar("shop_level", int, lambda v: 1 <= v <= 6),
    ObservationKind.PHASE: _obs_scalar("phase", str),
    ObservationKind.MODE: _obs_scalar("mode_id", str),
    ObservationKind.FREEZE: _obs_scalar("frozen", bool),
    ObservationKind.SHOP: _obs_shop,
    ObservationKind.BENCH: _obs_units(ZONE_BENCH),
    ObservationKind.BOARD: _obs_units(ZONE_BOARD),
    ObservationKind.BONDS: _obs_bonds,
    ObservationKind.SCENE: _obs_scene,
    ObservationKind.RESULT: _obs_result,
}
