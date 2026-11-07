"""纯合并函数单测：商店按槽位、单位按 slot。"""

from __future__ import annotations

import unittest

from covenant.core.types import ShopOffer, Unit
from covenant.state.merge import merge_shop, merge_units


class TestMergeShop(unittest.TestCase):
    def test_pads_to_slot_count(self):
        shop, changed = merge_shop([], [], 3)
        self.assertEqual(len(shop), 3)
        self.assertFalse(changed)

    def test_replaces_known_slot(self):
        shop = [ShopOffer(slot=0) for _ in range(3)]
        new, changed = merge_shop(shop, [{"slot": 1, "op_id": "a", "tier": 1}], 3)
        self.assertTrue(changed)
        self.assertEqual(new[1].op_id, "a")
        self.assertIsNone(new[0].op_id)

    def test_same_value_no_change(self):
        shop = [ShopOffer(slot=0, op_id="a", known=True), ShopOffer(slot=1), ShopOffer(slot=2)]
        _, changed = merge_shop(shop, [{"slot": 0, "op_id": "a", "known": True}], 3)
        self.assertFalse(changed)

    def test_out_of_range_and_garbage_ignored(self):
        shop = [ShopOffer(slot=0) for _ in range(2)]
        _, changed = merge_shop(shop, [{"slot": 9, "op_id": "x"}, "bad", {"slot": "?"}], 2)
        self.assertFalse(changed)

    def test_accepts_shop_offer_instance(self):
        shop = [ShopOffer(slot=0) for _ in range(2)]
        new, changed = merge_shop(shop, [ShopOffer(slot=0, op_id="b", known=True)], 2)
        self.assertTrue(changed)
        self.assertEqual(new[0].op_id, "b")


class TestMergeUnits(unittest.TestCase):
    def test_appends_new_slot(self):
        units, changed = merge_units([], [{"op_id": "a", "star": 1, "slot": 2}])
        self.assertTrue(changed)
        self.assertEqual(units[0].slot, 2)

    def test_replaces_same_slot_when_star_changes(self):
        base = [Unit("a", 1, slot=0)]
        units, changed = merge_units(base, [{"op_id": "a", "star": 2, "slot": 0}])
        self.assertTrue(changed)
        self.assertEqual(units[0].star, 2)

    def test_same_unit_no_change(self):
        base = [Unit("a", 1, slot=0)]
        _, changed = merge_units(base, [{"op_id": "a", "star": 1, "slot": 0}])
        self.assertFalse(changed)

    def test_skips_invalid(self):
        _, changed = merge_units([], [{"slot": 1}, {"op_id": "a", "slot": -1}, {"op_id": "a", "slot": "x"}])
        self.assertFalse(changed)

    def test_accepts_unit_instance(self):
        units, changed = merge_units([], [Unit("b", 1, slot=3)])
        self.assertTrue(changed)
        self.assertEqual(units[0].op_id, "b")

    def test_empty_observation_clears_existing_units(self):
        base = [Unit("a", 1, slot=0)]
        units, changed = merge_units(base, [])
        self.assertTrue(changed)
        self.assertEqual(units, [])


if __name__ == "__main__":
    unittest.main()
