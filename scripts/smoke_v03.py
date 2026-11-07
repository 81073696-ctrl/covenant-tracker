"""v0.3 本机 HTTP 冒烟检查；结束时自动停止服务。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from covenant.data.pack import load_latest
from covenant.state.engine import Engine
from covenant.ui.server import AppServer


def main() -> None:
    pack = load_latest("packs")
    with tempfile.TemporaryDirectory() as tmp:
        engine = Engine(pack)
        engine.attach_storage(Path(tmp) / "engine.json")
        app = AppServer(engine, port=0)
        url = app.start()
        try:
            with urlopen(f"{url}api/snapshot", timeout=5) as response:
                initial = json.load(response)
            assert initial["refresh_analysis"]["totals"]["refreshes"] == 0

            def post(action_type: str, args: dict) -> dict:
                request = Request(
                    f"{url}api/action",
                    data=json.dumps({"type": action_type, "args": args}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=5) as response:
                    return json.load(response)

            assert post("refresh", {})["ok"]
            op_id = pack.available_operators(engine.mode)[0].id
            assert post("set_shop", {"offers": [{"op_id": op_id}]})["ok"]
            with urlopen(f"{url}api/snapshot", timeout=5) as response:
                final = json.load(response)
            assert final["refresh_analysis"]["totals"]["refreshes"] == 1
            assert final["refresh_analysis"]["by_op"][op_id]["hits"] == 1
            print(f"HTTP 冒烟通过：{url}；确认刷新 1 次，目标命中 1 次。")
        finally:
            app.stop()


if __name__ == "__main__":
    main()
