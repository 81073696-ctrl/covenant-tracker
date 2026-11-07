"""战绩记录 / 存储 / 聚合 / 引擎联动的测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from covenant.community.server import RecordBank
from covenant.core.types import Action, Unit
from covenant.data.pack import load_latest
from covenant.stats.aggregate import summarize, wilson_interval
from covenant.stats.model import RESULT_CLEAR, RESULT_FAIL, RESULT_QUIT, MatchRecord
from covenant.stats.store import MatchStore
from covenant.state.engine import Engine


def rec(
    rid: str,
    *,
    mode: str = "mode_single_funny",
    band: str | None = None,
    patch: str = "pack@S1",
    result: str = RESULT_CLEAR,
    board: tuple[str, ...] = ("op_a", "op_b"),
    bonds: dict[str, int] | None = None,
    rounds: int = 7,
    hp: int | None = 12,
    timeline: list[dict] | None = None,
) -> MatchRecord:
    return MatchRecord(
        id=rid,
        ts=1_700_000_000.0,
        mode_id=mode,
        band_id=band,
        band_name=band or "",
        patch=patch,
        rounds=rounds,
        hp_left=hp,
        result=result,
        board=[{"op_id": x, "star": 1} for x in board],
        bonds=dict(bonds or {}),
        timeline=list(timeline or []),
    )


class TestAggregate(unittest.TestCase):
    def test_pick_and_win_rate(self):
        rows = [
            rec("1", result=RESULT_CLEAR, board=("A", "B")),
            rec("2", result=RESULT_CLEAR, board=("A", "B")),
            rec("3", result=RESULT_FAIL, board=("A",)),
            rec("4", result=RESULT_FAIL, board=("A",)),
        ]
        out = summarize(rows, min_sample=1)
        self.assertEqual(out["totals"]["matches"], 4)
        self.assertEqual(out["totals"]["clears"], 2)
        self.assertAlmostEqual(out["totals"]["clear_rate"], 0.5)

        ops = {o["op_id"]: o for o in out["operators"]}
        self.assertEqual(ops["A"]["picks"], 4)
        self.assertAlmostEqual(ops["A"]["pick_rate"], 1.0)
        self.assertAlmostEqual(ops["A"]["win_rate"], 0.5)
        self.assertAlmostEqual(ops["A"]["delta"], 0.0)
        self.assertEqual(ops["B"]["picks"], 2)
        self.assertAlmostEqual(ops["B"]["pick_rate"], 0.5)
        self.assertAlmostEqual(ops["B"]["win_rate"], 1.0)
        self.assertAlmostEqual(ops["B"]["delta"], 0.5)

    def test_same_operator_twice_in_board_counts_once(self):
        out = summarize([rec("1", board=("A", "A", "B"))], min_sample=1)
        ops = {o["op_id"]: o for o in out["operators"]}
        self.assertEqual(ops["A"]["picks"], 1)

    def test_filters(self):
        rows = [
            rec("1", mode="m1", patch="p1"),
            rec("2", mode="m2", patch="p1"),
            rec("3", mode="m1", patch="p2"),
        ]
        self.assertEqual(summarize(rows, mode_id="m1")["totals"]["matches"], 2)
        self.assertEqual(summarize(rows, patch="p2")["totals"]["matches"], 1)
        self.assertEqual(summarize(rows, mode_id="m1", patch="p2")["totals"]["matches"], 1)
        self.assertEqual(summarize(rows, mode_id="nope")["totals"]["matches"], 0)

    def test_sample_flag_and_empty(self):
        out = summarize([rec("1"), rec("2")], min_sample=3)
        self.assertFalse(out["operators"][0]["sample_ok"])
        empty = summarize([], min_sample=5)
        self.assertEqual(empty["totals"]["matches"], 0)
        self.assertEqual(empty["operators"], [])
        self.assertAlmostEqual(empty["totals"]["clear_rate"], 0.0)

    def test_comps_need_two_bonds(self):
        one = summarize([rec("1", bonds={"yanShip": 3})], min_sample=1)
        self.assertEqual(one["comps"], [])
        two = summarize([rec("1", bonds={"yanShip": 3, "swiftShip": 2})], min_sample=1)
        self.assertEqual(len(two["comps"]), 1)
        self.assertIn("+", two["comps"][0]["name"])

    def test_wilson_interval(self):
        self.assertEqual(wilson_interval(0, 0), (0.0, 0.0))
        lo, hi = wilson_interval(10, 10)
        self.assertGreater(lo, 0.6)
        self.assertLessEqual(hi, 1.0)
        self.assertGreater(hi, 0.9)
        lo2, hi2 = wilson_interval(1, 50)
        self.assertLess(lo2, hi2)
        self.assertLess(lo2, 0.1)

    def test_quit_is_not_a_win(self):
        out = summarize([rec("1", result=RESULT_QUIT)], min_sample=1)
        self.assertEqual(out["totals"]["clears"], 0)

    def test_early_quit_is_excluded_but_preserved_in_raw_count(self):
        rows = [
            rec("early", result=RESULT_QUIT, rounds=1),
            rec("later", result=RESULT_QUIT, rounds=2),
            rec("clear", result=RESULT_CLEAR, rounds=3),
        ]
        out = summarize(rows, min_sample=1)
        self.assertEqual(out["totals"]["raw_matches"], 3)
        self.assertEqual(out["totals"]["matches"], 2)
        self.assertEqual(out["totals"]["excluded_early_quit"], 1)
        self.assertEqual(out["totals"]["clears"], 1)
        ops = {row["op_id"]: row for row in out["operators"]}
        self.assertEqual(ops["op_a"]["picks"], 2)


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "matches.jsonl"
        self.store = MatchStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_dedupes_by_id(self):
        a = rec("abc")
        self.assertTrue(self.store.append(a))
        self.assertFalse(self.store.append(a))
        self.assertEqual(self.store.count(), 1)

    def test_extend_counts(self):
        res = self.store.extend([rec("1"), rec("2"), rec("1")])
        self.assertEqual(res["added"], 2)
        self.assertEqual(res["skipped"], 1)
        self.assertEqual(res["total"], 2)

    def test_import_bad_lines(self):
        text = "\n".join(
            [
                json.dumps(rec("1").to_dict()),
                "{broken json",
                json.dumps({"id": "2", "result": "clear", "board": [{"op_id": "A"}]}),
            ]
        )
        res = self.store.import_jsonl(text)
        self.assertEqual(res["added"], 2)
        self.assertEqual(res["bad"], 1)

    def test_jsonl_roundtrip(self):
        self.store.extend([rec("1"), rec("2")])
        text = self.store.export_jsonl()
        other = MatchStore(Path(self.tmp.name) / "b.jsonl")
        self.assertEqual(other.import_jsonl(text)["added"], 2)

    def test_csv(self):
        self.store.extend([rec("1", result=RESULT_CLEAR, bonds={"yanShip": 3})])
        csv = self.store.export_csv()
        self.assertIn("结果", csv)
        self.assertIn("通关", csv)
        self.assertEqual(len(csv.strip().splitlines()), 2)

    def test_missing_file_is_empty(self):
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(len(self.store.load()), 0)


class TestCommunityValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bank = RecordBank(Path(self.tmp.name) / "community.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_rejects_invalid_result(self):
        raw = rec("bad-result").to_dict()
        raw["result"] = "not-a-result"
        res = self.bank.add([raw])
        self.assertEqual(res["added"], 0)
        self.assertEqual(res["bad"], 1)

    def test_rejects_missing_id(self):
        raw = rec("missing-id").to_dict()
        del raw["id"]
        res = self.bank.add([raw])
        self.assertEqual(res["added"], 0)
        self.assertEqual(res["bad"], 1)

    def test_accepts_valid_record(self):
        res = self.bank.add([rec("valid-record").to_dict()])
        self.assertEqual(res["added"], 1)
        self.assertEqual(res["bad"], 0)


class TestEngineStats(unittest.TestCase):
    def setUp(self):
        self.pack = load_latest("packs")
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = Engine(self.pack)
        self.engine.attach_stats(Path(self.tmp.name) / "matches.jsonl")
        first = next(iter(self.pack.available_operators(self.engine.mode)))
        self.op_id = getattr(first, "id", first)

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_current_board(self):
        self.engine.state.board.append(Unit(self.op_id, 1))
        self.engine.state.round_no = 6
        res = self.engine.record_match(RESULT_CLEAR, cups=3, note="测试")
        self.assertTrue(res["ok"])
        self.assertEqual(res["count"], 1)

        view = self.engine.stats_view(min_sample=1)
        self.assertEqual(view["totals"]["matches"], 1)
        self.assertAlmostEqual(view["totals"]["clear_rate"], 1.0)
        names = [o["op_id"] for o in view["operators"]]
        self.assertIn(self.op_id, names)
        self.assertEqual(view["operators"][0]["picks"], 1)

    def test_invalid_result(self):
        res = self.engine.record_match("victory")
        self.assertFalse(res["ok"])
        self.assertEqual(self.engine.stats_store.count(), 0)

    def test_import_and_export(self):
        text = self.engine.export_matches("jsonl")
        self.assertEqual(text.strip(), "")
        added = self.engine.import_matches(json.dumps(rec("x1").to_dict()))
        self.assertEqual(added["added"], 1)
        self.assertEqual(self.engine.stats_view()["totals"]["matches"], 1)
        csv = self.engine.export_matches("csv")
        self.assertIn("id", csv)


class TestPerRound(unittest.TestCase):
    def _timeline(self, *pairs: tuple[int, tuple[str, ...]]) -> list[dict]:
        return [
            {"round": rd, "shop_level": 1 + (rd // 3), "gold": 10 - rd, "hp": 20, "board": list(ids), "bench": []}
            for rd, ids in pairs
        ]

    def test_record_roundtrip_keeps_timeline(self):
        r = rec("t1", timeline=self._timeline((1, ("A",)), (2, ("A", "B"))))
        back = MatchRecord.from_dict(json.loads(json.dumps(r.to_dict())))
        self.assertEqual([x["round"] for x in back.timeline], [1, 2])
        self.assertEqual(back.timeline[1]["board"], ["A", "B"])

    def test_old_record_without_timeline_is_fine(self):
        d = rec("t2").to_dict()
        d.pop("timeline")
        back = MatchRecord.from_dict(d)
        self.assertEqual(back.timeline, [])

    def test_by_round_pick_rate(self):
        rows = [
            rec("r1", timeline=self._timeline((1, ("A",)), (2, ("A", "B")), (3, ("A", "B")))),
            rec("r2", timeline=self._timeline((1, ("B",)), (2, ("B",)))),
        ]
        out = summarize(rows, min_sample=1)
        buckets = {b["round"]: b for b in out["by_round"]}
        self.assertEqual(set(buckets), {1, 2, 3})
        self.assertEqual(buckets[1]["records"], 2)
        self.assertEqual(buckets[3]["records"], 1)  # 只有一局打到第 3 回合
        round2 = {o["op_id"]: o for o in buckets[2]["ops"]}
        self.assertAlmostEqual(round2["A"]["pick_rate"], 0.5)
        self.assertAlmostEqual(round2["B"]["pick_rate"], 1.0)
        self.assertAlmostEqual(buckets[2]["avg_board"], 1.5)

    def test_from_state_appends_final_round(self):
        pack = load_latest("packs")
        engine = Engine(pack)
        engine.state.board.append(Unit(next(iter(pack.available_operators(engine.mode))).id, 1))
        engine.act(Action("next_round", {}))
        r = MatchRecord.from_state(engine.state, pack, result=RESULT_FAIL)
        self.assertEqual([x["round"] for x in r.timeline], [1, 2])
        self.assertEqual(r.timeline[-1]["round"], engine.state.round_no)


class TestUIContract(unittest.TestCase):
    def test_refresh_confirmation_and_analysis_controls_exist(self):
        root = Path(__file__).resolve().parent.parent / "covenant" / "ui" / "web"
        html = (root / "index.html").read_text(encoding="utf-8")
        js = (root / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="btn-confirm-shop"', html)
        self.assertIn('id="refresh-history"', html)
        self.assertIn('id="refresh-note"', html)
        self.assertIn('act("set_shop"', js)
        self.assertIn("renderRefreshAnalysis()", js)
        self.assertIn("估计", js)
        self.assertIn("rules_version", js)
        self.assertIn("source_type", js)
        self.assertIn("refresh_type", js)
        self.assertIn("shop_prices", js)
        self.assertIn("free_refresh_chance_percent", js)
        self.assertIn("eco.refresh_cost", js)
        self.assertIn("装备", js)
        self.assertIn("联防", js)
        self.assertIn("excluded_early_quit", js)


if __name__ == "__main__":
    unittest.main()


class TestMatchIdIdempotency(unittest.TestCase):
    """终局幂等：同一局重复保存只留一条；旧记录（无 match_id）不受影响。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "matches.jsonl"
        self.store = MatchStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def _rec(self, rid, match_id, ts=1.0):
        return MatchRecord(id=rid, match_id=match_id, ts=ts, mode_id="m", result=RESULT_CLEAR)

    def test_same_match_id_written_once(self):
        self.assertTrue(self.store.append(self._rec("a1", "M1")))
        self.assertFalse(self.store.append(self._rec("a2", "M1")))
        self.assertEqual(self.store.count(), 1)
        self.assertEqual(self.store.find_by_match_id("M1").id, "a1")

    def test_legacy_record_without_match_id(self):
        self.store.append(MatchRecord(id="old1", ts=1.0, mode_id="m"))
        self.store.append(MatchRecord(id="old2", ts=2.0, mode_id="m"))
        self.assertEqual(self.store.count(), 2)

    def test_empty_match_id_rejected_by_lookup(self):
        with self.assertRaises(ValueError):
            self.store.find_by_match_id("")

    def test_rewrite_is_atomic_and_leaves_no_tmp(self):
        self.store.append(self._rec("a1", "M1"))
        self.store.append(self._rec("a2", "M2", ts=2.0))
        self.assertFalse(self.path.with_suffix(self.path.suffix + ".tmp").exists())
        lines = [x for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]
        self.assertEqual(len(lines), 2)

    def test_csv_has_match_id_column(self):
        self.store.append(self._rec("a1", "M1"))
        text = self.store.export_csv()
        self.assertIn("对局ID", text.splitlines()[0])
        self.assertIn("M1", text)
