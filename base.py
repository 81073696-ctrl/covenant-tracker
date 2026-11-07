"""采集层的通用类型。"""

from __future__ import annotations

import io
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class Frame:
    """一帧图像。``image`` 是 HxWx3 的 RGB ``uint8`` 数组。"""

    image: np.ndarray
    ts: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def width(self) -> int:
        return int(self.image.shape[1])

    @property
    def height(self) -> int:
        return int(self.image.shape[0])

    def png(self) -> bytes:
        from PIL import Image

        buf = io.BytesIO()
        Image.fromarray(np.ascontiguousarray(self.image)).save(buf, format="PNG")
        return buf.getvalue()

    def crop(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        return self.image[max(0, y) : y + h, max(0, x) : x + w]


class FrameSource(ABC):
    """采集源接口。"""

    @abstractmethod
    def grab(self) -> Frame:
        """抓一帧。失败应抛异常，由上层决定降级策略。"""

    def png(self) -> bytes:
        return self.grab().png()

    def title(self) -> str:
        return self.__class__.__name__

    def close(self) -> None:  # pragma: no cover - 默认无资源
        pass
