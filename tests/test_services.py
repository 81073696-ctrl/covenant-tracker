"""服务层单测：采集线程、设置读写、社区配置。"""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

import numpy as np

from covenant.capture.base import Frame
from covenant.data.pack import load_latest
from covenant.services.capture import CaptureService
from covenant.services.community import CommunityService
from covenant.services.matches import MatchService
from covenant.services.settings import SettingsService
from covenant.state.engine import Engine

PACK = load_latest("packs")


class FakeSource:
    def __init__(self) -> None:
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        return Frame(np.zeros((16, 16, 3), dtype=np.uint8))

    def png(self) -> bytes:
        return b"not-really-png"

    def title(self) -> str:
        return "fake-window"


class FakeVision:
    def __init__(self) -> None:
        self.reads = 0
        self.items = []

    def read(self, frame):
        self.reads += 1
        return list(self.items)


class TestCaptureService(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(PACK)
        self.vision = FakeVision()
        self.service = CaptureService(self.engine, self.vision)

    def test_start_without_source_raises(self):
        with self.assertRaises(RuntimeError):
            self.service.start(1.0)

    def test_grab_png_without_source(self):
        body, code = self.service.grab_png()
        self.assertEqual(code, 503)
        self.assertEqual(body, b"")

    def test_start_reads_and_stops(self):
        self.service.source = FakeSource()
        status = self.service.start(0.5)
        self.assertTrue(status["on"])
        time.sleep(0.9)
        self.service.stop()
        self.assertFalse(self.service.on)
        self.assertEqual(self.service.status()["state"], "paused")
        self.assertGreaterEqual(self.service.reads, 1)
        self.assertEqual(self.service.error, None)

    def test_grab_caches_last_frame(self):
        self.service.source = FakeSource()
        frame = self.service.grab()
        self.assertIs(self.service.last_frame, frame)

    def test_stable_high_confidence_observation_syncs_on_second_frame(self):
        from covenant.core.types import Observation, ObservationKind

        self.vision.items = [Observation(ObservationKind.GOLD, 9, confidence=0.99)]
        self.service.source = FakeSource()
        first = self.service.read()
        self.assertEqual(first["state"], "stabilizing")
        self.assertEqual(self.engine.state.gold, PACK.economy.income(1))
        second = self.service.read()
        self.assertEqual(self.engine.state.gold, 9)
        self.assertEqual(second["batch"], 2)
        self.assertEqual(second["state"], "following")

    def test_low_confidence_does_not_write(self):
        from covenant.core.types import Observation, ObservationKind

        before = self.engine.state.gold
        self.vision.items = [Observation(ObservationKind.GOLD, 0, confidence=0.80)]
        self.service.source = FakeSource()
        self.service.read()
        self.service.read()
        self.assertEqual(self.engine.state.gold, before)
        self.assertEqual(self.service.status()["pending"][0]["kind"], ObservationKind.GOLD)

    def test_scene_change_requires_new_stable_candidate(self):
        from covenant.core.types import Observation, ObservationKind

        self.vision.items = [Observation(ObservationKind.GOLD, 9, confidence=0.99)]
        self.service.source = FakeSource()
        self.service.read()
        self.vision.items = [
            Observation(ObservationKind.SCENE, "battle", confidence=0.99),
            Observation(ObservationKind.GOLD, 9, confidence=0.99),
        ]
        result = self.service.read()
        self.assertEqual(result["state"], "stabilizing")
        self.assertNotEqual(self.engine.state.gold, 9)
        self.service.read()
        self.assertEqual(self.engine.state.gold, 9)

    def test_stable_refresh_is_recorded_once_without_extra_charge(self):
        from covenant.core.types import Observation, ObservationKind

        with tempfile.TemporaryDirectory() as tmp:
            self.engine.attach_storage(Path(tmp) / "engine.json")
            self.service.source = FakeSource()

            def frame(gold, op_id):
                shop = [
                    {"slot": slot, "op_id": op_id if slot == 0 else f"op_{slot}", "known": True}
                    for slot in range(3)
                ]
                self.vision.items = [
                    Observation(ObservationKind.GOLD, gold, confidence=0.99),
                    Observation(ObservationKind.SHOP, shop, confidence=0.99),
                ]

            frame(4, "op_a")
            self.service.read()
            self.service.read()
            frame(3, "op_b")
            self.service.read()
            self.service.read()
            self.service.read()
            events = self.engine.event_store.load()
            self.assertEqual(len([e for e in events if e.type == "refresh_observed"]), 1)
            self.assertEqual(self.engine.state.gold, 3)
            self.assertEqual(self.engine.state.shop[0].op_id, "op_b")

    def test_gold_and_shop_on_different_frames_still_record_refresh(self):
        from covenant.core.types import Observation, ObservationKind

        with tempfile.TemporaryDirectory() as tmp:
            self.engine.attach_storage(Path(tmp) / "engine.json")
            self.service.source = FakeSource()

            def shop(op_id):
                return Observation(ObservationKind.SHOP, [
                    {"slot": slot, "op_id": op_id if slot == 0 else f"op_{slot}", "known": True}
                    for slot in range(3)
                ], confidence=0.99)

            self.vision.items = [Observation(ObservationKind.GOLD, 4, confidence=0.99), shop("op_a")]
            self.service.read(only_changed=True)
            self.service.read(only_changed=True)
            self.vision.items = [Observation(ObservationKind.GOLD, 3, confidence=0.99)]
            self.service.read(only_changed=True)
            self.service.read(only_changed=True)
            self.vision.items = [shop("op_b")]
            self.service.read(only_changed=True)
            self.service.read(only_changed=True)
            events = [e for e in self.engine.event_store.load() if e.type == "refresh_observed"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].payload["gold_before"], 4)
            self.assertEqual(events[0].payload["gold_after"], 3)

    def test_paid_refresh_from_partially_sold_out_shop_is_recorded(self):
        from covenant.core.types import Observation, ObservationKind

        with tempfile.TemporaryDirectory() as tmp:
            self.engine.attach_storage(Path(tmp) / "engine.json")
            self.service.source = FakeSource()
            sold_out = Observation(ObservationKind.SHOP, [
                {"slot": 0, "op_id": None, "known": False},
                {"slot": 1, "op_id": "op_b", "known": True},
                {"slot": 2, "op_id": "op_c", "known": True},
            ], confidence=0.99)
            self.vision.items = [Observation(ObservationKind.GOLD, 4, confidence=0.99), sold_out]
            self.service.read()
            self.service.read()
            new_shop = Observation(ObservationKind.SHOP, [
                {"slot": slot, "op_id": f"new_{slot}", "known": True}
                for slot in range(3)
            ], confidence=0.99)
            self.vision.items = [Observation(ObservationKind.GOLD, 3, confidence=0.99), new_shop]
            self.service.read()
            self.service.read()
            events = [e for e in self.engine.event_store.load() if e.type == "refresh_observed"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].payload["gold_before"], 4)
            self.assertEqual(events[0].payload["gold_after"], 3)

    def test_zero_cost_shop_change_is_unattributed_not_normal_refresh(self):
        from covenant.core.types import Observation, ObservationKind

        with tempfile.TemporaryDirectory() as tmp:
            self.engine.attach_storage(Path(tmp) / "engine.json")
            self.service.source = FakeSource()

            def frame(op_id):
                shop = [
                    {"slot": slot, "op_id": op_id if slot == 0 else f"op_{slot}", "known": True}
                    for slot in range(3)
                ]
                self.vision.items = [
                    Observation(ObservationKind.GOLD, 4, confidence=0.99),
                    Observation(ObservationKind.SHOP, shop, confidence=0.99),
                ]

            frame("op_a")
            self.service.read()
            self.service.read()
            frame("op_b")
            self.service.read()
            self.service.read()
            self.service.read()
            events = self.engine.event_store.load()
            changes = [e for e in events if e.type == "shop_change_observed"]
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0].payload["refresh_type"], "unknown")
            self.assertEqual(changes[0].payload["refresh_source"], "unknown")
            self.assertFalse([e for e in events if e.type == "refresh_observed"])

    def test_special_strategy_refresh_is_labelled_as_inferred(self):
        from covenant.core.types import Observation, ObservationKind

        with tempfile.TemporaryDirectory() as tmp:
            self.engine.state.band_id = "band_orchid"
            self.engine.attach_storage(Path(tmp) / "engine.json")
            self.service.source = FakeSource()

            def frame(gold, op_id):
                shop = [
                    {"slot": slot, "op_id": op_id if slot == 0 else f"op_{slot}", "known": True}
                    for slot in range(3)
                ]
                self.vision.items = [
                    Observation(ObservationKind.GOLD, gold, confidence=0.99),
                    Observation(ObservationKind.SHOP, shop, confidence=0.99),
                ]

            frame(4, "op_a")
            self.service.read()
            self.service.read()
            frame(3, "op_b")
            self.service.read()
            self.service.read()
            events = [e for e in self.engine.event_store.load() if e.type == "refresh_observed"]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].payload["refresh_type"], "special")
            self.assertEqual(events[0].payload["refresh_source"], "band_orchid")
            self.assertEqual(events[0].payload["attribution"], "strategy_inferred")

    def test_pepe_paid_shop_change_is_not_guessed_from_manual_charge_count(self):
        from covenant.core.types import Observation, ObservationKind

        with tempfile.TemporaryDirectory() as tmp:
            self.engine.state.band_id = "band_pepe"
            self.engine.state.rules["special_refresh_charges"] = 1
            self.engine.attach_storage(Path(tmp) / "engine.json")
            self.service.source = FakeSource()

            def frame(gold, op_id):
                shop = [
                    {"slot": slot, "op_id": op_id if slot == 0 else f"op_{slot}", "known": True}
                    for slot in range(3)
                ]
                self.vision.items = [
                    Observation(ObservationKind.GOLD, gold, confidence=0.99),
                    Observation(ObservationKind.SHOP, shop, confidence=0.99),
                ]

            frame(4, "op_a")
            self.service.read()
            self.service.read()
            frame(3, "op_b")
            self.service.read()
            self.service.read()
            events = self.engine.event_store.load()
            changes = [e for e in events if e.type == "shop_change_observed"]
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0].payload["refresh_type"], "unknown")
            self.assertEqual(changes[0].payload["gold_before"], 4)
            self.assertEqual(changes[0].payload["gold_after"], 3)
            self.assertFalse([e for e in events if e.type == "refresh_observed"])

    def test_gold_drop_with_unit_change_is_not_called_refresh(self):
        from covenant.core.types import Observation, ObservationKind

        with tempfile.TemporaryDirectory() as tmp:
            self.engine.attach_storage(Path(tmp) / "engine.json")
            self.service.source = FakeSource()
            self.vision.items = [Observation(ObservationKind.GOLD, 4, confidence=0.99)]
            self.service.read()
            self.service.read()
            self.vision.items = [
                Observation(ObservationKind.GOLD, 3, confidence=0.99),
                Observation(ObservationKind.BENCH, [{"slot": 0, "op_id": "op_a", "star": 1}], confidence=0.99),
            ]
            self.service.read()
            result = self.service.read()
            self.assertFalse([e for e in self.engine.event_store.load() if e.type == "refresh_observed"])
            self.assertIn("refresh", [item["kind"] for item in result["pending"]])


class TestSettingsService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "settings.json"
        self.service = SettingsService(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_defaults(self):
        self.assertEqual(self.service.data["capture_interval"], 2.0)
        self.assertTrue(self.service.data["auto_capture"])

    def test_clamp_and_reload(self):
        self.service.save({"capture_interval": 100, "auto_capture": True})
        again = SettingsService(self.path)
        self.assertEqual(again.data["capture_interval"], 30.0)
        self.assertTrue(again.data["auto_capture"])

    def test_reject_bad_type(self):
        with self.assertRaises(ValueError):
            self.service.save({"capture_interval": "快一点"})
        with self.assertRaises(ValueError):
            self.service.save({"auto_capture": 1})


class TestCommunityService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(PACK)
        self.engine.attach_stats(Path(self.tmp.name) / "matches.jsonl")
        self.service = CommunityService(self.engine, Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_empty_view(self):
        view = self.service.view()
        self.assertTrue(view["ok"])
        self.assertEqual(view["records"], 0)

    def test_reject_bad_url(self):
        res = self.service.upload("ftp://example.com")
        self.assertFalse(res["ok"])
        self.assertIn("http", res["error"])

    def test_remembers_url(self):
        self.service.upload("http://127.0.0.1:1")
        self.assertEqual(self.service.config().get("url"), "http://127.0.0.1:1")


class TestMatchService(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store_path = Path(self.tmp.name) / "matches.jsonl"
        self.service = MatchService(self.store_path)
        self.engine = Engine(PACK)
        self.engine.attach_stats(self.store_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_rolls_back_when_ledger_fails(self):
        def boom(rec):
            raise RuntimeError("ledger down")

        with self.assertRaises(RuntimeError):
            self.service.record(self.engine.state, PACK, result="clear", ledger=boom)
        self.assertEqual(self.service.store.count(), 0)

    def test_record_then_view(self):
        self.service.record(self.engine.state, PACK, result="clear", cups=3)
        view = self.service.view(PACK, min_sample=1)
        self.assertEqual(view["totals"]["matches"], 1)
        self.assertEqual(view["totals"]["clears"], 1)

    def test_bad_result_rejected(self):
        with self.assertRaises(ValueError):
            self.service.record(self.engine.state, PACK, result="victory")


if __name__ == "__main__":
    unittest.main()
