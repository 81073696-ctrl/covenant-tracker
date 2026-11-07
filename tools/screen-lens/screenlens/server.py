"""Screen Lens 的 HTTP 服务：只读诊断 + 就地教学 + 区域编辑。

所有接口都围绕一份「识别配置目录」（默认 var/）：
layout.json / ocr_digits.npz / icons.npz / units.npz / stars.npz / results.npz / vision.json
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .capture.base import Frame
from .capture.folder import FolderSource
from .capture.screen import WindowCapture, list_windows
from .vision.inspect import inspect_frame
from .vision.pipeline import VisionPipeline
from .vision.roi import Layout

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_BODY = 2 * 1024 * 1024


class LensError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class LensApp:
    def __init__(self, var_dir: str | Path = "var", layout: str | Path | None = None) -> None:
        self.var = Path(var_dir)
        self.var.mkdir(parents=True, exist_ok=True)
        self.pipe = VisionPipeline.load(self.var)
        if layout:
            self.pipe.layout = Layout.load(Path(layout))
            self.pipe.save()
        self.source = None  # FrameSource | None
        self.source_title = "未接入"
        self.last_frame: Frame | None = None
        self.last_ts = 0.0
        self.lock = threading.Lock()

    # ---- 采一帧 ----
    def grab(self, next: bool = False) -> Frame:
        """取当前帧。

        next=True 才向回放目录要下一张；否则复用上一帧——否则「看图」和
        「诊断」会各自 grab 一次，页面上的图和诊断结果不是同一帧。
        窗口源按 0.2s 节流实时抓。
        """
        with self.lock:
            if self.source is None:
                raise LensError("还没有接入采集源：用 --samples 指定截图目录，或点「选择窗口」接入一个窗口", 503)
            now = time.time()
            is_folder = isinstance(self.source, FolderSource)
            if self.last_frame is not None:
                if is_folder and not next:
                    return self.last_frame
                if not is_folder and now - self.last_ts < 0.2:
                    return self.last_frame
            frame = self.source.grab()
            self.last_frame = frame
            self.last_ts = now
            return frame

    def use_window(self, title: str) -> dict[str, Any]:
        cap = WindowCapture.find(title or "")
        if not cap:
            raise LensError(f"没找到标题包含「{title}」的窗口", 404)
        self.source = cap
        self.source_title = cap.title or title
        self.last_frame = None
        return {"ok": True, "title": self.source_title}

    def use_samples(self, folder: str | Path, loop: bool = True) -> dict[str, Any]:
        src = FolderSource(folder, loop=loop)
        self.source = src
        self.source_title = f"回放：{Path(folder).name}"
        self.last_frame = None
        return {"ok": True, "title": self.source_title}

    # ---- 布局 ----
    def layout_view(self) -> dict[str, Any]:
        return {
            "ok": True,
            "layout": self.pipe.layout.to_dict(),
            "thresholds": self.pipe.config(),
            "source": self.source_title,
        }

    def save_layout(self, data: dict[str, Any]) -> dict[str, Any]:
        regions = data.get("regions") if isinstance(data, dict) else None
        if not isinstance(regions, dict):
            raise LensError("layout.regions 必须是 {name: region} 的对象")
        layout = Layout.from_dict({"regions": regions, "source": "edited"})
        self.pipe.layout = layout
        self.pipe.save()
        return {"ok": True, "layout": layout.to_dict()}

    # ---- 教学 ----
    def learn_digit(self, frame: Frame, region: str, text: str) -> dict[str, Any]:
        if not text:
            raise LensError("text 不能为空")
        n = self.pipe.learn_digit(frame, region, text)
        if n <= 0:
            raise LensError("切分出的字形数和文本长度对不上：把框收紧一点，或确认这一格有几位数字", 422)
        return {"ok": True, "learned": n}

    def learn_slot(self, frame: Frame, region: str, index: int, op_id: str) -> dict[str, Any]:
        if not self.pipe.learn_slot(frame, region, index, op_id):
            raise LensError("没学成：检查区域名/序号，或者这一格是空的")
        return {"ok": True}

    def learn_unit(self, frame: Frame, region: str, index: int, op_id: str) -> dict[str, Any]:
        if not self.pipe.learn_unit(frame, region, index, op_id):
            raise LensError("没学成：检查区域名/序号，或者这一格是空的")
        return {"ok": True}

    def learn_star(self, frame: Frame, region: str, index: int, star: int) -> dict[str, Any]:
        if star not in (1, 2, 3):
            raise LensError("star 只能是 1/2/3")
        if not self.pipe.learn_star(frame, region, index, star):
            raise LensError("没学成：单位区需要 params.star_rect（星级角标相对位置）")
        return {"ok": True}

    # ---- 诊断包 ----
    def save_report(self) -> dict[str, Any]:
        frame = self.grab(next=False)
        report = inspect_frame(self.pipe, frame)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out = self.var / "reports" / f"report-{stamp}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "frame.png").write_bytes(frame.png())
        (out / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {"ok": True, "path": str(out), "elapsed_ms": report.get("elapsed_ms")}


def _json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def make_handler(app: LensApp):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ScreenLens"

        def log_message(self, fmt: str, *args: Any) -> None:  # 静音
            return

        # ---------- helpers ----------
        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: Any) -> None:
            self._send(code, _json_bytes(data))

        def _body(self) -> dict[str, Any]:
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = 0
            if n <= 0:
                return {}
            if n > MAX_BODY:
                raise LensError("请求体太大", 413)
            raw = self.rfile.read(n)
            try:
                data = json.loads(raw.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise LensError("请求体不是合法 JSON")
            return data if isinstance(data, dict) else {}

        def _file(self, path: Path, ctype: str) -> None:
            try:
                data = path.read_bytes()
            except OSError:
                self._json(404, {"ok": False, "error": "文件不存在"})
                return
            self._send(200, data, ctype)

        # ---------- GET ----------
        def do_GET(self) -> None:  # noqa: N802
            try:
                u = urlparse(self.path)
                path = u.path
                if path in ("/", "/index.html"):
                    return self._file(WEB_DIR / "lens.html", "text/html; charset=utf-8")
                if path == "/lens":
                    return self._file(WEB_DIR / "lens.html", "text/html; charset=utf-8")
                if path.startswith("/static/"):
                    name = Path(path[len("/static/") :]).name
                    ctype = "application/javascript; charset=utf-8" if name.endswith(".js") else "text/plain; charset=utf-8"
                    return self._file(WEB_DIR / name, ctype)
                if path == "/api/health":
                    return self._json(200, {"ok": True, "version": __import__("screenlens").__version__})
                if path == "/api/frame":
                    frame = app.grab(next=False)
                    return self._send(200, frame.png(), "image/png")
                if path == "/api/vision/inspect":
                    advance = "next=1" in (u.query or "")
                    return self._json(200, inspect_frame(app.pipe, app.grab(next=advance)))
                if path == "/api/layout":
                    return self._json(200, app.layout_view())
                if path == "/api/windows":
                    wins = [w for w in list_windows() if w["width"] >= 320 and w["height"] >= 200]
                    return self._json(200, {"ok": True, "windows": wins[:40]})
                if path == "/api/snapshot":
                    # 单机调试台没有「面板状态」，返回 null 让前端跳过对比
                    return self._json(200, {"ok": True, "state": None})
                self._json(404, {"ok": False, "error": "没有这个接口"})
            except LensError as exc:
                self._json(exc.status, {"ok": False, "error": str(exc)})
            except Exception as exc:  # 诊断台自己不能挂
                self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

        # ---------- POST ----------
        def do_POST(self) -> None:  # noqa: N802
            try:
                u = urlparse(self.path)
                path = u.path
                body = self._body()
                frame = None
                if path.startswith("/api/vision/"):
                    frame = app.grab(next=False)
                if path == "/api/vision/config":
                    try:
                        return self._json(200, {"ok": True, "thresholds": app.pipe.set_config(body)})
                    except (TypeError, ValueError) as exc:
                        raise LensError(str(exc))
                if path == "/api/vision/learn-digit":
                    return self._json(200, app.learn_digit(frame, str(body.get("region") or ""), str(body.get("text") or "")))
                if path == "/api/vision/learn-slot":
                    return self._json(
                        200,
                        app.learn_slot(
                            frame,
                            str(body.get("region") or ""),
                            int(body.get("index", -1)),
                            str(body.get("op_id") or ""),
                        ),
                    )
                if path == "/api/vision/learn-unit":
                    return self._json(
                        200,
                        app.learn_unit(
                            frame,
                            str(body.get("region") or ""),
                            int(body.get("index", -1)),
                            str(body.get("op_id") or ""),
                        ),
                    )
                if path == "/api/vision/learn-star":
                    return self._json(
                        200,
                        app.learn_star(
                            frame,
                            str(body.get("region") or ""),
                            int(body.get("index", -1)),
                            int(body.get("star", 0)),
                        ),
                    )
                if path == "/api/vision/save-report":
                    return self._json(200, app.save_report())
                if path == "/api/frame-source":
                    return self._json(200, app.use_window(str(body.get("title") or "")))
                if path == "/api/layout":
                    return self._json(200, app.save_layout(body))
                self._json(404, {"ok": False, "error": "没有这个接口"})
            except LensError as exc:
                self._json(exc.status, {"ok": False, "error": str(exc)})
            except (TypeError, ValueError) as exc:
                self._json(400, {"ok": False, "error": f"参数不对：{exc}"})
            except Exception as exc:
                self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    return Handler


def serve(app: LensApp, host: str = "127.0.0.1", port: int = 8765, block: bool = True) -> str:
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    url = f"http://{host}:{httpd.server_address[1]}/"
    if block:
        print(f"Screen Lens → {url}  (Ctrl+C 结束)")
        httpd.serve_forever()
    else:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return url
