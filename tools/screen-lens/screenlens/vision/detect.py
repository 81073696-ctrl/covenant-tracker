"""自适应切分：粗区域 → 找出里面的「内容块」。

用户的原话是「画大范围然后逐步筛选」。这里的第一层筛选就是：
在粗区域里找连续的、有内容的列段（卡片/数字块），再按尺寸/宽高比过滤。

它不假设槽位数量或固定坐标，所以等级变化、卡片增减、界面缩放都能适应。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def _modal_color(crop: np.ndarray) -> np.ndarray:
    """粗量化后出现最多的颜色，当底板色。"""
    q = (crop // 8 * 8).reshape(-1, 3)
    if q.size == 0:
        return np.zeros(3, dtype=np.int16)
    packed = (q[:, 0].astype(np.int32) << 16) | (q[:, 1].astype(np.int32) << 8) | q[:, 2]
    vals, counts = np.unique(packed, return_counts=True)
    best = int(vals[int(np.argmax(counts))])
    return np.array([(best >> 16) & 0xFF, (best >> 8) & 0xFF, best & 0xFF], dtype=np.int16)


def _runs(mask: np.ndarray, *, min_len: int = 1) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    start = None
    for i, on in enumerate(mask):
        if on and start is None:
            start = i
        elif not on and start is not None:
            out.append([start, i])
            start = None
    if start is not None:
        out.append([start, len(mask)])
    return [(a, b) for a, b in out if (b - a) >= min_len]


def detect_runs(
    image: np.ndarray,
    rect: tuple[int, int, int, int],
    *,
    bg_tol: int = 26,
    min_content: float = 0.25,
    min_w_frac: float = 0.03,
    max_w_frac: float = 0.14,
    min_aspect: float = 0.7,
    max_aspect: float = 2.4,
) -> list[Box]:
    """横行：在 rect 里找内容段，返回卡片状的内容块。"""
    x, y, w, h = [int(v) for v in rect]
    x = max(0, x); y = max(0, y)
    w = min(w, image.shape[1] - x); h = min(h, image.shape[0] - y)
    if w < 40 or h < 20:
        return []
    crop = image[y:y + h, x:x + w].astype(np.int16)
    bg = _modal_color(crop)
    content = np.abs(crop - bg.reshape(1, 1, 3)).max(axis=2) > bg_tol

    prof = content.mean(axis=0)
    runs = _runs(prof >= min_content, min_len=max(4, int(w * 0.02)))

    boxes: list[Box] = []
    for a, b in runs:
        bw = b - a
        if bw < w * min_w_frac or bw > w * max_w_frac:
            continue
        sub = content[:, a:b]
        rprof = sub.mean(axis=1)
        rruns = _runs(rprof >= 0.10, min_len=max(4, int(h * 0.30)))
        if not rruns:
            continue
        top = min(r[0] for r in rruns)
        bottom = max(r[1] for r in rruns)
        bh = bottom - top
        if bh < 8:
            continue
        aspect = bh / bw
        if not (min_aspect <= aspect <= max_aspect):
            continue
        boxes.append(Box(x + a, y + top, bw, bh))
    return boxes
