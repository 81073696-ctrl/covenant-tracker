"""多套识别配置（皮肤）的测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from covenant.vision.profiles import DEFAULT_ID, ProfileError, ProfileManager


class TestProfileManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.var = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrates_legacy_files_into_default(self):
        (self.var / "layout.json").write_text('{"regions": {}}', encoding="utf-8")
        (self.var / "icons.npz").write_bytes(b"legacy")
        pm = ProfileManager(self.var)
        self.assertEqual(pm.active_id(), DEFAULT_ID)
        default = self.var / "profiles" / DEFAULT_ID
        self.assertTrue((default / "layout.json").is_file())
        self.assertEqual((default / "icons.npz").read_bytes(), b"legacy")
        self.assertTrue((default / "meta.json").is_file())

    def test_create_copies_active_and_activate_switches(self):
        pm = ProfileManager(self.var)
        (pm.active_dir() / "layout.json").write_text('{"regions": {"a": 1}}', encoding="utf-8")
        path = pm.create("ursus", "乌萨斯齐心团")
        self.assertTrue((path / "layout.json").is_file())
        self.assertIn("ursus", pm.ids())
        pm.activate("ursus")
        self.assertEqual(pm.active_id(), "ursus")
        self.assertTrue(pm.list()[0]["active"])

    def test_rejects_bad_or_duplicate_ids(self):
        pm = ProfileManager(self.var)
        with self.assertRaises(ProfileError):
            pm.create("bad id", "x")
        with self.assertRaises(ProfileError):
            pm.create("", "x")
        pm.create("ok", "ok")
        with self.assertRaises(ProfileError):
            pm.create("ok", "dup")
        with self.assertRaises(ProfileError):
            pm.activate("missing")

    def test_rename_updates_meta_only(self):
        pm = ProfileManager(self.var)
        pm.create("ursus", "旧名字")
        pm.rename("ursus", "乌萨斯")
        self.assertEqual(pm.meta("ursus")["name"], "乌萨斯")


if __name__ == "__main__":
    unittest.main()
