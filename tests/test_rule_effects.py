"""已核对策略 / 盟约经济效果的重放与边界测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from covenant.calc.economy import economy_view
from covenant.core.types import Action, Observation, Unit
from covenant.data.pack import load_latest
from covenant.state.engine import Engine

ROOT = Path(__file__).resolve().parent.parent


class TestRuleEffects(unittest.TestCase):
    def setUp(self):
        self.pack = load_latest(ROOT / "packs")
        self.eng = Engine(self.pack)

    def _ops(self, bond_id, count=2):
        ops = [
            op for op in self.pack.available_operators(self.eng.mode)
            if bond_id in op.bonds and not op.hidden
        ]
        self.assertGreaterEqual(len(ops), count, f"{bond_id} 可用干员不足")
        return ops[:count]

    def _activate(self, bond_id):
        for op in self._ops(bond_id):
            result = self.eng.act(Action("add_unit", {"zone": "board", "op_id": op.id}))
            self.assertTrue(result["ok"], result)

    def _set_layers(self, bond_id, value):
        result = self.eng.act(Action("set", {"path": f"bond_layers.{bond_id}", "value": value}))
        self.assertTrue(result["ok"], result)

    def test_cannot_carries_gold_and_pays_conditional_bonus(self):
        self.eng.state.band_id = "band_cannot"
        self.eng.state.gold = 5
        self.eng.act(Action("next_round"))
        self.assertEqual(self.eng.state.gold, 5 + self.pack.economy.income(2) + 1)
        self.eng.state.gold = 4
        self.eng.act(Action("next_round"))
        self.assertEqual(self.eng.state.gold, 4 + self.pack.economy.income(3))
        self.assertEqual(economy_view(self.pack, self.eng.state).income_next,
                         self.pack.economy.income(4) + 1)

    def test_cannot_manual_carry_override_does_not_leak_into_new_game(self):
        self.eng.state.band_id = "band_cannot"
        self.eng.state.gold = 5
        self.eng.state.rules["gold_carry_over"] = False
        self.eng.act(Action("next_round"))
        self.assertEqual(self.eng.state.gold, self.pack.economy.income(2) + 1)
        self.eng.act(Action("reset", {"mode_id": self.eng.state.mode_id, "band_id": "band_cannot"}))
        self.assertNotIn("gold_carry_over", self.eng.state.rules)

    def test_vision_rewards_crossed_milestones_once_and_observation_uses_same_rules(self):
        before = self.eng.state.gold
        self._activate("visiShip")
        self._set_layers("visiShip", 25)
        self.assertEqual(self.eng.state.gold, before + 4)
        milestones = self.eng.state.rules.get("effect_milestones", {})
        self.assertTrue(all(milestones.get(f"visiShip:{n}") for n in (10, 20)))
        self._set_layers("visiShip", 25)
        self._set_layers("visiShip", 5)
        self.assertEqual(self.eng.state.gold, before + 4)
        result = self.eng.observe(Observation("bonds", {"visiShip": 35}, confidence=0.99))
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.eng.state.gold, before + 6)

    def test_inactive_vision_pays_nothing_then_catches_up_when_activated(self):
        before = self.eng.state.gold
        self._set_layers("visiShip", 30)
        self.assertEqual(self.eng.state.gold, before)
        self.assertFalse(self.eng.state.rules.get("effect_milestones"))
        self._activate("visiShip")
        self.assertEqual(self.eng.state.gold, before + 6)

    def test_vision_discount_unlocks_at_80_expands_at_150_and_is_permanent(self):
        self._activate("visiShip")
        self._set_layers("visiShip", 80)
        vision = self._ops("visiShip", 1)[0]
        other = next(op for op in self.pack.available_operators(self.eng.mode)
                     if not op.hidden and "visiShip" not in op.bonds and op.price > 0)
        self.eng.state.gold = 99
        self.eng.act(Action("set_shop", {"offers": [vision.id, other.id]}))
        prices = self.eng.snapshot()["shop_prices"]
        self.assertEqual(prices, [vision.price - 1, other.price, None])  # 与商店槽位对齐。
        self.eng.act(Action("buy", {"slot": 0}))
        self.assertEqual(self.eng.state.gold, 99 - vision.price + 1)

        self._set_layers("visiShip", 150)
        self.eng.state.gold = 99  # 隔离跨档补发的资金，只验证本次购买价。
        self.eng.act(Action("set_shop", {"offers": [other.id]}))
        self.assertEqual(self.eng.snapshot()["shop_prices"], [other.price - 1, None, None])
        self.eng.act(Action("buy", {"slot": 0}))
        self.assertEqual(self.eng.state.gold, 99 - other.price + 1)

        self.eng.state.gold = 99
        self._set_layers("visiShip", 0)
        self.eng.state.board.clear()
        self.eng.act(Action("set_shop", {"offers": [other.id]}))
        self.eng.act(Action("buy", {"slot": 0}))
        self.assertEqual(self.eng.state.gold, 99 - other.price + 1)

    def test_miracle_awards_20_at_100_and_only_exposes_probability(self):
        before = self.eng.state.gold
        self._activate("miraShip")
        self._set_layers("miraShip", 100)
        self.assertEqual(self.eng.state.gold, before + 20)
        view = economy_view(self.pack, self.eng.state)
        self.assertAlmostEqual(view.free_refresh_chance_percent, 48.0)
        self._set_layers("miraShip", 0)
        self._set_layers("miraShip", 100)
        self.assertEqual(self.eng.state.gold, before + 20)
        # 盟约仍激活，概率仍应展示；重复达到 100 层只是不再发钱。
        self.assertAlmostEqual(economy_view(self.pack, self.eng.state).free_refresh_chance_percent, 48.0)

    def test_replay_does_not_pay_milestones_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "engine.json"
            self.eng.attach_storage(path)
            self._activate("visiShip")
            self._set_layers("visiShip", 80)
            gold = self.eng.state.gold
            milestones = dict(self.eng.state.rules["effect_milestones"])
            restored = Engine(self.pack)
            restored.attach_storage(path)
            self.assertEqual(restored.state.gold, gold)
            self.assertEqual(restored.state.rules["effect_milestones"], milestones)

    def test_existing_round_and_two_copy_rules_are_unchanged(self):
        self.assertEqual(self.pack.mode("mode_single_funny").rounds, 9)
        fang = next(op for op in self.pack.operators.values() if op.merge_num == 2)
        self.assertEqual(fang.merge_num, 2)


if __name__ == "__main__":
    unittest.main()
