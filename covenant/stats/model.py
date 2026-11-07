"""一局战绩：记牌器的「赛后复盘」最小单位。

为什么先有记录、再谈统计
-----------------------
「pick 率 / 胜率」这种数字，难点从来不是算，而是**数据从哪来**。
明日方舟没有给卫戍协议开战绩 API（炉石有官方插件接口，TFT 有 Riot Match API），
所以本项目的策略是：

1. 每局结束由玩家一键落一条 ``MatchRecord``（或以后从结算截图识别）；
2. 记录是纯 JSON、带版本号，可以导出成 JSONL 给别人合并；
3. 统计层只吃记录列表 —— 单机是「个人样本」，将来有社区汇总就是「社区样本」，
   两种模式共用同一套聚合代码，不需要改数据结构。

字段刻意保持「终局快照 + 轻量回合计」：终局阵容、结果、回合数、剩余生命，
外加每回合结束时的 ``timeline``（只存 op_id，不存整张牌）。商店刷新、每次
购买这种细粒度事件价值高但体积大、噪声大，等确实需要时再加。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

RESULT_CLEAR = "clear"
RESULT_FAIL = "fail"
RESULT_QUIT = "quit"
RESULTS = (RESULT_CLEAR, RESULT_FAIL, RESULT_QUIT)
RESULT_LABELS = {RESULT_CLEAR: "通关", RESULT_FAIL: "失败", RESULT_QUIT: "放弃/中途退出"}

RECORD_VERSION = 3


def validate_record(item: Any, *, strict: bool = False) -> list[str]:
    """检查一条外部记录能不能进门；返回错误列表，空列表代表通过。

    本地导入用宽松模式（``strict=False``）：只要求 id 与 result 合法，
    缺 mode_id 之类由 :meth:`MatchRecord.from_dict` 兜底，便于导入旧版本。
    社区入口用严格模式（``strict=True``）：要求 mode_id / patch / board 齐全，
    避免脏数据进公共库。

    两处共用这一个函数，规则才不会随版本漂移。
    """
    if not isinstance(item, dict):
        return ["不是对象"]
    errors: list[str] = []

    record_id = item.get("id")
    if not isinstance(record_id, str) or not record_id.strip():
        errors.append("缺少 id 或 id 不是字符串")
    elif len(record_id) > 128:
        errors.append("id 过长")

    if item.get("result") not in RESULTS:
        errors.append("result 必须是 clear/fail/quit")

    if strict:
        for key in ("mode_id", "patch"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"缺少 {key}")
        if not isinstance(item.get("board"), list):
            errors.append("board 必须是列表")

    return errors


def _unit_dicts(units) -> list[dict[str, Any]]:
    return [{"op_id": u.op_id, "star": int(u.star)} for u in units]


@dataclass
class MatchRecord:
    """一局的结果 + 终局阵容。"""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    match_id: str | None = None     # 对局账本 ID；旧记录保持 None，不伪造
    ts: float = field(default_factory=time.time)
    mode_id: str = ""
    mode_name: str = ""
    band_id: str | None = None
    band_name: str = ""
    patch: str = ""                 # 数据包 id@赛季，用于区分 11/8 前后
    rounds: int = 0                 # 打到第几回合
    total_rounds: int = 0           # 该模式总共几回合
    hp_left: int | None = None
    result: str = RESULT_FAIL
    cups: int | None = None         # 杯数/积分，可选（多人结算里能看到时再填）
    shop_level: int = 1
    gold_left: int = 0
    board: list[dict[str, Any]] = field(default_factory=list)
    bench: list[dict[str, Any]] = field(default_factory=list)
    bonds: dict[str, int] = field(default_factory=dict)   # 终局已激活盟约 → 人数/层数
    note: str = ""
    #: 每回合轻量快照：{round, shop_level, gold, hp, board[op_id], bench[op_id]}
    timeline: list[dict[str, Any]] = field(default_factory=list)
    v: int = RECORD_VERSION

    # -- 派生 -------------------------------------------------------------
    @property
    def board_ids(self) -> list[str]:
        return [str(u.get("op_id")) for u in self.board if u.get("op_id")]

    @property
    def won(self) -> bool:
        return self.result == RESULT_CLEAR

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "match_id": self.match_id,
            "ts": self.ts,
            "mode_id": self.mode_id,
            "mode_name": self.mode_name,
            "band_id": self.band_id,
            "band_name": self.band_name,
            "patch": self.patch,
            "rounds": self.rounds,
            "total_rounds": self.total_rounds,
            "hp_left": self.hp_left,
            "result": self.result,
            "cups": self.cups,
            "shop_level": self.shop_level,
            "gold_left": self.gold_left,
            "board": list(self.board),
            "bench": list(self.bench),
            "bonds": dict(self.bonds),
            "note": self.note,
            "timeline": [dict(x) for x in self.timeline],
            "v": RECORD_VERSION,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "MatchRecord":
        result = str(d.get("result") or RESULT_FAIL)
        if result not in RESULTS:
            result = RESULT_FAIL
        match_id = d.get("match_id")
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex[:12]),
            match_id=(str(match_id) if match_id is not None else None),
            ts=float(d.get("ts") or time.time()),
            mode_id=str(d.get("mode_id") or ""),
            mode_name=str(d.get("mode_name") or ""),
            band_id=(str(d["band_id"]) if d.get("band_id") else None),
            band_name=str(d.get("band_name") or ""),
            patch=str(d.get("patch") or ""),
            rounds=int(d.get("rounds") or 0),
            total_rounds=int(d.get("total_rounds") or 0),
            hp_left=(int(d["hp_left"]) if d.get("hp_left") is not None else None),
            result=result,
            cups=(int(d["cups"]) if d.get("cups") is not None else None),
            shop_level=int(d.get("shop_level") or 1),
            gold_left=int(d.get("gold_left") or 0),
            board=[dict(x) for x in (d.get("board") or []) if isinstance(x, dict)],
            bench=[dict(x) for x in (d.get("bench") or []) if isinstance(x, dict)],
            bonds={str(k): int(v) for k, v in (d.get("bonds") or {}).items()},
            note=str(d.get("note") or ""),
            timeline=[dict(x) for x in (d.get("timeline") or []) if isinstance(x, dict)],
            v=RECORD_VERSION,
        )

    # -- 从对局状态生成 ---------------------------------------------------
    @classmethod
    def from_state(
        cls,
        state,
        pack,
        *,
        result: str = RESULT_FAIL,
        cups: int | None = None,
        note: str = "",
    ) -> "MatchRecord":
        mode = pack.mode(state.mode_id) if state.mode_id else None
        band = (pack.bands or {}).get(state.band_id) if state.band_id else None

        bonds: dict[str, int] = {}
        try:
            from ..calc.bonds import bond_table

            table = bond_table(pack, state, mode=mode)
            bonds = {
                str(item["id"]): int(item.get("count") or 0)
                for item in table.get("items", [])
                if item.get("active")
            }
        except Exception:  # noqa: BLE001 - 统计不能因为一个盟约算错就丢整局
            bonds = {}

        timeline = [dict(x) for x in (getattr(state, "timeline", None) or []) if isinstance(x, dict)]
        rnd = int(getattr(state, "round_no", 0) or 0)
        if rnd and (not timeline or int(timeline[-1].get("round") or 0) != rnd):
            snap = getattr(state, "round_snapshot", None)
            if callable(snap):
                timeline.append(snap())

        return cls(
            match_id=state.match_id,
            mode_id=state.mode_id,
            mode_name=(mode.name if mode else ""),
            band_id=state.band_id,
            band_name=str((band or {}).get("name") or ""),
            patch=f"{pack.id}@{pack.season}" if pack.season else pack.id,
            rounds=int(state.round_no or 0),
            total_rounds=int(mode.rounds) if mode else 0,
            hp_left=state.hp,
            result=result if result in RESULTS else RESULT_FAIL,
            cups=cups,
            shop_level=int(state.shop_level or 1),
            gold_left=int(state.gold or 0),
            board=_unit_dicts(state.board),
            bench=_unit_dicts(state.bench),
            bonds=bonds,
            note=note.strip(),
            timeline=timeline,
        )
