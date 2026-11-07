"""卫戍协议 · 记牌器（Covenant Tracker）。

分层（数据自左向右流动，每层只依赖左边一层）::

    capture  →  vision  →  state  →  calc  →  ui
    截图        识别       状态       计算     展示

* ``capture``  只负责“拿到一帧图像”，可换屏幕/GDI/回放文件夹；
* ``vision``   把一帧图像变成 ``Observation``（带置信度）；
* ``state``    把观测和人工操作合并成一份“绝对快照”，可反复纠错；
* ``calc``     纯函数：卡池余量、商店概率、经济规划、盟约层数；
* ``ui``       本机网页面板 + 可选置顶小窗；
* ``data``     数据包加载与校验，赛季数据全部外置在 ``packs/``。

核心原则：**任何游戏内的东西都不写死在代码里**。UI 长什么样、池子多大、
有几张牌，全部来自数据包；识别不到就退化为人工点按，功能不降级。
"""

from .version import APP_NAME, APP_NAME_EN, APP_TAGLINE, PACK_SCHEMA, __version__

__all__ = [
    "__version__",
    "APP_NAME",
    "APP_NAME_EN",
    "APP_TAGLINE",
    "PACK_SCHEMA",
]
