"""命令行入口：python -m screenlens [serve|once] ..."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

from .server import LensApp, serve


def _attach_source(app: LensApp, args: argparse.Namespace) -> None:
    if getattr(args, "window", None):
        app.use_window(args.window)
    elif getattr(args, "samples", None):
        app.use_samples(args.samples)


def cmd_serve(args: argparse.Namespace) -> int:
    app = LensApp(var_dir=args.var, layout=args.layout)
    _attach_source(app, args)
    url = serve(app, args.host, args.port, block=False)
    if args.open:
        webbrowser.open(url)
    print(f"Screen Lens → {url}")
    print("  接入窗口：页面右上「选择窗口」；或重启时 --window 标题 / --samples 目录")
    try:
        while True:
            import time

            time.sleep(1)
    except KeyboardInterrupt:
        print("\n结束。")
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    from .vision.inspect import inspect_frame

    app = LensApp(var_dir=args.var, layout=args.layout)
    _attach_source(app, args)
    frame = app.grab()
    report = inspect_frame(app.pipe, frame)
    print(f"frame {report['width']}x{report['height']}  cost {report.get('elapsed_ms')} ms")
    for r in report["regions"]:
        extra = r.get("reason") or r.get("text") or ""
        print(f"  {r['name']:<10} {r['kind']:<7} {r['status']:<5} {extra}")
    for s in report.get("suggestions") or []:
        print("  ·", s)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="screenlens", description="Screen Lens：截图识别诊断台")
    sub = p.add_subparsers(dest="cmd")

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--window", help="按标题接一个可见窗口（模糊匹配）")
        sp.add_argument("--samples", help="回放一个截图目录（png/jpg）")
        sp.add_argument("--var", default="var", help="识别配置目录（默认 var/）")
        sp.add_argument("--layout", help="用指定 layout.json 覆盖配置目录里的布局")

    sp = sub.add_parser("serve", help="起网页诊断台（默认）")
    common(sp)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8765)
    sp.add_argument("--open", action="store_true", help="启动后打开浏览器")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("once", help="抓一帧，打印诊断摘要后退出")
    common(sp)
    sp.set_defaults(func=cmd_once)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["serve"]
    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 1
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"出错：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
