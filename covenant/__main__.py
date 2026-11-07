"""命令行入口：``python -m covenant <子命令>``。

    serve     启动本机面板（接窗口或纯手工）
    demo      用合成画面启动（不用开游戏）
    capture   列窗口 / 抓一张图存下来
    pack      从参考数据重新生成数据包
    selftest  跑一遍自检
    community 起一个社区战绩收集器（收别人上传的记录）
    share     把本机记录推到收集器
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .data.pack import Pack, load_latest
from .paths import default_packs_dir, is_frozen
from .state.engine import Engine
from .version import APP_NAME, __version__


def _load_pack(path: str | None) -> Pack:
    if path:
        return Pack.load(path)
    try:
        return load_latest(default_packs_dir())
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"没有可用的数据包（{exc}）。先跑：python -m covenant pack") from exc


def _make_engine(pack: Pack, var_dir: str) -> Engine:
    eng = Engine(pack)
    eng.attach_storage(Path(var_dir) / "engine.json")
    return eng


def cmd_serve(args: argparse.Namespace) -> int:
    from .ui.server import AppServer, find_free_port

    pack = _load_pack(args.pack)
    eng = _make_engine(pack, args.var)
    port = args.port or find_free_port(args.host)
    # DeepSeek 协作：--var 必须同时隔离引擎存档和识别配置，避免实测覆盖默认模板。
    app = AppServer(eng, args.host, port, var_dir=args.var)

    if args.frames:
        from .capture.folder import FolderSource

        app.frame_source = FolderSource(args.frames)
        print(f"采集源：回放目录 {args.frames}")
    elif args.window:
        from .capture.screen import WindowCapture

        cap = WindowCapture.find(args.window)
        if cap is None:
            print(f"⚠ 没找到标题包含「{args.window}」的窗口；可以稍后在 /teach 页面里连接。")
        else:
            app.frame_source = cap
            print(f"采集源：{cap.title()}")

    url = app.start()
    print(f"{APP_NAME} v{__version__} 已启动：{url}")
    print(f"  数据包：{pack.id}（{pack.name}，{pack.season}）")
    print(f"  教它认界面：{url}teach")
    print("  Ctrl+C 结束。")
    if app.settings.get("auto_capture") and app.frame_source is not None and not app.capture_service.on:
        try:
            app.start_capture(float(app.settings["capture_interval"]))
            print(f"  自动识别已开启（每 {app.settings['capture_interval']} 秒）。")
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ 自动识别没起来：{exc}")
    if not args.desktop and not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(url)), daemon=True).start()

    done = threading.Event()
    tray = None
    if getattr(args, "tray", False) or (is_frozen() and not getattr(args, "no_tray", False)):
        try:
            from .tray import TrayIcon

            tray = TrayIcon(
                f"{APP_NAME} v{__version__}",
                on_open=lambda: webbrowser.open(url),
                on_exit=done.set,
            )
            if tray.start():
                print("  托盘图标已就绪（双击打开面板，右键退出）。")
            else:
                tray = None
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠ 托盘不可用（{exc}），继续用控制台。")
            tray = None

    desktop_closed = False
    try:
        if args.desktop:
            try:
                from .ui.desktop import run_desktop

                run_desktop(url, args.var, done.set)
                desktop_closed = True
                print("\n桌面窗口已关闭。")
            except Exception as exc:  # noqa: BLE001 - 缺少 WebView 时保留浏览器入口
                print(
                    f"  ⚠ 桌面窗口不可用（{exc}），已回退浏览器。"
                    "安装：python -m pip install -r requirements-desktop.txt"
                )
                if not args.no_browser:
                    webbrowser.open(url)
        if not desktop_closed:
            while not done.wait(0.5):
                pass
            print("\n从托盘退出。")
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        if tray is not None:
            tray.stop()
        app.stop()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    from .capture.synthetic import SyntheticSource
    from .ui.server import AppServer, find_free_port

    pack = _load_pack(args.pack)
    eng = _make_engine(pack, args.var)
    app = AppServer(eng, args.host, args.port or find_free_port(args.host))
    app.frame_source = SyntheticSource()
    url = app.start()
    print(f"{APP_NAME} 演示模式：{url}（画面是合成的，不用开游戏）")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        app.stop()
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    from .capture.screen import WindowCapture, list_windows

    if args.list:
        for w in list_windows():
            if w["width"] < 200 or w["height"] < 120:
                continue
            print(f'{w["width"]:>5}×{w["height"]:<5}  {w["title"]}')
        return 0

    if not args.window:
        raise SystemExit("需要 --window 标题，或 --list 看看有哪些窗口")
    cap = WindowCapture.find(args.window)
    if cap is None:
        raise SystemExit(f"没找到标题包含「{args.window}」的窗口")
    frame = cap.grab()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(frame.png())
    print(f"已保存 {out}（{frame.width}×{frame.height}，{cap.last_method}）")
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    script = Path(__file__).resolve().parent.parent / "scripts" / "build_pack.py"
    if not script.exists():
        raise SystemExit(f"找不到生成脚本：{script}")
    cmd = [sys.executable, str(script), "--refs", args.refs, "--out", args.out]
    if args.id:
        cmd += ["--id", args.id]
    if args.season:
        cmd += ["--season", args.season]
    print("$", " ".join(cmd))
    return subprocess.call(cmd)


def cmd_selftest(_args: argparse.Namespace) -> int:
    from .calc.economy import economy_view
    from .calc.odds import shop_odds
    from .calc.pool import pool_snapshot
    from .core.types import Action

    pack = _load_pack(None)
    eng = Engine(pack)
    snap = eng.snapshot()
    need = {"pack", "mode", "state", "pool", "odds", "economy", "bonds"}
    missing = need - set(snap)
    if missing:
        raise SystemExit(f"快照缺字段：{missing}")

    mode = eng.mode
    print(f"数据包 {pack.id} · {pack.name}（{pack.season}）")
    print(f"模式 {mode.name}：{len(pack.available_operators(mode))} 名可用干员，{mode.rounds} 回合")
    pool = pool_snapshot(pack, eng.state)
    print(f"卡池 {pool.remaining}/{pool.total} 份 / 商店 {snap['odds']['slots']} 格")

    res = shop_odds(pack, eng.state)
    top = res.top(3)
    print("最可能刷到：" + "、".join(f"{o.name}({o.slot_prob:.1%})" for o in top))
    print(f"升下一级：{economy_view(pack, eng.state).level_up_now.cost_now} 金")

    # 买第一个格子的干员，确认状态机与卡池联动
    slot0 = eng.state.shop[0].op_id if eng.state.shop else None
    if slot0:
        r = eng.act(Action("buy", {"slot": 0}))
        print(f"买 {slot0}：{'成功' if r.get('ok') else r.get('error')}；资金 {eng.state.gold}")
    print("自检通过。")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from .vision.doctor import format_report, run_doctor

    pack = _load_pack(args.pack)
    report = run_doctor(pack, var_dir=args.var, frames_dir=args.frames)
    print(format_report(report))
    return 0 if report["ok"] else 2


def cmd_community(args: argparse.Namespace) -> int:
    from .community.server import CommunityServer

    srv = CommunityServer(
        host=args.host,
        port=args.port,
        data=Path(args.data),
        token=args.token,
        pack_dir=args.pack_dir,
    )
    if args.host not in ("127.0.0.1", "localhost"):
        print("⚠ 你正在监听非本机地址：请自己套 HTTPS 反代，并设置 --token。")
    url = srv.start()
    print(f"社区收集器已启动：{url}")
    print(f"  数据文件：{Path(args.data).resolve()}（已收 {srv.bank.count()} 条）")
    print(f"  上传接口：{url}api/submit" + ("（需要 X-Token）" if args.token else "（未设 token）"))
    print("  Ctrl+C 结束。")
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        srv.stop()
        print("\n已退出。")
    return 0


def cmd_share(args: argparse.Namespace) -> int:
    from .community.client import submit_file

    res = submit_file(args.url, args.file, token=args.token)
    if not res.get("ok"):
        raise SystemExit(f"上传失败：{res.get('error') or res}")
    print(
        f"已上传：新增 {res.get('added', 0)}，重复 {res.get('skipped', 0)}，"
        f"坏行 {res.get('bad', 0)}；服务端共 {res.get('total', 0)} 条。"
    )
    if res.get("note"):
        print(res["note"])
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="covenant", description=f"{APP_NAME} v{__version__}")
    sub = p.add_subparsers(dest="cmd")

    for name in ("serve", "demo"):
        sp = sub.add_parser(name)
        sp.add_argument("--pack", help="数据包目录（默认取 packs/ 下最新）")
        sp.add_argument("--host", default="127.0.0.1")
        sp.add_argument("--port", type=int, default=0, help="0 = 自动挑空闲端口")
        sp.add_argument("--var", default="var", help="存档/布局目录")
        sp.add_argument("--no-browser", action="store_true")
        if name == "serve":
            sp.add_argument("--window", help="窗口标题关键字，如 Arknights")
            sp.add_argument("--frames", help="回放一个截图目录（调试识别用）")
            sp.add_argument(
                "--tray",
                action="store_true",
                help="显示托盘图标（打包成 exe 后默认开启）",
            )
            sp.add_argument("--no-tray", action="store_true", help="不显示托盘图标")
            sp.add_argument("--desktop", action="store_true", help="用独立桌面窗口打开（需要 pywebview）")

    cap = sub.add_parser("capture")
    cap.add_argument("--window")
    cap.add_argument("--out", default="var/shot.png")
    cap.add_argument("--list", action="store_true")

    pk = sub.add_parser("pack")
    pk.add_argument("--refs", default="_refs/stronghold")
    pk.add_argument("--out", default="packs/act2autochess")
    pk.add_argument("--id")
    pk.add_argument("--season")

    sub.add_parser("selftest")

    doc = sub.add_parser("doctor", help="上线体检：数据包/布局/模板/截图回放")
    doc.add_argument("--pack", help="数据包目录（默认取 packs/ 下最新）")
    doc.add_argument("--var", default="var", help="布局与模板目录")
    doc.add_argument("--frames", help="可选：回放一个截图目录，统计识别覆盖")

    comm = sub.add_parser("community", help="起一个社区战绩收集器")
    comm.add_argument("--host", default="127.0.0.1")
    comm.add_argument("--port", type=int, default=8900)
    comm.add_argument("--data", default="var/community.jsonl")
    comm.add_argument("--token", help="只保护上传接口；公开部署时建议设置")
    comm.add_argument("--pack-dir", default="packs", help="数据包目录（用于把 id 翻成名字）")

    share = sub.add_parser("share", help="把本机记录推到收集器")
    share.add_argument("--url", required=True, help="收集器地址，如 http://127.0.0.1:8900")
    share.add_argument("--file", default="var/matches.jsonl")
    share.add_argument("--token")
    return p


def _ensure_utf8_console() -> None:
    """打包成 exe 后控制台默认是 936 代码页，中文会花；切成 UTF-8。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:  # noqa: BLE001
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_console()
    args_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    if not args_list:
        args_list = ["serve"]
    args = parser.parse_args(args_list)
    handlers = {
        "serve": cmd_serve,
        "demo": cmd_demo,
        "capture": cmd_capture,
        "pack": cmd_pack,
        "selftest": cmd_selftest,
        "doctor": cmd_doctor,
        "community": cmd_community,
        "share": cmd_share,
    }
    return handlers[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
