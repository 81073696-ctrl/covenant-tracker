"""合成采集源：不用游戏也能跑通整条链路。

生成一张「像那么回事」的调度中心截图（资金 / 等级 / 商店格 / 整备区），
用于：演示、识别回归测试、教它认界面时先有个样本。
"""

from __future__ import annotations

import random
import time

import numpy as np

from .base import Frame, FrameSource


class SyntheticSource(FrameSource):
    def __init__(self, width: int = 1280, height: int = 720, *, seed: int | None = None):
        self.w = width
        self.h = height
        self.rng = random.Random(seed)
        self.gold = 10
        self.level = 4
        self.round = 3
        self._names = ["隐现", "古米", "德克萨斯", "惊蛰", "刺玫", "普罗旺斯"]

    def title(self) -> str:
        return "合成画面（无游戏）"

    def grab(self) -> Frame:
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (self.w, self.h), (14, 16, 19))
        d = ImageDraw.Draw(img)

        def font(size: int):
            for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf"):
                try:
                    return ImageFont.truetype(name, size)
                except OSError:
                    continue
            return ImageFont.load_default()

        # 顶部资源条
        d.rectangle([0, 0, self.w, 52], fill=(23, 26, 32))
        d.text((24, 14), f"资金 {self.gold}", font=font(20), fill=(246, 163, 41))
        d.text((220, 14), f"调度中心 Lv.{self.level}", font=font(20), fill=(230, 232, 236))
        d.text((520, 14), f"回合 {self.round}", font=font(20), fill=(230, 232, 236))

        # 商店 5 格
        n = 5
        gap = 16
        cw = (self.w - 80 - gap * (n - 1)) // n
        for i in range(n):
            x = 40 + i * (cw + gap)
            y = 110
            d.rounded_rectangle([x, y, x + cw, y + 210], radius=12, fill=(27, 31, 38), outline=(52, 58, 70))
            d.text((x + 16, y + 16), self._names[i % len(self._names)], font=font(22), fill=(230, 232, 236))
            d.text((x + 16, y + 52), f"T{1 + i % 3}", font=font(14), fill=(120, 128, 140))
            d.text((x + cw - 44, y + 170), "2", font=font(22), fill=(246, 163, 41))
            d.rounded_rectangle([x + 12, y + 160, x + cw - 12, y + 196], radius=6, outline=(70, 78, 92))

        # 整备区
        d.text((40, 360), "整备区", font=font(16), fill=(139, 146, 158))
        for i in range(6):
            x = 40 + i * 96
            d.rounded_rectangle([x, 390, x + 84, 470], radius=8, fill=(27, 31, 38), outline=(45, 50, 60))

        arr = np.asarray(img, dtype=np.uint8).copy()
        return Frame(image=arr, ts=time.time(), meta={"synthetic": True})
