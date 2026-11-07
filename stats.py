"""统计、社区与设置的路由。"""

from __future__ import annotations

from typing import Any

from .core import ApiError, get, post


@get("/api/stats")
def stats(app: Any, query: dict) -> dict:
    try:
        minimum = int(query.get("min", "5"))
    except ValueError:
        minimum = 5
    return app.engine.stats_view(
        mode_id=query.get("mode", ""),
        band_id=query.get("band", ""),
        patch=query.get("patch", ""),
        min_sample=minimum,
    )


@get("/api/community")
def community(app: Any, query: dict) -> dict:
    return app.community_view()


@post("/api/community/upload")
def community_upload(app: Any, body: dict) -> dict:
    return app.upload_matches(body.get("url"), body.get("token"))


@get("/api/settings")
def settings(app: Any, query: dict) -> dict:
    return app.settings_view()


@post("/api/settings")
def save_settings(app: Any, body: dict) -> dict:
    try:
        return app.save_settings(body)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from exc
