"""逐对局事件账本与恢复测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from covenant.core.types import Action
from covenant.data.pack import load_latest
from covenant.state.events import EventStore, LedgerError, MatchEvent, replay_events
from covenant.state.state import new_state


class TestEvents(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "match.jsonl"
        self.store = EventStore(self.path)
        self.pack = load_latest("packs")
        self.state = new_state(mode_id=self.pack.mode().id, gold=4, shop_slots=3)

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, seq=1, kind="match_start", payload=None):
        return MatchEvent.create(
            match_id=self.state.match_id,
            seq=seq,
            round_no=self.state.round_no,
            phase=self.state.phase,
            type=kind,
            payload=payload if payload is not None else {"state": self.state.to_dict()},
        )

    def test_roundtrip_and_duplicate(self):
        event = self.event()
        self.assertTrue(self.store.append(event))
        self.assertFalse(self.store.append(event))
        self.assertEqual(self.store.load(), [event])

    def test_conflicting_sequence_is_rejected(self):
        self.store.append(self.event())
        with self.assertRaises(LedgerError):
            self.store.append(self.event(payload={"state": {"different": True}}))

    def test_corruption_is_not_silently_discarded(self):
        self.path.write_text("{broken\n", encoding="utf-8")
        with self.assertRaises(LedgerError):
            self.store.load()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken\n")

    def test_replay_uses_reducer(self):
        events = [self.event()]
        action = Action("set", {"path": "gold", "value": 7})
        events.append(MatchEvent.create(
            match_id=self.state.match_id, seq=2, round_no=1, phase="prep",
            type="set", payload={"action": action.to_dict()},
        ))
        restored = replay_events(self.pack, events)
        self.assertEqual(restored.gold, 7)
        self.assertEqual(restored.match_id, self.state.match_id)

    def test_replay_preserves_legacy_next_round_beyond_limit(self):
        max_rounds = self.pack.mode(self.state.mode_id).rounds
        self.state.round_no = max_rounds
        action = Action("next_round")
        events = [self.event(), MatchEvent.create(
            match_id=self.state.match_id, seq=2, round_no=max_rounds + 1, phase="prep",
            type=action.type, payload={"action": action.to_dict()},
        )]
        restored = replay_events(self.pack, events)
        self.assertEqual(restored.round_no, max_rounds + 1)

    def test_replay_preserves_legacy_manual_round_beyond_limit(self):
        max_rounds = self.pack.mode(self.state.mode_id).rounds
        action = Action("set", {"path": "round_no", "value": max_rounds + 1})
        events = [self.event(), MatchEvent.create(
            match_id=self.state.match_id, seq=2, round_no=max_rounds + 1, phase="prep",
            type=action.type, payload={"action": action.to_dict()},
        )]
        restored = replay_events(self.pack, events)
        self.assertEqual(restored.round_no, max_rounds + 1)

    def test_replay_preserves_legacy_round_observation_beyond_limit(self):
        from covenant.core.types import Observation
        max_rounds = self.pack.mode(self.state.mode_id).rounds
        obs = Observation("round", max_rounds + 1, confidence=1.0, source="vision")
        events = [self.event(), MatchEvent.create(
            match_id=self.state.match_id, seq=2, round_no=max_rounds + 1, phase="prep",
            type="observation", payload={"observation": obs.to_dict()},
        )]
        restored = replay_events(self.pack, events)
        self.assertEqual(restored.round_no, max_rounds + 1)

    def test_engine_recovers_from_events(self):
        from covenant.state.engine import Engine
        root = Path(self.tmp.name)
        eng = Engine(self.pack)
        eng.attach_storage(root / "engine.json")
        self.assertTrue(eng.act(Action("set", {"path": "gold", "value": 8}))["ok"])
        match_id = eng.state.match_id
        restored = Engine(self.pack)
        restored.attach_storage(root / "engine.json")
        self.assertEqual(restored.state.gold, 8)
        self.assertEqual(restored.state.match_id, match_id)

    def test_old_snapshot_migrates_without_fake_refresh(self):
        from covenant.state.engine import Engine
        root = Path(self.tmp.name)
        old = new_state(mode_id=self.pack.mode().id, gold=9, shop_slots=3)
        (root / "engine.json").write_text(json.dumps(old.to_dict()), encoding="utf-8")
        eng = Engine(self.pack)
        eng.attach_storage(root / "engine.json")
        events = eng.event_store.load()
        self.assertEqual([e.type for e in events], ["match_start"])
        self.assertEqual(eng.state.gold, 9)

    def test_reset_starts_a_new_ledger(self):
        from covenant.state.engine import Engine
        eng = Engine(self.pack)
        eng.attach_storage(Path(self.tmp.name) / "engine.json")
        old_id = eng.state.match_id
        eng.act(Action("reset", {"mode_id": eng.state.mode_id}))
        self.assertNotEqual(eng.state.match_id, old_id)
        self.assertEqual([e.type for e in eng.event_store.load()], ["match_start"])

    def test_snapshot_exposes_ledger_status(self):
        from covenant.state.engine import Engine
        eng = Engine(self.pack)
        eng.attach_storage(Path(self.tmp.name) / "engine.json")
        snap = eng.snapshot()
        ledger = snap["ledger"]
        self.assertEqual(ledger["match_id"], eng.state.match_id)
        self.assertEqual(ledger["events"], 1)
        self.assertIsNone(ledger["error"])
        self.assertEqual(snap["refresh_analysis"]["totals"]["refreshes"], 0)
        self.assertEqual(snap["refresh_analysis"]["source"], "process_events")

    def test_record_match_appends_match_end(self):
        from covenant.state.engine import Engine
        eng = Engine(self.pack)
        eng.attach_storage(Path(self.tmp.name) / "engine.json")
        result = eng.record_match("clear")
        self.assertTrue(result["ok"])
        events = eng.event_store.load()
        self.assertEqual(events[-1].type, "match_end")
        self.assertEqual(events[-1].payload["record_id"], result["record"]["id"])

    def test_observed_refresh_is_audit_only_and_replayable(self):
        from covenant.state.engine import Engine
        from covenant.stats.refreshes import refresh_history

        eng = Engine(self.pack)
        eng.attach_storage(Path(self.tmp.name) / "engine.json")
        before = eng.state.to_dict()
        result = eng.record_observed_refresh(
            expected_revision=eng.revision,
            gold_before=4,
            gold_after=3,
            shop=[{"slot": 0, "op_id": "A", "known": True}],
            evidence="stable_shop_and_gold",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(eng.state.to_dict(), before)
        events = eng.event_store.load()
        self.assertEqual(events[-1].type, "refresh_observed")
        self.assertEqual(eng.state.gold, 4)
        restored = replay_events(self.pack, events)
        self.assertEqual(restored.to_dict(), before)
        history = refresh_history(events)
        self.assertTrue(history[-1]["confirmed"])
        self.assertEqual(history[-1]["source"], "vision")
        self.assertEqual(history[-1]["refresh_type"], "normal")
        self.assertEqual(history[-1]["refresh_source"], "standard")
        stale = eng.record_observed_refresh(
            expected_revision=eng.revision - 1,
            gold_before=4,
            gold_after=3,
            shop=[{"slot": 0, "op_id": "A", "known": True}],
            evidence="stale",
        )
        self.assertFalse(stale["ok"])
        self.assertEqual(len(eng.event_store.load()), len(events))

    def test_refresh_sessions_and_observed_rates(self):
        from covenant.state.engine import Engine
        from covenant.stats.refreshes import refresh_history, summarize_refreshes
        eng = Engine(self.pack)
        eng.attach_storage(Path(self.tmp.name) / "engine.json")
        eng.act(Action("set", {"path": "gold", "value": 10}))
        eng.act(Action("refresh"))
        eng.act(Action("set_shop", {"offers": [{"op_id": "A"}, {"op_id": "A"}, {"op_id": "B"}]}))
        eng.act(Action("refresh"))
        events = eng.event_store.load()
        history = refresh_history(events)
        self.assertEqual(len(history), 2)
        self.assertTrue(history[0]["confirmed"])
        self.assertFalse(history[1]["confirmed"])
        self.assertEqual(history[0]["gold_before"], 10)
        self.assertEqual(history[0]["gold_after"], 9)
        out = summarize_refreshes(events)
        self.assertEqual(out["totals"]["refreshes"], 1)
        self.assertEqual(out["totals"]["unconfirmed"], 1)
        ops = {row["op_id"]: row for row in out["operators"]}
        self.assertEqual(ops["A"]["hits"], 1)
        self.assertEqual(ops["A"]["appearances"], 2)
        self.assertEqual(ops["A"]["hit_rate"], 1.0)
        self.assertEqual(ops["A"]["avg_cost_per_hit"], 1.0)
        self.assertEqual(out["source"], "process_events")
        self.assertIn("A", out["by_op"])

    def test_refresh_types_and_unknown_changes_are_audited(self):
        from covenant.state.engine import Engine
        from covenant.stats.refreshes import refresh_history, summarize_refreshes

        eng = Engine(self.pack)
        eng.attach_storage(Path(self.tmp.name) / "types.json")
        shop = [{"slot": 0, "op_id": "A", "known": True}]
        cases = [
            (3, "normal", "standard", "refresh_observed"),
            (4, "free", "miraShip", "refresh_observed"),
            (3, "special", "band_orchid", "refresh_observed"),
            (4, "unknown", "unknown", "shop_change_observed"),
        ]
        for gold_after, kind, source, event_type in cases:
            result = eng.record_observed_refresh(
                expected_revision=eng.revision,
                gold_before=4,
                gold_after=gold_after,
                shop=shop,
                evidence="complete_frame",
                refresh_type=kind,
                refresh_source=source,
                attribution="test_explicit",
            )
            self.assertTrue(result["ok"], result)
            self.assertEqual(eng.event_store.load()[-1].type, event_type)
        before = eng.state.to_dict()
        restored = replay_events(self.pack, eng.event_store.load())
        self.assertEqual(restored.to_dict(), before)

        history = refresh_history(eng.event_store.load())
        self.assertEqual([item["refresh_type"] for item in history], [
            "normal", "free", "special", "unknown",
        ])
        out = summarize_refreshes(eng.event_store.load())
        self.assertEqual(out["totals"]["refreshes"], 3)
        self.assertEqual(out["totals"]["unattributed"], 1)
        self.assertEqual(out["totals"]["by_type"], {"normal": 1, "free": 1, "special": 1})
        self.assertEqual(out["by_op"]["A"]["hits"], 3)
        self.assertEqual(out["by_op"]["A"]["refreshes"], 3)

    def test_manual_free_refresh_does_not_charge_gold(self):
        from covenant.state.engine import Engine

        eng = Engine(self.pack)
        eng.attach_storage(Path(self.tmp.name) / "free.json")
        before = eng.state.gold
        result = eng.act(Action("refresh", {"refresh_type": "free"}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(eng.state.gold, before)
        event = eng.event_store.load()[-1]
        self.assertEqual(event.payload["refresh_type"], "free")
        self.assertEqual(event.payload["refresh_source"], "manual")

    def test_duyaoye_only_first_two_refreshes_are_special(self):
        from covenant.state.engine import Engine

        eng = Engine(self.pack)
        eng.state.band_id = "band_duyaoy"
        eng.state.gold = 20
        eng.attach_storage(Path(self.tmp.name) / "duyaoye.json")
        for _ in range(3):
            self.assertTrue(eng.act(Action("refresh"))["ok"])
        events = [e for e in eng.event_store.load() if e.type == "refresh"]
        self.assertEqual([e.payload["refresh_type"] for e in events], [
            "special", "special", "normal",
        ])
        eng.act(Action("next_round"))
        eng.act(Action("refresh"))
        self.assertEqual(eng.event_store.load()[-1].payload["refresh_type"], "special")

    def test_pepe_upgrade_grants_one_special_refresh(self):
        from covenant.state.engine import Engine

        eng = Engine(self.pack)
        eng.state.band_id = "band_pepe"
        eng.state.gold = 20
        eng.attach_storage(Path(self.tmp.name) / "pepe.json")
        self.assertTrue(eng.act(Action("level_up"))["ok"])
        self.assertEqual(eng.state.rules.get("special_refresh_charges"), 1)
        eng.act(Action("refresh"))
        self.assertEqual(eng.event_store.load()[-1].payload["refresh_type"], "special")
        self.assertEqual(eng.state.rules.get("special_refresh_charges"), 0)
        eng.act(Action("refresh"))
        self.assertEqual(eng.event_store.load()[-1].payload["refresh_type"], "normal")

    def test_refresh_filters_and_empty(self):
        from covenant.state.engine import Engine
        from covenant.stats.refreshes import summarize_refreshes
        eng = Engine(self.pack)
        eng.attach_storage(Path(self.tmp.name) / "engine.json")
        eng.act(Action("set", {"path": "shop_level", "value": 2}))
        eng.act(Action("refresh"))
        eng.act(Action("set_shop", {"offers": [{"op_id": "A"}]}))
        events = eng.event_store.load()
        self.assertEqual(summarize_refreshes(events, shop_level=2)["totals"]["refreshes"], 1)
        self.assertEqual(summarize_refreshes(events, shop_level=3)["totals"]["refreshes"], 0)
        empty = summarize_refreshes([])
        self.assertEqual(empty["totals"]["refreshes"], 0)
        self.assertEqual(empty["operators"], [])
        self.assertTrue(any("样本" in note for note in empty["notes"]))


if __name__ == "__main__":
    unittest.main()
