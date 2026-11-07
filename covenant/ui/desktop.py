"""可选的 pywebview 桌面壳；普通浏览器模式不导入桌面依赖。"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable


class DesktopStore:
    """窗口偏好与对局存档分离，损坏时只重置窗口偏好。"""

    DEFAULTS = {
        "compact": False,
        "on_top": False,
        "width": 1280,
        "height": 840,
        "x": None,
        "y": None,
    }

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != set(self.DEFAULTS):
                raise ValueError("窗口配置字段不完整")
            for key in ("compact", "on_top"):
                if type(data[key]) is not bool:
                    raise ValueError(f"{key} 必须是布尔值")
            for key, low, high in (("width", 400, 3840), ("height", 500, 2160)):
                if type(data[key]) is not int or not low <= data[key] <= high:
                    raise ValueError(f"{key} 超出范围")
            for key in ("x", "y"):
                if data[key] is not None and (type(data[key]) is not int or not -32768 <= data[key] <= 32767):
                    raise ValueError(f"{key} 超出范围")
            return dict(data)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return dict(self.DEFAULTS)

    def save(self, state: dict) -> None:
        data = {key: state.get(key, default) for key, default in self.DEFAULTS.items()}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)


class DesktopController:
    """暴露给前端的窄接口；私有字段避免 pywebview 递归序列化原生对象。"""

    def __init__(self, window, store: DesktopStore, on_close: Callable[[], None]):
        self._window = window
        self._store = store
        self._on_close = on_close
        self._state = store.load()
        self._full_size = (
            (self._state["width"], self._state["height"])
            if not self._state["compact"]
            else (DesktopStore.DEFAULTS["width"], DesktopStore.DEFAULTS["height"])
        )
        self._closed = False

    def _remember_geometry(self) -> None:
        self._state.update(
            width=int(self._window.width),
            height=int(self._window.height),
            x=int(self._window.x),
            y=int(self._window.y),
        )

    def get_state(self) -> dict:
        self._remember_geometry()
        return dict(self._state)

    def _save(self) -> dict:
        self._remember_geometry()
        self._store.save(self._state)
        return dict(self._state)

    def remember_geometry(self) -> None:
        self._save()

    def set_compact(self, value: bool) -> dict:
        value = bool(value)
        if value == self._state["compact"]:
            return self.get_state()
        if value:
            self._full_size = (self._window.width, self._window.height)
            self._window.resize(430, 760)
            self._state.update(compact=True, on_top=True)
            self._window.on_top = True
        else:
            self._window.resize(*self._full_size)
            self._state["compact"] = False
        return self._save()

    def set_on_top(self, value: bool) -> dict:
        self._state["on_top"] = bool(value)
        self._window.on_top = self._state["on_top"]
        return self._save()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            try:
                self._save()
            except Exception:  # noqa: BLE001 - 原生窗口销毁后几何属性可能已不可用
                self._store.save(self._state)
        finally:
            self._on_close()


def _apply_icon_async(title: str, icon: Path) -> None:
    """给原生窗口换图标（任务栏 + 标题栏）。

    pywebview 在 Windows 上不直接暴露 HWND，这里用标题找回窗口再发 WM_SETICON；
    失败就算了，图标不该影响启动。
    """
    try:
        import ctypes
        import time
        from ctypes import wintypes

        if not icon.is_file():
            return
        user32 = ctypes.windll.user32
        user32.FindWindowW.restype = wintypes.HWND
        user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        time.sleep(0.8)  # 等 WebView 真正创建出顶层窗口
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return
        hicon = user32.LoadImageW(None, str(icon), 1, 0, 0, 0x10 | 0x40)  # IMAGE_ICON, FROMFILE|DEFAULTSIZE
        if hicon:
            user32.SendMessageW(hwnd, 0x80, 0, hicon)  # WM_SETICON, ICON_SMALL
            user32.SendMessageW(hwnd, 0x80, 1, hicon)  # WM_SETICON, ICON_BIG
    except Exception:  # noqa: BLE001 - 纯装饰，失败不影响主流程
        pass


def run_desktop(url: str, var_dir: str, on_close: Callable[[], None]) -> None:
    """创建原生 WebView 窗口；依赖只在本函数内加载。"""
    import webview

    from ..version import APP_NAME, __version__

    store = DesktopStore(Path(var_dir) / "desktop.json")
    state = store.load()
    options = {
        "width": state["width"],
        "height": state["height"],
        "min_size": (400, 500),
        "on_top": state["on_top"],
        "background_color": "#090c0f",
    }
    if state["x"] is not None and state["y"] is not None:
        options.update(x=state["x"], y=state["y"])

    title = f"{APP_NAME} v{__version__}"
    icon = Path(__file__).resolve().parents[2] / "assets" / "covenant-tracker.ico"
    controller = DesktopController(None, store, on_close)
    window = webview.create_window(title, url, js_api=controller, **options)
    controller._window = window

    # 多显示器坐标可能已失效；至少保留完整窗口在主屏幕可见范围内。
    if state["x"] is not None and state["y"] is not None and webview.screens:
        screen = webview.screens[0]
        x = min(max(state["x"], 0), max(0, screen.width - state["width"]))
        y = min(max(state["y"], 0), max(0, screen.height - state["height"]))
        if (x, y) != (state["x"], state["y"]):
            window.move(x, y)

    timer_lock = threading.Lock()
    pending_save: threading.Timer | None = None

    def save_now() -> None:
        nonlocal pending_save
        with timer_lock:
            pending_save = None
        if controller._closed:
            return
        try:
            controller.remember_geometry()
        except Exception as exc:  # noqa: BLE001 - 窗口偏好失败不能中断对局
            print(f"  ⚠ 窗口位置保存失败：{exc}")

    def schedule_save(*_args) -> None:
        nonlocal pending_save
        with timer_lock:
            if pending_save is not None:
                pending_save.cancel()
            pending_save = threading.Timer(0.35, save_now)
            pending_save.daemon = True
            pending_save.start()

    def closed(*_args) -> None:
        try:
            controller.close()
        except Exception as exc:  # noqa: BLE001 - 确保窗口关闭能结束服务
            print(f"  ⚠ 窗口状态保存失败：{exc}")
            on_close()

    window.events.closed += closed
    window.events.resized += schedule_save
    window.events.moved += schedule_save
    try:
        webview.start(lambda: _apply_icon_async(title, icon))
    finally:
        with timer_lock:
            if pending_save is not None:
                pending_save.cancel()
                pending_save = None
