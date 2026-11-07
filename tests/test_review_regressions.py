"""2026-11-07 代码审查所发现缺陷的回归测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from covenant.calc.economy import economy_view
from covenant.capture.base import Frame
from covenant.core.types import ObservationKind
from covenant.data.pack import load_latest
from covenant.state.engine import Engine
from covenant.state.events import LedgerError
from covenant.stats.store import MatchStore
from covenant.vision.pipeline import VisionPipeline
from covenant.vision.roi import Layout, Rect, Region

ROOT = Path(__file__).resolve().parents[1]


class TestRecordMatchConsistency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pack = load_latest(ROOT / "packs")
        self.eng = Engine(self.pack)
        self.eng.attach_storage(Path(self.tmp.name) / "engine.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_recording_same_match_twice_is_idempotent(self):
        first = self.eng.record_match("clear")
        second = self.eng.record_match("clear")

        self.assertTrue(second["ok"])
        self.assertEqual(second["record"]["id"], first["record"]["id"])
        self.assertEqual(self.eng.stats_store.count(), 1)
        ends = [e for e in self.eng.event_store.load() if e.type == "match_end"]
        self.assertEqual(len(ends), 1)
        self.assertEqual(ends[0].payload["record_id"], first["record"]["id"])

    def test_match_end_failure_rolls_back_record_and_pauses_engine(self):
        original_append = self.eng.event_store.append

        def fail_match_end(event):
            if event.type == "match_end":
                raise LedgerError("测试：终局事件写入失败")
            return original_append(event)

        self.eng.event_store.append = fail_match_end
        caught = None
        result = None
        try:
            result = self.eng.record_match("clear")
        except LedgerError as exc:
            caught = exc

        self.assertIsNone(caught, "账本异常应转成结构化失败，不应逃逸")
        self.assertFalse(result["ok"])
        self.assertEqual(self.eng.stats_store.count(), 0)
        self.assertFalse(any(e.type == "match_end" for e in self.eng.event_store.load()))
        self.assertIn("事件账本写入失败", self.eng._ledger_error)


class FakeIcons:
    min_sim = 0.6

    def classify(self, _patch):
        return "fake-op", 0.99


class TestUnknownShopLevel(unittest.TestCase):
    def test_unknown_level_skips_level_specific_shop_regions(self):
        layout = Layout(regions={
            "shop_low": Region(
                "shop_low", Rect(0, 0, 0.4, 0.4), "shop",
                params={"slots": 1, "levels": [1, 2, 3]},
            ),
            "shop_high": Region(
                "shop_high", Rect(0.5, 0, 0.4, 0.4), "shop",
                params={"slots": 1, "levels": [4, 5, 6]},
            ),
        })
        pipe = VisionPipeline(layout=layout, icons=FakeIcons(), results=FakeIcons())
        observations = pipe.read(Frame(image=np.zeros((100, 100, 3), dtype=np.uint8)))
        shops = [obs for obs in observations if obs.kind == ObservationKind.SHOP]
        self.assertEqual(shops, [])


class TestImportValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MatchStore(Path(self.tmp.name) / "matches.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_id_is_a_bad_row(self):
        result = self.store.import_jsonl("{}\n{}\n")
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["bad"], 2)
        self.assertEqual(self.store.count(), 0)

    def test_unknown_result_is_a_bad_row(self):
        line = json.dumps({"id": "bad-result", "result": "victory"}) + "\n"
        result = self.store.import_jsonl(line)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["bad"], 1)
        self.assertEqual(self.store.count(), 0)


class TestCumulativeUpgradeCost(unittest.TestCase):
    def test_decay_only_applies_to_first_immediate_upgrade(self):
        pack = load_latest(ROOT / "packs")
        eng = Engine(pack)
        eng.state.shop_level = 1
        eng.state.upgrade_decay = 2
        view = economy_view(pack, eng.state)
        mode = pack.mode(eng.state.mode_id)
        initial = [mode.rule(level).upgrade_price for level in range(1, pack.economy.max_shop_level)]
        expected = max(0, initial[0] - 2) + sum(initial[1:])

        self.assertEqual(view.plans[-1].cumulative_cost, expected)
        self.assertEqual(
            [plan.cost_now for plan in view.plans],
            [max(0, price - 2) if i == 0 else price for i, price in enumerate(initial)],
        )


if __name__ == "__main__":
    unittest.main()
