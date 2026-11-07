"""私服商店卡识别：用 DOM 标注自动学干员模板，按场次留出评测。

模板从**布局切格**里取（和线上 `VisionPipeline.read` 完全同一套几何），
所以评测分数就是线上真实分数；官方客户端用 /teach 重新教即可。

用法：python scripts/train_priv.py var/fixtures/priv [data/layouts/private-1600x900.json]
输出：var/icons_priv.npz + 控制台报告（模板 key 是数据包里的 op_id）
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from covenant.data.pack import load_latest  # noqa: E402
from covenant.vision.match import IconMatcher  # noqa: E402
from covenant.vision.roi import Layout  # noqa: E402

FRAME_RE = re.compile(r"m(\d+)_[A-Za-z0-9]+$")


def load_frames(base: Path) -> list[tuple[str, Path, dict]]:
    out = []
    for js in sorted(base.glob("m*_*.json")):
        m = FRAME_RE.search(js.stem)
        if not m:
            continue
        png = js.with_suffix(".png")
        if not png.exists():
            continue
        try:
            data = json.loads(js.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        out.append((f"m{m.group(1)}", png, data))
    return out


def parse_card(card: dict) -> tuple[int, str] | None:
    """返回 (价格, 干员名)；道具卡（3 段）返回 None。"""
    parts = [p.strip() for p in str(card.get("text", "")).split("/")]
    if len(parts) < 4:
        return None
    price, name = parts[0], parts[1]
    if not price.isdigit() or not name:
        return None
    return int(price), name


def resolve(name: str, price: int, index: dict[str, list]) -> str | None:
    cands = [c for c in index.get(name, []) if c[2] == price] or index.get(name, [])
    if not cands:
        return None
    cands.sort(key=lambda c: (c[1], c[0]))  # 非隐藏优先
    return cands[0][0]


def slots_for(layout: Layout, frame_w: int, frame_h: int, name: str = "shop") -> list[tuple[int, int, int, int]]:
    region = layout.region(name)
    if region is None:
        return []
    n = int(region.params.get("slots") or 1)
    gap = float(region.params.get("gap") or 0.008)
    art = float(region.params.get("art") or 1.0)
    x, y, w, h = region.rect.pixels(frame_w, frame_h)
    if n <= 1:
        return [(x, y, w, int(h * art))]
    step_gap = int(round(w * gap))
    cw = max(1, (w - step_gap * (n - 1)) // n)
    return [(x + i * (cw + step_gap), y, cw, int(h * art)) for i in range(n)]


def nearest_slot(slots: list[tuple[int, int, int, int]], card: dict) -> int | None:
    cx = float(card["x"]) + float(card["w"]) / 2
    cy = float(card["y"]) + float(card["h"]) / 2
    best, bestd = None, 1e9
    for i, (x, y, w, h) in enumerate(slots):
        d = abs(x + w / 2 - cx) + abs(y + h / 2 - cy)
        if d < bestd:
            best, bestd = i, d
    return best if bestd < 80 else None


def main(folder: str, layout_path: str = "data/layouts/private-1600x900.json") -> int:
    base = Path(folder)
    frames = load_frames(base)
    if not frames:
        print("没有帧")
        return 1
    matches = sorted({m for m, _, _ in frames})
    print(f"帧：{len(frames)}，场次：{matches}")

    layout = Layout.load(str(ROOT / layout_path))
    pack = load_latest(str(ROOT / "packs"))
    index: dict[str, list] = defaultdict(list)
    for oid, op in pack.operators.items():
        index[op.name].append((oid, op.hidden, op.price))

    items: list[tuple[str, Path, int, str]] = []
    for match, png, data in frames:
        img = Image.open(png)
        slots = slots_for(layout, img.width, img.height)
        if not slots:
            print("布局里没有 shop 区域")
            return 1
        for card in data.get("cards", []):
            parsed = parse_card(card)
            if not parsed:
                continue
            price, name = parsed
            oid = resolve(name, price, index)
            slot = nearest_slot(slots, card)
            if oid and slot is not None:
                items.append((match, png, slot, oid))
    print(f"干员样本：{len(items)} 张，{len({o for *_x, o in items})} 个 op_id")

    holdout = matches[-1] if len(matches) > 1 else matches[0]
    matcher = IconMatcher(min_sim=0.0, search=0.0)
    trained = 0
    for match in matches:
        if match == holdout:
            continue
        for _m, png, slot, oid in [it for it in items if it[0] == match]:
            img = Image.open(png)
            x, y, w, h = slots_for(layout, img.width, img.height)[slot]
            if matcher.add(oid, np.asarray(img.crop((x, y, x + w, y + h)).convert("RGB"))):
                trained += 1
    print(f"训练：{trained} 张，{len(matcher.keys())} 个模板；留出场次 {holdout}")

    ok = total = 0
    misses = []
    cache: dict[Path, Image.Image] = {}
    for match, png, slot, oid in [it for it in items if it[0] == holdout]:
        img = cache.setdefault(png, Image.open(png))
        x, y, w, h = slots_for(layout, img.width, img.height)[slot]
        key, score = matcher.classify(np.asarray(img.crop((x, y, x + w, y + h)).convert("RGB")))
        total += 1
        if key == oid:
            ok += 1
        else:
            misses.append((oid, key, score))
    print(f"留出 top-1：{ok}/{total} = {ok / total:.0%}" if total else "留出集为空")
    for oid, key, score in misses:
        print(f"  miss: {oid} -> {key or '?'} ({score:.2f})")

    out = ROOT / "var" / "icons_priv.npz"
    matcher.save(out)
    print(f"模板已保存：{out}")
    return 0 if total and ok == total else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "var/fixtures/priv"))
