"""路由表单测：注册完整、未知路径返回 None、错误转 ApiError。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from covenant.data.pack import load_latest
from covenant.state.engine import Engine
from covenant.ui.routes import GET_ROUTES, POST_ROUTES, ApiError, get_json, post_json

PACK = load_latest("packs")


class TestRegistry(unittest.TestCase):
    def test_expected_get_routes(self):
        for path in [
            "/api/pack",
            "/api/health",
            "/api/layout",
            "/api/layout/default",
            "/api/windows",
            "/api/stats",
            "/api/settings",
            "/api/capture",
            "/api/community",
        ]:
            self.assertIn(path, GET_ROUTES)

    def test_expected_post_routes(self):
        for path in [
            "/api/action",
            "/api/observe",
            "/api/match",
            "/api/match/import",
            "/api/settings",
            "/api/capture",
            "/api/reload-pack",
            "/api/frame-source",
            "/api/vision/read",
            "/api/vision/learn-digit",
            "/api/vision/learn-slot",
            "/api/vision/learn-result",
            "/api/community/upload",
        ]:
            self.assertIn(path, POST_ROUTES)

    def test_unknown_path_is_none(self):
        app = SimpleNamespace()
        self.assertIsNone(get_json(app, "/api/nope", {}))
        self.assertIsNone(post_json(app, "/api/nope", {}))


class TestHandlers(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(PACK)
        self.app = SimpleNamespace(
            engine=self.engine,
            capture_status=lambda: {"ok": True, "on": False},
            settings_view=lambda: {"ok": True, "settings": {}},
        )

    def test_health(self):
        code, payload = get_json(self.app, "/api/health", {})
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["pack"], PACK.id)

    def test_bad_confidence(self):
        with self.assertRaises(ApiError) as ctx:
            post_json(self.app, "/api/observe", {"kind": "gold", "value": 1, "confidence": "x"})
        self.assertEqual(ctx.exception.status, 400)

    def test_bad_cups(self):
        with self.assertRaises(ApiError) as ctx:
            post_json(self.app, "/api/match", {"result": "clear", "cups": "abc"})
        self.assertEqual(ctx.exception.status, 400)

    def test_empty_import(self):
        with self.assertRaises(ApiError) as ctx:
            post_json(self.app, "/api/match/import", {"text": "   "})
        self.assertEqual(ctx.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
