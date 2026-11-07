"""最小冒烟：布局、诊断、学数字、帧同步。

跑法：python -m unittest discover -s tests
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screenlens.server import LensApp  # noqa: E402
from screenlens.vision.inspect import inspect_frame  # noqa: E402

SAMPLES = ROOT / "samples"
LAYOUT = ROOT / "layouts" / "mock.json"


class TestSmoke(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="lens-"))
        self.app = LensApp(var_dir=self.tmp, layout=LAYOUT)
        self.app.use_samples(SAMPLES, loop=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_layout_regions(self) -> None:
        names = set(self.app.pipe.layout.names())
        self.assertIn("gold", names)
        self.assertIn("shop", names)
        self.assertEqual(len(names), 7)

    def test_inspect_before_learning(self) -> None:
        report = inspect_frame(self.app.pipe, self.app.grab())
        self.assertEqual(report["width"], 1600)
        kinds = {r["kind"] for r in report["regions"]}
        self.assertIn("digits", kinds)
        self.assertIn("shop", kinds)
        # 没学过任何模板时，数字区不可能 ok
        self.assertTrue(any(r["status"] != "ok" for r in report["regions"]))

    def test_learn_digits_then_read(self) -> None:
        frame = self.app.grab()
        n = self.app.pipe.learn_digit(frame, "digits_demo", "0123456789")
        self.assertEqual(n, 10)
        report = inspect_frame(self.app.pipe, frame)
        demo = next(r for r in report["regions"] if r["name"] == "digits_demo")
        self.assertEqual(demo["status"], "ok")
        self.assertEqual(demo["text"], "0123456789")

    def test_folder_advances_only_on_next(self) -> None:
        a = self.app.grab()
        b = self.app.grab()
        self.assertIs(a, b)
        c = self.app.grab(next=True)
        self.assertIsNot(a, c)


if __name__ == "__main__":
    unittest.main()
