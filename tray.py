"""托盘图标（纯 Win32，不加依赖）。

用法::

    tray = TrayIcon("卫戍协议 · 记牌器", on_open=lambda: webbrowser.open(url))
    tray.start()
    ...
    tray.stop()

菜单只有「打开面板」「退出」。双击图标也会打开面板。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import threading
from typing import Callable

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
NIM_ADD = 0
NIM_DELETE = 2
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
IDI_APPLICATION = 32512
TPM_LEFTBUTTON = 0x0000
TPM_RIGHTALIGN = 0x0008
TPM_BOTTOMALIGN = 0x0020
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wt.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE),
        ("hIcon", wt.HICON),
        ("hCursor", wt.HANDLE),
        ("hbrBackground", wt.HBRUSH),
        ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uTimeoutOrVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
    ]


def _bind_win32() -> None:
    """ctypes 默认按 c_int 处理参数，64 位下句柄会被截断，必须显式声明。"""
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wt.ATOM
    user32.CreateWindowExW.argtypes = [
        wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID,
    ]
    user32.CreateWindowExW.restype = wt.HWND
    user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.DefWindowProcW.restype = ctypes.c_ssize_t
    user32.DestroyWindow.argtypes = [wt.HWND]
    user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wt.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    user32.LoadIconW.argtypes = [wt.HINSTANCE, wt.LPCWSTR]
    user32.LoadIconW.restype = wt.HICON
    user32.CreatePopupMenu.restype = wt.HMENU
    user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
    user32.TrackPopupMenu.argtypes = [
        wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.LPVOID,
    ]
    user32.DestroyMenu.argtypes = [wt.HMENU]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
    user32.SetForegroundWindow.argtypes = [wt.HWND]
    shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
    kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wt.HINSTANCE


_bind_win32()


class TrayIcon:
    def __init__(
        self,
        title: str,
        on_open: Callable[[], None] | None = None,
        on_exit: Callable[[], None] | None = None,
    ):
        self.title = title[:120]
        self.on_open = on_open
        self.on_exit = on_exit
        self._thread: threading.Thread | None = None
        self._hwnd: int | None = None
        self._ready = threading.Event()
        self._wndproc = None  # 保持引用，避免被 GC

    # ---- 外部接口 ----
    def start(self) -> bool:
        self._thread = threading.Thread(target=self._run, name="covenant-tray", daemon=True)
        self._thread.start()
        self._ready.wait(1.5)
        return self._hwnd is not None

    def stop(self) -> None:
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)

    # ---- 内部 ----
    def _run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = "CovenantTrayWnd"

        @WNDPROC
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAY:
                if lparam == WM_LBUTTONDBLCLK and self.on_open:
                    try:
                        self.on_open()
                    except Exception:  # noqa: BLE001
                        pass
                elif lparam == WM_RBUTTONUP:
                    self._popup(hwnd)
                return 0
            if msg == WM_COMMAND:
                cmd = wparam & 0xFFFF
                if cmd == 1 and self.on_open:
                    try:
                        self.on_open()
                    except Exception:  # noqa: BLE001
                        pass
                elif cmd == 2:
                    if self.on_exit:
                        try:
                            self.on_exit()
                        except Exception:  # noqa: BLE001
                            pass
                    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                return 0
            if msg == WM_CLOSE:
                self._remove_icon(hwnd)
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc = wndproc
        wc = WNDCLASSW()
        wc.lpfnWndProc = wndproc
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        user32.RegisterClassW(ctypes.byref(wc))

        hwnd = user32.CreateWindowExW(0, cls_name, self.title, 0, 0, 0, 0, 0, None, None, hinst, None)
        if not hwnd:
            self._ready.set()
            return
        self._hwnd = hwnd

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = user32.LoadIconW(None, ctypes.cast(IDI_APPLICATION, wt.LPCWSTR))
        nid.szTip = self.title
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        self._ready.set()

        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        self._hwnd = None

    def _popup(self, hwnd: int) -> None:
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, 1, "打开面板")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, 2, "退出")
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(hwnd)
        user32.TrackPopupMenu(
            menu,
            TPM_LEFTBUTTON | TPM_RIGHTALIGN | TPM_BOTTOMALIGN,
            pt.x,
            pt.y,
            0,
            hwnd,
            None,
        )
        user32.DestroyMenu(menu)

    def _remove_icon(self, hwnd: int) -> None:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = 1
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
