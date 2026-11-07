"""路由层的直接测试：不起 HTTP 服务器，只验「路径 → 数据/错误码」。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from covenant.data.pack import load_latest
from covenant.state.engine import Engine
from covenant.ui.routes import ApiError, get_json, post_json
from covenant.ui.server import AppServer


class TestGetRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(load_latest("packs"))
        self.app = AppServer(self.engine, var_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_health_and_pack(self):
        code, payload = get_json(self.app, "/api/health", {})
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        code, pack = get_json(self.app, "/api/pack", {})
        self.assertIn("operators", pack)

    def test_settings_and_capture(self):
        for path in ("/api/settings", "/api/capture", "/api/community", "/api/layout"):
            code, payload = get_json(self.app, path, {})
            self.assertEqual(code, 200, path)
            self.assertIsInstance(payload, dict)

    def test_unknown_path_returns_none(self):
        self.assertIsNone(get_json(self.app, "/api/nope", {}))

    def test_profiles_list_and_switch(self):
        code, payload = get_json(self.app, "/api/profiles", {})
        self.assertEqual(code, 200)
        self.assertEqual(payload["active"], "default")
        code, created = post_json(
            self.app, "/api/profiles/save", {"id": "ursus", "name": "乌萨斯齐心团"}
        )
        self.assertEqual(code, 200, created)
        self.assertEqual(created["active"], "ursus")
        code, back = post_json(self.app, "/api/profiles/activate", {"id": "default"})
        self.assertEqual(code, 200)
        self.assertEqual(back["active"], "default")
        with self.assertRaises(ApiError):
            post_json(self.app, "/api/profiles/activate", {"id": "missing"})

    def test_stats_bad_min_falls_back(self):
        code, payload = get_json(self.app, "/api/stats", {"min": "abc"})
        self.assertEqual(code, 200)
        self.assertIn("totals", payload)


class TestPostRoutes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(load_latest("packs"))
        self.app = AppServer(self.engine, var_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_action_ok(self):
        code, payload = post_json(
            self.app, "/api/action", {"type": "set", "args": {"path": "gold", "value": 7}}
        )
        self.assertEqual(code, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.engine.state.gold, 7)

    def test_action_bad_value_is_reported_not_raised(self):
        code, payload = post_json(
            self.app, "/api/action", {"type": "set", "args": {"path": "gold", "value": "x"}}
        )
        self.assertEqual(code, 200)
        self.assertFalse(payload["ok"])

    def test_observe_bad_confidence_is_400(self):
        with self.assertRaises(ApiError) as ctx:
            post_json(self.app, "/api/observe", {"kind": "gold", "value": 3, "confidence": "x"})
        self.assertEqual(ctx.exception.status, 400)

    def test_vision_read_without_source_is_503(self):
        with self.assertRaises(ApiError) as ctx:
            post_json(self.app, "/api/vision/read", {})
        self.assertEqual(ctx.exception.status, 503)

    def test_settings_roundtrip(self):
        code, payload = post_json(self.app, "/api/settings", {"capture_interval": 1.5})
        self.assertEqual(code, 200)
        self.assertAlmostEqual(payload["settings"]["capture_interval"], 1.5)

    def test_match_and_import(self):
        code, payload = post_json(self.app, "/api/match", {"result": "clear", "cups": 2})
        self.assertTrue(payload["ok"])
        code, payload = post_json(self.app, "/api/match/import", {"text": "{}"})
        self.assertEqual(payload.get("bad"), 1)

    def test_unknown_path_returns_none(self):
        self.assertIsNone(post_json(self.app, "/api/nope", {}))


if __name__ == "__main__":
    unittest.main()
