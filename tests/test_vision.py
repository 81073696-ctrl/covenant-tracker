"""识别层测试：数字切分/识别、图标匹配、管线产出观测。"""

from __future__ import annotations

import unittest

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from covenant.capture.synthetic import SyntheticSource
from covenant.core.types import ObservationKind
from covenant.vision.match import IconMatcher
from covenant.vision.ocr import DigitReader
from covenant.vision.pipeline import VisionPipeline
from covenant.vision.roi import Layout, Rect, Region


def _draw_number(text: str, w: int = 220, h: int = 80) -> np.ndarray:
    """黑底白字画一个数字，字号随画布高度走（避免裁切）。"""
    img = Image.new("RGB", (w, h), (10, 11, 13))
    d = ImageDraw.Draw(img)
    size = max(12, int(h * 0.62))
    try:
        font = ImageFont.truetype("arial.ttf", size)
    except OSError:
        font = ImageFont.load_default()
    d.text((6, max(0, (h - size) // 2 - 2)), text, font=font, fill=(235, 237, 240))
    return np.asarray(img)


def _stripe(color_a, color_b, w=40, h=60) -> np.ndarray:
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, :] = color_a
    arr[h // 3 : 2 * h // 3, :] = color_b
    return arr


class TestDigits(unittest.TestCase):
    def test_learn_then_read(self):
        r = DigitReader()
        self.assertGreater(r.learn(_draw_number("12"), "12"), 0)
        text, conf = r.read(_draw_number("12"))
        self.assertEqual(text, "12")
        self.assertGreater(conf, 0.6)

    def test_unknown_digit_rejected(self):
        r = DigitReader()
        r.learn(_draw_number("1"), "1")
        text, _ = r.read(_draw_number("8"))
        self.assertEqual(text, "")  # 没学过就不猜

    def test_save_load(self):
        import tempfile
        from pathlib import Path

        r = DigitReader()
        r.learn(_draw_number("37"), "37")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "digits.npz"
            r.save(path)
            r2 = DigitReader()
            self.assertTrue(r2.load(path))
            self.assertEqual(r2.read(_draw_number("37"))[0], "37")


class TestIcons(unittest.TestCase):
    def test_match(self):
        red = _stripe((200, 40, 40), (30, 200, 60))
        blue = _stripe((40, 80, 220), (240, 220, 60))
        m = IconMatcher()
        self.assertTrue(m.add("red", red))
        self.assertTrue(m.add("blue", blue))
        key, score = m.classify(red)
        self.assertEqual(key, "red")
        self.assertGreater(score, 0.9)
        self.assertEqual(m.classify(blue)[0], "blue")


class TestPipeline(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.var = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_reads_learned_shop_slot(self):
        frame = SyntheticSource(seed=1).grab()
        default = Layout.default()
        layout = Layout(
            regions={
                "shop": default.region("shop"),
                "bench": default.region("bench"),
            }
        )
        pipe = VisionPipeline(layout=layout, var_dir=self.var)
        self.assertTrue(pipe.learn_slot(frame, "shop", 0, "chess_test"))
        obs = pipe.read(frame)
        shop = [o for o in obs if o.kind == ObservationKind.SHOP]
        self.assertEqual(len(shop), 1)
        cards = shop[0].value
        self.assertEqual(cards[0]["op_id"], "chess_test")
        self.assertTrue(cards[0]["known"])

    def test_digit_region(self):
        frame = SyntheticSource(seed=1).grab()
        # 在左上角贴一块纯数字区域，模拟 HUD 上的资金
        frame.image[8:48, 60:130] = _draw_number("17", 70, 40)
        region = Region("gold", Rect(60 / 1280, 8 / 720, 70 / 1280, 40 / 720), "digits")
        pipe = VisionPipeline(layout=Layout(regions={"gold": region}), var_dir=self.var)
        self.assertGreater(pipe.learn_digit(frame, "gold", "17"), 0)
        obs = pipe.read(frame)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0].kind, ObservationKind.GOLD)
        self.assertEqual(obs[0].value, 17)

    def test_result_template(self):
        frame = SyntheticSource(seed=1).grab()
        frame.image[300:380, 900:1180] = _stripe((240, 200, 60), (20, 20, 20), w=280, h=80)
        region = Region("settle", Rect(900 / 1280, 300 / 720, 280 / 1280, 80 / 720), "result")
        pipe = VisionPipeline(layout=Layout(regions={"settle": region}), var_dir=self.var)
        self.assertTrue(pipe.learn_result(frame, "settle", "clear"))
        obs = pipe.read(frame)
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0].kind, ObservationKind.RESULT)
        self.assertEqual(obs[0].value["result"], "clear")
        # 换一块不像任何模板的图，不该硬猜
        frame.image[300:380, 900:1180] = _stripe((10, 10, 10), (250, 250, 250), w=280, h=80)
        self.assertFalse([o for o in pipe.read(frame) if o.kind == ObservationKind.RESULT])

    def test_shop_levels_gate(self):
        """shop 区按 levels 过滤：等级 2 只读 shop_l2，等级 1 只读 shop。"""
        frame = SyntheticSource(seed=1).grab()
        # 等级区贴数字；一级商店 1 格（红），二级商店 2 格（蓝）
        frame.image[8:48, 60:130] = _draw_number("1", 70, 40)
        frame.image[600:680, 100:220] = _stripe((220, 40, 40), (30, 30, 30), w=120, h=80)
        frame.image[600:680, 400:460] = _stripe((40, 80, 220), (30, 30, 30), w=60, h=80)
        frame.image[600:680, 520:580] = _stripe((40, 80, 220), (30, 30, 30), w=60, h=80)
        layout = Layout(
            regions={
                "level": Region("level", Rect(60 / 1280, 8 / 720, 70 / 1280, 40 / 720), "digits"),
                "shop": Region("shop", Rect(100 / 1280, 600 / 720, 120 / 1280, 80 / 720),
                               "shop", params={"slots": 1, "levels": [1], "art": 1.0}),
                "shop_l2": Region("shop_l2", Rect(400 / 1280, 600 / 720, 240 / 1280, 80 / 720),
                                  "shop", params={"slots": 2, "levels": [2, 3], "art": 1.0}),
            }
        )
        pipe = VisionPipeline(layout=layout, var_dir=self.var)
        self.assertTrue(pipe.learn_slot(frame, "shop", 0, "op_a"))
        self.assertTrue(pipe.learn_slot(frame, "shop_l2", 0, "op_b"))
        self.assertTrue(pipe.learn_slot(frame, "shop_l2", 1, "op_b"))
        self.assertGreater(pipe.learn_digit(frame, "level", "1"), 0)

        obs = pipe.read(frame)
        shops = [o for o in obs if o.kind == ObservationKind.SHOP]
        self.assertEqual(len(shops), 1)
        self.assertEqual(len(shops[0].value), 1)
        self.assertEqual(shops[0].value[0]["op_id"], "op_a")

        # 换成等级 2：应当只读 shop_l2 的两格
        frame.image[8:48, 60:130] = _draw_number("2", 70, 40)
        self.assertGreater(pipe.learn_digit(frame, "level", "2"), 0)
        obs = pipe.read(frame)
        shops = [o for o in obs if o.kind == ObservationKind.SHOP]
        self.assertEqual(len(shops), 1)
        self.assertEqual(len(shops[0].value), 2)

    def test_doctor_reports_gaps(self):
        from covenant.data.pack import load_latest
        from covenant.vision.doctor import format_report, run_doctor

        pack = load_latest("packs")
        report = run_doctor(pack, var_dir=self.var)
        names = {item["name"]: item["level"] for item in report["items"]}
        self.assertFalse(report["ok"])
        # 数字/结算是「锦上添花」，缺了只警告；布局、HUD、商店、干员模板才阻塞。
        self.assertIn(names["数字模板"], ("warn", "missing"))
        self.assertEqual(names["干员模板"], "missing")
        self.assertIn(names["结算模板"], ("warn", "missing"))
        text = format_report(report)
        self.assertIn("结论：还缺", text)

    def test_doctor_after_learning(self):
        from covenant.data.pack import load_latest
        from covenant.vision.doctor import run_doctor

        frame = SyntheticSource(seed=2).grab()
        frame.image[8:48, 60:130] = _draw_number("7", 70, 40)
        region = Region("gold", Rect(60 / 1280, 8 / 720, 70 / 1280, 40 / 720), "digits")
        pipe = VisionPipeline(layout=Layout(regions={"gold": region}), var_dir=self.var)
        self.assertGreater(pipe.learn_digit(frame, "gold", "7"), 0)
        pipe.save()

        report = run_doctor(load_latest("packs"), var_dir=self.var)
        names = {item["name"]: item["level"] for item in report["items"]}
        # 学过一个字形后不再算「缺失」，但只有 1 个，仍是警告
        self.assertEqual(names["数字模板"], "warn")


if __name__ == "__main__":
    unittest.main()


class TestCustomSplits(unittest.TestCase):
    """Kimi 提案的自定义切分：槽位边界可以在布局里写死，非法时回退均分。"""

    def _region(self, splits):
        from covenant.vision.roi import Rect, Region

        return Region("shop", Rect(0.0, 0.0, 1.0, 1.0), "shop", params={"slots": 3, "splits": splits})

    def test_valid_splits(self):
        from covenant.vision.roi import Rect, Region

        region = self._region([0.0, 0.5, 0.7, 1.0])
        bounds, mode = region.split_boundaries()
        self.assertEqual(mode, "custom")
        self.assertEqual(bounds, [0.0, 0.5, 0.7, 1.0])

    def test_invalid_splits_fall_back(self):
        region = self._region([0.0, 0.5, 1.0])
        bounds, mode = region.split_boundaries()
        self.assertIsNone(bounds)
        self.assertTrue(mode.startswith("invalid"))

    def test_pipeline_uses_splits(self):
        from covenant.vision.pipeline import VisionPipeline

        pipe = VisionPipeline()
        rects = pipe.slot_rects(self._region([0.0, 0.5, 0.7, 1.0]), 1000, 100)
        self.assertEqual([r[2] for r in rects], [500, 200, 300])

class TestUnitRecognition(unittest.TestCase):
    """整备区/战场单位：独立模板 + 星级角标。"""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.var = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _frame_and_layout(self):
        from covenant.capture.base import Frame
        from covenant.vision.roi import Layout, Rect, Region

        img = np.zeros((100, 300, 3), dtype=np.uint8)
        # 槽 0：竖条；槽 1：横条；槽 2：空
        for x in range(0, 100, 10):
            img[0:70, x : x + 5] = 220
        for y in range(0, 70, 10):
            img[y : y + 5, 100:200] = 220
        # 星级：槽 0 = 一根粗横条（1 星）；槽 1 = 三根细横条（3 星）
        img[78:92, 10:90] = 255
        for y in (76, 83, 90):
            img[y : y + 3, 110:190] = 255
        region = Region(
            "bench",
            Rect(0.0, 0.0, 1.0, 1.0),
            "units",
            params={"slots": 3, "art": 0.7, "star_rect": [0.0, 0.7, 1.0, 0.3]},
        )
        return Frame(img), Layout(regions={"bench": region})

    def test_learn_unit_and_star_then_read(self):
        frame, layout = self._frame_and_layout()
        pipe = VisionPipeline(layout=layout, var_dir=self.var)
        self.assertTrue(pipe.learn_unit(frame, "bench", 0, "op_a"))
        self.assertTrue(pipe.learn_unit(frame, "bench", 1, "op_b"))
        self.assertTrue(pipe.learn_star(frame, "bench", 0, 1))
        self.assertTrue(pipe.learn_star(frame, "bench", 1, 3))

        obs = [o for o in pipe.read(frame) if o.kind == ObservationKind.BENCH]
        self.assertEqual(len(obs), 1)
        units = {u["slot"]: u for u in obs[0].value}
        self.assertEqual(units[0]["op_id"], "op_a")
        self.assertEqual(units[0]["star"], 1)
        self.assertEqual(units[1]["op_id"], "op_b")
        self.assertEqual(units[1]["star"], 3)
        self.assertNotIn(2, units)  # 空格不产出

    def test_star_falls_back_to_one_without_templates(self):
        frame, layout = self._frame_and_layout()
        pipe = VisionPipeline(layout=layout, var_dir=self.var)
        pipe.learn_unit(frame, "bench", 0, "op_a")
        units = [u for o in pipe.read(frame) if o.kind == ObservationKind.BENCH for u in o.value]
        self.assertEqual(units[0]["star"], 1)

    def test_confidently_empty_region_emits_empty_observation(self):
        frame, layout = self._frame_and_layout()
        pipe = VisionPipeline(layout=layout, var_dir=self.var)
        pipe.learn_unit(frame, "bench", 0, "op_a")
        frame.image[:] = 0
        obs = [o for o in pipe.read(frame) if o.kind == ObservationKind.BENCH]
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0].value, [])
        self.assertGreaterEqual(obs[0].confidence, 0.95)

    def test_tiny_window_does_not_emit_empty_observation(self):
        from covenant.capture.base import Frame

        frame, layout = self._frame_and_layout()
        pipe = VisionPipeline(layout=layout, var_dir=self.var)
        pipe.learn_unit(frame, "bench", 0, "op_a")
        tiny = Frame(np.zeros((10, 30, 3), dtype=np.uint8))
        obs = [o for o in pipe.read(tiny) if o.kind == ObservationKind.BENCH]
        self.assertEqual(obs, [])

    def test_bad_index_and_missing_star_rect(self):
        from covenant.vision.roi import Layout, Rect, Region

        frame, layout = self._frame_and_layout()
        pipe = VisionPipeline(layout=layout, var_dir=self.var)
        self.assertFalse(pipe.learn_unit(frame, "bench", 9, "op_a"))
        plain = Region("board", Rect(0.0, 0.0, 1.0, 1.0), "units", params={"slots": 2})
        pipe2 = VisionPipeline(layout=Layout(regions={"board": plain}), var_dir=self.var)
        self.assertFalse(pipe2.learn_star(frame, "board", 0, 2))

class TestInspect(unittest.TestCase):
    """工作台诊断：说清楚「看见了什么 / 为什么没过」。"""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.var = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def _pipe(self, regions):
        from covenant.vision.roi import Layout

        return VisionPipeline(layout=Layout(regions=regions), var_dir=self.var)

    def test_digits_report_ok_and_glyphs(self):
        from covenant.vision.inspect import inspect_frame

        frame = SyntheticSource(seed=1).grab()
        frame.image[8:48, 60:130] = _draw_number("12", 70, 40)
        region = Region("gold", Rect(60 / 1280, 8 / 720, 70 / 1280, 40 / 720), "digits", target="gold")
        pipe = self._pipe({"gold": region})
        self.assertGreater(pipe.learn_digit(frame, "gold", "12"), 0)

        rep = inspect_frame(pipe, frame)
        self.assertTrue(rep["ok"])
        gold = next(r for r in rep["regions"] if r["name"] == "gold")
        self.assertEqual(gold["status"], "ok")
        self.assertEqual(gold["text"], "12")
        self.assertEqual(len(gold["glyphs"]), 2)
        self.assertIn("1", rep["coverage"]["digits"]["present"])
        # 缺 0/3/5… 时应该给出人话建议
        self.assertTrue(any("数字模板缺" in s for s in rep["suggestions"]))

    def test_unknown_digit_is_warn_not_crash(self):
        from covenant.vision.inspect import inspect_frame

        frame = SyntheticSource(seed=2).grab()
        frame.image[8:48, 60:130] = _draw_number("12", 70, 40)
        frame.image[8:48, 60:130] = _draw_number("56", 70, 40)
        region = Region("gold", Rect(60 / 1280, 8 / 720, 70 / 1280, 40 / 720), "digits", target="gold")
        pipe = self._pipe({"gold": region})  # 一个模板都没学：应该老实说失败
        rep = inspect_frame(pipe, frame)
        gold = next(r for r in rep["regions"] if r["name"] == "gold")
        self.assertIn(gold["status"], ("warn", "fail"))
        self.assertIsNone(gold["text"])
        for g in gold["glyphs"]:
            self.assertIn("best", g)
            self.assertIn("crop", g)

    def test_shop_without_level_is_skipped_with_reason(self):
        from covenant.vision.inspect import inspect_frame

        frame = SyntheticSource(seed=3).grab()
        region = Region(
            "shop_l2",
            Rect(0.4, 0.7, 0.4, 0.2),
            "shop",
            params={"slots": 5, "levels": [2, 3]},
        )
        pipe = self._pipe({"shop_l2": region})
        rep = inspect_frame(pipe, frame)
        shop = next(r for r in rep["regions"] if r["name"] == "shop_l2")
        self.assertEqual(shop["status"], "skip")
        self.assertIn("等级", shop["reason"])

    def test_icon_rank_and_config_roundtrip(self):
        frame = SyntheticSource(seed=4).grab()
        pipe = self._pipe({})
        patch = frame.image[100:164, 100:148]
        self.assertTrue(pipe.icons.add("op_a", patch))
        top = pipe.icons.rank(patch, k=3)
        self.assertEqual(top[0][0], "op_a")
        self.assertGreaterEqual(top[0][1], 0.9)

        cfg = pipe.set_config({"icon_min_sim": 0.75, "digit_min_sim": 0.6})
        self.assertEqual(cfg["icon_min_sim"], 0.75)
        again = VisionPipeline.load(self.var)
        self.assertEqual(again.config()["digit_min_sim"], 0.6)
