"""把私服截图喂进 VisionPipeline → Engine，验证「截图 → 状态」链路。

用法：python scripts/replay_priv.py var/fixtures/priv
帧格式：priv_f*.png + .txt（早期手工帧）或 m*_*.png + .json（_collect.mjs）
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from covenant.capture.base import Frame  # noqa: E402
from covenant.data.pack import load_latest  # noqa: E402
from covenant.state.engine import Engine  # noqa: E402
from covenant.vision.match import IconMatcher  # noqa: E402
from covenant.vision.ocr import DigitReader  # noqa: E402
from covenant.vision.pipeline import VisionPipeline  # noqa: E402
from covenant.vision.roi import Layout  # noqa: E402


def parse_label(path: Path) -> dict | None:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    elif raw.count(b"\x00") > len(raw) // 4:
        text = raw.decode("utf-16-le", errors="replace")
    else:
        text = raw.decode("utf-8", errors="replace")
    i = text.find("{")
    try:
        return json.loads(text[i:]) if i >= 0 else None
    except json.JSONDecodeError:
        return None


def label_for(png: Path) -> dict | None:
    for suffix in (".txt", ".json"):
        p = png.with_suffix(suffix)
        if p.exists():
            return parse_label(p)
    return None


def main(folder: str) -> int:
    base = Path(folder)
    pack = load_latest(str(ROOT / "packs"))
    engine = Engine(pack)
    layout = Layout.load(str(ROOT / "data" / "layouts" / "private-1600x900.json"))
    digits = DigitReader()
    digits.load(ROOT / "var" / "ocr_priv.npz")
    icons = IconMatcher(min_sim=0.90)
    icons.load(ROOT / "var" / "icons_priv.npz")
    pipeline = VisionPipeline(layout, digits=digits, icons=icons, var_dir=str(ROOT / "var"))

    names = {oid: op.name for oid, op in pack.operators.items()}
    ok = total = shop_ok = shop_total = 0
    for png in sorted(base.glob("*.png")):
        label = label_for(png)
        if not label:
            continue
        image = np.asarray(Image.open(png).convert("RGB")).copy()
        for obs in pipeline.read(Frame(image)):
            engine.observe(obs)
        state = engine.state
        got = {"round": state.round_no, "hp": state.hp, "gold": state.gold, "level": state.shop_level}
        row = []
        for field, value in got.items():
            want = int(label[field])
            total += 1
            hit = value == want
            ok += 1 if hit else 0
            row.append(f"{field}:{value}{'' if hit else f'≠{want}'}")

        want_shop = []
        for card in label.get("cards", []):
            parts = [p.strip() for p in str(card.get("text", "")).split("/")]
            if len(parts) >= 4 and parts[1]:
                want_shop.append(parts[1])
        got_shop = [names.get(o.op_id, o.op_id) for o in state.shop if o.op_id]
        hit_shop = [w for w in want_shop if w in got_shop]
        shop_total += len(want_shop)
        shop_ok += len(hit_shop)
        row.append(f"shop:{len(hit_shop)}/{len(want_shop)}")
        print(f"{png.name}: " + "  ".join(row))

    print(f"截图→状态 一致率：{ok}/{total} = {ok / total:.0%}" if total else "没有可用帧")
    print(f"商店卡命中：{shop_ok}/{shop_total}")
    return 0 if total and ok == total else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "var/fixtures/priv"))
