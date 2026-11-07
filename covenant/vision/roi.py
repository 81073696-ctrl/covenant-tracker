"""ROI 布局：用相对比例描述「界面的哪一块是什么」。

为什么用比例而不是像素：游戏窗口大小、全屏/窗口化、不同分辨率都会变。
比例坐标在 1920x1080 上标一次，1280x720 也能用。

一个布局长这样::

    {
      "schema": 1,
      "source": "taught",
      "regions": {
        "gold":       {"rect": [0.02, 0.015, 0.09, 0.035], "kind": "digits"},
        "shop_level": {"rect": [0.13, 0.015, 0.06, 0.035], "kind": "digits"},
        "shop":       {"rect": [0.03, 0.14, 0.94, 0.30], "kind": "shop", "params": {"slots": 5}},
        "bench":      {"rect": [0.03, 0.52, 0.94, 0.12], "kind": "units", "params": {"slots": 10}}
      }
    }
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LAYOUT_SCHEMA = 1


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        return (
            int(round(self.x * width)),
            int(round(self.y * height)),
            max(1, int(round(self.w * width))),
            max(1, int(round(self.h * height))),
        )

    @classmethod
    def from_pixels(cls, x: int, y: int, w: int, h: int, width: int, height: int) -> Rect:
        return cls(x / max(1, width), y / max(1, height), w / max(1, width), h / max(1, height))

    def to_list(self) -> list[float]:
        return [round(self.x, 5), round(self.y, 5), round(self.w, 5), round(self.h, 5)]


@dataclass
class Region:
    name: str
    rect: Rect
    kind: str = "digits"  # digits | shop | units | bond | any
    target: str = ""  # 对应的 ObservationKind，空则按名字推断
    params: dict[str, Any] = field(default_factory=dict)


    def split_boundaries(self) -> tuple[list[float] | None, str]:
        """自定义切分边界（相对宽度，长度 = 槽数 + 1）。

        返回 ``(boundaries, mode)``：合法时 mode="custom"；没配置返回
        ``(None, "default")``；配置非法返回 ``(None, "invalid:原因")``，
        调用方应回退到均分，避免一处笔误让整块区域读不出来。
        """
        raw = self.params.get("splits")
        if raw is None:
            return None, "default"
        try:
            values = [float(v) for v in raw]
        except (TypeError, ValueError):
            return None, "invalid:边界必须是数字"
        slots = int(self.params.get("slots") or 1)
        if len(values) != slots + 1:
            return None, "invalid:边界长度必须等于槽数加一"
        if not all(math.isfinite(v) for v in values):
            return None, "invalid:边界必须是有限数"
        if values[0] != 0.0 or values[-1] != 1.0:
            return None, "invalid:首尾必须为0和1"
        if any(b - a < 0.01 for a, b in zip(values, values[1:])):
            return None, "invalid:每格宽度至少为0.01"
        return values, "custom"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"rect": self.rect.to_list(), "kind": self.kind}
        if self.target:
            out["target"] = self.target
        if self.params:
            out["params"] = dict(self.params)
        return out

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> Region:
        rect = data.get("rect") or [0, 0, 0.1, 0.1]
        return cls(
            name=name,
            rect=Rect(*(float(v) for v in rect[:4])),
            kind=str(data.get("kind") or "digits"),
            target=str(data.get("target") or ""),
            params=dict(data.get("params") or {}),
        )


@dataclass
class Layout:
    regions: dict[str, Region] = field(default_factory=dict)
    source: str = "default"
    schema: int = LAYOUT_SCHEMA

    def region(self, name: str) -> Region | None:
        return self.regions.get(name)

    def names(self) -> list[str]:
        return list(self.regions)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source": self.source,
            "regions": {k: v.to_dict() for k, v in self.regions.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Layout:
        regs = {
            k: Region.from_dict(k, v) for k, v in (data.get("regions") or {}).items() if isinstance(v, dict)
        }
        return cls(regions=regs, source=str(data.get("source") or "taught"), schema=int(data.get("schema") or 1))

    @classmethod
    def load(cls, path: str | Path) -> Layout:
        p = Path(path)
        if not p.exists():
            return cls.default()
        try:
            return cls.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return cls.default()

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def default(cls) -> Layout:
        """16:9 的保守猜测。11/8 上线后请用 /teach 重新标注。"""
        regs = {
            "gold": Region("gold", Rect(0.018, 0.012, 0.10, 0.038), "digits"),
            "shop_level": Region("shop_level", Rect(0.135, 0.012, 0.075, 0.038), "digits"),
            "round": Region("round", Rect(0.78, 0.012, 0.09, 0.038), "digits"),
            "hp": Region("hp", Rect(0.90, 0.012, 0.08, 0.038), "digits"),
            "shop": Region("shop", Rect(0.03, 0.14, 0.94, 0.30), "shop", params={"slots": 5}),
            "bench": Region("bench", Rect(0.03, 0.52, 0.94, 0.12), "units", params={"slots": 10}),
            "board": Region("board", Rect(0.03, 0.66, 0.94, 0.20), "units", params={"slots": 8}),
        }
        return cls(regions=regs, source="default")

    def describe(self) -> str:
        return "\n".join(f"{r.name:12s} {r.kind:6s} {r.rect.to_list()}" for r in self.regions.values())
