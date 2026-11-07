"""把视觉观测合并进现有状态的纯函数。

抽出来的原因：``observers.py`` 的处理器同时做了「解析脏输入」「按槽位对齐」
「判断有没有变化」三件事，混在状态对象上很难单独测。这里只吃 list/dict，
返回新列表 + 是否变化，处理器只负责拿结果写回 ``GameState``。
"""

from __future__ import annotations

from typing import Any

from ..core.types import KIND_CHAR, ShopOffer, Unit


def merge_shop(current: list[ShopOffer], raw: Any, slots: int) -> tuple[list[ShopOffer], bool]:
    """按槽位合并商店；未提到的槽位保持原样（识别部分格子时不清空其余格）。"""
    shop = list(current)
    if len(shop) < slots:
        shop.extend(ShopOffer(slot=i) for i in range(len(shop), slots))
    changed = False
    for item in raw or []:
        if isinstance(item, ShopOffer):
            item = item.to_dict()
        if not isinstance(item, dict):
            continue
        try:
            slot = int(item.get("slot", -1))
        except (TypeError, ValueError):
            continue
        if not 0 <= slot < len(shop):
            continue
        current_offer = shop[slot]
        new_id = item.get("op_id")
        new_known = bool(item.get("known", new_id is not None))
        if current_offer.op_id == new_id and current_offer.known == new_known:
            continue
        shop[slot] = ShopOffer(
            slot=slot,
            op_id=new_id,
            tier=int(item.get("tier", 0) or 0),
            kind=item.get("kind", KIND_CHAR),
            known=new_known,
            bought=bool(item.get("bought", False)),
        )
        changed = True
    return shop, changed


def merge_units(units: list[Unit], raw: Any) -> tuple[list[Unit], bool]:
    """按 slot 合并整备区/战场；没认出来的槽位保留，新槽位追加。"""
    # DeepSeek 协作：经过稳定帧门控的完整空区域必须撤下旧单位，不能永久保留残影。
    if raw == []:
        return [], bool(units)
    merged = list(units)
    changed = False
    for item in raw or []:
        if isinstance(item, Unit):
            item = item.to_dict()
        if not isinstance(item, dict) or not item.get("op_id"):
            continue
        try:
            slot = int(item.get("slot", -1))
        except (TypeError, ValueError):
            continue
        if slot < 0:
            continue
        new_unit = Unit.from_dict(item)
        new_unit.slot = slot
        index = next(
            (
                i
                for i, unit in enumerate(merged)
                if int(unit.slot if unit.slot is not None else i) == slot
            ),
            None,
        )
        if index is None:
            merged.append(new_unit)
            changed = True
            continue
        old = merged[index]
        if (old.op_id, old.star, old.slot) == (new_unit.op_id, new_unit.star, new_unit.slot):
            continue
        merged[index] = new_unit
        changed = True
    return merged, changed
