"""状态的错误类型。

单独一个模块，是为了让 actions / observers / reducer / engine 都能引用它，
又不互相 import 成环。
"""

from __future__ import annotations


class ActionError(ValueError):
    """动作或观测不合法（资金不足、格子为空、超上限……）。"""
