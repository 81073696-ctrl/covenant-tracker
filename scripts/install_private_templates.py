"""把私服（或任何一次校准）的模板装进 var/，省得手动 copy。

用法：
    py -3 scripts/install_private_templates.py
    py -3 scripts/install_private_templates.py --layout data/layouts/private-1600x900.json \
        --digits var/ocr_priv.npz --icons var/icons_priv.npz

会覆盖 var/layout.json、var/ocr_digits.npz、var/icons.npz；
不存在的文件跳过并提示。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def copy_one(src: Path, dst: Path) -> bool:
    if not src.exists():
        print(f"跳过（不存在）：{src}")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    print(f"{src.name} → {dst}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--layout", default="data/layouts/private-1600x900.json")
    ap.add_argument("--digits", default="var/ocr_priv.npz")
    ap.add_argument("--icons", default="var/icons_priv.npz")
    ap.add_argument("--var", default="var")
    args = ap.parse_args()

    var = (ROOT / args.var).resolve()
    done = 0
    done += copy_one((ROOT / args.layout).resolve(), var / "layout.json")
    done += copy_one((ROOT / args.digits).resolve(), var / "ocr_digits.npz")
    done += copy_one((ROOT / args.icons).resolve(), var / "icons.npz")
    print(f"完成 {done} 个文件。重启服务后生效。")
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
