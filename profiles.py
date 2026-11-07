"""识别配置（profile）：一套皮肤 / 分辨率对应一份布局 + 模板。

为什么要这个：官方客户端会出皮肤（比如「乌萨斯齐心团」终端主题），不同皮肤的
取色、描边、HUD 底衬都不一样；同一套数字模板不可能通吃。所以把「怎么认」拆成
一组可切换的配置，每套配置独立存：

    var/profiles/<id>/
        layout.json      区域（比例坐标）
        ocr_digits.npz   数字模板
        icons.npz        干员头像模板
        results.npz      通关/失败模板
        vision.json      阈值等参数
        meta.json        名字、创建时间、备注

切换配置只改 ``var/active_profile.json``，不改任何模板内容。
旧版本把模板直接放在 ``var/`` 根下，第一次启动会自动迁到 ``profiles/default/``。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

DEFAULT_ID = "default"
FILES = ("layout.json", "ocr_digits.npz", "icons.npz", "results.npz", "vision.json")


class ProfileError(ValueError):
    """配置不存在 / 名字非法。"""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _safe_id(pid: str) -> str:
    pid = str(pid or "").strip()
    if not pid:
        raise ProfileError("配置 id 不能为空")
    if not all(ch.isalnum() or ch in "-_" for ch in pid):
        raise ProfileError("配置 id 只能用字母、数字、- 和 _")
    return pid[:48]


class ProfileManager:
    """管理 ``var/profiles/`` 下的多套识别配置。"""

    def __init__(self, var_dir: str | Path = "var"):
        self.var_dir = Path(var_dir)
        self.root = self.var_dir / "profiles"
        self.active_path = self.var_dir / "active_profile.json"
        self.ensure()

    # -- 初始化 -----------------------------------------------------------
    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        default = self.root / DEFAULT_ID
        if not default.exists():
            default.mkdir()
            for name in FILES:
                src = self.var_dir / name
                if src.is_file():
                    # 迁移而不是复制：避免根目录留一份过期模板，两个来源打架
                    try:
                        shutil.move(str(src), str(default / name))
                    except OSError:
                        shutil.copy2(src, default / name)
            self._write_meta(DEFAULT_ID, name="默认（旧版模板迁移）")
        if not self.active_path.exists():
            self._set_active(DEFAULT_ID)

    # -- 查询 -------------------------------------------------------------
    def ids(self) -> list[str]:
        out = []
        if self.root.exists():
            for p in sorted(self.root.iterdir()):
                if p.is_dir() and (p / "meta.json").exists():
                    out.append(p.name)
        return out

    def exists(self, pid: str) -> bool:
        return _safe_id(pid) in self.ids()

    def meta(self, pid: str) -> dict:
        pid = _safe_id(pid)
        path = self.root / pid / "meta.json"
        if not path.exists():
            raise ProfileError(f"配置不存在：{pid}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data.setdefault("id", pid)
        data.setdefault("name", pid)
        return data

    def list(self) -> list[dict]:
        active = self.active_id()
        items = []
        for pid in self.ids():
            meta = self.meta(pid)
            d = self.root / pid
            items.append(
                {
                    "id": pid,
                    "name": meta.get("name") or pid,
                    "created": meta.get("created", ""),
                    "updated": meta.get("updated", ""),
                    "note": meta.get("note", ""),
                    "active": pid == active,
                    "files": {name: (d / name).is_file() for name in FILES},
                }
            )
        items.sort(key=lambda it: (not it["active"], it["id"]))
        return items

    def active_id(self) -> str:
        try:
            data = json.loads(self.active_path.read_text(encoding="utf-8"))
            pid = str(data.get("id") or DEFAULT_ID)
        except (OSError, ValueError, AttributeError):
            pid = DEFAULT_ID
        return pid if (self.root / pid).is_dir() else DEFAULT_ID

    def active_dir(self) -> Path:
        return self.root / self.active_id()

    # -- 切换 / 新建 -------------------------------------------------------
    def activate(self, pid: str) -> Path:
        pid = _safe_id(pid)
        if not (self.root / pid).is_dir():
            raise ProfileError(f"配置不存在：{pid}")
        self._set_active(pid)
        return self.root / pid

    def create(self, pid: str, name: str = "", *, copy_from: str | None = None) -> Path:
        pid = _safe_id(pid)
        target = self.root / pid
        if target.exists():
            raise ProfileError(f"配置已存在：{pid}")
        target.mkdir(parents=True)
        src = self.root / (_safe_id(copy_from) if copy_from else self.active_id())
        if not src.is_dir():
            raise ProfileError(f"来源配置不存在：{copy_from or self.active_id()}")
        for fname in FILES:
            if (src / fname).is_file():
                shutil.copy2(src / fname, target / fname)
        self._write_meta(pid, name=name or pid)
        return target

    def rename(self, pid: str, name: str) -> None:
        pid = _safe_id(pid)
        meta = self.meta(pid)
        meta["name"] = str(name).strip()[:40] or pid
        meta["updated"] = _now()
        (self.root / pid / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # -- 内部 -------------------------------------------------------------
    def _set_active(self, pid: str) -> None:
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.active_path.write_text(
            json.dumps({"id": pid}, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def _write_meta(self, pid: str, *, name: str) -> None:
        path = self.root / pid / "meta.json"
        data = {"id": pid, "name": name, "created": _now(), "updated": _now(), "note": ""}
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def active_vision_dir(var_dir: str | Path = "var") -> Path:
    """给 doctor / 管线用：返回当前激活配置的目录（会自动迁移旧文件）。"""
    return ProfileManager(var_dir).active_dir()
