"""给演示截图学一遍模板：数字 0-9、四张商店卡。

用法：python tools/bootstrap_demo.py [--var var] [--layout layouts/mock.json]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from screenlens.server import LensApp

CARDS = ["card_a", "card_b", "card_c", "card_d"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--var", default="var")
    ap.add_argument("--layout", default="layouts/mock.json")
    ap.add_argument("--samples", default="samples")
    args = ap.parse_args()

    app = LensApp(var_dir=args.var, layout=Path(args.layout))
    app.use_samples(args.samples, loop=True)

    frame = app.grab()
    n = app.pipe.learn_digit(frame, "digits_demo", "0123456789")
    print(f"digits: {n} glyphs")
    for region, text in (("round", "1"), ("level", "1"), ("gold", "4"), ("hp", "28")):
        k = app.pipe.learn_digit(frame, region, text)
        print(f"  {region}: {k}")
    for i, name in enumerate(CARDS):
        ok = app.pipe.learn_slot(frame, "shop", i, name)
        print(f"  shop[{i}] -> {name}: {ok}")

    app.pipe.save()
    print("saved to", Path(args.var).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
