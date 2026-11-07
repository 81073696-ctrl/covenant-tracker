"""生成演示用的合成截图 + 对应布局，避免把游戏画面放进仓库。

用法：python tools/make_samples.py
产物：samples/mock_1.png、samples/mock_2.png、layouts/mock.json
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1600, 900
ROOT = Path(__file__).resolve().parents[1]

BG = (16, 20, 26)
PANEL = (24, 29, 38)
LINE = (58, 64, 78)
TEXT = (232, 228, 218)
DIM = (150, 158, 172)
ACCENT = (217, 179, 106)
CARDS = [(196, 96, 84), (86, 150, 196), (120, 176, 120), (176, 132, 196)]


def font(size: int) -> ImageFont.ImageFont:
    for name in ("msyh.ttc", "simhei.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def frame(round_no: int, gold: int, level: int, hp: int, cards: list[int], labels: list[str]) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 顶栏
    d.rectangle((0, 0, W, 86), fill=PANEL, outline=LINE)
    f = font(40)
    fl = font(20)
    d.text((560, 16), "回合", font=fl, fill=DIM)
    d.text((640, 20), str(round_no), font=f, fill=TEXT)
    d.text((280, 16), "等级", font=fl, fill=DIM)
    d.text((360, 20), str(level), font=f, fill=ACCENT)
    d.text((1040, 16), "资金", font=fl, fill=DIM)
    d.text((1120, 20), str(gold), font=f, fill=ACCENT)
    d.text((1360, 16), "生命", font=fl, fill=DIM)
    d.text((1440, 20), str(hp), font=f, fill=TEXT)

    # 商店四张卡
    x0, y0, cw, ch = 540, 680, 220, 190
    for i in range(4):
        x = x0 + i * (cw + 20)
        color = CARDS[cards[i] % len(CARDS)]
        d.rounded_rectangle((x, y0, x + cw, y0 + ch), radius=8, fill=PANEL, outline=color, width=3)
        d.rectangle((x + 12, y0 + 12, x + cw - 12, y0 + 96), fill=color)
        d.text((x + 20, y0 + 110), labels[i], font=font(26), fill=TEXT)
        d.text((x + 20, y0 + 148), f"${2 + i % 3}", font=font(24), fill=ACCENT)

    # 整备区八个位
    for i in range(8):
        bx = 40 + i * 190
        d.rounded_rectangle((bx, 520, bx + 170, 640), radius=6, outline=LINE, width=2)
        d.text((bx + 70, 570), "+", font=font(28), fill=DIM)

    # 底部提示
    d.text((40, 790), "0123456789", font=font(44), fill=TEXT)
    d.text((40, 860), "SCREEN LENS · 合成演示画面（无游戏素材）", font=font(18), fill=DIM)
    return img


LAYOUT = {
    "source": "mock",
    "regions": {
        "round": {"rect": [0.398, 0.022, 0.045, 0.056], "kind": "digits", "target": "round"},
        "level": {"rect": [0.224, 0.022, 0.045, 0.056], "kind": "digits", "target": "level"},
        "gold": {"rect": [0.698, 0.022, 0.060, 0.056], "kind": "digits", "target": "gold"},
        "hp": {"rect": [0.898, 0.022, 0.060, 0.056], "kind": "digits", "target": "hp"},
        "shop": {
            "rect": [0.335, 0.748, 0.610, 0.218],
            "kind": "shop",
            "params": {"slots": 4, "art": 0.60},
        },
        "digits_demo": {"rect": [0.020, 0.870, 0.300, 0.062], "kind": "digits"},
        "bench": {
            "rect": [0.025, 0.575, 0.950, 0.140],
            "kind": "units",
            "params": {"slots": 8},
        },
    },
}


def main() -> None:
    (ROOT / "samples").mkdir(exist_ok=True)
    (ROOT / "layouts").mkdir(exist_ok=True)
    frame(1, 4, 1, 28, [0, 1, 2, 3], ["A", "B", "C", "D"]).save(ROOT / "samples" / "mock_1.png")
    frame(2, 7, 1, 26, [3, 2, 0, 1], ["D", "C", "A", "B"]).save(ROOT / "samples" / "mock_2.png")
    (ROOT / "layouts" / "mock.json").write_text(
        json.dumps(LAYOUT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("samples/mock_1.png samples/mock_2.png layouts/mock.json")


if __name__ == "__main__":
    main()
