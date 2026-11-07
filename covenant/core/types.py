"""核心数据类型：干员实例、商店格、观测、动作。

三种东西必须区分清楚，否则后面会互相污染：

``Observation``
    观测 —— “我从画面上看到了什么”。带置信度、时间戳、来源。
    观测可以错、可以缺、可以重复；它只负责提供证据。

``Action``
    动作 —— “棋手做了什么”（买/卖/刷/升/移动）。由 reducer 结算成状态变化，
    同时联动金币与卡池消耗。

``GameState``（见 :mod:`covenant.state.state`）
    状态 —— 当前这一局的绝对快照。由上面两者共同推进，随时可被人工修正。

所有结构都支持 ``to_dict()``，因此可以整体塞进 JSON：录像、回放、前端传输
都用同一套格式。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


def now() -> float:
    """统一时间源（秒，浮点）。测试里可以 monkeypatch。"""
    return time.time()


# ---------------------------------------------------------------------------
# 干员实例
# ---------------------------------------------------------------------------

#: 星级 → 占用的卡池份数。精锐（2 星）= 3 张合成，所以占 3 份。
STAR_COPIES: dict[int, int] = {1: 1, 2: 3, 3: 9}

ZONE_BENCH = "bench"
ZONE_BOARD = "board"
ZONE_TEMP = "temp"


@dataclass
class Unit:
    """棋盘或整备区上的一张牌。

    ``slot`` 是该区域内的序号（0 起），用于人工界面定位与拖拽；
    纯逻辑计算不依赖它。
    """

    op_id: str
    star: int = 1
    slot: int | None = None
    note: str = ""
    pool_copies: int | None = None  # 特殊合成保留实际消耗；普通牌按星级计算

    @property
    def copies(self) -> int:
        """这张牌占用了卡池多少份。"""
        if self.pool_copies is not None:
            return max(0, int(self.pool_copies))
        return STAR_COPIES.get(self.star, 3**max(0, self.star - 1))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"op_id": self.op_id, "star": self.star}
        if self.slot is not None:
            d["slot"] = self.slot
        if self.note:
            d["note"] = self.note
        if self.pool_copies is not None:
            d["pool_copies"] = self.pool_copies
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Unit:
        return cls(
            op_id=str(d["op_id"]),
            star=int(d.get("star", 1)),
            slot=d.get("slot"),
            note=d.get("note", ""),
            pool_copies=(int(d["pool_copies"]) if d.get("pool_copies") is not None else None),
        )


# ---------------------------------------------------------------------------
# 商店
# ---------------------------------------------------------------------------

KIND_CHAR = "char"
KIND_ITEM = "item"


@dataclass
class ShopOffer:
    """调度中心（商店）里的一格。

    ``op_id is None`` 表示“这一格我们不知道是什么” —— 空位、未识别、已买走
    都用它表示，再配合 ``known`` 区分。
    """

    slot: int
    op_id: str | None = None
    tier: int = 0
    kind: str = KIND_CHAR
    known: bool = False
    bought: bool = False

    @property
    def empty(self) -> bool:
        return self.op_id is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot": self.slot,
            "op_id": self.op_id,
            "tier": self.tier,
            "kind": self.kind,
            "known": self.known,
            "bought": self.bought,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShopOffer:
        return cls(
            slot=int(d.get("slot", 0)),
            op_id=d.get("op_id"),
            tier=int(d.get("tier", 0)),
            kind=d.get("kind", KIND_CHAR),
            known=bool(d.get("known", False)),
            bought=bool(d.get("bought", False)),
        )


# ---------------------------------------------------------------------------
# 观测
# ---------------------------------------------------------------------------


class ObservationKind:
    """观测种类。新增识别能力时在这里加常量，然后让 reducer 认识它。"""

    ROUND = "round"            # int，当前回合数
    PHASE = "phase"            # "prep" | "battle" | "result" | "menu"
    GOLD = "gold"              # int，资金
    HP = "hp"                  # int，剩余生命
    SHOP_LEVEL = "shop_level"  # int，调度中心等级 1..6
    SHOP = "shop"              # list[ShopOffer]，一次看到的所有商店格
    BENCH = "bench"            # list[Unit]，整备区全部
    BOARD = "board"            # list[Unit]，战场区全部
    BONDS = "bonds"            # dict[bond_id, int]，盟约层数
    MODE = "mode"              # str，模式 id
    FREEZE = "freeze"          # bool，商店是否锁定
    SCENE = "scene"            # str，界面场景标识（由适配器定义）
    RESULT = "result"          # dict{result, confidence}，结算画面识别（clear/fail）

    ALL = (
        ROUND, PHASE, GOLD, HP, SHOP_LEVEL, SHOP,
        BENCH, BOARD, BONDS, MODE, FREEZE, SCENE, RESULT,
    )


@dataclass
class Observation:
    """一条带置信度的观测。"""

    kind: str
    value: Any
    confidence: float = 1.0
    source: str = "vision"   # vision | manual | replay | adapter:<name>
    ts: float = field(default_factory=now)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "value": self.value,
            "confidence": round(float(self.confidence), 4),
            "source": self.source,
            "ts": self.ts,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Observation:
        return cls(
            kind=d["kind"],
            value=d.get("value"),
            confidence=float(d.get("confidence", 1.0)),
            source=d.get("source", "unknown"),
            ts=float(d.get("ts", now())),
            note=d.get("note", ""),
        )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Obs {self.kind}={self.value!r} conf={self.confidence:.2f}>"


# ---------------------------------------------------------------------------
# 动作
# ---------------------------------------------------------------------------


class ActionType:
    """玩家/UI 发起的动作。"""

    BUY = "buy"                # {slot}
    SELL = "sell"              # {zone, slot} 或 {op_id}
    REFRESH = "refresh"        # {} 扣 1 金，商店内容变为“未知”
    LEVEL_UP = "level_up"      # {} 按当前价格升级
    MOVE = "move"              # {from_zone, from_slot, to_zone, to_slot}
    SET_SHOP = "set_shop"      # {offers: [op_id|null, ...]} 人工录入
    SET = "set"                # {path, value} 人工改任意字段（纠错）
    NEXT_ROUND = "next_round"  # {} 进入下一回合：结算收入、降价、清临时格
    FREEZE = "freeze"          # {value: bool}
    MERGE = "merge_check"      # {} 手动触发三合一检查
    RESET = "reset"            # {mode_id?, band_id?} 开新局
    ADD_UNIT = "add_unit"      # {zone, op_id, star?} 直接摆一张牌（人工）
    REMOVE_UNIT = "remove_unit"  # {zone, slot}

    ALL = (
        BUY, SELL, REFRESH, LEVEL_UP, MOVE, SET_SHOP, SET, NEXT_ROUND,
        FREEZE, MERGE, RESET, ADD_UNIT, REMOVE_UNIT,
    )


@dataclass
class Action:
    type: str
    args: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=now)
    source: str = "manual"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "args": self.args,
            "ts": self.ts,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Action:
        return cls(
            type=d["type"],
            args=d.get("args") or {},
            ts=float(d.get("ts", now())),
            source=d.get("source", "manual"),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Action {self.type} {self.args}>"
