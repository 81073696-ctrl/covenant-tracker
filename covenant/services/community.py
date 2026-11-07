"""社区收集器：记住地址/令牌、把本机 matches.jsonl 推上去。

网络请求本身在 `covenant/community/client.py`；这里只管「配置 + 视图 + 上传编排」。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CommunityService:
    def __init__(self, engine: Any, var_dir: Path) -> None:
        self.engine = engine
        self.var_dir = var_dir

    @property
    def config_path(self) -> Path:
        return self.var_dir / "community.json"

    def config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 配置坏了就当没配
            return {}
        return data if isinstance(data, dict) else {}

    def view(self) -> dict[str, Any]:
        cfg = self.config()
        store = self.engine.stats_store
        return {
            "ok": True,
            "url": str(cfg.get("url", "")),
            "token_set": bool(cfg.get("token")),
            "records": store.count(),
            "path": str(store.path),
        }

    def upload(self, url: str | None = None, token: str | None = None) -> dict[str, Any]:
        from ..community.client import submit_file

        cfg = self.config()
        target = (url or cfg.get("url") or "").strip().rstrip("/")
        if not target:
            return {"ok": False, "error": "先填社区地址，例如 http://127.0.0.1:8900"}
        if not target.startswith(("http://", "https://")):
            return {"ok": False, "error": "地址要以 http:// 或 https:// 开头"}
        secret = (token or cfg.get("token") or "").strip() or None
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps({"url": target, "token": secret or ""}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            return submit_file(target, self.engine.stats_store.path, token=secret)
        except Exception as exc:  # noqa: BLE001 - 网络错误给前端一句话
            return {"ok": False, "error": f"上传失败：{exc}"}
