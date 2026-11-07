"""一键自检：编译 → 单测 → 前端语法 → 上线体检。

用法：
    python scripts/check_all.py              # 全量（含 doctor 的模板检查）
    python scripts/check_all.py --frames var/demo_frames
    python scripts/check_all.py --quick      # 跳过 doctor 的截图回放

任何一步失败都会以非零退出码结束，方便在提交前 / 交给 Kimi review 前跑一遍。
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _utf8_console() -> None:
    """Windows 控制台默认 GBK，直接 print ✓/✗ 会 UnicodeEncodeError。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def run(title: str, cmd: list[str], *, cwd: Path = ROOT) -> tuple[bool, str]:
    print(f"\n== {title} ==")
    print("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-6:])
    if tail:
        print(tail)
    ok = proc.returncode == 0
    print(("  ✓ " if ok else "  ✗ ") + title + ("" if ok else f"（exit {proc.returncode}）"))
    return ok, out


def main() -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="Covenant Tracker self-check")
    parser.add_argument("--frames", default=None, help="doctor 回放用的截图目录")
    parser.add_argument("--quick", action="store_true", help="跳过 doctor")
    args = parser.parse_args()

    results: list[tuple[str, bool]] = []

    ok, _ = run("编译 covenant/scripts/tests", [sys.executable, "-m", "compileall", "-q", "covenant", "scripts", "tests"])
    results.append(("compileall", ok))

    ok, _ = run("单元测试", [sys.executable, "-m", "unittest", "discover", "-s", "tests"])
    results.append(("unittest", ok))

    # Windows 的 PATH 可能先返回无扩展名 shim；Python 应经 cmd 执行 node.cmd。
    node = shutil.which("node.cmd") or shutil.which("node")
    if node:
        prefix = ["cmd.exe", "/d", "/s", "/c", node] if node.lower().endswith((".cmd", ".bat")) else [node]
        for js in (
            "covenant/ui/web/app.js",
            "covenant/ui/web/desktop.js",
            "covenant/ui/web/teach.js",
            "covenant/ui/web/lens.js",
        ):
            ok, _ = run(f"node --check {js}", [*prefix, "--check", js])
            results.append((f"node {js.split('/')[-1]}", ok))
    else:
        print("\n== node ==")
        print("  跳过：没找到 node")

    if not args.quick:
        cmd = [sys.executable, "-m", "covenant", "doctor"]
        if args.frames:
            cmd += ["--frames", args.frames]
        ok, _ = run("上线体检", cmd)
        results.append(("doctor", ok))

    print("\n== 汇总 ==")
    for name, ok in results:
        print(f"  {'✓' if ok else '✗'} {name}")
    failed = [name for name, ok in results if not ok]
    if failed:
        print(f"\n失败：{', '.join(failed)}")
        return 1
    print("\n全部通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
