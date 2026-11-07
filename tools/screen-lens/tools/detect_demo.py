"""在真实截图上试 detect_runs：给一个大区域，看它能不能切出卡片。

用法：
    python tools/detect_demo.py 图片 [x0 y0 x1 y1]   # 比例坐标，默认底部一条
输出：
    var/detect_<name>.png  画了框的图
    终端打印每个框的位置
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screenlens.vision.detect import detect_runs  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    path = Path(sys.argv[1])
    img = Image.open(path).convert("RGB")
    W, H = img.size
    if len(sys.argv) >= 6:
        fx0, fy0, fx1, fy1 = [float(v) for v in sys.argv[2:6]]
    else:
        fx0, fy0, fx1, fy1 = 0.45, 0.55, 1.0, 0.80
    rect = (int(W * fx0), int(H * fy0), int(W * (fx1 - fx0)), int(H * (fy1 - fy0)))
    boxes = detect_runs(np.asarray(img), rect)
    print(f"image {W}x{H} region {rect} -> {len(boxes)} boxes")
    draw = ImageDraw.Draw(img)
    draw.rectangle([rect[0], rect[1], rect[0] + rect[2], rect[1] + rect[3]], outline=(255, 210, 0), width=3)
    for i, b in enumerate(boxes):
        draw.rectangle([b.x, b.y, b.x + b.w, b.y + b.h], outline=(90, 220, 160), width=3)
        draw.text((b.x + 4, b.y + 4), str(i), fill=(255, 255, 255))
        print(f"  #{i} x={b.x} y={b.y} w={b.w} h={b.h}")
    out = ROOT / "var" / f"detect_{path.stem}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
