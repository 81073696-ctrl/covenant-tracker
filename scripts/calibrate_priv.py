"""用私服截图 + DOM 标注校准 HUD 数字识别（v1.0 可行性验证）。

用法：python scripts/calibrate_priv.py var/fixtures/priv
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from covenant.vision.ocr import DigitReader  # noqa: E402

FIELDS = ("round", "hp", "gold", "level")


def load_label(path: Path) -> dict | None:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    elif raw.count(b"\x00") > len(raw) // 4:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    idx = text.find("{")
    if idx < 0:
        return None
    try:
        return json.loads(text[idx:])
    except json.JSONDecodeError:
        return None


def crop(image: Image.Image, rect: list[int], pad: int = 2) -> np.ndarray:
    x, y, w, h = rect
    box = (max(0, x - pad), max(0, y - pad), min(image.width, x + w + pad), min(image.height, y + h + pad))
    return np.asarray(image.crop(box).convert("RGB"))


def main(folder: str) -> int:
    base = Path(folder)
    frames: list[tuple[Path, dict]] = []
    for png in sorted(base.glob("priv_f*.png")):
        label = load_label(png.with_suffix(".txt"))
        if label:
            frames.append((png, label))
    if not frames:
        print("没有可用的帧（需要 priv_f*.png 与同名 .txt）")
        return 1

    print(f"帧数：{len(frames)}")
    images = {p: Image.open(p) for p, _ in frames}
    for p, lab in frames:
        print(f"  {p.name}: round={lab['round']} hp={lab['hp']} gold={lab['gold']} level={lab['level']}")

    reader = DigitReader(min_sim=0.45)
    for _, lab in frames:
        for field in FIELDS:
            rect = lab["rects"].get(field)
            if rect:
                reader.learn(crop(images[_], rect), lab[field])

    print(f"学到字形：{sorted(reader.templates)}（共 {sum(len(v) for v in reader.templates.values())} 个样本）")

    ok = total = 0
    for p, lab in frames:
        img = images[p]
        row = []
        for field in FIELDS:
            rect = lab["rects"].get(field)
            if not rect:
                continue
            text, conf = reader.read(crop(img, rect))
            total += 1
            hit = text == lab[field]
            ok += 1 if hit else 0
            row.append(f"{field}:{text or '?'}({'ok' if hit else 'x'} {conf:.2f})")
        print(f"  {p.name}: " + "  ".join(row))

    print(f"整串准确率：{ok}/{total} = {ok / total:.0%}")
    out = ROOT / "var" / "ocr_priv.npz"
    reader.save(out)
    print(f"模板已保存：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "var/fixtures/priv"))
