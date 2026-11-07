"""归档自动切格功能的回归测试。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from screenlens.vision.pipeline import VisionPipeline  # noqa: E402
from screenlens.vision.roi import Rect, Region  # noqa: E402


def two_card_image() -> np.ndarray:
    image = np.full((100, 300, 3), 20, dtype=np.uint8)
    image[10:90, 20:60] = 220
    image[10:90, 160:200] = 220
    return image


class TestArchiveDetection(unittest.TestCase):
    def test_detect_runs_finds_content_blocks(self) -> None:
        try:
            from screenlens.vision.detect import detect_runs
        except ImportError:
            self.fail("归档中的 detect_runs 尚未合并")
        boxes = detect_runs(two_card_image(), (0, 0, 300, 100))
        self.assertEqual([box.as_tuple() for box in boxes], [(20, 10, 40, 80), (160, 10, 40, 80)])

    def test_pipeline_auto_mode_uses_detected_blocks(self) -> None:
        pipe = VisionPipeline()
        region = Region("shop", Rect(0, 0, 1, 1), "shop", params={"slots": 5, "auto": True})
        region_slots = getattr(pipe, "region_slots", None)
        self.assertIsNotNone(region_slots, "归档中的自动切格入口尚未合并")
        self.assertEqual(region_slots(region, two_card_image()), [(20, 10, 40, 80), (160, 10, 40, 80)])

    def test_pipeline_non_auto_mode_keeps_fixed_slots(self) -> None:
        pipe = VisionPipeline()
        region = Region("shop", Rect(0, 0, 1, 1), "shop", params={"slots": 2})
        self.assertEqual(pipe.region_slots(region, two_card_image()), [(0, 0, 149, 100), (151, 0, 149, 100)])

    def test_detect_row_rejects_flat_image(self) -> None:
        try:
            from screenlens.vision.detect_row import detect_row
        except ImportError:
            self.fail("归档中的 detect_row 尚未合并")
        image = np.full((100, 300, 3), 20, dtype=np.uint8)
        self.assertEqual(detect_row(image, (0, 0, 300, 100)), [])

    def test_detection_demo_imports(self) -> None:
        path = ROOT / "tools" / "detect_demo.py"
        self.assertTrue(path.exists(), "归档中的自动切格演示尚未合并")
        spec = importlib.util.spec_from_file_location("detect_demo", path)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        spec.loader.exec_module(module)


if __name__ == "__main__":
    unittest.main()
