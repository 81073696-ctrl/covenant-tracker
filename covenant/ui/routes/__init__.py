"""HTTP 路由表：路径 → 业务调用。

``ui/http.py`` 只负责 HTTP 细节；这里按领域分成 game / vision / stats 三组，
每个处理函数返回 dict，失败抛 :class:`ApiError`。
"""

from __future__ import annotations

from . import game, stats, vision  # noqa: F401  导入即注册
from .core import ApiError, GET_ROUTES, POST_ROUTES, get, get_json, post, post_json
from .game import set_frame_source

__all__ = [
    "ApiError",
    "GET_ROUTES",
    "POST_ROUTES",
    "get",
    "get_json",
    "post",
    "post_json",
    "set_frame_source",
]
