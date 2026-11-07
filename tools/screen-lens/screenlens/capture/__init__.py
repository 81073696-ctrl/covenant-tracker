"""采集层：把「屏幕上的游戏窗口」变成一帧 numpy 图像。

接口只有三个方法，方便替换成任何来源（录屏、采集卡、回放文件夹）::

    class FrameSource:
        def grab(self) -> Frame: ...
        def png(self) -> bytes: ...
        def title(self) -> str: ...

设计约束：**只读屏幕**。不注入、不读取游戏内存、不碰任何反作弊敏感的东西。
所有识别都发生在像素层面，最坏情况就是识别失败退回手工点按。
"""

from .base import Frame, FrameSource
from .folder import FolderSource
from .screen import WindowCapture, list_windows
from .synthetic import SyntheticSource

__all__ = [
    "Frame",
    "FrameSource",
    "FolderSource",
    "SyntheticSource",
    "WindowCapture",
    "list_windows",
]
