"""面板设置：少量「下次启动仍然生效」的项。

只有两项：识别间隔、是否随面板自动开始。端口、数据包目录刻意不放进来
（改它们需要重启，放进面板只会让人以为改了立刻生效）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULTS = {"capture_interval": 2.0, "auto_capture": True}
MIN_INTERVAL = 0.5
MAX_INTERVAL = 30.0


class SettingsService:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> dict[str, Any]:
        data = dict(DEFAULTS)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 文件坏了就用默认值
            raw = None
        if isinstance(raw, dict):
            if isinstance(raw.get("capture_interval"), (int, float)):
                data["capture_interval"] = min(
                    MAX_INTERVAL, max(MIN_INTERVAL, float(raw["capture_interval"]))
                )
            if isinstance(raw.get("auto_capture"), bool):
                data["auto_capture"] = raw["auto_capture"]
        self.data = data
        return data

    def save(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("设置必须是对象")
        interval = patch.get("capture_interval", self.data["capture_interval"])
        if not isinstance(interval, (int, float)) or isinstance(interval, bool):
            raise ValueError("识别间隔必须是数字")
        interval = round(min(MAX_INTERVAL, max(MIN_INTERVAL, float(interval))), 2)

        auto = patch.get("auto_capture", self.data["auto_capture"])
        if not isinstance(auto, bool):
            raise ValueError("自动识别开关必须是 true/false")

        data = {"capture_interval": interval, "auto_capture": auto}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            raise ValueError(f"保存失败：{exc}") from exc
        self.data = data
        return dict(data)
