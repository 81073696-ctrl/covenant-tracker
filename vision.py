"""识别与布局相关的路由。"""

from __future__ import annotations

from typing import Any

from .core import ApiError, get, post


@get("/api/layout")
def layout(app: Any, query: dict) -> dict:
    return app.layout_view()


@get("/api/profiles")
def profiles(app: Any, query: dict) -> dict:
    return app.profile_view()


@post("/api/profiles/activate")
def activate_profile(app: Any, body: dict) -> dict:
    pid = str(body.get("id", "")).strip()
    if not pid:
        raise ApiError(400, "需要配置 id")
    try:
        return app.activate_profile(pid)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from exc


@post("/api/profiles/save")
def save_profile(app: Any, body: dict) -> dict:
    pid = str(body.get("id", "")).strip()
    name = str(body.get("name", "")).strip()
    if not pid:
        raise ApiError(400, "需要配置 id（字母/数字/-/_）")
    try:
        return app.save_profile(pid, name)
    except ValueError as exc:
        raise ApiError(400, str(exc)) from exc


@get("/api/layout/default")
def layout_default(app: Any, query: dict) -> dict:
    from ...vision.roi import Layout

    return {"ok": True, "layout": Layout.default().to_dict()}


@get("/api/windows")
def windows(app: Any, query: dict) -> dict:
    from ...capture.screen import list_windows

    wins = [w for w in list_windows() if w["width"] >= 320 and w["height"] >= 200]
    return {
        "ok": True,
        "windows": [
            {"title": w["title"], "hwnd": w["hwnd"], "width": w["width"], "height": w["height"]}
            for w in wins[:40]
        ],
    }


@post("/api/layout")
def save_layout(app: Any, body: dict) -> dict:
    inner = body.get("layout") if isinstance(body.get("layout"), dict) else body
    if not isinstance(inner.get("regions"), dict):
        raise ApiError(400, "layout 需要 regions 对象（name → 区域）")
    app.save_layout_data(inner)
    return app.layout_view()


@post("/api/vision/read")
def vision_read(app: Any, body: dict) -> dict:
    try:
        return app.read_frame()
    except RuntimeError as exc:
        raise ApiError(503, str(exc)) from exc


@get("/api/vision/inspect")
def vision_inspect(app: Any, query: dict) -> dict:
    """识别工作台：这一帧每个区域看见了什么、为什么没过。"""
    from ...vision.inspect import inspect_frame

    try:
        frame = app.last_frame or app.grab()
    except RuntimeError as exc:
        raise ApiError(503, str(exc)) from exc
    return inspect_frame(app.vision, frame)


@post("/api/vision/save-report")
def vision_save_report(app: Any, body: dict) -> dict:
    """把当前帧与诊断报告写入 var/reports/report-<时间>/。"""
    try:
        return app.save_vision_report()
    except RuntimeError as exc:
        raise ApiError(503, str(exc)) from exc


@post("/api/vision/config")
def vision_config(app: Any, body: dict) -> dict:
    """调阈值（0.3–0.99），立即写回当前配置并热更新管线。"""
    allowed = {
        "digit_min_sim": "digit",
        "icon_min_sim": "icon",
        "unit_min_sim": "unit",
        "star_min_sim": "star",
    }
    updates: dict[str, float] = {}
    for key in allowed:
        if key not in body:
            continue
        try:
            value = float(body[key])
        except (TypeError, ValueError) as exc:
            raise ApiError(400, f"{key} 必须是数字：{exc}") from exc
        if not 0.3 <= value <= 0.99:
            raise ApiError(400, f"{key} 需要在 0.3–0.99 之间")
        updates[key] = round(value, 3)
    if not updates:
        raise ApiError(400, "没有可更新的阈值")
    app.set_vision_config(updates)
    return {"ok": True, "config": app.vision.config(), "vision": app.vision.status()}


@post("/api/vision/learn-digit")
def learn_digit(app: Any, body: dict) -> dict:
    region = str(body.get("region", "")).strip()
    text = str(body.get("text", "")).strip()
    if not region or not text.isdigit():
        raise ApiError(400, "需要 region 与纯数字 text")
    frame = app.last_frame or app.grab()
    learned = app.vision.learn_digit(frame, region, text)
    if not learned:
        raise ApiError(422, "切分出的字形数和文本长度对不上，换个更紧的框试试")
    return {"ok": True, "learned": learned, "vision": app.vision.status()}


@post("/api/vision/learn-slot")
def learn_slot(app: Any, body: dict) -> dict:
    region = str(body.get("region", "")).strip()
    op_id = str(body.get("op_id", "")).strip()
    try:
        index = int(body.get("index", 0))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, f"index 必须是整数：{exc}") from exc
    if not region or not op_id:
        raise ApiError(400, "需要 region 与 op_id")
    frame = app.last_frame or app.grab()
    if not app.vision.learn_slot(frame, region, index, op_id):
        raise ApiError(422, "无法学习这个格子：区域或序号不对")
    return {"ok": True, "vision": app.vision.status()}


@post("/api/vision/learn-result")
def learn_result(app: Any, body: dict) -> dict:
    region = str(body.get("region", "")).strip()
    result = str(body.get("result", "")).strip()
    if not region or result not in ("clear", "fail"):
        raise ApiError(400, "需要 region 与 result（clear / fail）")
    frame = app.last_frame or app.grab()
    if not app.vision.learn_result(frame, region, result):
        raise ApiError(422, "无法学习这个结算模板：区域不存在或框太小")
    return {"ok": True, "vision": app.vision.status()}


@post("/api/vision/learn-unit")
def learn_unit(app: Any, body: dict) -> dict:
    """学一个整备区 / 战场单位（模板单独存，不污染商店卡模板）。"""
    region = str(body.get("region", "")).strip()
    op_id = str(body.get("op_id", "")).strip()
    try:
        index = int(body.get("index", 0))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, f"index 必须是整数：{exc}") from exc
    if not region or not op_id:
        raise ApiError(400, "需要 region 与 op_id")
    frame = app.last_frame or app.grab()
    if not app.vision.learn_unit(frame, region, index, op_id):
        raise ApiError(422, "无法学习这个单位：区域、序号或截图不对")
    return {"ok": True, "vision": app.vision.status()}


@post("/api/vision/learn-star")
def learn_star(app: Any, body: dict) -> dict:
    """学星级角标；区域需要在 params 里配 star_rect（相对格子的比例框）。"""
    region = str(body.get("region", "")).strip()
    try:
        index = int(body.get("index", 0))
        star = int(body.get("star", 0))
    except (TypeError, ValueError) as exc:
        raise ApiError(400, f"index / star 必须是整数：{exc}") from exc
    if not region or star not in (1, 2, 3):
        raise ApiError(400, "需要 region 与 star（1 / 2 / 3）")
    frame = app.last_frame or app.grab()
    if not app.vision.learn_star(frame, region, index, star):
        raise ApiError(422, "无法学习星级：检查区域的 star_rect 和格子序号")
    return {"ok": True, "vision": app.vision.status()}
