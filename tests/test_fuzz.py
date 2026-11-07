"""随机动作冒烟：不许出现 ActionError 以外的异常，也不许破坏基本不变量。

不是穷举，是「让随机输入去撞 KeyError / IndexError / TypeError」——这类错误
在 UI 上表现为 500，在真实对局里就是记牌器突然不动了。
"""

from __future__ import annotations

import random
import unittest

from covenant.core.types import Action, ActionType, Observation, ObservationKind
from covenant.data.pack import load_latest
from covenant.state.engine import Engine


class TestRandomActions(unittest.TestCase):
    def setUp(self):
        self.pack = load_latest("packs")
        self.engine = Engine(self.pack)
        self.mode = self.pack.mode(self.pack.default_mode)

    def _check(self, where: str):
        st = self.engine.state
        eco = self.pack.economy
        self.assertGreaterEqual(st.gold, 0, where)
        self.assertLessEqual(len(st.bench), eco.bench_max, f"{where}: bench 超上限")
        self.assertLessEqual(len(st.board), eco.board_max, f"{where}: board 超上限")
        self.assertGreaterEqual(st.shop_level, 1, where)
        self.assertLessEqual(st.shop_level, eco.max_shop_level, where)
        for zone in ("bench", "board", "temp"):
            for unit in getattr(st, zone):
                if unit.op_id:
                    self.assertIsNotNone(self.pack.op(unit.op_id), f"{where}: 未知干员 {unit.op_id}")
        self.assertLessEqual(len(st.timeline), 30, where)

    def test_random_actions(self):
        rng = random.Random(20261107)
        ops = [o.id for o in self.pack.available_operators(self.mode)]
        kinds = [
            ActionType.BUY,
            ActionType.SELL,
            ActionType.REFRESH,
            ActionType.LEVEL_UP,
            ActionType.NEXT_ROUND,
            ActionType.FREEZE,
            ActionType.MOVE,
            ActionType.ADD_UNIT,
            ActionType.REMOVE_UNIT,
            ActionType.SET,
            ActionType.SET_SHOP,
            ActionType.MERGE,
        ]
        for step in range(400):
            kind = rng.choice(kinds)
            args = self._args(rng, kind, ops)
            try:
                result = self.engine.act(Action(kind, args))
            except Exception as exc:  # noqa: BLE001
                self.fail(f"step {step} {kind} {args} 抛了 {type(exc).__name__}: {exc}")
            self.assertIn("ok", result)
            self._check(f"step {step} {kind}")

    def _args(self, rng, kind, ops):
        slot = rng.choice([-1, 0, 1, 2, 3, 8, "x", None])
        op_id = rng.choice(ops + [None, "nope", ""])
        if kind == ActionType.BUY:
            return {"slot": slot}
        if kind == ActionType.SELL:
            return {"slot": slot, "zone": rng.choice(["bench", "board", "temp"])}
        if kind == ActionType.REFRESH:
            return {"free": rng.random() < 0.2}
        if kind == ActionType.LEVEL_UP:
            return {}
        if kind == ActionType.NEXT_ROUND:
            return {}
        if kind == ActionType.FREEZE:
            return {"value": rng.choice([True, False, None])}
        if kind == ActionType.MOVE:
            return {
                "from_zone": rng.choice(["bench", "board", "temp"]),
                "from_slot": slot,
                "to_zone": rng.choice(["bench", "board", "temp"]),
                "to_slot": rng.choice([None, 0, 1, 9, "x"]),
            }
        if kind == ActionType.ADD_UNIT:
            return {"op_id": op_id, "zone": rng.choice(["bench", "board"]), "star": rng.choice([1, 2, 3, "x"])}
        if kind == ActionType.REMOVE_UNIT:
            return {"zone": rng.choice(["bench", "board"]), "slot": slot}
        if kind == ActionType.SET:
            return {
                "path": rng.choice(
                    ["gold", "round_no", "shop_level", "hp", "upgrade_decay", "frozen",
                     "choices.bond_group", "rules.pool_extra", "bond_layers.yanShip"]
                ),
                "value": rng.choice([0, 1, 3, -1, "x", None, True]),
            }
        if kind == ActionType.SET_SHOP:
            return {"slot": slot, "op_id": op_id}
        if kind == ActionType.MERGE:
            return {}
        return {}


class TestRandomObservations(unittest.TestCase):
    def setUp(self):
        from covenant.data.pack import load_latest
        from covenant.state.engine import Engine

        self.engine = Engine(load_latest("packs"))

    def test_random_observations(self):
        kinds = list(ObservationKind.ALL)
        rng = random.Random(7)
        for step in range(200):
            kind = rng.choice(kinds)
            value = rng.choice(
                [
                    0, 1, 5, 99, -1, "x", None, True,
                    [{"slot": 0, "op_id": "chess_char_1_01_a", "known": True}],
                    [{"slot": 99, "op_id": "nope", "known": True}],
                    [{"op_id": None}],
                    {"result": "clear"},
                    {"result": "nope"},
                    {"yanShip": 3, "bad": "x"},
                ]
            )
            obs = Observation(kind, value, confidence=rng.choice([0.1, 0.6, 0.95]), source=rng.choice(["vision", "manual"]))
            try:
                result = self.engine.observe(obs)
            except Exception as exc:  # noqa: BLE001
                self.fail(f"step {step} {kind} {value!r} 抛了 {type(exc).__name__}: {exc}")
            self.assertIn("ok", result)


if __name__ == "__main__":
    unittest.main()
