"""路由注册表：路径 → 处理函数。

处理函数只返回 dict（成功），失败抛 :class:`ApiError`；
HTTP 状态码由这里统一给 200，错误交给 ``ui/http.py`` 翻译。
"""

from __future__ import annotations

from typing import Any, Callable

Handler = Callable[[Any, dict], dict]

GET_ROUTES: dict[str, Handler] = {}
POST_ROUTES: dict[str, Handler] = {}


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def get(path: str):
    def deco(fn: Handler) -> Handler:
        GET_ROUTES[path] = fn
        return fn

    return deco


def post(path: str):
    def deco(fn: Handler) -> Handler:
        POST_ROUTES[path] = fn
        return fn

    return deco


def get_json(app: Any, path: str, query: dict[str, str]) -> tuple[int, dict[str, Any]] | None:
    fn = GET_ROUTES.get(path)
    if fn is None:
        return None
    return 200, fn(app, query)


def post_json(app: Any, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]] | None:
    fn = POST_ROUTES.get(path)
    if fn is None:
        return None
    return 200, fn(app, body)
