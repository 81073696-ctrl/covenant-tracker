"""数字识别：模板法（自己学字形），不依赖 tesseract。

为什么不用现成 OCR：
- 游戏里的数字是固定字体、固定字号，模板法的准确率远高于通用 OCR；
- 不用装几百 MB 的依赖；
- 用户可以在 /teach 里点一下「这是 12」，就把字形学进去。

限制：只认「亮字暗底」（游戏 HUD 基本都这样）。反色界面用 ``invert=True``。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

GLYPH_W, GLYPH_H = 20, 28
MIN_SIM = 0.55


def to_gray(image: np.ndarray) -> np.ndarray:
    arr = np.asarray(image)
    if arr.ndim == 3:
        return arr[..., :3].mean(axis=2)
    return arr.astype(np.float32)


def _bbox(mask: np.ndarray) -> tuple[int, int, int, int] | None:
    ys = np.where(mask.any(axis=1))[0]
    xs = np.where(mask.any(axis=0))[0]
    if not len(ys) or not len(xs):
        return None
    return int(xs[0]), int(ys[0]), int(xs[-1] + 1), int(ys[-1] + 1)


def normalize_glyph(gray: np.ndarray) -> np.ndarray | None:
    """裁到墨迹外框，等比缩放并居中到固定画布，返回 0~1 的 float32。"""
    from PIL import Image

    mask = gray > max(1.0, float(gray.max()) * 0.45)
    box = _bbox(mask)
    if box is None:
        return None
    x0, y0, x1, y1 = box
    if x1 - x0 < 2 or y1 - y0 < 3:
        return None
    crop = gray[y0:y1, x0:x1]
    img = Image.fromarray(np.clip(crop, 0, 255).astype(np.uint8))
    scale = min(GLYPH_W / max(1, img.width), GLYPH_H / max(1, img.height))
    img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.BILINEAR)
    canvas = Image.new("L", (GLYPH_W, GLYPH_H), 0)
    canvas.paste(img, ((GLYPH_W - img.width) // 2, (GLYPH_H - img.height) // 2))
    out = np.asarray(canvas, dtype=np.float32) / 255.0
    return out


def _vec(glyph: np.ndarray) -> np.ndarray:
    v = glyph.reshape(-1).astype(np.float32)
    v = v - v.mean()
    n = float(np.linalg.norm(v))
    return v / (n + 1e-6)


class DigitReader:
    def __init__(self, min_sim: float = MIN_SIM):
        self.templates: dict[str, list[np.ndarray]] = {}
        self.min_sim = min_sim

    # -- 学习 -------------------------------------------------------------
    def add_glyph(self, glyph: np.ndarray, label: str) -> None:
        self.templates.setdefault(label, []).append(_vec(glyph))

    def learn(self, image: np.ndarray, text: str) -> int:
        """把一张「完整的数字截图」按字切分后逐字学进去。返回学到的字数。"""
        glyphs = self.segment(to_gray(image))
        if len(glyphs) != len(text):
            # 切分数对不上就整体缩放当单字学（适用于单字）
            if len(text) == 1 and len(glyphs) == 1:
                self.add_glyph(glyphs[0], text)
                return 1
            return 0
        for glyph, ch in zip(glyphs, text):
            self.add_glyph(glyph, ch)
        return len(glyphs)

    # -- 切分 -------------------------------------------------------------
    @staticmethod
    def segment(gray: np.ndarray, invert: bool = False) -> list[np.ndarray]:
        return [glyph for _left, _right, glyph in DigitReader.segment_boxes(gray, invert)]

    @staticmethod
    def segment_boxes(
        gray: np.ndarray, invert: bool = False
    ) -> list[tuple[int, int, np.ndarray]]:
        """和 :meth:`segment` 同一切分，但保留每个字的左右边界（诊断用）。"""
        g = 255.0 - gray if invert else gray
        thr = max(1.0, float(g.max()) * 0.45)
        mask = g > thr
        if not mask.any():
            return []
        cols = mask.any(axis=0)
        out: list[tuple[int, int, np.ndarray]] = []
        start = None
        for i, on in enumerate(cols):
            if on and start is None:
                start = i
            elif not on and start is not None:
                piece = g[:, start:i]
                norm = normalize_glyph(piece)
                if norm is not None:
                    out.append((start, i, norm))
                start = None
        if start is not None:
            piece = g[:, start:]
            norm = normalize_glyph(piece)
            if norm is not None:
                out.append((start, len(cols), norm))
        return out

    # -- 识别 -------------------------------------------------------------
    def rank(self, glyph: np.ndarray, k: int = 3) -> list[tuple[str, float]]:
        """返回最像的 k 个字符及分数（不过阈值），给诊断工具用。"""
        if not self.templates:
            return []
        v = _vec(glyph)
        best: dict[str, float] = {}
        for label, vecs in self.templates.items():
            for t in vecs:
                score = float(np.dot(v, t))
                if score > best.get(label, -1.0):
                    best[label] = score
        return sorted(best.items(), key=lambda kv: kv[1], reverse=True)[: max(1, int(k))]

    def classify(self, glyph: np.ndarray) -> tuple[str, float]:
        if not self.templates:
            return "", 0.0
        v = _vec(glyph)
        best_label, best = "", -1.0
        for label, vecs in self.templates.items():
            for t in vecs:
                score = float(np.dot(v, t))
                if score > best:
                    best, best_label = score, label
        return (best_label, best) if best >= self.min_sim else ("", best)

    def read(self, image: np.ndarray, invert: bool = False) -> tuple[str, float]:
        """返回 (文本, 平均置信度)。"""
        glyphs = self.segment(to_gray(image), invert=invert)
        if not glyphs:
            return "", 0.0
        out: list[str] = []
        scores: list[float] = []
        for g in glyphs:
            ch, sc = self.classify(g)
            if not ch:
                return "".join(out), 0.0
            out.append(ch)
            scores.append(sc)
        return "".join(out), float(np.mean(scores)) if scores else 0.0

    # -- 持久化 -----------------------------------------------------------
    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        arrays: dict[str, np.ndarray] = {}
        for label, vecs in self.templates.items():
            if vecs:
                arrays[label] = np.stack(vecs)
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
