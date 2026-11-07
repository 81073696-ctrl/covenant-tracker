"""上线体检：一次性回答「现在这套东西能不能用、缺什么」。

不连游戏、不改状态，只读数据包 / 布局 / 模板 / 截图，输出一份清单：

    py -3 -m covenant doctor
    py -3 -m covenant doctor --frames var/demo_frames

设计原则：每一项都给「缺了会怎样 + 怎么补」，而不是只报 True/False。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..core.types import ObservationKind
from .pipeline import VisionPipeline

# digits 区域按名字决定读到什么；这四个是 HUD 的必备项
REQUIRED_DIGITS = {
    "gold": "资金",
    "level": "调度中心等级",
    "round": "回合",
    "hp": "生命",
}
LEVEL_ALIASES = {"level", "shop_level"}


def _item(name: str, level: str, detail: str, fix: str = "") -> dict[str, Any]:
    return {"name": name, "level": level, "detail": detail, "fix": fix}


def run_doctor(pack, var_dir: str | Path = "var", frames_dir: str | Path | None = None) -> dict[str, Any]:
    var_dir = Path(var_dir)
    # 多套配置时，体检看的是当前激活的那套（旧布局会自动迁到 profiles/default/）
    from .profiles import active_vision_dir

    vision_dir = active_vision_dir(var_dir)
    pipeline = VisionPipeline.load(vision_dir)
    layout = pipeline.layout
    regions = layout.regions

    items: list[dict[str, Any]] = []

    # 1) 数据包
    modes = getattr(pack, "modes", {}) or {}
    ops = getattr(pack, "operators", {}) or {}
    warn_count = len(getattr(pack, "warnings", []) or [])
    items.append(
        _item(
            "数据包",
            "ok" if modes and ops else "missing",
            f"{pack.id} · {pack.season} · {len(ops)} 干员 / {len(modes)} 模式"
            + (f" · {warn_count} 条警告" if warn_count else ""),
            "跑 py -3 -m covenant pack 生成，或把新赛季数据包放进 packs/",
        )
    )

    # 2) 布局
    source = getattr(layout, "source", "default") or "default"
    if not regions:
        level = "missing"
    elif source in ("default", "preset?"):
        level = "warn"
    else:
        level = "ok"
    items.append(
        _item(
            "布局",
            level,
            f"来源 {source} · {len(regions)} 个区域",
            "进 /teach 把资金/等级/回合/生命/商店框一遍并保存",
        )
    )

    # 3) HUD 数字区
    digit_names = {
        name
        for name, region in regions.items()
        if getattr(region, "kind", "") == "digits"
    }
    missing_digits = [
        label
        for key, label in REQUIRED_DIGITS.items()
        if key not in digit_names and not (key == "level" and (digit_names & LEVEL_ALIASES))
    ]
    items.append(
        _item(
            "HUD 区域",
            "missing" if missing_digits else "ok",
            "缺：" + "、".join(missing_digits) if missing_digits else f"{len(digit_names)} 个数字区齐全",
            "在 /teach 补框；等级区可以叫 level 或 shop_level",
        )
    )

    # 4) 模板
    n_digits = len(getattr(pipeline.digits, "templates", {}) or {})
    n_icons = len(getattr(pipeline.icons, "templates", {}) or {})
    n_results = len(getattr(pipeline.results, "templates", {}) or {})
    items.append(
        _item(
            "数字模板",
            "ok" if n_digits >= 8 else "warn",
            f"{n_digits} 个字形",
            "至少学 0-9 各一次；框紧一点，别带边框",
        )
    )
    items.append(
        _item(
            "干员模板",
            "ok" if n_icons >= 10 else ("warn" if n_icons else "missing"),
            f"{n_icons} 个干员",
            "点商店格 → 选干员 → 学习；先学当前池里常见的 20~30 个",
        )
    )
    items.append(
        _item(
            "结算模板",
            "ok" if n_results >= 2 else "warn",
            f"{n_results} 个（通关/失败各一个才算齐）",
            "在 /teach 第 5 节分别框通关、失败画面各学一次",
        )
    )

    # 5) 商店格数覆盖
    shop_regions = [r for r in regions.values() if getattr(r, "kind", "") == "shop"]
    covered: set[int] = set()
    for region in shop_regions:
        levels = (getattr(region, "params", {}) or {}).get("levels")
        if levels:
            covered |= {int(x) for x in levels}
        else:
            covered |= {1, 2, 3, 4, 5, 6}
    missing_levels = [n for n in range(1, 7) if n not in covered]
    items.append(
        _item(
            "商店格数",
            "ok" if not missing_levels else "warn",
            "覆盖 L" + "/L".join(str(n) for n in sorted(covered))
            if covered
            else "没有商店区域",
            "L1 与 L2+ 格数不同时，各加一个 shop 区域并写 params.levels",
        )
    )

    # 6) 阈值配置
    threshold = float(getattr(pipeline.icons, "min_sim", 0.62))
    items.append(
        _item(
            "识别阈值",
            "ok" if 0.5 <= threshold <= 0.95 else "warn",
            f"icon_min_sim = {threshold:.2f}",
            "var/vision.json 里调；0.90 偏严（宁缺毋滥），0.75 偏宽",
        )
    )

    # 7) 截图回放（可选）
    frames_report: dict[str, Any] = {}
    if frames_dir:
        frames_report = _check_frames(pipeline, Path(frames_dir))

    gaps = [item["name"] for item in items if item["level"] == "missing"]
    return {
        "ok": not gaps,
        "items": items,
        "gaps": gaps,
        "frames": frames_report,
        "version": getattr(pack, "season", ""),
    }


def _check_frames(pipeline: VisionPipeline, frames_dir: Path) -> dict[str, Any]:
    from PIL import Image

    from ..capture.base import Frame

    files = sorted(
        p for p in frames_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp")
    )
    if not files:
        return {"ok": False, "detail": f"{frames_dir} 里没有图片"}

    seen = {kind: 0 for kind in (ObservationKind.GOLD, ObservationKind.SHOP_LEVEL,
                                 ObservationKind.ROUND, ObservationKind.HP,
                                 ObservationKind.SHOP, ObservationKind.BENCH,
                                 ObservationKind.BOARD, ObservationKind.RESULT)}
    shop_known = shop_total = 0
    errors: list[str] = []
    for path in files:
        try:
            image = np.asarray(Image.open(path).convert("RGB"))
        except Exception as exc:  # noqa: BLE001 - 体检要尽量跑完
            errors.append(f"{path.name}: {exc}")
            continue
        try:
            obs = pipeline.read(Frame(image))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.name}: {exc}")
            continue
        for o in obs:
            seen[o.kind] = seen.get(o.kind, 0) + 1
            if o.kind == ObservationKind.SHOP:
                for card in o.value:
                    shop_total += 1
                    if card.get("known"):
                        shop_known += 1

    return {
        "ok": bool(files),
        "frames": len(files),
        "errors": errors[:5],
        "hud": {k: seen.get(k, 0) for k in ("gold", "shop_level", "round", "hp")},
        "shop": {"known": shop_known, "total": shop_total},
        "result": seen.get(ObservationKind.RESULT, 0),
    }


def format_report(report: dict[str, Any]) -> str:
    icon = {"ok": "✓", "warn": "!", "missing": "✗"}
    lines = [f"上线体检（数据包 {report.get('version') or '?'}）", ""]
    for item in report["items"]:
        lines.append(f" {icon.get(item['level'], '?')} {item['name']}：{item['detail']}")
        if item["level"] != "ok" and item.get("fix"):
            lines.append(f"     → {item['fix']}")

    frames = report.get("frames") or {}
    if frames:
        lines.append("")
        lines.append(f"截图回放（{frames.get('frames', 0)} 帧）")
        hud = frames.get("hud", {})
        lines.append(
            "  HUD：" + " · ".join(f"{k} {v}" for k, v in hud.items())
        )
        shop = frames.get("shop", {})
        rate = (shop.get("known", 0) / shop["total"]) if shop.get("total") else 0.0
        lines.append(
            f"  商店：{shop.get('known', 0)}/{shop.get('total', 0)} = {rate:.0%}"
            "（只算干员格；道具格不读）"
        )
        if frames.get("errors"):
            lines.append("  错误：" + "；".join(frames["errors"]))

    lines.append("")
    if report["ok"]:
        lines.append("结论：没有缺项，可以开一局试识别。")
    else:
        lines.append("结论：还缺 " + "、".join(report["gaps"]) + "，按上面的 → 补齐。")
    return "\n".join(lines)
