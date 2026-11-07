"""对局与采集相关的路由。"""

from __future__ import annotations

import time
from typing import Any

from ...core.types import Action, Observation
from ...version import __version__
from .core import ApiError, get, post


@get("/api/pack")
def pack(app: Any, query: dict) -> dict:
    return app.engine.pack_view()


@get("/api/health")
def health(app: Any, query: dict) -> dict:
    return {
        "ok": True,
        "version": __version__,
        "pack": app.engine.pack.id,
        "ts": time.time(),
    }


@get("/api/capture")
def capture_status(app: Any, query: dict) -> dict:
    return app.capture_status()


@post("/api/action")
def action(app: Any, body: dict) -> dict:
    return app.engine.act(
        Action(
            type=str(body.get("type", "")),
            args=body.get("args") or {},
            source=str(body.get("source", "manual")),
        )
    )


@post("/api/observe")
def observe(app: Any, body: dict) -> dict:
    try:
        confidence = float(body.get("confidence", 1.0))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, f"confidence 必须是数字：{exc}") from exc
    return app.engine.observe(
        Observation(
            kind=str(body.get("kind", "")),
            value=body.get("value"),
            confidence=confidence,
            source=str(body.get("source", "manual")),
        )
    )


@post("/api/capture")
def capture_toggle(app: Any, body: dict) -> dict:
    if body.get("on"):
        try:
            return app.start_capture(float(body.get("interval", 2.0)))
        except (TypeError, ValueError) as exc:
            raise ApiError(400, f"interval 必须是数字：{exc}") from exc
        except RuntimeError as exc:
            raise ApiError(503, str(exc)) from exc
    return app.stop_capture()


@post("/api/reload-pack")
def reload_pack(app: Any, body: dict) -> dict:
    app.engine.reload_pack()
    return {"ok": True, "pack": app.engine.pack.summary()}


@post("/api/frame-source")
def frame_source(app: Any, body: dict) -> dict:
    name = str(body.get("name", "")).strip()
    if not name:
        raise ApiError(400, "缺少窗口名")
    return set_frame_source(app, name)


@post("/api/match")
def match(app: Any, body: dict) -> dict:
    raw_cups = body.get("cups")
    try:
        cups = int(raw_cups) if raw_cups not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ApiError(400, f"cups 必须是整数：{exc}") from exc
    return app.engine.record_match(
        str(body.get("result", "fail")),
        cups=cups,
        note=str(body.get("note", ""))[:400],
    )


@post("/api/match/import")
def match_import(app: Any, body: dict) -> dict:
    text = body.get("text") if isinstance(body.get("text"), str) else ""
    if not text.strip():
        raise ApiError(400, "导入内容为空")
    return app.engine.import_matches(text)


def set_frame_source(app: Any, window_name: str) -> dict[str, Any]:
    """把采集源换成某个窗口（按标题模糊匹配）。"""
    from ...capture.screen import WindowCapture

    cap = WindowCapture.find(window_name)
    if cap is None:
        return {"ok": False, "error": f"没找到标题包含「{window_name}」的窗口"}
    app.frame_source = cap
    return {"ok": True, "window": cap.title(), "capture": app.capture_status()}
