"""自动识别用到的「同值判断」单测：宁多写一条，也不要漏更新。"""

from __future__ import annotations

import unittest

from covenant.core.types import Observation, ObservationKind
from covenant.state.state import empty_shop, new_state
from covenant.ui.server import _same_as_state


class TestSameAsState(unittest.TestCase):
    def setUp(self):
        self.st = new_state(mode_id="mode_single_funny", gold=4, shop_slots=3)
        self.st.shop = empty_shop(3)
        self.st.shop[0].op_id = "op_a"
        self.st.shop[0].known = True

    def test_scalars(self):
        self.assertTrue(_same_as_state(self.st, Observation(ObservationKind.GOLD, 4)))
        self.assertFalse(_same_as_state(self.st, Observation(ObservationKind.GOLD, 5)))
        self.assertTrue(_same_as_state(self.st, Observation(ObservationKind.ROUND, 1)))
        self.assertFalse(_same_as_state(self.st, Observation(ObservationKind.ROUND, 2)))

    def test_shop_partial(self):
        same = Observation(ObservationKind.SHOP, [{"slot": 0, "op_id": "op_a", "known": True}])
        self.assertTrue(_same_as_state(self.st, same))
        other = Observation(ObservationKind.SHOP, [{"slot": 0, "op_id": "op_b", "known": True}])
        self.assertFalse(_same_as_state(self.st, other))

    def test_shop_unknown_never_matches(self):
        unknown = Observation(ObservationKind.SHOP, [{"slot": 1, "op_id": None, "known": False}])
        self.assertFalse(_same_as_state(self.st, unknown))

    def test_units(self):
        self.st.board = []
        empty = Observation(ObservationKind.BOARD, [])
        self.assertTrue(_same_as_state(self.st, empty))


class TestCommunityBridge(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from covenant.data.pack import load_latest
        from covenant.state.engine import Engine
        from covenant.ui.server import AppServer

        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(load_latest("packs"))
        self.engine.attach_stats(Path(self.tmp.name) / "matches.jsonl")
        self.app = AppServer(self.engine, var_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_view_empty(self):
        view = self.app.community_view()
        self.assertTrue(view["ok"])
        self.assertEqual(view["records"], 0)
        self.assertFalse(view["token_set"])

    def test_reject_bad_url(self):
        res = self.app.upload_matches("ftp://example.com")
        self.assertFalse(res["ok"])
        self.assertIn("http", res["error"])

    def test_remember_url(self):
        # 不管上传成不成，URL 都要记住（下次不用重填）
        res = self.app.upload_matches("http://127.0.0.1:1")
        self.assertIsInstance(res, dict)
        cfg = self.app._community_cfg()
        self.assertEqual(cfg.get("url"), "http://127.0.0.1:1")


class TestSettings(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        from covenant.data.pack import load_latest
        from covenant.state.engine import Engine
        from covenant.ui.server import AppServer

        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(load_latest("packs"))
        self.app = AppServer(self.engine, var_dir=Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        view = self.app.settings_view()
        self.assertEqual(view["settings"]["capture_interval"], 2.0)
        self.assertTrue(view["settings"]["auto_capture"])
        self.assertIn("pack", view["paths"])

    def test_connecting_source_starts_follow_and_resets_batch(self):
        from tests.test_services import FakeSource

        self.app.frame_source = FakeSource()
        try:
            self.assertTrue(self.app.capture_status()["on"])
            self.app.capture_service.batch = 8
            self.app.frame_source = FakeSource()
            self.assertLessEqual(self.app.capture_status()["batch"], 1)
        finally:
            self.app.stop_capture()

    def test_server_stop_also_stops_follow_thread(self):
        from tests.test_services import FakeSource

        self.app.frame_source = FakeSource()
        self.assertTrue(self.app.capture_status()["on"])
        self.app.stop()
        self.assertFalse(self.app.capture_status()["on"])

    def test_clamp_and_persist(self):
        self.app.save_settings({"capture_interval": 0.1, "auto_capture": True})
        self.assertEqual(self.app.settings_service.data["capture_interval"], 0.5)
        self.app.save_settings({"capture_interval": 99})
        self.assertEqual(self.app.settings_service.data["capture_interval"], 30.0)
        self.assertTrue(self.app.settings_service.path.exists())
        # 新实例从磁盘读回
        from covenant.ui.server import AppServer

        again = AppServer(self.engine, var_dir=self.app.settings_service.path.parent)
        self.assertEqual(again.settings["capture_interval"], 30.0)
        self.assertTrue(again.settings["auto_capture"])

    def test_reject_bad_type(self):
        with self.assertRaises(ValueError):
            self.app.save_settings({"capture_interval": "快一点"})
        with self.assertRaises(ValueError):
            self.app.save_settings({"auto_capture": "yes"})


class TestAutoFollowUI(unittest.TestCase):
    def test_follow_status_and_manual_correction_contract(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "covenant" / "ui" / "web"
        html = (root / "index.html").read_text(encoding="utf-8")
        js = (root / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="follow-status"', html)
        self.assertIn('id="follow-detail"', html)
        self.assertIn('id="follow-pending"', html)
        self.assertIn('id="manual-actions"', html)
        manual = html.split('id="manual-actions"', 1)[1].split("</details>", 1)[0]
        for button in ("btn-refresh", "btn-levelup", "btn-next"):
            self.assertIn(f'id="{button}"', manual)
        self.assertIn("CAPTURE.on && CAPTURE.source", js)
        self.assertIn("CAPTURE.pending", js)


if __name__ == "__main__":
    unittest.main()
