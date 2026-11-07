"""盟约（羁绊）：谁会亮、还差几个、层数多少。

三条容易搞混的规则，分开处理：

1. **激活看「不同干员数」**，不是看张数：同一个干员上两个也只算 1。
2. **默认只数战场**；少数盟约（``in_deck=True``）把整备区也算进去。
3. **层数（layer）是另一套东西**：在战斗内由各种效果累积，跨回合保留，
   与「场上几个」没有必然关系。层数由识别/手填维护，激活只看人数。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..data.pack import Bond, Mode, Pack
from ..core.types import ZONE_BENCH, ZONE_BOARD

if TYPE_CHECKING:  # 只为类型注解，避免 calc ←→ state 循环导入
    from ..state.state import GameState


@dataclass
class BondStatus:
    id: str
    name: str
    category: str
    count: int                     # 当前场上（+整备区，视规则）不同干员数
    activate_at: int
    active: bool
    next_threshold: int | None     # 下一个档位（暂只用于展示）
    missing_to_next: int | None
    layers: int
    in_deck: bool
    #: 整备区里带有该盟约、可以补上场的干员名字
    bench_candidates: list[str] = field(default_factory=list)
    desc: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "count": self.count,
            "activate_at": self.activate_at,
            "active": self.active,
            "next_threshold": self.next_threshold,
            "missing_to_next": self.missing_to_next,
            "layers": self.layers,
            "in_deck": self.in_deck,
            "bench_candidates": self.bench_candidates,
            "desc": self.desc,
        }


def _distinct_ops(units) -> set[str]:
    return {u.op_id for u in units}


def bond_status(
    pack: Pack,
    state: GameState,
    *,
    mode: Mode | None = None,
    extra_ids: set[str] | None = None,
) -> list[BondStatus]:
    """计算全部盟约状态，按「已激活 → 接近激活 → 其它」排序。

    ``extra_ids`` 可以模拟「再上一张牌会怎样」。
    """
    mode = mode or pack.mode(state.mode_id)
    board_ops = _distinct_ops(state.board)
    bench_ops = _distinct_ops(state.bench)
    if extra_ids:
        board_ops = board_ops | set(extra_ids)

    counts: dict[str, int] = {}
    for op_id in board_ops:
        op = pack.op(op_id)
        if not op:
            continue
        for b in op.bonds:
            counts[b] = counts.get(b, 0) + 1

    statuses: list[BondStatus] = []
    for bond in pack.bonds.values():
        count = counts.get(bond.id, 0)
        if bond.in_deck:
            deck_only = bench_ops - board_ops
            for op_id in deck_only:
                op = pack.op(op_id)
                if op and bond.id in op.bonds:
                    count += 1
        active = count >= bond.activate_at
        levels = [x for x in bond.levels if x > count]
        nxt = min(levels) if levels else None
        missing = (nxt - count) if nxt is not None else None

        candidates: list[str] = []
        want = bond.id
        for op_id in bench_ops - board_ops:
            op = pack.op(op_id)
            if op and want in op.bonds:
                candidates.append(op.name)

        statuses.append(
            BondStatus(
                id=bond.id,
                name=bond.name,
                category=bond.category,
                count=count,
                activate_at=bond.activate_at,
                active=active,
                next_threshold=nxt,
                missing_to_next=missing,
                layers=state.bond_layers.get(bond.id, 0),
                in_deck=bond.in_deck,
                bench_candidates=sorted(candidates),
                desc=bond.desc,
            )
        )

    statuses.sort(
        key=lambda s: (
            0 if s.active else 1,
            s.missing_to_next if s.missing_to_next is not None else 99,
            -s.count,
            s.name,
        )
    )
    return statuses


def bond_table(pack: Pack, state: GameState, *, mode: Mode | None = None) -> dict[str, Any]:
    statuses = bond_status(pack, state, mode=mode)
    return {
        "items": [s.to_dict() for s in statuses],
        "active_count": sum(1 for s in statuses if s.active),
        "total": len(statuses),
    }
