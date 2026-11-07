"""逐对局事件账本：严格 JSONL 追加、去重与 reducer 重放。"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..core.types import Action, Observation
from ..data.pack import Pack
from .state import GameState


class LedgerError(ValueError):
    """账本损坏、乱序或事件内容冲突。"""


@dataclass(frozen=True)
class MatchEvent:
    schema_version: int
    match_id: str
    seq: int
    recorded_at: float
    round_no: int
    phase: str
    type: str
    payload: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        match_id: str,
        seq: int,
        round_no: int,
        phase: str,
        type: str,
        payload: dict[str, Any],
    ) -> MatchEvent:
        return cls(
            schema_version=1,
            match_id=str(match_id),
            seq=int(seq),
            recorded_at=time.time(),
            round_no=int(round_no),
            phase=str(phase),
            type=str(type),
            payload=dict(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "match_id": self.match_id,
            "seq": self.seq,
            "recorded_at": self.recorded_at,
            "round_no": self.round_no,
            "phase": self.phase,
            "type": self.type,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MatchEvent:
        if not isinstance(data, dict):
            raise LedgerError("事件必须是 JSON 对象")
        try:
            schema = int(data["schema_version"])
            match_id = str(data["match_id"])
            seq = int(data["seq"])
            recorded_at = float(data["recorded_at"])
            round_no = int(data["round_no"])
            phase = str(data["phase"])
            event_type = str(data["type"])
            payload = data["payload"]
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerError(f"事件字段缺失或无效：{exc}") from exc
        if schema != 1:
            raise LedgerError(f"不支持的事件 schema：{schema}")
        if not match_id or not event_type or seq <= 0 or round_no <= 0:
            raise LedgerError("事件 ID、类型、序号或回合无效")
        if not isinstance(payload, dict):
            raise LedgerError("事件 payload 必须是对象")
        return cls(schema, match_id, seq, recorded_at, round_no, phase, event_type, payload)


class EventStore:
    """一场对局一个文件；任何损坏都显式报错，不静默丢弃。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> list[MatchEvent]:
        if not self.path.exists():
            return []
        events: list[MatchEvent] = []
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise LedgerError(f"账本读取失败：{exc}") from exc
        for line_no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                event = MatchEvent.from_dict(json.loads(line))
            except (json.JSONDecodeError, LedgerError) as exc:
                raise LedgerError(f"账本第 {line_no} 行损坏：{exc}") from exc
            expected = len(events) + 1
            if event.seq != expected:
                raise LedgerError(f"账本第 {line_no} 行序号应为 {expected}，实际为 {event.seq}")
            if events and event.match_id != events[0].match_id:
                raise LedgerError("一个账本文件不能混合多个对局 ID")
            events.append(event)
        return events

    def append(self, event: MatchEvent) -> bool:
        events = self.load()
        expected = len(events) + 1
        if event.seq < expected:
            existing = events[event.seq - 1] if event.seq > 0 else None
            if existing == event:
                return False
            raise LedgerError(f"事件序号 {event.seq} 已存在且内容不同")
        if event.seq != expected:
            raise LedgerError(f"下一事件序号应为 {expected}，实际为 {event.seq}")
        if events and event.match_id != events[0].match_id:
            raise LedgerError("不能向当前账本写入另一个对局的事件")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except OSError as exc:
            raise LedgerError(f"账本写入失败：{exc}") from exc
        return True

    def next_seq(self) -> int:
        return len(self.load()) + 1


def replay_events(pack: Pack, events: Iterable[MatchEvent]) -> GameState:
    """从开局快照重放动作和观测，恢复路径与正常运行共用 reducer。"""
    from .reducer import apply, apply_observation

    ordered = sorted(events, key=lambda e: e.seq)
    if not ordered or ordered[0].type != "match_start":
        raise LedgerError("账本必须从 match_start 开始")
    try:
        state = GameState.from_dict(ordered[0].payload["state"])
        for event in ordered[1:]:
            if event.type in ("match_end", "refresh_observed", "shop_change_observed"):
                continue
            if event.type == "observation":
                obs = Observation.from_dict(event.payload["observation"])
                state = apply_observation(state, obs, pack, replay=True)
            else:
                action = Action.from_dict(event.payload["action"])
                # Kimi：旧账本按当时规则重放；新增上限只拦截新输入，避免旧存档无法打开。
                state = apply(state, action, pack, replay=True)
        return state
    except (KeyError, TypeError, ValueError) as exc:
        raise LedgerError(f"账本重放失败：{exc}") from exc
