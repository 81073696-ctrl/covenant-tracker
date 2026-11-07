"""生成给外部审查用的文件清单（路径 + sha1 + 行数），方便对方快速 diff。

只扫源码/测试/脚本/文档，跳过 var/、packs/（数据太大）、__pycache__。
输出：var/collab/changed-files.txt
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "var" / "collab" / "changed-files.txt"
INCLUDE = ("covenant", "tests", "scripts", "docs")
SUFFIX = {".py", ".js", ".html", ".css", ".md", ".json", ".bat", ".vbs"}
SKIP = {"__pycache__", "var", "packs", "dist", "build"}


def main() -> None:
    rows: list[str] = []
    for top in INCLUDE:
        base = ROOT / top
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in SUFFIX:
                continue
            if any(part in SKIP for part in path.parts):
                continue
            rel = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            digest = hashlib.sha1(data).hexdigest()[:12]
            lines = data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
            rows.append(f"{digest}  {lines:>5}  {rel}")
    rows.sort(key=lambda r: r.split()[-1])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"{len(rows)} files -> {OUT}")


if __name__ == "__main__":
    main()
