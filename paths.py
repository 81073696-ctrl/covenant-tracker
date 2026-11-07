"""打包（PyInstaller）后也能找到数据的位置。

源码运行时：项目根目录（``covenant/`` 的上一级）。
打包后：``sys._MEIPASS``（onefile 解压目录）。
运行期产出的 ``var/`` 始终跟着当前工作目录走，方便用户拷走。
"""

from __future__ import annotations

import sys
from pathlib import Path


def bundle_root() -> Path:
    """只读资源（web 静态文件、内置数据包）的根。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[1]


def web_dir() -> Path:
    return bundle_root() / "covenant" / "ui" / "web"


def docs_dir() -> Path:
    return bundle_root() / "docs"


def default_packs_dir() -> Path:
    """优先用用户目录下的 packs/（可替换数据包），否则用打包内置的。"""
    local = Path("packs")
    if (local / "act2autochess" / "pack.json").exists() or any(local.glob("*/pack.json")):
        return local
    return bundle_root() / "packs"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))
