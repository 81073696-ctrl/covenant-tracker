"""独立桌面窗口：配置、窗口控制与关闭生命周期。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from covenant.ui.desktop import DesktopController, DesktopStore


class FakeWindow:
    def __init__(self, state):
        self.width, self.height = state["width"], state["height"]
        self.x, self.y = state["x"] or 20, state["y"] or 20
        self.on_top = state["on_top"]
        self.calls = []

    def resize(self, width, height):
        self.width, self.height = width, height
        self.calls.append(("resize", width, height))

    def move(self, x, y):
        self.x, self.y = x, y
        self.calls.append(("move", x, y))


class DesktopTests(unittest.TestCase):
    def test_corrupt_config_falls_back_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "desktop.json"
            path.write_text("not-json", encoding="utf-8")
            store = DesktopStore(path)
            self.assertEqual(store.load()["width"], 1280)
            state = dict(store.load(), compact=True, on_top=True, width=430, height=760, x=40, y=50)
            store.save(state)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["width"], 430)
            self.assertFalse(path.with_suffix(".tmp").exists())

    def test_invalid_values_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "desktop.json"
            path.write_text(json.dumps({"compact": "yes", "width": 20}), encoding="utf-8")
            state = DesktopStore(path).load()
            self.assertFalse(state["compact"])
            self.assertEqual(state["width"], 1280)

    def test_compact_topmost_and_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DesktopStore(Path(tmp) / "desktop.json")
            window = FakeWindow(store.load())
            closed = []
            controller = DesktopController(window, store, lambda: closed.append(True))
            state = controller.set_compact(True)
            self.assertEqual((state["width"], state["height"]), (430, 760))
            self.assertTrue(state["on_top"])
            self.assertIn(("resize", 430, 760), window.calls)
            state = controller.set_on_top(False)
            self.assertFalse(state["on_top"])
            controller.close()
            controller.close()
            self.assertEqual(closed, [True])
            self.assertFalse(store.load()["on_top"])

    def test_close_saves_last_known_geometry_when_window_is_gone(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DesktopStore(Path(tmp) / "desktop.json")
            window = FakeWindow(store.load())
            closed = []
            controller = DesktopController(window, store, lambda: closed.append(True))
            controller.remember_geometry()
            window.width = None
            controller.close()
            self.assertEqual(closed, [True])
            self.assertEqual(store.load()["width"], 1280)

    def test_desktop_flag_only_on_serve(self):
        from covenant.__main__ import build_parser

        args = build_parser().parse_args(["serve", "--desktop", "--no-browser"])
        self.assertTrue(args.desktop)
        self.assertFalse(hasattr(build_parser().parse_args(["demo"]), "desktop"))

    def test_serve_passes_isolated_var_dir_to_app_server(self):
        from covenant.__main__ import cmd_serve

        captured = {}

        class FakeAppServer:
            def __init__(self, engine, host, port, **kwargs):
                captured.update(kwargs)
                self.settings = {"auto_capture": False}
                self.frame_source = None
                self.capture_service = SimpleNamespace(on=False)

            def start(self):
                return "http://127.0.0.1:8777/"

            def stop(self):
                pass

        class FakeEvent:
            def wait(self, _timeout):
                return True

        args = SimpleNamespace(
            pack=None, host="127.0.0.1", port=0, var="isolated-var",
            frames=None, window=None, desktop=False, no_browser=True,
            tray=False, no_tray=True,
        )
        with patch("covenant.ui.server.AppServer", FakeAppServer), patch(
            "covenant.__main__.threading.Event", FakeEvent
        ):
            self.assertEqual(cmd_serve(args), 0)
        self.assertEqual(captured.get("var_dir"), "isolated-var")


if __name__ == "__main__":
    unittest.main()
