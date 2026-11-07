"""回放采集源：把一叠截图当视频用。

适合在没有游戏（或不想开游戏）的时候调识别：把界面截图按顺序命名放进一个
目录，``FolderSource`` 每次 ``grab()`` 前进一帧，循环播放。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import Frame, FrameSource

EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}


class FolderSource(FrameSource):
    def __init__(self, folder: str | Path, *, loop: bool = True):
        self.folder = Path(folder)
        self.files = sorted(p for p in self.folder.iterdir() if p.suffix.lower() in EXTS)
        if not self.files:
            raise FileNotFoundError(f"{self.folder} 里没有图片")
        self.loop = loop
        self.index = 0

    def title(self) -> str:
        return f"回放：{self.folder.name}（{len(self.files)} 帧）"

    def grab(self) -> Frame:
        from PIL import Image

        path = self.files[self.index]
        self.index += 1
        if self.index >= len(self.files):
            self.index = 0 if self.loop else len(self.files) - 1
        with Image.open(path) as img:
            rgb = np.asarray(img.convert("RGB"))
        return Frame(image=np.ascontiguousarray(rgb).copy(), meta={"path": str(path)})
