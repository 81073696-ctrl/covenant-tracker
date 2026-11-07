"""行内等距切分：先找卡片行的「节奏」，再对齐边界。

适用：商店/整备区这类一排等宽卡片（数量会变，但间距稳定）。
不依赖固定槽位坐标，也不依赖卡片内部颜色——只看竖直边缘的周期性。
"""

from __future__ import annotations

import numpy as np

from .detect import Box


def _edge_profile(gray: np.ndarray, axis: int) -> np.ndarray:
    d = np.abs(np.diff(gray, axis=axis)).mean(axis=1 - axis)
    return d


def detect_row(
    image: np.ndarray,
    rect: tuple[int, int, int, int],
    *,
    pitch_range: tuple[float, float] = (0.04, 0.22),
    min_content: float = 0.22,
) -> list[Box]:
    """在 rect 里按等距节奏切出一排卡片。"""
    x, y, w, h = [int(v) for v in rect]
    x = max(0, x); y = max(0, y)
    w = min(w, image.shape[1] - x); h = min(h, image.shape[0] - y)
    if w < 40 or h < 20:
        return []
    crop = image[y:y + h, x:x + w].astype(np.float32)
    g = 0.299 * crop[..., 0] + 0.587 * crop[..., 1] + 0.114 * crop[..., 2]

    dx = _edge_profile(g, axis=0)
    dx = dx - dx.mean()
    if not np.any(dx):
        return []
    acf = np.correlate(dx, dx, mode="full")[len(dx) - 1:]
    acf = acf / (acf[0] + 1e-9)
    lo = max(4, int(w * pitch_range[0]))
    hi = min(len(dx) - 2, int(w * pitch_range[1]))
    if hi <= lo:
        return []
    pitch = int(np.argmax(acf[lo:hi]) + lo)
    if acf[pitch] < 0.10:  # 没有明显节奏，交回通用切分
        return []

    # 相位对齐：让边界落在竖直边缘最强的地方
    best = (0.0, 0)
    for off in range(pitch):
        score = 0.0
        for bx in range(off, len(dx), pitch):
            score += float(np.abs(dx[max(0, bx - 2):bx + 3]).sum())
        if score > best[0]:
            best = (score, off)
    start = best[1]

    # 每条边界在 ±18% 间距内做一次局部对齐
    bounds = [start + i * pitch for i in range(0, (len(dx) - start) // pitch + 2)]
    refined: list[int] = []
    for b in bounds:
        a = int(max(0, b - pitch * 0.18))
        z = int(min(len(dx) - 1, b + pitch * 0.18))
        if z <= a:
            continue
        j = a + int(np.argmax(np.abs(dx[a:z])))
        if not refined or j - refined[-1] >= pitch * 0.55:
            refined.append(j)
    if len(refined) < 2:
        return []

    content = np.abs(np.diff(g, axis=1)).mean(axis=0)
    lo_e, hi_e = np.percentile(content, 10), np.percentile(content, 95)
    denom = max(1e-6, hi_e - lo_e)

    boxes: list[Box] = []
    for a, b in zip(refined, refined[1:]):
        if b - a < pitch * 0.6:
            continue
        seg = content[a + 2:b - 2]
        if seg.size == 0:
            continue
        # 段内「有内容」的列占比
        frac = float(np.mean((seg - lo_e) / denom > 0.25))
        if frac < min_content:
            continue
        rows = np.abs(np.diff(g[:, a + 2:b - 2], axis=0)).mean(axis=1)
        rr = rows - rows.mean()
        th = max(1e-6, float(rr.max()) * 0.25)
        ys = np.where(rr > th)[0]
        if ys.size < 8:
            continue
        top, bottom = int(ys.min()), int(ys.max()) + 1
        boxes.append(Box(x + a, y + top, b - a, bottom - top))
    return boxes
