"""把当前校准（布局 + 数字/干员/结算模板 + 阈值）打包成 zip，方便换机器或分享。

用法：
    py -3 scripts/export_templates.py
    py -3 scripts/export_templates.py --var var --out dist

产物：dist/covenant-templates-YYYYMMDD-HHMM.zip
导入：解压到 var/ 即可（同名文件覆盖）。
"""

from __future__ import annotations

import argparse
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("layout.json", "ocr_digits.npz", "icons.npz", "results.npz", "vision.json")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--var", default="var")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--name", help="自定义文件名（不含 .zip）")
    args = ap.parse_args()

    var = (ROOT / args.var).resolve()
    out_dir = (ROOT / args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    files = [var / name for name in NAMES if (var / name).exists()]
    if not files:
        print(f"{var} 里没有可导出的校准文件（先跑 /teach 或 install_private_templates.py）")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M")
    name = args.name or f"covenant-templates-{stamp}"
    target = out_dir / f"{name}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, arcname=path.name)
        zf.writestr(
            "README.txt",
            "解压到记牌器的 var/ 目录即可。\n"
            "布局里的比例坐标按你的窗口尺寸标定，换分辨率需要重新框。\n",
        )
    print(f"已导出 {len(files)} 个文件 → {target}")
    for path in files:
        print(f"  {path.name}  {path.stat().st_size} B")
    return 0


if __name__ == "__main__":
    sys.exit(main())
