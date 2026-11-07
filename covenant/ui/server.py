"""本机 Web 面板：组装服务 + 启停。

HTTP 细节（处理器、静态文件、快照缓存、端口探测）在 ``ui/http.py``；
路由表在 ``ui/routes.py``；这里只负责把 engine / vision / 服务接起来。

只监听 127.0.0.1，不对外网开放；所有数据都在本机内存 + ``var/`` 目录里。
"""

from __future__ import annotations

import os
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..services.capture import CaptureService, same_as_state
from ..services.community import CommunityService
from ..services.settings import DEFAULTS, SettingsService
from ..state.engine import Engine
from ..version import APP_NAME, __version__
from ..vision.pipeline import VisionPipeline
from ..vision.profiles import ProfileError, ProfileManager
from .http import SnapshotCache, find_free_port, make_handler
from .routes import set_frame_source

# 兼容旧调用：实现已挪到 services/capture.py / ui/http.py
_same_as_state = same_as_state


class AppServer:
    """持有 engine + http server 的小外壳。"""

    DEFAULT_SETTINGS = DEFAULTS  # 兼容旧引用

    def __init__(
        self,
        engine: Engine,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        var_dir: Path | str | None = None,
    ):
        self.engine = engine
        self.host = host
        self.port = port
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.var_dir = Path(var_dir) if var_dir is not None else Path("var")
        #: 多套识别配置（不同皮肤/分辨率各一份 layout + 模板）
        self.profiles = ProfileManager(self.var_dir)
        self.vision = VisionPipeline.load(self.profiles.active_dir())
        self.capture_service = CaptureService(engine, self.vision)  # 采集 + 自动识别
        self._snapshot_cache = SnapshotCache()
        # 非本机监听时由 serve() 生成，前端用 ?token= 带回来
        self.token: str | None = None

        # 面板设置：只放「下次启动仍然生效」的少量项
        self.settings_service = SettingsService(self.var_dir / "settings.json")
        self.community_service = CommunityService(engine, self.var_dir)

    @property
    def frame_source(self):
        return self.capture_service.source

    @frame_source.setter
    def frame_source(self, value):
        self.capture_service.set_source(value)
        if value is not None and self.settings.get("auto_capture"):
            self.start_capture(float(self.settings["capture_interval"]))

    @property
    def last_frame(self):
        return self.capture_service.last_frame

    @last_frame.setter
    def last_frame(self, value):
        self.capture_service.last_frame = value

    # ---- 设置 ----
    @property
    def settings(self) -> dict:
        return self.settings_service.data

    def settings_view(self) -> dict:
        return {
            "settings": dict(self.settings_service.data),
            "capture": self.capture_status(),
            "profile": {
                "id": self.profiles.active_id(),
                "name": self.profiles.meta(self.profiles.active_id()).get("name"),
            },
            "paths": {
                "var": str(self.var_dir.resolve()),
                "pack": str(getattr(self.engine.pack, "path", "")),
            },
            "frame_source": self.frame_source.title() if self.frame_source else None,
            "version": __version__,
        }

    def save_settings(self, data: dict) -> dict:
        saved = self.settings_service.save(data)
        self.capture_service.interval = saved["capture_interval"]
        if saved["auto_capture"] and not self.capture_service.on and self.frame_source is not None:
            self.start_capture(saved["capture_interval"])
        if not saved["auto_capture"] and self.capture_service.on:
            self.stop_capture()
        return self.settings_view()

    def snapshot_payload(self) -> tuple[bytes, str]:
        """返回 (JSON bytes, ETag)。状态没变就直接复用上次的序列化结果。"""
        return self._snapshot_cache.payload(self.engine)

    # -- 启停 -------------------------------------------------------------
    def start(self, *, block: bool = False) -> str:
        self.httpd = ThreadingHTTPServer((self.host, self.port), make_handler(self))
        self.port = self.httpd.server_address[1]
        url = f"http://{self.host}:{self.port}/"
        if block:
            print(f"{APP_NAME} v{__version__} → {url}")
            self.httpd.serve_forever()
            return url
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return url

    def stop(self) -> None:
        self.stop_capture()
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    # -- 识别配置（皮肤） -------------------------------------------------
    def profile_view(self) -> dict[str, Any]:
        """当前激活的识别配置 + 全部可选配置。"""
        return {
            "ok": True,
            "active": self.profiles.active_id(),
            "items": self.profiles.list(),
        }

    def activate_profile(self, pid: str) -> dict[str, Any]:
        """切换配置：先把当前模板落盘，再加载新配置并让采集线程用新的。"""
        self.vision.save()
        try:
            path = self.profiles.activate(pid)
        except ProfileError as exc:
            raise ValueError(str(exc)) from exc
        self.vision = VisionPipeline.load(path)
        self.capture_service.vision = self.vision
        return self.profile_view()

    def save_profile(self, pid: str, name: str = "") -> dict[str, Any]:
        """把当前配置另存为新 id，并切过去（用于「新皮肤校准完，存一套」）。"""
        self.vision.save()
        try:
            self.profiles.create(pid, name, copy_from=self.profiles.active_id())
        except ProfileError as exc:
            raise ValueError(str(exc)) from exc
        return self.activate_profile(pid)

    # -- 辅助 -------------------------------------------------------------
    def layout_view(self) -> dict[str, Any]:
        return {
            "ok": True,
            "profile": self.profiles.active_id(),
            "layout": self.vision.layout.to_dict(),
            "vision": self.vision.status(),
        }

    def save_layout_data(self, data: dict[str, Any]) -> None:
        from ..vision.roi import Layout

        self.vision.layout = Layout.from_dict(data)
        self.vision.layout.source = "taught"
        self.vision.save()

    def set_vision_config(self, updates: dict[str, float]) -> dict[str, float]:
        """改识别阈值并落盘（工作台滑杆用）。"""
        return self.vision.set_config(updates)

    def save_vision_report(self) -> dict[str, Any]:
        """把当前帧 + 诊断报告落盘，方便离线/发给别人复核。"""
        import json as _json
        from datetime import datetime

        from ..vision.inspect import inspect_frame

        frame = self.last_frame or self.grab()
        report = inspect_frame(self.vision, frame)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        out = Path(self.var_dir) / "reports" / f"report-{stamp}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "frame.png").write_bytes(frame.png())
        (out / "report.json").write_text(
            _json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"ok": True, "path": str(out), "elapsed_ms": report.get("elapsed_ms")}

    def grab(self):
        """抓一帧 Frame 对象（识别用）。"""
        return self.capture_service.grab()

    def grab_frame(self) -> tuple[bytes, int]:
        """抓一帧 PNG，返回 (bytes, http_status)。"""
        return self.capture_service.grab_png()

    def read_frame(self, *, only_changed: bool = False) -> dict[str, Any]:
        """抓帧 → 识别 → 喂 reducer；only_changed 跳过与当前状态相同的观测。"""
        return self.capture_service.read(only_changed=only_changed)

    # -- 自动识别 ---------------------------------------------------------
    def capture_status(self) -> dict[str, Any]:
        return self.capture_service.status()

    def start_capture(self, interval: float = 2.0) -> dict[str, Any]:
        return self.capture_service.start(interval)

    def stop_capture(self) -> dict[str, Any]:
        return self.capture_service.stop()

    # -- 社区上传 ---------------------------------------------------------
    def _community_cfg(self) -> dict[str, Any]:
        return self.community_service.config()

    def community_view(self) -> dict[str, Any]:
        return self.community_service.view()

    def upload_matches(self, url: str | None = None, token: str | None = None) -> dict[str, Any]:
        return self.community_service.upload(url, token)


_set_frame_source = set_frame_source


def serve(
    engine: Engine,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
) -> AppServer:
    """启动服务并（可选）打开浏览器，返回 server 对象。"""
    if host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get("COVENANT_ALLOW_LAN"):
        raise SystemExit(
            "拒绝监听非本机地址：这个面板没有鉴权，只适合自己电脑用。\n"
            "确实要在局域网里打开，先设 COVENANT_ALLOW_LAN=1，并自己加反向代理/防火墙。"
        )
    if port == 0:
        port = find_free_port(host)
    app = AppServer(engine, host, port)
    url = app.start()
    print(f"{APP_NAME} v{__version__} 已启动：{url}")
    print(f"  数据包：{engine.pack.id}（{engine.pack.name}）")
    print("  关闭窗口或 Ctrl+C 结束。")
    if open_browser:
        threading.Thread(target=lambda: (time.sleep(0.4), webbrowser.open(url)), daemon=True).start()
    return app


__all__ = ["AppServer", "find_free_port", "serve"]
