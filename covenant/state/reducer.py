"""reducer：动作与观测的唯一入口。

真正的实现分成两个模块：

* :mod:`covenant.state.actions` —— 买/卖/刷/升/移动/过回合；
* :mod:`covenant.state.observers` —— 识别结果怎么并进状态。

这里只保留两个入口和公共约定，避免一个文件同时长动作和长观测：

``apply(state, action, pack)``
    结算一个玩家动作，会联动金币与卡池。

``apply_observation(state, obs, pack)``
    合并一条观测（来自识别或人工）。带「手填优先」策略：同一回合内，人工设置
    过的字段不会被低置信度的视觉结果覆盖，但 0.95 以上的高置信度识别仍可纠正
    手误。

所有函数都是**纯函数**：输入旧状态，返回新状态（深拷贝），出错抛
:class:`ActionError`，旧状态保持不变。
"""

from __future__ import annotations

from ..calc.effects import settle_effects
from ..core.types import Action, Observation
from ..data.pack import Pack
from .actions import ACTION_HANDLERS
from .errors import ActionError
from .observers import dispatch as _dispatch_observation
from .state import GameState

__all__ = ["ActionError", "apply", "apply_observation"]


def apply(state: GameState, action: Action, pack: Pack, *, replay: bool = False) -> GameState:
    """结算一个动作；``replay`` 仅用于兼容旧账本的历史规则。"""
    handler = ACTION_HANDLERS.get(action.type)
    if handler is None:
        raise ActionError(f"未知动作：{action.type!r}")
    st = state.clone()
    args = dict(action.args or {})
    args.pop("_replay", None)  # 不允许外部动作伪造重放来绕过校验
    if replay:
        args["_replay"] = True
    result = handler(st, args, pack)
    st = result or st
    settle_effects(pack, st)
    st.bump()
    return st


def apply_observation(
    state: GameState,
    obs: Observation,
    pack: Pack,
    *,
    min_confidence: float = 0.6,
    replay: bool = False,
) -> GameState:
    """合并一条观测，返回新状态。

    低于 ``min_confidence`` 且非人工的观测会被丢弃（记一条日志），
    这样识别器的噪声不会污染状态。
    """
    if obs.source != "manual" and obs.confidence < min_confidence:
        st = state.clone()
        st.add_log(
            "obs_reject",
            f"丢弃低置信度观测 {obs.kind}（{obs.confidence:.2f} < {min_confidence:.2f}）",
        )
        st.bump()
        return st

    st = state.clone()
    changed = _dispatch_observation(st, obs, pack, replay=replay)
    # DeepSeek 协作：识别到手填共用结算入口，激活顺序和跨档层数都不能漏发。
    changed = settle_effects(pack, st) or changed
    if changed:
        st.bump()
    return st
