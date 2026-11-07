"""自动跟随的稳定帧门控；候选只留在内存，不写账本。"""

from __future__ import annotations

import json
from typing import Any

from ..core.types import Observation, ObservationKind


class FollowGate:
    """同一字段连续两帧一致且高置信度时才放行。"""

    def __init__(self, min_confidence: float = 0.95) -> None:
        self.min_confidence = min_confidence
        self._candidates: dict[str, tuple[str, Observation]] = {}
        self._scene: str | None = None

    def reset(self) -> None:
        self._candidates.clear()
        self._scene = None

    @staticmethod
    def _fingerprint(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)

    def accept(self, observations: list[Observation]) -> tuple[list[Observation], list[dict[str, Any]]]:
        latest = {obs.kind: obs for obs in observations}
        scene = latest.get(ObservationKind.SCENE)
        if scene is not None and scene.confidence >= self.min_confidence:
            scene_name = str(scene.value or "")
            if scene_name != self._scene:
                self.reset()
            self._scene = scene_name

        accepted: list[Observation] = []
        pending: list[dict[str, Any]] = []
        for kind, obs in latest.items():
            if obs.confidence < self.min_confidence or obs.value is None or obs.value == "unknown":
                self._candidates.pop(kind, None)
                pending.append({
                    "kind": kind,
                    "confidence": round(float(obs.confidence), 3),
                    "reason": "low_confidence",
                })
                continue
            fingerprint = self._fingerprint(obs.value)
            previous = self._candidates.get(kind)
            self._candidates[kind] = (fingerprint, obs)
            if previous is not None and previous[0] == fingerprint:
                accepted.append(obs)
        return accepted, pending
