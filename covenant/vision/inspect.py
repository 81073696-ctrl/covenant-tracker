"""识别工作台：把「为什么认不出」拆开给人看。

只做只读诊断 + 生成缩略图；不写状态、不动模板。
真正的「教它」仍然走 learn_* 接口，这里只给出建议。
"""

from __future__ import annotations

import base64
import io
from typing import Any

import numpy as np

from .ocr import to_gray
from .pipeline import KIND_BY_NAME, VisionPipeline

MAX_CROP_SIDE = 220  # 缩略图最长边，避免诊断包过大


def _crop_uri(image: np.ndarray) -> str:
    from PIL import Image

    arr = np.asarray(image)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    arr = np.clip(arr[..., :3], 0, 255).astype(np.uint8)
    img = Image.fromarray(arr)
    if max(img.size) > MAX_CROP_SIDE:
        img.thumbnail((MAX_CROP_SIDE, MAX_CROP_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _top(pairs: list[tuple[str, float]], k: int = 3) -> list[dict[str, Any]]:
    return [{"key": key, "score": round(float(score), 3)} for key, score in pairs[:k]]


def _digit_region(pipe: VisionPipeline, img: np.ndarray, region, x, y, w, h) -> dict[str, Any]:
    crop = img[y : y + h, x : x + w]
    gray = to_gray(crop)
    glyphs = pipe.digits.segment_boxes(gray, invert=bool(region.params.get("invert")))
    rows: list[dict[str, Any]] = []
    text: list[str] = []
    failed = False
    weak = False
    for left, right, glyph in glyphs:
        cand = pipe.digits.rank(glyph, k=3)
        best = cand[0] if cand else ("", 0.0)
        ok = bool(best[0]) and best[1] >= pipe.digits.min_sim
        if ok:
            text.append(best[0])
            if best[1] < 0.80:
                weak = True
        else:
            failed = True
        rows.append(
            {
                "x": int(left),
                "w": int(right - left),
                "crop": _crop_uri(gray[:, max(0, left - 2) : right + 2]),
                "best": {"key": best[0], "score": round(float(best[1]), 3)},
                "top": _top(cand),
                "ok": ok,
            }
        )
    joined = "".join(text)
    if not rows:
        status = "fail"
    elif failed:
        status = "warn"
    elif weak:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "text": joined if not failed else None,
        "partial": joined if failed else None,
        "confidence": round(
            float(np.mean([r["best"]["score"] for r in rows])) if rows else 0.0, 3
        ),
        "glyphs": rows,
        "crop": _crop_uri(crop),
        "hint": ""
        if not failed
        else "有字没学过或分数太低：看下面每个字的最像候选，补学缺的字",
    }


def _shop_region(pipe: VisionPipeline, img: np.ndarray, region, name, x, y, w, h, level) -> dict[str, Any]:
    levels = region.params.get("levels")
    if levels and level is None:
        return {
            "status": "skip",
            "reason": "商店等级没读出来，无法决定用 4 格还是 5 格布局",
            "crop": _crop_uri(img[y : y + h, x : x + w]),
            "slots": [],
        }
    if levels and int(level) not in [int(v) for v in levels]:
        return {
            "status": "skip",
            "reason": f"当前商店等级 L{level} 不归这块区域管（适用 {levels}）",
            "crop": _crop_uri(img[y : y + h, x : x + w]),
            "slots": [],
        }
    slots_rect = pipe.slot_rects(region, img.shape[1], img.shape[0])
    chars = int(region.params.get("chars") or len(slots_rect))
    slots_rect = slots_rect[: max(1, min(chars, len(slots_rect)))]
    art = float(region.params.get("art") or 1.0)
    slots: list[dict[str, Any]] = []
    for i, (sx, sy, sw, sh) in enumerate(slots_rect):
        patch = img[sy : sy + int(sh * art), sx : sx + sw]
        cand = pipe.icons.rank(patch, k=3)
        best = cand[0] if cand else ("", 0.0)
        ok = bool(best[0]) and best[1] >= pipe.icons.min_sim
        slots.append(
            {
                "index": i,
                "rect": [int(sx), int(sy), int(sw), int(sh)],
                "crop": _crop_uri(patch),
                "accepted": best[0] if ok else None,
                "best": {"key": best[0], "score": round(float(best[1]), 3)},
                "top": _top(cand),
                "threshold": pipe.icons.min_sim,
            }
        )
    known = sum(1 for s in slots if s["accepted"])
    return {
        "status": "ok" if known else "warn",
        "known": known,
        "total": len(slots),
        "crop": _crop_uri(img[y : y + h, x : x + w]),
        "slots": slots,
    }


def _units_region(pipe: VisionPipeline, img: np.ndarray, region, x, y, w, h) -> dict[str, Any]:
    slots_rect = pipe.slot_rects(region, img.shape[1], img.shape[0])
    art = float(region.params.get("art") or 1.0)
    star_box = region.params.get("star_rect")
    slots: list[dict[str, Any]] = []
    for i, (sx, sy, sw, sh) in enumerate(slots_rect):
        patch = img[sy : sy + int(sh * art), sx : sx + sw]
        cand = pipe.units.rank(patch, k=3)
        best = cand[0] if cand else ("", 0.0)
        ok = bool(best[0]) and best[1] >= pipe.units.min_sim
        star = None
        star_top: list[dict[str, Any]] = []
        if star_box and pipe.stars.keys():
            try:
                bx, by, bw, bh = (float(v) for v in star_box)
            except (TypeError, ValueError):
                bx = by = bw = bh = 0.0
            if bw > 0 and bh > 0:
                sp = img[
                    sy + int(sh * by) : sy + int(sh * (by + bh)),
                    sx + int(sw * bx) : sx + int(sw * (bx + bw)),
                ]
                sc = pipe.stars.rank(sp, k=3)
                star_top = _top(sc)
                if sc and sc[0][1] >= pipe.stars.min_sim:
                    star = sc[0][0]
        slots.append(
            {
                "index": i,
                "rect": [int(sx), int(sy), int(sw), int(sh)],
                "crop": _crop_uri(patch),
                "accepted": best[0] if ok else None,
                "best": {"key": best[0], "score": round(float(best[1]), 3)},
                "top": _top(cand),
                "star": star,
                "star_top": star_top,
                "threshold": pipe.units.min_sim,
            }
        )
    known = sum(1 for s in slots if s["accepted"])
    return {
        "status": "ok" if known else "warn",
        "known": known,
        "total": len(slots),
        "crop": _crop_uri(img[y : y + h, x : x + w]),
        "slots": slots,
    }


def inspect_frame(pipe: VisionPipeline, frame) -> dict[str, Any]:
    """把一帧拆成「每个区域看见了什么、为什么没过」。"""
    import time

    started = time.perf_counter()
    img = frame.image
    h, w = img.shape[:2]
    regions_out: list[dict[str, Any]] = []

    # 先读数字，商店格的判级依赖它。
    level: int | None = None
    for name, region in pipe.layout.regions.items():
        if region.kind != "digits":
            continue
        target = (region.target or name).lower()
        if target in ("shop_level", "level"):
            x, y, rw, rh = region.rect.pixels(w, h)
            crop = to_gray(img[y : y + rh, x : x + rw])
            glyphs = pipe.digits.segment_boxes(crop, invert=bool(region.params.get("invert")))
            text = "".join(
                (pipe.digits.rank(g, 1) or [("", 0.0)])[0][0] for _l, _r, g in glyphs
            )
            if text.isdigit():
                level = int(text)

    for name, region in pipe.layout.regions.items():
        x, y, rw, rh = region.rect.pixels(w, h)
        base: dict[str, Any] = {
            "name": name,
            "kind": region.kind,
            "rect": [int(x), int(y), int(rw), int(rh)],
            "target": region.target or "",
        }
        if rw < 4 or rh < 4:
            base.update(status="skip", reason="区域太小或未标定")
        elif region.kind in ("digits", "shop", "units"):
            levels = region.params.get("levels")
            if levels and level is not None and int(level) not in [int(v) for v in levels]:
                base.update(
                    status="skip",
                    reason=f"当前等级 L{level} 不用这块区域（适用 {list(levels)}）",
                    crop=_crop_uri(img[y : y + rh, x : x + rw]),
                )
            elif region.kind == "digits":
                base.update(_digit_region(pipe, img, region, x, y, rw, rh))
                kind = KIND_BY_NAME.get(region.target or name)
                base["maps_to"] = getattr(kind, "value", kind) if kind else None
            elif region.kind == "shop":
                base.update(_shop_region(pipe, img, region, name, x, y, rw, rh, level))
            else:
                base.update(_units_region(pipe, img, region, x, y, rw, rh))
        elif region.kind == "result":
            crop = img[y : y + rh, x : x + rw]
            cand = pipe.results.rank(crop, k=3)
            best = cand[0] if cand else ("", 0.0)
            base.update(
                status="ok" if best[0] and best[1] >= pipe.results.min_sim else "warn",
                crop=_crop_uri(crop),
                best={"key": best[0], "score": round(float(best[1]), 3)},
                top=_top(cand),
            )
        else:
            base.update(status="skip", reason=f"未知区域类型 {region.kind}")
        regions_out.append(base)

    digits_present = sorted(pipe.digits.templates)
    missing = [str(d) for d in range(10) if str(d) not in pipe.digits.templates]
    suggestions: list[str] = []
    if missing:
        suggestions.append(
            "数字模板缺 " + "/".join(missing) + f"：任何含这些数字的读数都会整串失败（当前有 {', '.join(digits_present) or '无'}）"
        )
    if len(pipe.icons.keys()) < 20:
        suggestions.append(
            f"商店模板只有 {len(pipe.icons.keys())} 个干员；没学过的会显示「未知」。这是正常保护，不是 bug。"
        )
    if not pipe.units.keys():
        suggestions.append("整备区/战场单位模板为空：需要在工作台里对每个单位点一次「这是谁」。")
    if not pipe.stars.keys():
        suggestions.append("星级模板为空：所有单位会按 1 星显示；学 2/3 星角标后才能区分。")
    if not pipe.results.keys():
        suggestions.append("结算模板为空：不会自动弹「本局通关/失败」，需要手动记录。")
    weak = [
        r["name"]
        for r in regions_out
        if r.get("kind") == "digits" and r.get("status") == "warn" and r.get("text")
    ]
    if weak:
        suggestions.append(
            "这些区域有低分命中（<0.80），数字可能认错：" + "、".join(weak) + "。看一眼实际值，必要时补学。"
        )

    # 这一帧如果直接喂给 reducer，会提交哪些观测（只读，不改状态）。
    emitted: list[dict[str, Any]] = []
    try:
        for obs in pipe.read(frame):
            emitted.append(
                {
                    "kind": getattr(obs.kind, "value", obs.kind),
                    "value": obs.value,
                    "confidence": round(float(obs.confidence), 3),
                    "note": obs.note or "",
                }
            )
    except Exception as exc:  # 诊断工具自己不能把页面打挂
        emitted = [{"kind": "error", "value": str(exc), "confidence": 0, "note": "read() 抛异常"}]

    return {
        "ok": True,
        "width": int(w),
        "height": int(h),
        "ts": getattr(frame, "ts", None),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
        "thresholds": {
            "digit": pipe.digits.min_sim,
            "icon": pipe.icons.min_sim,
            "unit": pipe.units.min_sim,
            "star": pipe.stars.min_sim,
        },
        "coverage": {
            "digits": {
                "present": digits_present,
                "missing": missing,
                "counts": {k: len(v) for k, v in sorted(pipe.digits.templates.items())},
            },
            "icons": len(pipe.icons.keys()),
            "units": len(pipe.units.keys()),
            "stars": len(pipe.stars.keys()),
            "results": len(pipe.results.keys()),
        },
        "shop_level": level,
        "regions": regions_out,
        "suggestions": suggestions,
        "observations": emitted,
    }
