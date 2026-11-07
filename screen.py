"""Windows 窗口截图：纯 ctypes，不依赖 mss/pywin32。

两条路径
--------
1. ``PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)`` —— 对 D3D/Unity 窗口通常
   有效，即使窗口被别的窗口遮挡也能拿到内容；
2. 失败则退回 ``BitBlt``（只能拿到屏幕上可见的部分，被遮挡会变黑）。

实践建议：把游戏切到「无边框窗口」或让窗口保持在最前，两种路径都更稳。

图像坐标一律相对**客户区**（不含标题栏/边框），这样 ROI 布局不会因为窗口
移动而失效。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any

import numpy as np

from .base import Frame, FrameSource

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020

# 高 DPI 下不做这一步，客户区尺寸和实际像素会对不上
try:  # pragma: no cover - 仅 Windows
    user32.SetProcessDPIAware()
except Exception:  # noqa: BLE001
    pass


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC,
    ctypes.POINTER(_BITMAPINFO),
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p),
    wintypes.HANDLE,
    wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.BitBlt.argtypes = [
    wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
]
gdi32.BitBlt.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def _window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def list_windows(visible_only: bool = True) -> list[dict[str, Any]]:
    """列出当前所有带标题的顶层窗口。"""
    out: list[dict[str, Any]] = []

    def _cb(hwnd, _lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        title = _window_title(hwnd)
        if not title.strip():
            return True
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        out.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "pid": _pid_of(hwnd),
                "width": int(rect.right - rect.left),
                "height": int(rect.bottom - rect.top),
            }
        )
        return True

    user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    out.sort(key=lambda w: w["width"] * w["height"], reverse=True)
    return out


class WindowCapture(FrameSource):
    """按窗口句柄截图。"""

    def __init__(self, hwnd: int, title: str = ""):
        self.hwnd = int(hwnd)
        self._title = title or _window_title(self.hwnd)
        self.last_method = ""

    # -- 构造 -------------------------------------------------------------
    @classmethod
    def find(cls, keyword: str, *, min_area: int = 200 * 200) -> WindowCapture | None:
        """按标题模糊匹配窗口；多个命中时取面积最大的。"""
        kw = keyword.strip().lower()
        if not kw:
            return None
        hits = [w for w in list_windows() if kw in w["title"].lower()]
        hits = [w for w in hits if w["width"] * w["height"] >= min_area] or hits
        if not hits:
            return None
        best = max(hits, key=lambda w: w["width"] * w["height"])
        return cls(best["hwnd"], best["title"])

    def title(self) -> str:
        return self._title or _window_title(self.hwnd)

    def size(self) -> tuple[int, int]:
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        return int(rect.right - rect.left), int(rect.bottom - rect.top)

    # -- 采集 -------------------------------------------------------------
    def grab(self) -> Frame:
        w, h = self.size()
        if w <= 0 or h <= 0:
            raise RuntimeError(f"窗口「{self.title()}」没有客户区（可能已最小化）")

        hdc = user32.GetDC(self.hwnd)
        if not hdc:
            raise RuntimeError("GetDC 失败")
        mem = gdi32.CreateCompatibleDC(hdc)
        if not mem:
            user32.ReleaseDC(self.hwnd, hdc)
            raise RuntimeError("CreateCompatibleDC 失败")

        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # 负数 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB

        bits = ctypes.c_void_p()
        bmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
        if not bmp or not bits:
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(self.hwnd, hdc)
            raise RuntimeError("CreateDIBSection 失败")

        old = gdi32.SelectObject(mem, bmp)
        try:
            ok = bool(user32.PrintWindow(self.hwnd, mem, PW_RENDERFULLCONTENT))
            method = "PrintWindow"
            if not ok:
                ok = bool(gdi32.BitBlt(mem, 0, 0, w, h, hdc, 0, 0, SRCCOPY))
                method = "BitBlt"
            if not ok:
                raise RuntimeError("PrintWindow 与 BitBlt 都失败（窗口可能被保护）")
            raw = ctypes.string_at(bits, w * h * 4)
            arr = np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 4)
            rgb = np.ascontiguousarray(arr[:, :, :3][:, :, ::-1]).copy()  # BGRA → RGB（copy 保证可写）
        finally:
            if old:
                gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(self.hwnd, hdc)

        self.last_method = method
        if rgb.max() == 0:
            raise RuntimeError("抓到全黑帧：请把游戏切到无边框窗口，或让窗口保持可见")
        return Frame(image=rgb, ts=time.time(), meta={"window": self.title(), "method": method})
