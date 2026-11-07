"""引擎：数据包 + 状态 + 计算，产出前端要的一份快照。

这是「后端唯一门面」：UI、命令行、将来的识别管线都只跟它打交道。
它内部持有一把锁，因此 Web 服务是多线程安全的。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from ..calc.bonds import bond_table
from ..calc.economy import economy_view
from ..calc.effects import refresh_details
from ..calc.odds import shop_odds
from ..calc.pool import pool_snapshot
from ..core.types import Action, Observation
from ..data.pack import Pack
from ..services.matches import MatchService
from .events import EventStore, LedgerError, MatchEvent, replay_events
from .reducer import ActionError, apply, apply_observation
from .state import GameState, new_state


class Engine:
    def __init__(self, pack: Pack, state: GameState | None = None):
        self.pack = pack
        self.state = state or new_state(
            mode_id=pack.mode().id,
            gold=pack.economy.income(1),
            shop_slots=pack.mode().char_slots(1),
        )
        self._lock = threading.RLock()
        self.last_error: str | None = None
        self.last_change = time.time()
        self._dirty = False
        self._save_path: Path | None = None
        self._matches: MatchService | None = None
        self.event_store: EventStore | None = None
        self._ledger_error: str | None = None
        self._rev = 0

    @property
    def revision(self) -> int:
        """单调递增的变更号，给快照缓存做 key（reset 后也不会撞车）。"""
        return self._rev

    # -- 生命周期 ---------------------------------------------------------
    @property
    def mode(self):
        return self.pack.mode(self.state.mode_id)

    def _store_for(self, state: GameState) -> EventStore:
        if self._save_path is None:
            raise LedgerError("尚未设置存档目录")
        return EventStore(self._save_path.parent / "events" / f"{state.match_id}.jsonl")

    def _start_event(self, store: EventStore, state: GameState) -> None:
        store.append(MatchEvent.create(
            match_id=state.match_id,
            seq=1,
            round_no=state.round_no,
            phase=state.phase,
            type="match_start",
            payload={"state": state.to_dict()},
        ))

    def attach_storage(self, path: str | Path) -> None:
        """恢复事件账本；旧快照只用于首次迁移，损坏账本绝不覆盖。"""
        self._save_path = Path(path)
        self._save_path.parent.mkdir(parents=True, exist_ok=True)
        if self._matches is None:
            self._matches = MatchService(self._save_path.parent / "matches.jsonl")
        if self._save_path.exists():
            try:
                data = json.loads(self._save_path.read_text(encoding="utf-8"))
                self.state = GameState.from_dict(data)
            except (OSError, ValueError, KeyError):
                self.last_error = "存档读取失败，已开新局"
                self.state = new_state(
                    mode_id=self.pack.mode().id,
                    gold=self.pack.economy.income(1),
                    shop_slots=self.pack.mode().char_slots(1),
                )
        self.event_store = self._store_for(self.state)
        self._ledger_error = None
        try:
            if self.event_store.path.exists():
                self.state = replay_events(self.pack, self.event_store.load())
            else:
                self._start_event(self.event_store, self.state)
        except LedgerError as exc:
            self._ledger_error = str(exc)
            self.last_error = f"事件账本不可用：{exc}；可开新局，原文件已保留"
        self.save()

    def save(self) -> None:
        if self._save_path is None:
            return
        try:
            self._save_path.write_text(
                json.dumps(self.state.to_dict(), ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        except OSError as exc:  # pragma: no cover - 磁盘问题
            self.last_error = f"存档失败：{exc}"

    # -- 入口 -------------------------------------------------------------
    def act(self, action: Action) -> dict[str, Any]:
        with self._lock:
            if self._ledger_error and action.type != "reset":
                return {"ok": False, "error": self.last_error, "state": self.state.to_dict()}
            before = self.state
            try:
                candidate = apply(before, action, self.pack)
                if action.type == "reset" and self._save_path is not None:
                    next_store = self._store_for(candidate)
                    self._start_event(next_store, candidate)
                    self.event_store = next_store
                    self._ledger_error = None
                elif self.event_store is not None:
                    self.event_store.append(MatchEvent.create(
                        match_id=candidate.match_id,
                        seq=self.event_store.next_seq(),
                        round_no=candidate.round_no,
                        phase=candidate.phase,
                        type=action.type,
                        payload={
                            "action": action.to_dict(),
                            "gold_before": before.gold,
                            "gold_after": candidate.gold,
                            "shop_level": before.shop_level,
                            "shop_after": [o.to_dict() for o in candidate.shop],
                            **(
                                refresh_details(self.pack, before, action.args)
                                if action.type == "refresh" else {}
                            ),
                        },
                    ))
                self.state = candidate
                self.last_error = None
                ok = True
            except ActionError as exc:
                self.last_error = str(exc)
                ok = False
            except LedgerError as exc:
                # 账本写不进去就不能再让状态前进，否则重放会与内存分叉。
                self.last_error = f"事件账本写入失败，已暂停操作：{exc}"
                self._ledger_error = self.last_error
                ok = False
            self.last_change = time.time()
            self._rev += 1
            if ok:
                self.save()
            return {"ok": ok, "error": self.last_error, "state": self.state.to_dict()}

    def observe(self, obs: Observation, *, min_confidence: float = 0.6) -> dict[str, Any]:
        with self._lock:
            if self._ledger_error:
                return {"ok": False, "error": self.last_error, "state": self.state.to_dict()}
            before = self.state
            try:
                candidate = apply_observation(before, obs, self.pack, min_confidence=min_confidence)
                if self.event_store is not None and candidate.to_dict() != before.to_dict():
                    self.event_store.append(MatchEvent.create(
                        match_id=candidate.match_id,
                        seq=self.event_store.next_seq(),
                        round_no=candidate.round_no,
                        phase=candidate.phase,
                        type="observation",
                        payload={
                            "observation": obs.to_dict(),
                            "shop_after": [o.to_dict() for o in candidate.shop],
                        },
                    ))
                self.state = candidate
                self.last_error = None
            except LedgerError as exc:
                self.last_error = f"事件账本写入失败，已暂停操作：{exc}"
                self._ledger_error = self.last_error
                return {"ok": False, "error": self.last_error, "state": self.state.to_dict()}
            self.last_change = time.time()
            self._rev += 1
            self.save()
            return {"ok": True, "state": self.state.to_dict()}

    def observe_batch(
        self, observations: list[Observation], *, min_confidence: float = 0.95
    ) -> list[dict[str, Any]]:
        """同一帧的观测作为一个批次同步，期间不插入手工动作。"""
        with self._lock:
            return [self.observe(obs, min_confidence=min_confidence) for obs in observations]

    def record_observed_refresh(
        self,
        *,
        expected_revision: int,
        gold_before: int,
        gold_after: int,
        shop: list[dict],
        evidence: str,
        refresh_type: str = "normal",
        refresh_source: str = "standard",
        attribution: str = "gold_and_shop",
    ) -> dict[str, Any]:
        """只记录画面证明的结果；未知商店变化不扣款、不伪装成普通刷新。"""
        with self._lock:
            if self._ledger_error:
                return {"ok": False, "error": self.last_error}
            if self.event_store is None:
                return {"ok": False, "error": "尚未设置存档目录"}
            if expected_revision != self.revision:
                return {"ok": False, "error": "同步批次已过期"}
            if refresh_type not in ("normal", "free", "special", "unknown"):
                return {"ok": False, "error": "未知刷新类型"}
            gold_drop = gold_before - gold_after
            valid_drop = (
                gold_drop in (0, self.pack.economy.refresh_cost) if refresh_type == "unknown"
                else gold_drop == (0 if refresh_type == "free" else self.pack.economy.refresh_cost)
            )
            if not valid_drop:
                return {"ok": False, "error": "资金变化与刷新类型不符"}
            if not shop or not all(isinstance(item, dict) and item.get("known") and item.get("op_id") for item in shop):
                return {"ok": False, "error": "商店证据不完整"}
            event_type = "shop_change_observed" if refresh_type == "unknown" else "refresh_observed"
            try:
                self.event_store.append(MatchEvent.create(
                    match_id=self.state.match_id,
                    seq=self.event_store.next_seq(),
                    round_no=self.state.round_no,
                    phase=self.state.phase,
                    type=event_type,
                    payload={
                        "action": {"source": "vision"},
                        "gold_before": gold_before,
                        "gold_after": gold_after,
                        "shop_level": self.state.shop_level,
                        "shop_after": shop,
                        "evidence": evidence,
                        "refresh_type": refresh_type,
                        "refresh_source": refresh_source,
                        "attribution": attribution,
                    },
                ))
            except LedgerError as exc:
                self.last_error = f"事件账本写入失败，已暂停操作：{exc}"
                self._ledger_error = self.last_error
                return {"ok": False, "error": self.last_error}
            self.last_change = time.time()
            self._rev += 1
            return {"ok": True}

    def reset(self, mode_id: str | None = None, band_id: str | None = None) -> None:
        self.act(Action("reset", {"mode_id": mode_id, "band_id": band_id}))

    def event_history(self, limit: int = 50) -> list[dict[str, Any]]:
        if self.event_store is None or self._ledger_error:
            return []
        return [e.to_dict() for e in self.event_store.load()[-max(1, limit):]]

    def reload_pack(self) -> None:
        with self._lock:
            self.pack = self.pack.reload()
            self._rev += 1

    # -- 战绩 -------------------------------------------------------------
    @property
    def matches(self) -> MatchService:
        if self._matches is None:
            self._matches = MatchService("var/matches.jsonl")
        return self._matches

    @property
    def stats_store(self):
        return self.matches.store

    def attach_stats(self, path: str | Path) -> None:
        self.matches.attach(path)

    def record_match(
        self,
        result: str = "fail",
        *,
        cups: int | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """把当前终局快照落成一条战绩（同一局重复点只记一次）。"""
        from ..stats.model import RESULTS

        if result not in RESULTS:
            return {"ok": False, "error": f"result 必须是 {', '.join(RESULTS)} 之一"}
        with self._lock:
            if self._ledger_error:
                return {"ok": False, "error": self.last_error}
            existing = self.matches.find_by_match_id(self.state.match_id)
            if existing is not None:
                return {
                    "ok": True, "record": existing.to_dict(),
                    "count": self.stats_store.count(), "deduplicated": True,
                }

            def _append_end(rec) -> None:
                if self.event_store is None:
                    return
                self.event_store.append(MatchEvent.create(
                    match_id=self.state.match_id,
                    seq=self.event_store.next_seq(),
                    round_no=self.state.round_no,
                    phase=self.state.phase,
                    type="match_end",
                    payload={"record_id": rec.id, "result": result},
                ))

            try:
                rec = self.matches.record(
                    self.state, self.pack,
                    result=result, cups=cups, note=note, ledger=_append_end,
                )
            except LedgerError as exc:
                self.last_error = f"事件账本写入失败，已暂停操作：{exc}"
                self._ledger_error = self.last_error
                self.last_change = time.time()
                self._rev += 1
                return {"ok": False, "error": self.last_error, "count": self.stats_store.count()}
            self.last_change = time.time()
            self._rev += 1
            return {"ok": True, "record": rec.to_dict(), "count": self.stats_store.count()}

    def stats_view(
        self,
        *,
        mode_id: str | None = None,
        band_id: str | None = None,
        patch: str | None = None,
        min_sample: int = 5,
    ) -> dict[str, Any]:
        with self._lock:
            return self.matches.view(
                self.pack,
                mode_id=mode_id, band_id=band_id, patch=patch, min_sample=min_sample,
            )

    def import_matches(self, text: str) -> dict[str, Any]:
        with self._lock:
            return self.matches.import_text(text)

    def export_matches(self, fmt: str = "jsonl") -> str:
        with self._lock:
            return self.matches.export(fmt)

    # -- 快照 -------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            st = self.state
            mode = self.mode
            pool = pool_snapshot(self.pack, st, mode=mode)
            odds = shop_odds(self.pack, st, mode=mode, pool=pool)
            econ = economy_view(self.pack, st, mode=mode)
            bonds = bond_table(self.pack, st, mode=mode)
            shop_ops = {o.op_id for o in st.shop if o.op_id}
            owned = st.owned_copies()
            focus = [
                {
                    "op_id": oid,
                    "name": (self.pack.op(oid).name if self.pack.op(oid) else oid),
                    "tier": (self.pack.op(oid).tier if self.pack.op(oid) else 0),
                    "owned": cnt,
                    "in_shop": oid in shop_ops,
                }
                for oid, cnt in sorted(owned.items(), key=lambda kv: -kv[1])
            ]
            from ..calc.effects import purchase_price
            from ..stats.refreshes import summarize_refreshes
            shop_prices = [
                purchase_price(self.pack, st, offer.op_id, offer.tier or 1)
                if offer.op_id and offer.kind != "item" else None
                for offer in st.shop
            ]
            events = (
                self.event_store.load()
                if self.event_store is not None and not self._ledger_error else []
            )
            return {
                "pack": {
                    "id": self.pack.id,
                    "name": self.pack.name,
                    "season": self.pack.season,
                    "updated": self.pack.updated,
                    "rules_version": self.pack.rules_version,
                    "source_type": self.pack.source_type,
                    "source": self.pack.source,
                    "limitations": self.pack.limitations,
                    "warnings": self.pack.warnings,
                    "confidence": self.pack.confidence,
                },
                "mode": {
                    "id": mode.id,
                    "name": mode.name,
                    "code": mode.code,
                    "type": mode.mode_type,
                    "difficulty": mode.difficulty,
                    "rounds": mode.rounds,
                    "char_slots": mode.char_slots(st.shop_level),
                    "upgrade_price": (
                        mode.rule(st.shop_level).upgrade_price if mode.rule(st.shop_level) else 0
                    ),
                },
                "state": st.to_dict(),
                "pool": pool.to_dict(),
                "odds": odds.to_dict(),
                "economy": econ.to_dict(),
                "bonds": bonds,
                "shop_prices": shop_prices,
                "focus": focus,
                "refresh_analysis": summarize_refreshes(events),
                "ledger": {
                    "match_id": st.match_id,
                    "events": len(events),
                    "error": self._ledger_error,
                },
                "error": self.last_error,
                "ts": time.time(),
            }

    def pack_view(self) -> dict[str, Any]:
        """前端启动时拉一次的静态数据（干员、盟约、模式、策略）。"""
        with self._lock:
            return {
                "summary": self.pack.summary(),
                "core_bond_groups": [list(g) for g in getattr(self.pack, "core_bond_groups", [])],
                "operators": {
                    op.id: {
                        "name": op.name,
                        "tier": op.tier,
                        "bonds": list(op.bonds),
                        "price": op.price,
                        "sell": op.sell,
                        "profession": op.profession,
                        "sub": op.sub,
                        "rarity": op.rarity,
                    }
                    for op in self.pack.operators.values()
                    if not op.hidden
                },
                "bonds": {
                    b.id: {
                        "name": b.name,
                        "category": b.category,
                        "activate_at": b.activate_at,
                        "in_deck": b.in_deck,
                        "desc": b.desc,
                        "levels": list(b.levels),
                        "effects": b.effects,
                    }
                    for b in self.pack.bonds.values()
                },
                "bands": list(self.pack.bands.values()),
            }
