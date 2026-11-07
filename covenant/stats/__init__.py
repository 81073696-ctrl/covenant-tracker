"""战绩与统计：记录一局 → 存 JSONL → 聚合出 pick 率 / 通关率。"""

from .aggregate import DEFAULT_MIN_SAMPLE, summarize, wilson_interval
from .model import RESULT_CLEAR, RESULT_FAIL, RESULT_LABELS, RESULT_QUIT, RESULTS, MatchRecord
from .store import MatchStore

__all__ = [
    "MatchRecord",
    "MatchStore",
    "RESULTS",
    "RESULT_CLEAR",
    "RESULT_FAIL",
    "RESULT_QUIT",
    "RESULT_LABELS",
    "summarize",
    "wilson_interval",
    "DEFAULT_MIN_SAMPLE",
]
