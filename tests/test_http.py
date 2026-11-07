"""HTTP 细节单测：静态文件、快照缓存、CORS、端口探测。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from covenant.core.types import Action
from covenant.data.pack import load_latest
from covenant.state.engine import Engine
from covenant.ui.http import WEB_DIR, SnapshotCache, cors_origin, find_free_port, read_static

PACK = load_latest("packs")


class TestStatic(unittest.TestCase):
    def test_index_exists(self):
        found = read_static("index.html")
        self.assertIsNotNone(found)
        body, ctype = found
        self.assertGreater(len(body), 1000)
        self.assertIn("html", ctype)

    def test_traversal_rejected(self):
        self.assertIsNone(read_static("../server.py"))
        self.assertIsNone(read_static("nope.js"))

    def test_web_dir_points_at_ui_web(self):
        self.assertTrue((WEB_DIR / "app.js").is_file())


class TestSnapshotCache(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(PACK)
        self.cache = SnapshotCache()

    def test_reuses_body_until_revision_changes(self):
        body1, etag1 = self.cache.payload(self.engine)
        body2, etag2 = self.cache.payload(self.engine)
        self.assertIs(body1, body2)
        self.assertEqual(etag1, etag2)
        self.assertTrue(etag1.startswith('W/"'))

        self.engine.act(Action("set", {"path": "gold", "value": 7}))
        body3, etag3 = self.cache.payload(self.engine)
        self.assertNotEqual(etag1, etag3)
        self.assertIn(b'"gold": 7', body3)

    def test_pack_reload_invalidates(self):
        _, etag1 = self.cache.payload(self.engine)
        self.engine.pack = type(self.engine.pack).load(self.engine.pack.path)
        _, etag2 = self.cache.payload(self.engine)
        self.assertNotEqual(etag1, etag2)


class TestCors(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(host="127.0.0.1", port=8801)

    def test_same_port_allowed(self):
        h = {"Origin": "http://127.0.0.1:8801"}
        self.assertEqual(cors_origin(h, self.app), "http://127.0.0.1:8801")

    def test_other_port_rejected(self):
        h = {"Origin": "http://127.0.0.1:9999"}
        self.assertIsNone(cors_origin(h, self.app))

    def test_https_rejected(self):
        h = {"Origin": "https://127.0.0.1:8801"}
        self.assertIsNone(cors_origin(h, self.app))

    def test_missing_origin(self):
        self.assertIsNone(cors_origin({}, self.app))


class TestPort(unittest.TestCase):
    def test_find_free_port(self):
        port = find_free_port("127.0.0.1", 9200, tries=50)
        self.assertGreaterEqual(port, 9200)
        self.assertLess(port, 9250)


if __name__ == "__main__":
    unittest.main()
