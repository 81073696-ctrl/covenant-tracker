"""自托管的战绩收集器（标准库，单文件跑起来）。

用法::

    python -m covenant community --port 8900 --data var/community.jsonl
    python -m covenant share --url http://127.0.0.1:8900

安全边界
--------
* 只做「收记录 + 聚合」，不做账号、不做登录，记录里也没有玩家标识；
* ``--token`` 只保护 ``POST /api/submit``，防止被人拿脚本灌垃圾；
* 默认监听 127.0.0.1；要放到公网请自己套 HTTPS 反代 + 限流 + 备份。
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..stats.aggregate import summarize
from ..stats.model import RESULTS, MatchRecord, validate_record
from ..stats.store import MatchStore
from ..version import __version__

MAX_BODY = 2 * 1024 * 1024
MAX_RECORDS = 500




class _RateLimiter:
    """每个 IP 每分钟最多 ``limit`` 次提交，够用且不用引入依赖。"""

    def __init__(self, limit: int = 30, window: float = 60.0):
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            bucket = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(bucket) >= self.limit:
                self._hits[key] = bucket
                return False
            bucket.append(now)
            self._hits[key] = bucket
            return True


class RecordBank:
    """全部社区记录（JSONL，一行一条，按 id 去重）。"""

    def __init__(self, path: str | Path = "var/community.jsonl"):
        self.store = MatchStore(path)

    def count(self) -> int:
        return self.store.count()

    def records(self) -> list[MatchRecord]:
        return self.store.load()

    def add(self, raw: list[Any]) -> dict[str, int]:
        good: list[MatchRecord] = []
        bad = 0
        for item in raw[:MAX_RECORDS]:
            if validate_record(item, strict=True):
                bad += 1
                continue
            try:
                good.append(MatchRecord.from_dict(item))
            except (TypeError, ValueError, KeyError):
                bad += 1
        res = self.store.extend(good)
        return {"added": res["added"], "skipped": res["skipped"], "bad": bad, "total": res["total"]}


class CommunityServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8900,
        data: str | Path = "var/community.jsonl",
        token: str | None = None,
        pack_dir: str | Path = "packs",
    ):
        self.host = host
        self.port = port
        self.bank = RecordBank(data)
        self.token = token
        self.pack_dir = Path(pack_dir)
        self._pack = None
        self._pack_loaded = False
        self._limiter = _RateLimiter()
        self._cache: tuple[tuple, float, dict] | None = None
        self.httpd: ThreadingHTTPServer | None = None

    # -- 数据 -------------------------------------------------------------
    def pack(self):
        if not self._pack_loaded:
            self._pack_loaded = True
            try:
                from ..data.pack import load_latest

                self._pack = load_latest(self.pack_dir)
            except (OSError, ValueError, KeyError):
                self._pack = None
        return self._pack

    def stats(self, *, mode_id: str = "", band_id: str = "", patch: str = "", minimum: int = 5) -> dict:
        key = (self.bank.count(), mode_id, band_id, patch, minimum)
        now = time.time()
        if self._cache and self._cache[0] == key and now - self._cache[1] < 15:
            return self._cache[2]
        out = summarize(
            self.bank.records(),
            self.pack(),
            mode_id=mode_id,
            band_id=band_id,
            patch=patch,
            min_sample=minimum,
        )
        self._cache = (key, now, out)
        return out

    # -- 启停 -------------------------------------------------------------
    def start(self, *, block: bool = False) -> str:
        self.httpd = ThreadingHTTPServer((self.host, self.port), _make_handler(self))
        self.port = self.httpd.server_address[1]
        url = f"http://{self.host}:{self.port}/"
        if block:
            self.httpd.serve_forever()
            return url
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return url

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None


def _make_handler(app: CommunityServer):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"CovenantCommunity/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
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
                raise ValueError("请求体为空")
            if length > MAX_BODY:
                raise ValueError("请求体太大（>2MB）")
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"不是合法 JSON：{exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("请求体必须是对象")
            return data

        def _token_ok(self) -> bool:
            if not app.token:
                return True
            given = self.headers.get("X-Token", "")
            return given == app.token

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            try:
                if path in ("/", "/index.html"):
                    return self._send(200, _page(app).encode("utf-8"), "text/html; charset=utf-8")
                if path == "/api/health":
                    return self._json({"ok": True, "version": __version__, "records": app.bank.count()})
                if path == "/api/stats":
                    q = parse_qs(urlparse(self.path).query)

                    def q1(key: str, default: str = "") -> str:
                        return (q.get(key) or [default])[0]

                    try:
                        minimum = max(1, min(100, int(q1("min", "5"))))
                    except ValueError:
                        minimum = 5
                    return self._json(
                        app.stats(
                            mode_id=q1("mode"),
                            band_id=q1("band"),
                            patch=q1("patch"),
                            minimum=minimum,
                        )
                    )
                return self._error(404, f"没有这个路由：{path}")
            except Exception:  # noqa: BLE001
                return self._error(500, "内部错误")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Token")
            self.send_header("Access-Control-Max-Age", "600")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path != "/api/submit":
                return self._error(404, f"没有这个路由：{path}")
            if not app._limiter.allow(self.client_address[0]):
                return self._error(429, "提交太频繁，请稍后再试")
            if not self._token_ok():
                return self._error(401, "token 不对")
            try:
                data = self._body()
            except ValueError as exc:
                return self._error(400, str(exc))
            raw = data.get("records")
            if not isinstance(raw, list) or not raw:
                return self._error(400, "records 需要是非空数组")
            try:
                res = app.bank.add(raw)
            except OSError:
                return self._error(500, "写入失败，稍后再试")
            return self._json({"ok": True, **res})

    return Handler


def _page(app: CommunityServer) -> str:
    """极简首页：让人确认服务活着，顺便看前 10 名。"""
    stats = app.stats(minimum=1)
    rows = "".join(
        f"<tr><td>{o['name']}</td><td>T{o['tier']}</td><td>{o['picks']}</td>"
        f"<td>{o['pick_rate'] * 100:.1f}%</td>"
        f"<td>{o['win_rate'] * 100:.1f}%</td></tr>"
        for o in stats["operators"][:10]
    )
    return f"""<!doctype html><meta charset="utf-8">
<title>卫戍协议 · 社区战绩</title>
<style>
 body{{background:#0d0f12;color:#e8eaed;font:14px/1.6 system-ui,sans-serif;padding:32px}}
 h1{{font-size:20px}} table{{border-collapse:collapse;margin-top:12px}}
 th,td{{border-bottom:1px solid #262a31;padding:6px 14px;text-align:left}}
 th{{color:#9aa0a6;font-weight:500}} .muted{{color:#9aa0a6}}
</style>
<h1>卫戍协议 · 社区战绩</h1>
<p class="muted">当前 {app.bank.count()} 条记录 · 聚合接口 <code>/api/stats</code> ·
上传接口 <code>POST /api/submit</code></p>
<table><tr><th>干员</th><th>阶</th><th>出场</th><th>出场率</th><th>通关率</th></tr>{rows}</table>
<p class="muted">出场 = 终局战场；样本少的行会标灰。数据由玩家自愿上传，存在选择偏差。</p>
"""
