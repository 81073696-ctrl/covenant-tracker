"""战绩存储：一行一条 JSON（JSONL），可追加、可导入导出、可去重。

为什么不是数据库
----------------
个人使用就是几百到几千条记录，JSONL 足够；它是纯文本，坏了能手工修，
发群里就能分享，将来要传给社区服务器也不用换格式。真到了十万条再换 SQLite，
届时只要换掉这个类，聚合层不用动。
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Iterable

from ..core.storage import atomic_write_bytes
from .model import MatchRecord


class MatchStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._cache: list[MatchRecord] | None = None
        self._mtime: float = -1.0
        self.bad_lines = 0

    # -- 读取 -------------------------------------------------------------
    def load(self, *, force: bool = False) -> list[MatchRecord]:
        """读全部记录；文件变了才重新解析。"""
        with self._lock:
            if not self.path.exists():
                self._cache = []
                self._mtime = -1.0
                return []
            mtime = self.path.stat().st_mtime
            if self._cache is not None and not force and mtime == self._mtime:
                return self._cache

            records: list[MatchRecord] = []
            seen: set[str] = set()
            seen_match_ids: set[str] = set()
            self.bad_lines = 0
            try:
                text = self.path.read_text(encoding="utf-8")
            except OSError:
                self._cache = []
                self._mtime = mtime
                return []

            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = MatchRecord.from_dict(json.loads(line))
                except (ValueError, TypeError, KeyError):
                    self.bad_lines += 1
                    continue
                if rec.id in seen or (rec.match_id and rec.match_id in seen_match_ids):
                    continue
                seen.add(rec.id)
                if rec.match_id:
                    seen_match_ids.add(rec.match_id)
                records.append(rec)

            records.sort(key=lambda r: r.ts)
            self._cache = records
            self._mtime = mtime
            return records

    def count(self) -> int:
        return len(self.load())

    def find_by_match_id(self, match_id: str) -> MatchRecord | None:
        """按对局 ID 查询；旧记录的空 ID 不参与判重。"""
        if not match_id:
            raise ValueError("match_id 不能为空")
        return next((record for record in self.load() if record.match_id == match_id), None)

    # -- 写入 -------------------------------------------------------------
    def append(self, rec: MatchRecord) -> bool:
        """追加一条；记录 id 或非空对局 id 重复则忽略，返回是否真的写入。"""
        with self._lock:
            existing = self.load()
            if any(r.id == rec.id or (rec.match_id and r.match_id == rec.match_id) for r in existing):
                return False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(rec.to_dict(), ensure_ascii=False)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            existing.append(rec)
            self._cache = existing
            self._mtime = self.path.stat().st_mtime
            return True

    def remove(self, record_id: str) -> bool:
        """删除一条记录；用于跨存储写入失败时撤销刚追加的战绩。"""
        with self._lock:
            existing = self.load()
            remaining = [rec for rec in existing if rec.id != record_id]
            if len(remaining) == len(existing):
                return False
            self._rewrite(remaining)
            return True

    def extend(self, records: Iterable[MatchRecord]) -> dict[str, int]:
        """批量并入（导入用），按 id 去重后整体重写。"""
        with self._lock:
            existing = list(self.load())
            seen = {r.id for r in existing}
            incoming = list(records)
            added = 0
            seen_match_ids = {r.match_id for r in existing if r.match_id}
            for rec in incoming:
                if rec.id in seen or (rec.match_id and rec.match_id in seen_match_ids):
                    continue
                seen.add(rec.id)
                if rec.match_id:
                    seen_match_ids.add(rec.match_id)
                existing.append(rec)
                added += 1
            if added:
                self._rewrite(existing)
            return {"added": added, "skipped": len(incoming) - added, "total": len(existing)}

    def clear(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rewrite([])

    def _rewrite(self, records: list[MatchRecord]) -> None:
        records = sorted(records, key=lambda r: r.ts)
        data = "".join(
            json.dumps(rec.to_dict(), ensure_ascii=False) + "\n" for rec in records
        ).encode("utf-8")
        atomic_write_bytes(self.path, data)
        self._cache = records
        self._mtime = self.path.stat().st_mtime

    # -- 导入导出 ---------------------------------------------------------
    def export_jsonl(self) -> str:
        with self._lock:
            return "".join(
                json.dumps(r.to_dict(), ensure_ascii=False) + "\n" for r in self.load()
            )

    def import_jsonl(self, text: str) -> dict[str, int]:
        from .model import validate_record

        records: list[MatchRecord] = []
        bad = 0
        for line in (text or "").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise TypeError("战绩必须是对象")
                errors = validate_record(data)
                if errors:
                    raise ValueError("；".join(errors))
                records.append(MatchRecord.from_dict(data))
            except (ValueError, TypeError, KeyError):
                bad += 1
        if not records:
            return {"added": 0, "skipped": 0, "bad": bad, "total": self.count()}
        result = self.extend(records)
        result["bad"] = bad
        return result

    def export_csv(self) -> str:
        """给 Excel 看的扁平表：一局一行，阵容用分号拼。"""
        import csv
        import io

        from .model import RESULT_LABELS

        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "id", "对局ID", "时间", "模式", "策略", "赛季", "回合", "总回合", "剩余生命",
                "结果", "杯数", "商店等级", "剩余资金", "终局阵容", "盟约", "备注",
            ]
        )
        for r in self.load():
            comp = " ".join(f"{u.get('op_id')}{'★' * int(u.get('star') or 1)}" for u in r.board)
            writer.writerow(
                [
                    r.id,
                    r.match_id or "",
                    time_str(r.ts),
                    r.mode_name or r.mode_id,
                    r.band_name or (r.band_id or ""),
                    r.patch,
                    r.rounds,
                    r.total_rounds,
                    "" if r.hp_left is None else r.hp_left,
                    RESULT_LABELS.get(r.result, r.result),
                    "" if r.cups is None else r.cups,
                    r.shop_level,
                    r.gold_left,
                    comp,
                    " ".join(f"{k}:{v}" for k, v in r.bonds.items()),
                    r.note,
                ]
            )
        return buf.getvalue()


def time_str(ts: float) -> str:
    import time

    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
