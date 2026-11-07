"""识别管线：一帧图像 → 一组观测（Observation）。

不追求「一次认全」。原则是：认得出的给出高置信度，认不出的给低置信度或干脆
不产出；reducer 里手工值优先，所以识别错了不会把手填的正确值冲掉。

商店/整备区的格子用「整格截图」当模板，不依赖具体 UI 结构：
- 商店区域按 ``params.slots`` 均分成格子；
- 每格交给 ``IconMatcher`` 找最像的已学模板；
- 分数够高才认为「这一格是某个干员」，否则标记为未知格。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from ..core.types import Observation, ObservationKind
from .match import IconMatcher
from .ocr import DigitReader
from .roi import Layout, Region

KIND_BY_NAME = {
    "gold": ObservationKind.GOLD,
    "shop_level": ObservationKind.SHOP_LEVEL,
    "level": ObservationKind.SHOP_LEVEL,
    "round": ObservationKind.ROUND,
    "hp": ObservationKind.HP,
}


class VisionPipeline:
    def __init__(
        self,
        layout: Layout | None = None,
        digits: DigitReader | None = None,
        icons: IconMatcher | None = None,
        results: IconMatcher | None = None,
        units: IconMatcher | None = None,
        stars: IconMatcher | None = None,
        var_dir: str | Path = "var",
    ):
        self.layout = layout or Layout.default()
        self.digits = digits or DigitReader()
        self.icons = icons or IconMatcher()
        #: 结算画面模板（key 直接是 clear / fail），和干员头像分开存，
        #: 否则「最像谁」会在干员与结算横幅之间串味。
        self.results = results or IconMatcher()
        #: 整备区/战场的单位卡：和商店卡尺寸、构图都不同，单独一套模板。
        self.units = units or IconMatcher(min_sim=0.80)
        #: 星级角标（key 是 "1"/"2"/"3"）；没学过就按 1 星处理。
        self.stars = stars or IconMatcher(min_sim=0.85)
        self.var_dir = Path(var_dir)

    # -- 持久化 -----------------------------------------------------------
    def paths(self) -> dict[str, Path]:
        return {
            "layout": self.var_dir / "layout.json",
            "digits": self.var_dir / "ocr_digits.npz",
            "icons": self.var_dir / "icons.npz",
            "results": self.var_dir / "results.npz",
            "units": self.var_dir / "units.npz",
            "stars": self.var_dir / "stars.npz",
            "config": self.var_dir / "vision.json",
        }

    def config(self) -> dict[str, float]:
        """当前阈值（工作台/诊断读取）。"""
        return {
            "digit_min_sim": round(float(self.digits.min_sim), 3),
            "icon_min_sim": round(float(self.icons.min_sim), 3),
            "unit_min_sim": round(float(self.units.min_sim), 3),
            "star_min_sim": round(float(self.stars.min_sim), 3),
        }

    def set_config(self, updates: dict[str, float]) -> dict[str, float]:
        """只改阈值并落盘；调用方负责校验取值范围。"""
        mapping = {
            "digit_min_sim": self.digits,
            "icon_min_sim": self.icons,
            "unit_min_sim": self.units,
            "star_min_sim": self.stars,
        }
        for key, value in updates.items():
            target = mapping.get(key)
            if target is not None:
                target.min_sim = float(value)
        self.save()
        return self.config()

    def save(self) -> None:
        p = self.paths()
        p["layout"].parent.mkdir(parents=True, exist_ok=True)
        self.layout.save(p["layout"])
        self.digits.save(p["digits"])
        self.icons.save(p["icons"])
        self.results.save(p["results"])
        self.units.save(p["units"])
        self.stars.save(p["stars"])
        p["config"].write_text(
            json.dumps(self.config(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load(cls, var_dir: str | Path = "var") -> VisionPipeline:
        vd = Path(var_dir)
        pipe = cls(layout=Layout.load(vd / "layout.json"), var_dir=vd)
        pipe.digits.load(vd / "ocr_digits.npz")
        pipe.icons.load(vd / "icons.npz")
        pipe.results.load(vd / "results.npz")
        pipe.units.load(vd / "units.npz")
        pipe.stars.load(vd / "stars.npz")
        cfg = vd / "vision.json"
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                for key, target in (
                    ("digit_min_sim", pipe.digits),
                    ("icon_min_sim", pipe.icons),
                    ("unit_min_sim", pipe.units),
                    ("star_min_sim", pipe.stars),
                ):
                    if key in data:
                        target.min_sim = float(data[key])
            except (json.JSONDecodeError, TypeError, ValueError):
                pass
        return pipe

    # -- 切格 -------------------------------------------------------------
    def slot_rects(self, region: Region, frame_w: int, frame_h: int) -> list[tuple[int, int, int, int]]:
        n = int(region.params.get("slots") or 1)
        gap = float(region.params.get("gap") or 0.008)
        x, y, w, h = region.rect.pixels(frame_w, frame_h)
        boundaries, _mode = region.split_boundaries()
        if boundaries is not None:
            out = []
            for left, right in zip(boundaries, boundaries[1:]):
                lx = x + int(round(w * left))
                rx = x + int(round(w * right))
                out.append((lx, y, max(1, rx - lx), h))
            return out
        if n <= 1:
            return [(x, y, w, h)]
        total_gap = int(round(w * gap)) * (n - 1)
        cw = max(1, (w - total_gap) // n)
        out = []
        for i in range(n):
            out.append((x + i * (cw + int(round(w * gap))), y, cw, h))
        return out

    # -- 读一帧 -----------------------------------------------------------
    def read(self, frame) -> list[Observation]:
        img = frame.image
        h, w = img.shape[:2]
        obs: list[Observation] = []
        shop_level: int | None = None

        # 第一遍：数字与结算先读——商店区域的规模依赖商店等级。
        scalars = [r for r in self.layout.regions.items() if r[1].kind in ("digits", "result")]
        digit_hits: list[tuple[Any, int, float, str, list[int] | None]] = []
        for name, region in scalars:
            x, y, rw, rh = region.rect.pixels(w, h)
            if rw < 4 or rh < 4:
                continue
            crop = img[y : y + rh, x : x + rw]

            if region.kind == "digits":
                text, conf = self.digits.read(crop, invert=bool(region.params.get("invert")))
                if not text or not text.isdigit():
                    continue
                kind = KIND_BY_NAME.get(region.target or name, None)
                if kind is None:
                    continue
                levels = region.params.get("levels")
                digit_hits.append(
                    (
                        kind,
                        int(text),
                        float(conf),
                        name,
                        [int(v) for v in levels] if levels else None,
                    )
                )

            elif region.kind == "result":
                key, score = self.results.classify(crop)
                if key:
                    obs.append(
                        Observation(
                            kind=ObservationKind.RESULT,
                            value={"result": key, "confidence": round(float(score), 3)},
                            confidence=float(score),
                            source="vision",
                            note=f"roi={name}",
                        )
                    )

        # 同一个字段可能有多块区域（L1 的角标与 L2+ 的角标位置不同）：
        # 优先信「读到的值落在该区域适用等级里」的那一块。
        chosen: dict[Any, tuple[int, float, str, bool]] = {}
        for kind, val, conf, name, levels in digit_hits:
            valid = levels is None or val in levels
            prev = chosen.get(kind)
            if prev is None or (valid and not prev[3]):
                chosen[kind] = (val, conf, name, valid)
        for kind, (val, conf, name, _valid) in chosen.items():
            if kind == ObservationKind.SHOP_LEVEL:
                shop_level = val
            obs.append(
                Observation(
                    kind=kind,
                    value=val,
                    confidence=conf,
                    source="vision",
                    note=f"roi={name}",
                )
            )

        # 第二遍：按商店等级挑对应的商店区域（4 格/5 格），再读单位区。
        for name, region in self.layout.regions.items():
            x, y, rw, rh = region.rect.pixels(w, h)
            if rw < 4 or rh < 4:
                continue

            if region.kind == "shop":
                levels = region.params.get("levels")
                if levels:
                    if shop_level is None:
                        continue
                    if int(shop_level) not in [int(v) for v in levels]:
                        continue
                slots = self.slot_rects(region, w, h)
                chars = int(region.params.get("chars") or len(slots))
                slots = slots[: max(1, min(chars, len(slots)))]
                art = float(region.params.get("art") or 1.0)
                cards: list[dict[str, Any]] = []
                for i, (sx, sy, sw, sh) in enumerate(slots):
                    patch = img[sy : sy + int(sh * art), sx : sx + sw]
                    key, score = self.icons.classify(patch)
                    cards.append(
                        {
                            "slot": i,
                            "op_id": key or None,
                            "known": bool(key),
                            "confidence": round(float(score), 3),
                        }
                    )
                if any(c["known"] for c in cards):
                    obs.append(
                        Observation(
                            kind=ObservationKind.SHOP,
                            value=cards,
                            confidence=float(np.mean([c["confidence"] for c in cards if c["known"]])),
                            source="vision",
                            note=f"roi={name}",
                        )
                    )

            elif region.kind == "units":
                slots = self.slot_rects(region, w, h)
                art = float(region.params.get("art") or 1.0)
                # 极小／最小化窗口里的几像素色块不是可靠空区证据。
                if any(sw < 16 or int(sh * art) < 16 for _sx, _sy, sw, sh in slots):
                    continue
                star_box = region.params.get("star_rect")
                units: list[dict[str, Any]] = []
                scores: list[float] = []
                for i, (sx, sy, sw, sh) in enumerate(slots):
                    ch = max(1, int(sh * art))
                    patch = img[sy : sy + ch, sx : sx + sw]
                    key, score = self.units.classify(patch)
                    scores.append(float(score))
                    if not key:
                        continue
                    star = 1
                    if star_box and self.stars.keys():
                        try:
                            bx, by, bw, bh = (float(v) for v in star_box)
                        except (TypeError, ValueError):
                            bx = by = bw = bh = 0.0
                        if bw > 0 and bh > 0:
                            sp = img[
                                sy + int(sh * by) : sy + int(sh * (by + bh)),
                                sx + int(sw * bx) : sx + int(sw * (bx + bw)),
                            ]
                            sk, _ss = self.stars.classify(sp)
                            if sk in ("1", "2", "3"):
                                star = int(sk)
                    units.append(
                        {"op_id": key, "star": star, "slot": i, "confidence": round(float(score), 3)}
                    )
                kind = ObservationKind.BOARD if name.startswith("board") else ObservationKind.BENCH
                if units:
                    obs.append(
                        Observation(
                            kind=kind,
                            value=units,
                            confidence=float(np.mean([u["confidence"] for u in units])),
                            source="vision",
                            note=f"roi={name}",
                        )
                    )
                elif self.units.keys() and scores:
                    # Kimi 协作：空区域也要产出观测，否则撤下的单位会永久留在状态里。
                    # 相似度不够低时只给低置信度，让 FollowGate 等待，不能误清空。
                    empty_limit = float(region.params.get("empty_max_sim", max(0.0, self.units.min_sim - 0.30)))
                    confidence = 0.99 if max(scores) < empty_limit else 0.50
                    obs.append(
                        Observation(
                            kind=kind,
                            value=[],
                            confidence=confidence,
                            source="vision",
                            note=f"roi={name}",
                        )
                    )
        return obs

    # -- 教它 -------------------------------------------------------------
    def learn_digit(self, frame, region_name: str, text: str) -> int:
        region = self.layout.region(region_name)
        if region is None:
            return 0
        h, w = frame.image.shape[:2]
        x, y, rw, rh = region.rect.pixels(w, h)
        n = self.digits.learn(frame.image[y : y + rh, x : x + rw], text)
        if n:
            self.digits.save(self.paths()["digits"])
        return n

    def learn_slot(self, frame, region_name: str, index: int, op_id: str) -> bool:
        region = self.layout.region(region_name)
        if region is None:
            return False
        h, w = frame.image.shape[:2]
        rects = self.slot_rects(region, w, h)
        if not (0 <= index < len(rects)):
            return False
        sx, sy, sw, sh = rects[index]
        art = float(region.params.get("art") or 1.0)
        ok = self.icons.add(op_id, frame.image[sy : sy + int(sh * art), sx : sx + sw])
        if ok:
            self.icons.save(self.paths()["icons"])
        return ok

    def learn_result(self, frame, region_name: str, result: str) -> bool:
        """教一张结算画面模板；``result`` 只接受 clear / fail。"""
        if result not in ("clear", "fail"):
            return False
        region = self.layout.region(region_name)
        if region is None:
            return False
        h, w = frame.image.shape[:2]
        x, y, rw, rh = region.rect.pixels(w, h)
        ok = self.results.add(result, frame.image[y : y + rh, x : x + rw])
        if ok:
            self.results.save(self.paths()["results"])
        return ok

    def learn_unit(self, frame, region_name: str, index: int, op_id: str) -> bool:
        """学一个整备区/战场单位（和商店卡分开存，几何也不同）。"""
        region = self.layout.region(region_name)
        if region is None:
            return False
        h, w = frame.image.shape[:2]
        rects = self.slot_rects(region, w, h)
        if not (0 <= index < len(rects)):
            return False
        sx, sy, sw, sh = rects[index]
        art = float(region.params.get("art") or 1.0)
        ok = self.units.add(op_id, frame.image[sy : sy + int(sh * art), sx : sx + sw])
        if ok:
            self.units.save(self.paths()["units"])
        return ok

    def learn_star(self, frame, region_name: str, index: int, star: int) -> bool:
        """学一个星级角标（1/2/3）；需要在区域 params 里配 ``star_rect``。"""
        star = int(star)
        if star not in (1, 2, 3):
            return False
        region = self.layout.region(region_name)
        if region is None:
            return False
        box = region.params.get("star_rect")
        try:
            bx, by, bw, bh = (float(v) for v in box)  # type: ignore[union-attr]
        except (TypeError, ValueError):
            return False
        if bw <= 0 or bh <= 0:
            return False
        h, w = frame.image.shape[:2]
        rects = self.slot_rects(region, w, h)
        if not (0 <= index < len(rects)):
            return False
        sx, sy, sw, sh = rects[index]
        patch = frame.image[
            sy + int(sh * by) : sy + int(sh * (by + bh)),
            sx + int(sw * bx) : sx + int(sw * (bx + bw)),
        ]
        ok = self.stars.add(str(star), patch)
        if ok:
            self.stars.save(self.paths()["stars"])
        return ok

    def status(self) -> dict[str, Any]:
        return {
            "layout_source": self.layout.source,
            "regions": {k: v.kind for k, v in self.layout.regions.items()},
            "digit_templates": {k: len(v) for k, v in self.digits.templates.items()},
            "icon_templates": len(self.icons.keys()),
            "result_templates": len(self.results.keys()),
            "unit_templates": len(self.units.keys()),
            "star_templates": len(self.stars.keys()),
        }
