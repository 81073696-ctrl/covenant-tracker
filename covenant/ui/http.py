"""HTTP 细节：请求处理器、静态文件、快照缓存、端口探测。

``server.py`` 只管「组装服务 + 启停」，这里管「怎么把字节写回去」。
两者分开后，加路由不用碰 HTTP 管道，改缓存/压缩/跨域也不用翻业务代码。
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import socket
import traceback
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..paths import web_dir
from ..version import __version__
from .routes import ApiError, get_json, post_json

WEB_DIR = web_dir()


class SnapshotCache:
    """按 engine.revision 缓存快照 JSON；状态不变时连序列化都省掉。

    ``revision`` 在动作/观测/重载时自增，所以 key 用 (pack 身份, revision)：
    reload_pack 换了 pack 对象，即使 revision 没变也会失效。
    """

    def __init__(self) -> None:
        self._key: tuple[int, int] | None = None
        self._body = b""
        self._etag = ""

    def payload(self, engine) -> tuple[bytes, str]:
        key = (id(engine.pack), engine.revision)
        if key != self._key:
            body = json.dumps(engine.snapshot(), ensure_ascii=False).encode("utf-8")
            self._key = key
            self._body = body
            self._etag = 'W/"%s"' % hashlib.sha1(body).hexdigest()[:16]
        return self._body, self._etag


def read_static(rel: str, root: Path | None = None) -> tuple[bytes, str] | None:
    """读 web 目录下的文件，越界/不存在返回 None。"""
    base = (root or WEB_DIR).resolve()
    target = (base / rel).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        return None
    ctype, _ = mimetypes.guess_type(str(target))
    return target.read_bytes(), ctype or "application/octet-stream"


def cors_origin(headers, app) -> str | None:
    """只放行同端口的本机页面，避免任意网站读本地面板。"""
    origin = headers.get("Origin")
    if not origin:
        return None
    parsed = urlparse(origin)
    if parsed.scheme != "http":
        return None
    host = (parsed.hostname or "").lower()
    allowed = {"127.0.0.1", "localhost", "::1"}
    if app.host not in ("127.0.0.1", "localhost", "::1"):
        allowed.add(app.host.lower())
    if host in allowed and (parsed.port or 80) == app.port:
        return origin
    return None


def find_free_port(host: str, start: int = 8765, tries: int = 30) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"{start}..{start + tries} 之间没有空闲端口")


def make_handler(app):
    """构造绑定到某个 AppServer 的请求处理器类。"""

    class Handler(BaseHTTPRequestHandler):
        server_version = f"CovenantTracker/{__version__}"
        protocol_version = "HTTP/1.1"

        # -- 基础 -------------------------------------------------------
        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            if app.engine.state.rules.get("verbose_http"):
                print(f"[http] {self.address_string()} {fmt % args}")

        def _cors_origin(self) -> str | None:
            return cors_origin(self.headers, app)

        def _send(
            self,
            code: int,
            body: bytes,
            ctype: str = "application/json; charset=utf-8",
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            origin = self._cors_origin()
            if origin:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            for key, val in (headers or {}).items():
                self.send_header(key, val)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj: Any, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _error(self, code: int, message: str) -> None:
            self._json({"ok": False, "error": message}, code)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0:
                return {}
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"请求体不是合法 JSON：{exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("请求体必须是对象")
            return data

        # -- 路由 -------------------------------------------------------
        def do_OPTIONS(self) -> None:  # noqa: N802
            origin = self._cors_origin()
            if not origin:
                self.send_response(403)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    return self._static("index.html")
                if path == "/teach":
                    return self._static("teach.html")
                if path == "/lens":
                    return self._static("lens.html")
                if path == "/api/snapshot":
                    body, etag = app.snapshot_payload()
                    if self.headers.get("If-None-Match") == etag:
                        self.send_response(304)
                        self.send_header("ETag", etag)
                        self.send_header("Cache-Control", "no-store")
                        origin = self._cors_origin()
                        if origin:
                            self.send_header("Access-Control-Allow-Origin", origin)
                        self.end_headers()
                        return None
                    return self._send(200, body, headers={"ETag": etag})
                if path == "/api/frame":
                    body, code = app.grab_frame()
                    if code == 200:
                        return self._send(200, body, "image/png")
                    if code == 503:
                        return self._json(
                            {
                                "ok": False,
                                "error": "没有接入采集源（在 teach 页或 config 里指定窗口）",
                            },
                            503,
                        )
                    return self._send(code, body)
                if path == "/api/match/export":
                    q = parse_qs(urlparse(self.path).query)
                    fmt = ((q.get("fmt") or ["jsonl"])[0] or "jsonl").lower()
                    text = app.engine.export_matches(fmt)
                    if fmt == "csv":
                        return self._send(
                            200,
                            text.encode("utf-8-sig"),
                            "text/csv; charset=utf-8",
                            {"Content-Disposition": 'attachment; filename="covenant-matches.csv"'},
                        )
                    return self._send(
                        200,
                        text.encode("utf-8"),
                        "application/x-ndjson; charset=utf-8",
                        {"Content-Disposition": 'attachment; filename="covenant-matches.jsonl"'},
                    )
                query = {
                    k: v[0] for k, v in parse_qs(urlparse(self.path).query).items() if v
                }
                routed = get_json(app, path, query)
                if routed is not None:
                    code, payload = routed
                    return self._json(payload, code)
                if path.startswith("/static/"):
                    return self._static(path[len("/static/") :])
                return self._error(404, f"没有这个路由：{path}")
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._error(500, "服务器内部错误，详见控制台")

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                body = self._body() if path.startswith("/api/") else {}
                routed = post_json(app, path, body)
                if routed is not None:
                    code, payload = routed
                    return self._json(payload, code)
                return self._error(404, f"没有这个路由：{path}")
            except ApiError as exc:
                return self._error(exc.status, exc.message)
            except ValueError as exc:
                return self._error(400, str(exc))
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                return self._error(500, "服务器内部错误，详见控制台")

        # -- 静态文件 ---------------------------------------------------
        def _static(self, rel: str) -> None:
            found = read_static(rel)
            if found is None:
                return self._error(404, f"静态文件不存在：{rel}")
            body, ctype = found
            self._send(200, body, ctype)

    return Handler
