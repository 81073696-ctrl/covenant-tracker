"""对局状态：一份可反复纠正的绝对快照。

为什么不用事件溯源
------------------
记牌器的输入是**带噪声的视觉观测**：可能一帧里只认出了 3 个商店格，下一帧
又认出第 4 个；也可能 OCR 把 12 读成 17。纯事件溯源会把每一次误读都记进
历史，越滚越歪。所以这里采用「绝对快照 + 来源标注」：

* 每个字段都记录 ``provenance``（谁在什么时候用什么置信度写的）；
* 人工输入优先级最高，且在同一回合内不会被低置信度的视觉观测覆盖；
* 任何字段都能被 ``SET`` 动作直接改掉 —— 纠错是一等公民，不是补丁。

状态本身不依赖数据包；需要规则（几格商店、升级多少钱）时由 reducer 传入
:class:`~covenant.data.pack.Pack`。
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..core.types import (
    KIND_CHAR,
    ZONE_BENCH,
    ZONE_BOARD,
    ZONE_TEMP,
    ShopOffer,
    Unit,
)

MAX_LOG = 400
MAX_TIMELINE = 30


def _t() -> float:
    return time.time()


@dataclass
class GameState:
    """一局「卫戍协议」的完整快照。"""

    match_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    mode_id: str = ""
    band_id: str | None = None
    round_no: int = 1
    phase: str = "prep"          # prep | battle | result | menu
    gold: int = 0
    shop_level: int = 1
    hp: int | None = None
    shop: list[ShopOffer] = field(default_factory=list)
    bench: list[Unit] = field(default_factory=list)
    board: list[Unit] = field(default_factory=list)
    temp: list[Unit] = field(default_factory=list)
    bond_layers: dict[str, int] = field(default_factory=dict)
    frozen: bool = False
    upgrade_decay: int = 0        # 本等级已累积的降价点数
    rules: dict[str, Any] = field(default_factory=dict)  # 规则开关覆盖
    #: 开局选择：bond_group=0/1（核心盟约二选一）、loadout=[op_id…] 等。
    #: 不进 to_dict 的旧字段，单独序列化；缺省为空，旧存档照样能读。
    choices: dict[str, Any] = field(default_factory=dict)

    #: 每回合结束时的轻量快照（回合/等级/资金/生命/战场/整备区）。
    #: 只留 op_id，不留整张牌，体积小；用于「按回合的在场率」这类统计。
    timeline: list[dict[str, Any]] = field(default_factory=list)

    seq: int = 0
    created_at: float = field(default_factory=_t)
    updated_at: float = field(default_factory=_t)
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)
    log: list[dict[str, Any]] = field(default_factory=list)

    # -- 查询 -------------------------------------------------------------
    def zone(self, name: str) -> list[Unit]:
        return {ZONE_BENCH: self.bench, ZONE_BOARD: self.board, ZONE_TEMP: self.temp}[name]

    def zones(self) -> Iterator[tuple[str, list[Unit]]]:
        yield ZONE_BOARD, self.board
        yield ZONE_BENCH, self.bench
        yield ZONE_TEMP, self.temp

    def all_units(self) -> Iterator[Unit]:
        for _, units in self.zones():
            yield from units

    def owned_copies(self) -> dict[str, int]:
        """自己已经占用的卡池份数（按干员 id 汇总）。"""
        out: dict[str, int] = {}
        for u in self.all_units():
            out[u.op_id] = out.get(u.op_id, 0) + u.copies
        return out

    def shop_offers(self, kind: str = KIND_CHAR) -> list[ShopOffer]:
        return [o for o in self.shop if o.kind == kind]

    def known_shop(self) -> list[ShopOffer]:
        return [o for o in self.shop if o.known]

    # -- 修改 -------------------------------------------------------------
    def touch(self, field_name: str, source: str, confidence: float, note: str = "") -> None:
        self.provenance[field_name] = {
            "source": source,
            "confidence": round(float(confidence), 3),
            "ts": _t(),
            "round": self.round_no,
            "note": note,
        }

    def bump(self) -> None:
        self.seq += 1
        self.updated_at = _t()

    def add_log(self, kind: str, text: str, **extra: Any) -> None:
        entry = {"seq": self.seq, "ts": _t(), "kind": kind, "text": text}
        if extra:
            entry.update(extra)
        self.log.append(entry)
        if len(self.log) > MAX_LOG:
            del self.log[: len(self.log) - MAX_LOG]

    def source_of(self, field_name: str) -> str:
        return str(self.provenance.get(field_name, {}).get("source", ""))

    # -- 回合快照 ---------------------------------------------------------
    def round_snapshot(self) -> dict[str, Any]:
        """当前回合的轻量快照（不修改状态）。"""
        return {
            "round": int(self.round_no),
            "shop_level": int(self.shop_level),
            "gold": int(self.gold),
            "hp": self.hp,
            "board": [u.op_id for u in self.board],
            "bench": [u.op_id for u in self.bench],
        }

    def push_timeline(self) -> None:
        """把当前回合记进 timeline；同一回合重复调用会覆盖，不会重复计数。"""
        snap = self.round_snapshot()
        if self.timeline and int(self.timeline[-1].get("round") or 0) == snap["round"]:
            self.timeline[-1] = snap
        else:
            self.timeline.append(snap)
        if len(self.timeline) > MAX_TIMELINE:
            del self.timeline[: -MAX_TIMELINE]

    # -- 序列化 -----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "mode_id": self.mode_id,
            "band_id": self.band_id,
            "round_no": self.round_no,
            "phase": self.phase,
            "gold": self.gold,
            "shop_level": self.shop_level,
            "hp": self.hp,
            "shop": [o.to_dict() for o in self.shop],
            "bench": [u.to_dict() for u in self.bench],
            "board": [u.to_dict() for u in self.board],
            "temp": [u.to_dict() for u in self.temp],
            "bond_layers": dict(self.bond_layers),
            "frozen": self.frozen,
            "upgrade_decay": self.upgrade_decay,
            "rules": dict(self.rules),
            "choices": dict(self.choices),
            "timeline": [dict(x) for x in self.timeline[-MAX_TIMELINE:]],
            "seq": self.seq,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "provenance": copy.deepcopy(self.provenance),
            "log": self.log[-80:],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GameState:
        return cls(
            match_id=str(d.get("match_id") or uuid.uuid4().hex),
            mode_id=d.get("mode_id", ""),
            band_id=d.get("band_id"),
            round_no=int(d.get("round_no", 1)),
            phase=d.get("phase", "prep"),
            gold=int(d.get("gold", 0)),
            shop_level=int(d.get("shop_level", 1)),
            hp=d.get("hp"),
            shop=[ShopOffer.from_dict(x) for x in d.get("shop", [])],
            bench=[Unit.from_dict(x) for x in d.get("bench", [])],
            board=[Unit.from_dict(x) for x in d.get("board", [])],
            temp=[Unit.from_dict(x) for x in d.get("temp", [])],
            bond_layers={k: int(v) for k, v in (d.get("bond_layers") or {}).items()},
            frozen=bool(d.get("frozen", False)),
            upgrade_decay=int(d.get("upgrade_decay", 0)),
            rules=dict(d.get("rules") or {}),
            choices=dict(d.get("choices") or {}),
            timeline=[dict(x) for x in (d.get("timeline") or []) if isinstance(x, dict)],
            seq=int(d.get("seq", 0)),
            created_at=float(d.get("created_at", _t())),
            updated_at=float(d.get("updated_at", _t())),
            provenance=copy.deepcopy(d.get("provenance") or {}),
            log=list(d.get("log") or []),
        )

    def clone(self) -> GameState:
        return GameState.from_dict(self.to_dict())


def empty_shop(n: int) -> list[ShopOffer]:
    """生成 ``n`` 个「未知」商店格。"""
    return [ShopOffer(slot=i, known=False) for i in range(n)]


def new_state(
    *,
    mode_id: str,
    gold: int = 4,
    shop_slots: int = 3,
    band_id: str | None = None,
    hp: int | None = None,
) -> GameState:
    """开一局新的。首回合资金默认 = 4（income(1)）。"""
    st = GameState(
        mode_id=mode_id,
        band_id=band_id,
        round_no=1,
        gold=gold,
        shop_level=1,
        hp=hp,
        shop=empty_shop(shop_slots),
        created_at=_t(),
        updated_at=_t(),
    )
    st.add_log("new_game", f"新对局：{mode_id}，第 1 回合，资金 {gold}")
    return st
