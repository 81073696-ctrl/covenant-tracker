"""图标/头像模板匹配：归一化互相关（NCC），纯 numpy。

用法和数字识别一样——「教」一遍：
- 在 /teach 里框住商店某一格，选择它是哪个干员，模板就存下来；
- 之后同一位置（或同尺寸的格位）切出来的图，就和模板比相似度。

模板存的是**整格截图**（不是美术资源），所以 UI 改版后重新教即可，不用改代码。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

TEMPLATE_SIZE = (48, 64)  # (w, h)
MIN_SIM = 0.62


def _prep(image: np.ndarray) -> np.ndarray | None:
    from PIL import Image

    arr = np.asarray(image)
    if arr.ndim == 3:
        arr = arr[..., :3].mean(axis=2)
    if arr.size == 0:
        return None
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    img = img.convert("L").resize(TEMPLATE_SIZE, Image.BILINEAR)
    v = np.asarray(img, dtype=np.float32).reshape(-1)
    v = v - v.mean()
    n = float(np.linalg.norm(v))
    if n < 1e-3:
        return None
    return v / n


def _shifted(image: np.ndarray, dx: int, dy: int) -> np.ndarray | None:
    h, w = image.shape[:2]
    x0, y0 = max(0, dx), max(0, dy)
    x1, y1 = min(w, w + dx), min(h, h + dy)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return image[y0:y1, x0:x1]


class IconMatcher:
    def __init__(self, min_sim: float = MIN_SIM, search: float = 0.06):
        self.templates: dict[str, list[np.ndarray]] = {}
        self.min_sim = min_sim
        self.search = search  # 平移搜索半径（相对短边），0 表示不搜索

    def add(self, key: str, image: np.ndarray) -> bool:
        v = _prep(image)
        if v is None:
            return False
        self.templates.setdefault(str(key), []).append(v)
        return True

    def rank(self, image: np.ndarray, k: int = 3) -> list[tuple[str, float]]:
        """返回最像的 k 个模板及分数（不过阈值），给诊断/工作台用。"""
        if not self.templates:
            return []
        arr = np.asarray(image)
        if arr.ndim == 2:
            arr = arr[:, :, None]
        s = int(round(min(arr.shape[0], arr.shape[1]) * self.search)) if self.search else 0
        offsets = [(0, 0)]
        if s > 0:
            offsets += [(dx, dy) for dx in (-s, s) for dy in (-s, 0, s)]
            offsets += [(0, dy) for dy in (-s, s)]
        best: dict[str, float] = {}
        for dx, dy in offsets:
            patch = _shifted(arr, dx, dy)
            if patch is None:
                continue
            v = _prep(patch)
            if v is None:
                continue
            for key, vecs in self.templates.items():
                for t in vecs:
                    score = float(np.dot(v, t))
                    if score > best.get(key, -1.0):
                        best[key] = score
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)[: max(1, int(k))]

    def classify(self, image: np.ndarray) -> tuple[str, float]:
        top = self.rank(image, k=1)
        if not top:
            return "", 0.0
        key, score = top[0]
        return (key, score) if score >= self.min_sim else ("", score)

    def keys(self) -> list[str]:
        return list(self.templates)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        arrays = {k: np.stack(v) for k, v in self.templates.items() if v}
        if arrays:
            np.savez_compressed(p, **arrays)

    def load(self, path: str | Path) -> bool:
        p = Path(path)
        if not p.exists():
            return False
        try:
            with np.load(p) as data:
                self.templates = {k: [row for row in data[k]] for k in data.files}
        except (OSError, ValueError):
            return False
        return True
