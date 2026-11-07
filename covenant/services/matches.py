"""战绩：落一条记录、导入导出、聚合视图。

从 ``Engine`` 里抽出来，是因为「记录」和「对局状态」是两条独立的生命周期：
对局可以重开，战绩库不清；社区上传、CSV 导出也不该碰 engine 的锁。
``Engine`` 只保留薄委派，保持旧 API 不变。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..stats.aggregate import summarize
from ..stats.model import RESULTS, MatchRecord
from ..stats.store import MatchStore


class MatchService:
    def __init__(self, path: Path | str | None = None, store: MatchStore | None = None) -> None:
        self.store = store or MatchStore(path or "var/matches.jsonl")

    def attach(self, path: Path | str) -> MatchStore:
        self.store = MatchStore(path)
        return self.store

    def find_by_match_id(self, match_id: str) -> MatchRecord | None:
        return self.store.find_by_match_id(match_id)

    def record(
        self,
        state,
        pack,
        *,
        result: str = "fail",
        cups: int | None = None,
        note: str = "",
        ledger: Callable[[MatchRecord], None] | None = None,
    ) -> MatchRecord:
        """写一条战绩；``ledger`` 失败时回滚这条记录并抛出。

        调用方负责去重（按 match_id）和把异常翻译成用户能看的话。
        """
        if result not in RESULTS:
            raise ValueError(f"result 必须是 {', '.join(RESULTS)} 之一")
        rec = MatchRecord.from_state(state, pack, result=result, cups=cups, note=note)
        self.store.append(rec)
        if ledger is not None:
            try:
                ledger(rec)
            except Exception:
                self.store.remove(rec.id)
                raise
        return rec

    def view(
        self,
        pack,
        *,
        mode_id: str | None = None,
        band_id: str | None = None,
        patch: str | None = None,
        min_sample: int = 5,
    ) -> dict[str, Any]:
        return summarize(
            self.store.load(),
            pack,
            mode_id=mode_id or None,
            band_id=band_id or None,
            patch=patch or None,
            min_sample=max(1, int(min_sample)),
        )

    def import_text(self, text: str) -> dict[str, Any]:
        return {"ok": True, **self.store.import_jsonl(text)}

    def export(self, fmt: str = "jsonl") -> str:
        if fmt == "csv":
            return self.store.export_csv()
        return self.store.export_jsonl()
