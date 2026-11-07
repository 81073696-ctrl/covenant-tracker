"""把本机记录推到收集器；只用标准库。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable

from ..stats.model import MatchRecord


def _payload(records: Iterable[MatchRecord]) -> bytes:
    body = {
        "source": "covenant-tracker",
        "records": [r.to_dict() for r in records],
    }
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def submit_records(
    url: str,
    records: Iterable[MatchRecord],
    *,
    token: str | None = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """POST 一批记录，返回服务端的 {added, skipped, bad, total}。

    ``url`` 可以是 ``http://host:8900`` 或完整提交地址；没有 ``/api/submit``
    后缀时会自动补上。
    """
    url = url.rstrip("/")
    if not url.endswith("/api/submit"):
        url = f"{url}/api/submit"
    data = _payload(records)
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("X-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 服务端明确拒绝时把原因带回来
        try:
            return json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            return {"ok": False, "error": f"HTTP {exc.code}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"连不上服务：{exc.reason}"}


def submit_file(
    url: str,
    path: str | Path = "var/matches.jsonl",
    *,
    token: str | None = None,
) -> dict[str, Any]:
    """读本地 JSONL 并上传。"""
    from ..stats.store import MatchStore

    records = MatchStore(path).load()
    if not records:
        return {"ok": True, "added": 0, "skipped": 0, "bad": 0, "total": 0, "note": "本机还没有记录"}
    return submit_records(url, records, token=token)
