"""核心逻辑测试：数据包、卡池、概率、经济、状态机。

跑法（项目根目录）::

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from pathlib import Path

from covenant.calc.economy import economy_view
from covenant.calc.odds import prob_at_least_one, refreshes_for_confidence, shop_odds
from covenant.calc.pool import pool_snapshot
from covenant.core.types import Action, Observation, Unit
from covenant.data.pack import Mode, Operator, Pack, load_latest
from covenant.state.engine import Engine

ROOT = Path(__file__).resolve().parent.parent


class TestPack(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack = load_latest(ROOT / "packs")
        cls.mode = cls.pack.mode("mode_single_funny")

    def test_counts(self):
        self.assertGreaterEqual(len(self.pack.operators), 100)
        self.assertEqual(len(self.pack.bonds), 23)
        self.assertGreaterEqual(len(self.pack.bands), 30)

    def test_rule_source_metadata_reaches_summary_and_snapshot(self):
        summary = self.pack.summary()
        self.assertEqual(summary["rules_version"], "2026-lower-half-community-audit-1")
        self.assertEqual(summary["source_type"], "community_replica")
        self.assertTrue(summary["source"])
        self.assertIn("装备", "；".join(summary["limitations"]))
        snapshot = Engine(self.pack).snapshot()
        self.assertEqual(snapshot["pack"]["rules_version"], summary["rules_version"])
        self.assertEqual(snapshot["pack"]["source_type"], summary["source_type"])
        self.assertEqual(snapshot["pack"]["source"], summary["source"])
        self.assertEqual(snapshot["pack"]["limitations"], summary["limitations"])

    def test_replica_confidence_is_not_mislabelled_as_official_data(self):
        for key in ("income", "pool", "odds_model", "operators", "bonds"):
            self.assertEqual(self.pack.confidence[key], "community", key)
        self.assertTrue(all(
            source.get("confidence") == "community" for source in self.pack.source
        ))

    def test_shop_rules_per_mode(self):
        r1 = self.mode.rule(1)
        self.assertIsNotNone(r1)
        self.assertGreaterEqual(r1.char_slots, 1)
        self.assertEqual(self.mode.char_slots(1), r1.char_slots)
        # 每个模式一张表，不能拿别的模式的数字
        other = self.pack.mode("mode_training_1")
        self.assertNotEqual(
            (other.rule(1).char_slots, other.rule(1).upgrade_price),
            (r1.char_slots, r1.upgrade_price),
        )

    def test_available_filters_hidden(self):
        avail = self.pack.available_operators(self.mode)
        self.assertTrue(avail)
        self.assertTrue(all(not op.hidden for op in avail))
        self.assertLess(len(avail), len(self.pack.operators))


class TestDisabledBonds(unittest.TestCase):
    """Kimi 提出、我们复核过的规则：静态禁用盟约只在「全部盟约都被禁」时排除干员。"""

    def _pack(self):
        return Pack(
            path=ROOT / "packs",
            schema=1,
            id="test",
            name="test",
            season="test",
            operators={
                "dual": Operator("dual", "双盟约", 1, ("a", "b")),
                "single": Operator("single", "单盟约", 1, ("a",)),
                "none": Operator("none", "无盟约", 1, ()),
            },
        )

    def test_only_all_bonds_inactive_is_excluded(self):
        ids = {o.id for o in self._pack().available_operators(Mode("m", "m", inactive_bonds=("a",)))}
        self.assertEqual(ids, {"dual", "none"})

    def test_single_bond_operator_excluded(self):
        ids = {o.id for o in self._pack().available_operators(Mode("m", "m", inactive_bonds=("a", "b")))}
        self.assertEqual(ids, {"none"})

    def test_bondless_operator_never_excluded(self):
        ids = {o.id for o in self._pack().available_operators(Mode("m", "m", inactive_bonds=("unused",)))}
        self.assertIn("none", ids)
        self.assertIn("dual", ids)


class TestPoolAndOdds(unittest.TestCase):
    def setUp(self):
        # 每个测试一局新对局，避免买牌污染卡池断言
        self.pack = load_latest(ROOT / "packs")
        self.eng = Engine(self.pack)

    def test_pool_total_matches_copies(self):
        snap = pool_snapshot(self.pack, self.eng.state)
        self.assertGreater(snap.total, 0)
        self.assertEqual(snap.remaining, snap.total)
        self.assertTrue(all(e.remaining == e.total for e in snap.entries.values()))

    def test_shop_odds_accepts_precomputed_pool(self):
        # snapshot 里 pool 只建一次、传给 shop_odds，两条路径必须给出同样的数
        snap = pool_snapshot(self.pack, self.eng.state)
        plain = shop_odds(self.pack, self.eng.state)
        reused = shop_odds(self.pack, self.eng.state, pool=snap)
        self.assertEqual(plain.eligible_operators, reused.eligible_operators)
        self.assertEqual(plain.eligible_copies, reused.eligible_copies)
        self.assertEqual(plain.slots, reused.slots)

    def test_prob_at_least_one(self):
        # 各格独立抽样：同一商店允许重复干员。
        p = prob_at_least_one(156, 12, 3)
        self.assertAlmostEqual(p, 1 - (144 / 156) ** 3)
        self.assertEqual(prob_at_least_one(0, 0, 3), 0.0)
        self.assertEqual(prob_at_least_one(12, 12, 3), 1.0)

    def test_refreshes_for_confidence(self):
        self.assertEqual(refreshes_for_confidence(0.5, 0.5), 1)
        self.assertEqual(refreshes_for_confidence(0.5, 0.8), 3)
        self.assertIsNone(refreshes_for_confidence(0.0, 0.8))
        with self.assertRaises(ValueError):
            refreshes_for_confidence(0.5, 1.0)

    def test_full_bench_rejects_buy_even_when_merge_would_fit(self):
        ops = self.pack.available_operators(self.eng.mode)
        op = next(o for o in ops if o.merge_num == 3)
        others = [o for o in ops if o.id != op.id]
        self.eng.state.bench = [Unit(op.id) for _ in range(2)] + [
            Unit(o.id) for o in others[: self.pack.economy.bench_max - 2]
        ]
        self.eng.state.gold = 99
        self.eng.act(Action("set_shop", {"slot": 0, "op_id": op.id}))
        result = self.eng.act(Action("buy", {"slot": 0}))
        self.assertFalse(result["ok"])
        self.assertIn("整备区已满", result["error"])
        self.assertEqual(self.eng.state.gold, 99)

    def test_elite_sell_uses_standard_refund(self):
        op = self.pack.available_operators(self.eng.mode)[0]
        self.eng.state.bench = [Unit(op.id, star=2, pool_copies=3)]
        self.eng.state.gold = 0
        self.eng.act(Action("sell", {"zone": "bench", "slot": 0}))
        self.assertEqual(self.eng.state.gold, 1)

    def test_level_up_leaves_new_slots_empty(self):
        self.eng.state.gold = 99
        before = len(self.eng.state.shop)
        self.eng.act(Action("level_up"))
        after = len(self.eng.state.shop)
        self.assertGreater(after, before)
        self.assertTrue(all(o.op_id is None for o in self.eng.state.shop[before:]))

    def test_income_cap_and_default_reset(self):
        self.assertEqual(self.pack.economy.income(1), 4)
        self.assertEqual(self.pack.economy.income(3), 6)
        self.assertEqual(self.pack.economy.income(20), 12)
        self.eng.state.gold = 9
        self.eng.act(Action("next_round"))
        self.assertEqual(self.eng.state.gold, 5)

    def test_no_carry_does_not_accumulate_future_income(self):
        self.eng.state.gold = 0
        self.eng.state.shop_level = 4
        self.eng.state.rules["gold_carry_over"] = False
        plan = economy_view(self.pack, self.eng.state).level_up_now
        self.assertIsNotNone(plan)
        self.assertEqual(plan.cost_now, 8)
        self.assertEqual(plan.rounds_to_afford, 4)
        self.assertFalse(plan.can_afford_this_round_gain)

    def test_shop_odds_sane(self):
        res = shop_odds(self.pack, self.eng.state)
        self.assertGreater(res.slots, 0)
        for item in res.per_tier.values():
            self.assertGreaterEqual(item.slot_prob, 0.0)
            self.assertLessEqual(item.slot_prob, 1.0)

    def test_buy_consumes_pool(self):
        res = shop_odds(self.pack, self.eng.state)
        op = res.top(1)[0]
        before = pool_snapshot(self.pack, self.eng.state).remaining_of(op.op_id)
        # 直接放一张到商店再买，避免依赖随机
        self.eng.act(Action("set_shop", {"slot": 0, "op_id": op.op_id}))
        gold = self.eng.state.gold
        r = self.eng.act(Action("buy", {"slot": 0}))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.eng.state.gold, gold - self.pack.op(op.op_id).price)
        after = pool_snapshot(self.pack, self.eng.state).remaining_of(op.op_id)
        self.assertEqual(before - after, 1)

    def test_two_copy_merge_preserves_actual_pool_usage(self):
        op = next(op for op in self.pack.operators.values() if op.merge_num == 2)
        self.eng.act(Action("add_unit", {"op_id": op.id, "zone": "bench"}))
        self.eng.act(Action("add_unit", {"op_id": op.id, "zone": "bench"}))
        self.assertEqual(self.eng.state.owned_copies()[op.id], 2)

    def test_partial_units_observation_preserves_unrecognized_slots(self):
        ops = self.pack.available_operators(self.eng.mode)[:2]
        self.eng.state.board = [Unit(ops[0].id, slot=0), Unit(ops[1].id, slot=1)]
        self.eng.observe(Observation(
            "board",
            [{"op_id": ops[1].id, "star": 1, "slot": 1}],
            confidence=0.99,
            source="vision",
        ))
        self.assertEqual([u.op_id for u in self.eng.state.board], [ops[0].id, ops[1].id])

    def test_round_observation_records_previous_round(self):
        self.eng.state.board.append(Unit("chess_char_1_01_a"))
        self.eng.observe(Observation("round", 2, confidence=0.99, source="vision"))
        self.assertEqual([x["round"] for x in self.eng.state.timeline], [1])
        self.assertEqual(self.eng.state.timeline[0]["board"], ["chess_char_1_01_a"])


class TestEconomyAndRounds(unittest.TestCase):
    def setUp(self):
        self.pack = load_latest(ROOT / "packs")
        self.eng = Engine(self.pack)

    def test_income_curve(self):
        view = economy_view(self.pack, self.eng.state)
        self.assertGreaterEqual(view.income_now, 3)
        self.assertGreaterEqual(view.income_next, view.income_now)

    def test_next_round_pays_income(self):
        r = self.eng.act(Action("next_round", {}))
        self.assertTrue(r.get("ok"), r)
        self.assertEqual(self.eng.state.round_no, 2)
        self.assertEqual(self.eng.state.gold, self.pack.economy.income(2))
        self.assertTrue(all(o.op_id is None for o in self.eng.state.shop))

    def test_next_round_stops_at_mode_limit(self):
        self.eng.state.round_no = self.eng.mode.rounds
        before = self.eng.state.to_dict()
        result = self.eng.act(Action("next_round", {}))
        self.assertFalse(result["ok"])
        self.assertIn("最后一回合", result["error"])
        self.assertEqual(self.eng.state.to_dict(), before)

    def test_manual_beats_low_confidence_vision(self):
        from covenant.core.types import Observation

        self.eng.act(Action("set", {"path": "gold", "value": 9}))
        self.eng.observe(Observation("gold", 3, confidence=0.5, source="vision"))
        self.assertEqual(self.eng.state.gold, 9)  # 低置信度不覆盖手工值

    def test_next_round_records_timeline(self):
        from covenant.core.types import Unit

        st = self.eng.state
        st.board.append(Unit("chess_char_1_01_a"))
        self.eng.act(Action("next_round", {}))
        self.eng.act(Action("next_round", {}))
        timeline = self.eng.state.timeline
        self.assertEqual([x["round"] for x in timeline], [1, 2])
        self.assertEqual(timeline[0]["board"], ["chess_char_1_01_a"])
        self.assertEqual(timeline[0]["shop_level"], 1)

    def test_result_observation_sets_hint(self):
        from covenant.core.types import Observation

        self.eng.observe(Observation("result", {"result": "clear", "confidence": 0.9}, confidence=0.9))
        hint = self.eng.state.rules.get("result_hint")
        self.assertIsInstance(hint, dict)
        self.assertEqual(hint["result"], "clear")
        # 同一回合重复识别不该重复写日志
        before = len(self.eng.state.log)
        self.eng.observe(Observation("result", {"result": "clear", "confidence": 0.92}, confidence=0.92))
        self.assertEqual(len(self.eng.state.log), before)


class TestCoreBondGroups(unittest.TestCase):
    """11/8 起「独立模拟」开局核心盟约二选一：没选的组不能再出现在卡池里。"""

    def setUp(self):
        import dataclasses

        from covenant.data.pack import load_latest
        from covenant.state.state import new_state

        self.pack = load_latest("packs")
        self.mode = self.pack.mode(self.pack.default_mode)
        # 用真实盟约里两两不重叠的两组做样本
        bond_ids = list(self.pack.bonds)
        self.groups = [bond_ids[:1], bond_ids[1:2]]
        self.pack = dataclasses.replace(self.pack, core_bond_groups=self.groups)
        self.state = new_state(mode_id=self.mode.id, shop_slots=3)

    def test_extra_inactive_picks_other_groups(self):
        from covenant.calc.pool import state_extra_inactive

        self.assertEqual(state_extra_inactive(self.state, self.pack), ())
        self.state.choices["bond_group"] = 0
        self.assertEqual(state_extra_inactive(self.state, self.pack), tuple(self.groups[1]))
        self.state.choices["bond_group"] = 1
        self.assertEqual(state_extra_inactive(self.state, self.pack), tuple(self.groups[0]))
        self.state.choices["bond_group"] = 9
        self.assertEqual(state_extra_inactive(self.state, self.pack), ())

    def test_available_operators_respects_choice(self):
        base = self.pack.available_operators(self.mode)
        off = set(self.groups[1])
        filtered = self.pack.available_operators(self.mode, extra_inactive=tuple(off))
        self.assertLessEqual(len(filtered), len(base))
        for op in filtered:
            if not op.bonds:
                continue
            self.assertTrue(set(op.bonds) - off, f"{op.id} 的盟约全被禁用却还在池里")

    def test_choice_roundtrip(self):
        self.state.choices["bond_group"] = 1
        again = type(self.state).from_dict(self.state.to_dict())
        self.assertEqual(again.choices.get("bond_group"), 1)


class TestChoiceValidation(unittest.TestCase):
    """核心盟约二选一：重叠组与非法输入。"""

    def setUp(self):
        import dataclasses

        from covenant.data.pack import load_latest
        from covenant.state.state import new_state

        self.pack = load_latest("packs")
        self.mode = self.pack.mode(self.pack.default_mode)
        bond_ids = list(self.pack.bonds)
        # 故意让两组共享 y：选第 0 组时 y 仍然有效，只有 z 该被禁
        self.x, self.y, self.z = bond_ids[0], bond_ids[1], bond_ids[2]
        self.pack = dataclasses.replace(
            self.pack, core_bond_groups=[[self.x, self.y], [self.y, self.z]]
        )
        self.state = new_state(mode_id=self.mode.id, shop_slots=3)

    def test_overlap_keeps_chosen_bond(self):
        from covenant.calc.pool import state_extra_inactive

        self.state.choices["bond_group"] = 0
        self.assertEqual(state_extra_inactive(self.state, self.pack), (self.z,))
        self.state.choices["bond_group"] = 1
        self.assertEqual(state_extra_inactive(self.state, self.pack), (self.x,))

    def test_invalid_set_is_action_error(self):
        from covenant.core.types import Action
        from covenant.state.engine import Engine

        engine = Engine(self.pack)
        result = engine.act(Action("set", {"path": "choices.bond_group", "value": "abc"}))
        self.assertFalse(result["ok"])
        self.assertIn("整数", result["error"])

    def test_invalid_reset_is_action_error(self):
        from covenant.core.types import Action
        from covenant.state.engine import Engine

        engine = Engine(self.pack)
        result = engine.act(Action("reset", {"bond_group": "第一组"}))
        self.assertFalse(result["ok"])
        self.assertIn("整数", result["error"])
        # 失败不应改状态
        self.assertNotIn("bond_group", engine.state.choices)

    def test_reset_accepts_index(self):
        from covenant.core.types import Action
        from covenant.state.engine import Engine

        engine = Engine(self.pack)
        result = engine.act(Action("reset", {"bond_group": 1}))
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(engine.state.choices.get("bond_group"), 1)


class TestSetValidation(unittest.TestCase):
    """手填数字的容错：非法输入应该是 ActionError，不是 500。"""

    def setUp(self):
        from covenant.data.pack import load_latest
        from covenant.state.engine import Engine

        self.engine = Engine(load_latest("packs"))

    def _set(self, path, value):
        from covenant.core.types import Action

        return self.engine.act(Action("set", {"path": path, "value": value}))

    def test_bad_int_is_action_error(self):
        result = self._set("gold", "abc")
        self.assertFalse(result["ok"])
        self.assertIn("整数", result["error"])
        self.assertEqual(self.engine.state.gold, 4)

    def test_numeric_string_is_accepted(self):
        result = self._set("gold", "12")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(self.engine.state.gold, 12)

    def test_bond_layers_coerced(self):
        result = self._set("bond_layers.yanShip", "3")
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(self.engine.state.bond_layers.get("yanShip"), 3)

    def test_frozen_string_false(self):
        self._set("frozen", "false")
        self.assertFalse(self.engine.state.frozen)
        self._set("frozen", "true")
        self.assertTrue(self.engine.state.frozen)

    def test_manual_round_above_mode_limit_is_rejected(self):
        result = self._set("round_no", self.engine.mode.rounds + 1)
        self.assertFalse(result["ok"])
        self.assertIn("回合", result["error"])
        self.assertEqual(self.engine.state.round_no, 1)

    def test_vision_round_above_mode_limit_is_ignored(self):
        self.engine.observe(Observation("round", self.engine.mode.rounds + 1, confidence=1.0))
        self.assertEqual(self.engine.state.round_no, 1)
        self.assertIn("越界", self.engine.state.log[-1]["text"])


class TestActionInputFuzz(unittest.TestCase):
    """所有吃下标/数字的动作，传字符串都不能让 500 漏出去。"""

    def setUp(self):
        from covenant.data.pack import load_latest
        from covenant.state.engine import Engine

        self.engine = Engine(load_latest("packs"))

    def _act(self, kind, **args):
        from covenant.core.types import Action

        return self.engine.act(Action(kind, args))

    def test_bad_slots(self):
        cases = [
            ("buy", {"slot": "x"}),
            ("sell", {"slot": "x"}),
            ("move", {"from_slot": "x", "to_slot": 0}),
            ("add_unit", {"op_id": "chess_char_1_01_a", "star": "x"}),
            ("remove_unit", {"slot": "x"}),
            ("set_shop", {"slot": "x", "op_id": None}),
        ]
        for kind, args in cases:
            result = self._act(kind, **args)
            self.assertFalse(result["ok"], kind)
            self.assertIn("整数", result["error"], kind)

    def test_bad_slot_does_not_change_state(self):
        before = self.engine.state.to_dict()
        self._act("buy", slot="nope")
        self.assertEqual(self.engine.state.to_dict()["seq"], before["seq"])


if __name__ == "__main__":
    unittest.main()
