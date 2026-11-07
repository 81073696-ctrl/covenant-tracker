"""采集与自动识别：把「抓帧 → 识别 → 喂给引擎」从 HTTP 层里拿出来。

为什么单独一层
--------------
自动识别是一个**有生命周期的后台线程**（启动、节流、停止、错误上报）。
之前它和 HTTP handler 混在 `AppServer` 里，线程状态靠几个字段 + 注释维持约定，
既不好测，也不好换 UI。这里把它收成一个对象：

- :class:`CaptureService` 持有采集源、最近一帧、线程与计数；
- `AppServer` 只做委托，路由层拿到的仍是同样的 dict。

只读屏幕、不碰游戏内存的边界不变。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from ..calc.effects import observed_refresh_details
from ..core.types import Observation, ObservationKind


def same_as_state(state: Any, obs: Observation) -> bool:
    """这条观测和当前状态是不是「一样」，一样就不用再喂给引擎。

    自动识别每 2 秒读一次，如果同值也写账本，事件文件会被刷爆；
    只跳过**确定相同**的，`unknown` 永远不相等（宁可多写一次，也不漏变化）。
    """
    kind = obs.kind
    if kind == ObservationKind.GOLD:
        return state.gold == obs.value
    if kind == ObservationKind.ROUND:
        return state.round_no == obs.value
    if kind == ObservationKind.HP:
        return state.hp == obs.value
    if kind == ObservationKind.SHOP_LEVEL:
        return state.shop_level == obs.value
    if kind == ObservationKind.SHOP:
        raw = obs.value if isinstance(obs.value, list) else []
        known = {o.slot: o.op_id for o in state.shop if o.op_id}
        incoming = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            op_id = item.get("op_id")
            if item.get("known") and op_id:
                incoming[int(item.get("slot", -1))] = op_id
        return bool(incoming) and incoming == known
    if kind in (ObservationKind.BENCH, ObservationKind.BOARD):
        zone = state.bench if kind == ObservationKind.BENCH else state.board
        incoming = [
            item.get("op_id")
            for item in (obs.value or [])
            if isinstance(item, dict) and item.get("op_id")
        ]
        return incoming == [u.op_id for u in zone]
    return False


class CaptureService:
    """采集源 + 自动识别线程。"""

    def __init__(self, engine: Any, vision: Any) -> None:
        from .follow import FollowGate

        self.engine = engine
        self.vision = vision
        self.source: Any = None
        self.last_frame: Any = None
        self.interval = 2.0
        self.on = False
        self.last_ts = 0.0
        self.last_sync: float | None = None
        self.reads = 0
        self.changed = 0
        self.batch = 0
        self.pending: list[dict[str, Any]] = []
        self.follow_state = "disconnected"
        self.error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sync_lock = threading.RLock()
        self._gate = FollowGate()
        self._last_gold_change: dict[str, Any] | None = None

    # -- 单帧 ---------------------------------------------------------------
    def grab(self):
        if self.source is None:
            raise RuntimeError("还没有接入采集源")
        frame = self.source.grab()
        self.last_frame = frame
        return frame

    def grab_png(self) -> tuple[bytes, int]:
        if self.source is None:
            return b"", 503
        try:
            return self.source.png(), 200
        except Exception as exc:  # noqa: BLE001 - 前端需要看到原因
            import json

            return json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"), 500

    def read(self, *, only_changed: bool = False) -> dict[str, Any]:
        with self._sync_lock:
            before = self.engine.state.to_dict()
            frame = self.grab()
            stable, self.pending = self._gate.accept(list(self.vision.read(frame)))
            observations = [
                ob for ob in stable
                if not only_changed or not same_as_state(self.engine.state, ob)
            ]
            responses = self.engine.observe_batch(observations)
            after = self.engine.state
            before_shop = [item.get("op_id") for item in before["shop"] if item.get("known")]
            after_shop = [item.op_id for item in after.shop if item.known]
            # DeepSeek 协作：首次形成完整基线不是刷新，避免把零扣款初始化重复记账。
            before_full_shop = bool(before["shop"]) and all(
                item.get("known") and item.get("op_id") for item in before["shop"]
            )
            full_shop = bool(after.shop) and all(item.known and item.op_id for item in after.shop)
            gold_drop = before["gold"] - after.gold
            same_round = (
                before["match_id"] == after.match_id
                and before["round_no"] == after.round_no
                and before["shop_level"] == after.shop_level
            )
            after_bench = [item.to_dict() for item in after.bench]
            after_board = [item.to_dict() for item in after.board]
            same_context = (
                same_round
                and before["bench"] == after_bench
                and before["board"] == after_board
            )
            standard_cost = self.engine.pack.economy.refresh_cost
            now = time.monotonic()
            if gold_drop == standard_cost and same_context:
                # Kimi 协作：资金可能比商店早一帧稳定，保留短期证据供下一帧归因。
                self._last_gold_change = {
                    "ts": now,
                    "gold_before": before["gold"],
                    "gold_after": after.gold,
                    "match_id": after.match_id,
                    "round_no": after.round_no,
                    "shop_level": after.shop_level,
                    "bench": after_bench,
                    "board": after_board,
                }
            cached = self._last_gold_change
            cache_matches = bool(
                cached
                and gold_drop == 0
                and same_context
                and now - cached["ts"] <= 15.0
                and cached["match_id"] == after.match_id
                and cached["round_no"] == after.round_no
                and cached["shop_level"] == after.shop_level
                and cached["gold_after"] == after.gold
                and cached["bench"] == after_bench
                and cached["board"] == after_board
            )
            gold_before = cached["gold_before"] if cache_matches and cached else before["gold"]
            gold_after = cached["gold_after"] if cache_matches and cached else after.gold
            effective_gold_drop = gold_before - gold_after
            refresh_key = (after.match_id, after.round_no, gold_before, gold_after, tuple(after_shop))
            shop_changed = before_shop != after_shop
            refresh_details = observed_refresh_details(self.engine.pack, after, effective_gold_drop)
            valid_gold_drop = (
                effective_gold_drop in (0, standard_cost) if refresh_details["refresh_type"] == "unknown"
                else effective_gold_drop == standard_cost
            )
            refresh_details.pop("cost", None)  # 观测只记录结果，不再次扣款。
            # Kimi 协作：有真实扣款时，已招募空格不影响付费刷新归因；
            # 零扣款仍要求完整基线，避免把首次学习商店误记成免费刷新。
            shop_baseline_ready = before_full_shop or (
                effective_gold_drop == standard_cost and bool(before_shop)
            )
            if (
                stable and not self.pending and same_context and shop_baseline_ready and full_shop
                and shop_changed and valid_gold_drop
                and getattr(self, "_last_refresh_key", None) != refresh_key
            ):
                recorded = self.engine.record_observed_refresh(
                    expected_revision=self.engine.revision,
                    gold_before=gold_before,
                    gold_after=gold_after,
                    shop=[item.to_dict() for item in after.shop],
                    evidence="stable_shop_and_gold",
                    **refresh_details,
                )
                if recorded.get("ok"):
                    self._last_refresh_key = refresh_key
                    self._last_gold_change = None
            elif stable and same_round and (
                effective_gold_drop == standard_cost
                or (before_full_shop and shop_changed and effective_gold_drop == 0)
            ):
                self.pending.append({"kind": "refresh", "confidence": 0.0, "reason": "incomplete_evidence"})
            results = [
                {
                    "kind": ob.kind,
                    "value": ob.value,
                    "confidence": round(ob.confidence, 3),
                    "ok": bool(res.get("ok")),
                    "error": res.get("error"),
                }
                for ob, res in zip(observations, responses)
            ]
            self.batch += 1
            if results:
                self.last_sync = time.time()
            self.follow_state = (
                "needs_confirmation" if self.pending
                else "stabilizing" if self.batch == 1 or not stable
                else "following"
            )
            return {
                "ok": True,
                "observations": results,
                "count": len(results),
                "state": self.follow_state,
                "pending": list(self.pending),
                "batch": self.batch,
                "last_sync": self.last_sync,
            }

    # -- 状态 ---------------------------------------------------------------
    def source_title(self) -> str | None:
        if self.source is None:
            return None
        try:
            return self.source.title()
        except Exception:  # noqa: BLE001
            return type(self.source).__name__

    def status(self) -> dict[str, Any]:
        state = self.follow_state
        if self.source is None:
            state = "disconnected"
        elif not self.on:
            state = "paused"
        return {
            "ok": True,
            "source": self.source_title(),
            "on": self.on,
            "interval": self.interval,
            "last_ts": self.last_ts,
            "last_sync": self.last_sync,
            "reads": self.reads,
            "changed": self.changed,
            "state": state,
            "pending": list(self.pending),
            "batch": self.batch,
            "error": self.error,
        }

    # -- 线程 ---------------------------------------------------------------
    def set_source(self, source: Any) -> None:
        """换源后不得沿用旧窗口的稳定帧候选。"""
        self.stop()
        with self._sync_lock:
            self.source = source
            self._gate.reset()
            self._last_refresh_key = None
            self._last_gold_change = None
            self.batch = 0
            self.pending = []
            self.last_sync = None
            self.follow_state = "disconnected" if source is None else "paused"

    def start(self, interval: float = 2.0) -> dict[str, Any]:
        if self.source is None:
            raise RuntimeError("还没有接入采集源：先用 --window / /api/frame-source 接一个")
        self.stop()
        self._gate.reset()
        self._last_refresh_key = None
        self._last_gold_change = None
        self.batch = 0
        self.pending = []
        self.last_sync = None
        self.interval = max(0.5, min(30.0, float(interval or 2.0)))
        self.on = True
        self.error = None
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None
        self.on = False
        return self.status()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                res = self.read(only_changed=True)
                self.reads += 1
                self.changed += int(res.get("count") or 0)
                self.last_ts = time.time()
                self.error = None
            except Exception as exc:  # noqa: BLE001 - 状态里给前端看
                self.error = str(exc)
            self._stop.wait(self.interval)
