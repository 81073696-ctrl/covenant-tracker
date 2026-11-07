"""Kimi 2026-11-07 代码审查里 5 个最小复现的回归版。

这些用例只断言「当时的坏行为不会回来」，不改产品代码；
来源：`deepseek-original-code-review-2026-11-07.md` + `original-project-review/repro_bugs.py`。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from covenant.calc.economy import economy_view
from covenant.capture.base import Frame
from covenant.core.types import Action, ActionType, Observation, ObservationKind
from covenant.data.pack import load_latest
from covenant.state.engine import Engine
from covenant.state.events import LedgerError
from covenant.stats.store import MatchStore
from covenant.vision.pipeline import VisionPipeline
from covenant.vision.roi import Layout, Rect, Region

PACK = load_latest("packs")


def _engine(tmp: Path) -> Engine:
    eng = Engine(PACK)
    eng.attach_storage(tmp / "engine.json")
    return eng


class TestMatchRecordIdempotent(unittest.TestCase):
    """2.1 同一局连点两次「记录本局」：只能有一条战绩、一条 match_end。"""

    def test_second_save_is_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = _engine(Path(tmp))
            first = eng.record_match("clear")
            second = eng.record_match("clear")

            self.assertTrue(first["ok"])
            self.assertTrue(second["ok"])
            self.assertEqual(eng.stats_store.count(), 1)
            self.assertEqual(first["record"]["id"], second["record"]["id"])

            if eng.event_store is not None:
                ends = [e for e in eng.event_store.load() if e.type == "match_end"]
                self.assertEqual(len(ends), 1)
                stored = {r.id for r in eng.stats_store.load()}
                self.assertIn(ends[0].payload["record_id"], stored)


class TestRecordMatchLedgerFailure(unittest.TestCase):
    """2.2 战绩已写、match_end 写失败：不能逃逸异常，也不能留下半条战绩。"""

    def test_failure_is_contained(self):
        with tempfile.TemporaryDirectory() as tmp:
            eng = _engine(Path(tmp))
            original = eng.event_store.append

            def fail_match_end(event):
                if event.type == "match_end":
                    raise LedgerError("复现：模拟 match_end 写入失败")
                return original(event)

            eng.event_store.append = fail_match_end
            result = eng.record_match("clear")

            self.assertFalse(result["ok"])
            self.assertIn("账本", result["error"])
            self.assertEqual(eng.stats_store.count(), 0)
            self.assertTrue(eng._ledger_error)
            self.assertEqual(
                sum(1 for e in eng.event_store.load() if e.type == "match_end"), 0
            )
            # 账本坏了之后，再记录也要拒绝而不是半写
            again = eng.record_match("clear")
            self.assertFalse(again["ok"])


class TestUnknownShopLevel(unittest.TestCase):
    """2.3 商店等级 OCR 不出来时，不能把互斥布局全读一遍。"""

    class _FakeIcons:
        min_sim = 0.6

        def classify(self, _patch):
            return "fake-op", 0.99

    def test_no_shop_observations_without_level(self):
        layout = Layout(
            regions={
                "shop_low": Region(
                    "shop_low",
                    Rect(0, 0, 0.4, 0.4),
                    "shop",
                    params={"slots": 1, "levels": [1, 2, 3]},
                ),
                "shop_high": Region(
                    "shop_high",
                    Rect(0.5, 0, 0.4, 0.4),
                    "shop",
                    params={"slots": 1, "levels": [4, 5, 6]},
                ),
            }
        )
        pipe = VisionPipeline(
            layout=layout,
            icons=self._FakeIcons(),
            results=self._FakeIcons(),
        )
        frame = Frame(image=np.zeros((100, 100, 3), dtype=np.uint8))
        shops = [o for o in pipe.read(frame) if o.kind == ObservationKind.SHOP]
        self.assertEqual(shops, [])


class TestImportValidation(unittest.TestCase):
    """2.4 外部导入必须校验：缺 id 不能随机补，非法 result 不能静默改 fail。"""

    def test_reject_empty_and_bad_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MatchStore(Path(tmp) / "matches.jsonl")
            first = store.import_jsonl("{}\n")
            second = store.import_jsonl("{}\n")
            for res in (first, second):
                self.assertEqual(res["added"], 0)
                self.assertEqual(res["bad"], 1)

            bad = store.import_jsonl(
                json.dumps({"id": "invalid-result", "result": "victory"}) + "\n"
            )
            self.assertEqual(bad["added"], 0)
            self.assertEqual(bad["bad"], 1)
            self.assertEqual(store.load(), [])


class TestCumulativeUpgradeCost(unittest.TestCase):
    """2.5 当前等级已降价 2 点时，连升总价只能让第一级享受这次降价。"""

    def test_decay_applies_once(self):
        eng = Engine(PACK)
        eng.state.shop_level = 1
        eng.state.upgrade_decay = 2
        view = economy_view(PACK, eng.state)

        mode = PACK.mode(eng.state.mode_id)
        initial = [
            mode.rule(level).upgrade_price
            for level in range(1, PACK.economy.max_shop_level)
        ]
        expected = max(0, initial[0] - 2) + sum(initial[1:])
        self.assertEqual(view.plans[-1].cumulative_cost, expected)
        # 单级「现在升」仍享受当前降价
        self.assertEqual(view.plans[0].cost_now, max(0, initial[0] - 2))


class TestMalformedInputs(unittest.TestCase):
    """fuzz 2026-11-07 抓到的三类脏输入：观测、动作、区域名。"""

    def test_shop_observation_non_list_is_ignored(self):
        eng = Engine(PACK)
        before = [o.op_id for o in eng.state.shop]
        out = eng.observe(Observation(ObservationKind.SHOP, 5))
        self.assertTrue(out["ok"])
        self.assertEqual([o.op_id for o in eng.state.shop], before)

    def test_add_unit_rejects_unknown_operator(self):
        eng = Engine(PACK)
        out = eng.act(Action(ActionType.ADD_UNIT, {"op_id": "nope", "star": 1}))
        self.assertFalse(out["ok"])
        self.assertIn("未知干员", out["error"])

    def test_move_rejects_unknown_zone(self):
        eng = Engine(PACK)
        out = eng.act(
            Action(
                ActionType.MOVE,
                {"from_zone": "zzz", "from_slot": 0, "to_zone": "board"},
            )
        )
        self.assertFalse(out["ok"])
        self.assertIn("未知区域", out["error"])


if __name__ == "__main__":
    unittest.main()
