"""打包成单文件 exe（预演用）。

    python scripts/build_exe.py            # 产物 dist/CovenantTracker.exe
    python scripts/build_exe.py --onedir   # 调试用，启动更快

exe 只带只读资源（网页 + 数据包）；运行期的 var/ 落在 exe 同目录。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    onefile = "--onedir" not in sys.argv
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--name", "CovenantTracker",
        "--paths", str(ROOT),
        "--add-data", f"{ROOT / 'covenant' / 'ui' / 'web'};covenant/ui/web",
        "--add-data", f"{ROOT / 'packs'};packs",
        "--hidden-import", "covenant.ui.server",
        "--hidden-import", "covenant.community.server",
        "--collect-submodules", "covenant",
    ]
    args.append("--onefile" if onefile else "--onedir")
    if not onefile:
        args.remove("--onefile")
    args.append(str(ROOT / "scripts" / "exe_entry.py"))

    print(" ".join(args))
    proc = subprocess.run(args, cwd=ROOT)
    if proc.returncode != 0:
        return proc.returncode
    exe = ROOT / "dist" / ("CovenantTracker.exe" if onefile else "CovenantTracker" / "CovenantTracker.exe")
    print(f"\n产物：{exe}")
    return 0 if exe.exists() else 1


if __name__ == "__main__":
    raise SystemExit(main())
