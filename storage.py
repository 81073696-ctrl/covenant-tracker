"""本地数据的安全持久化原语。"""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_bytes(path: str | Path, data: bytes) -> None:
    """先完整写入同目录临时文件，再原子替换目标文件。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            tmp.unlink()
