"""Screen Lens —— 把「游戏画面为什么认不出来」摊开给人看的只读诊断台。

不依赖任何游戏数据包：你给它一张截图或一个窗口，它告诉你
每个区域读到了什么、每个字/每格最像什么、差在哪、怎么教。
"""

from __future__ import annotations

__version__ = "0.1.0"
APP_NAME = "Screen Lens"
__all__ = ["__version__", "APP_NAME"]
