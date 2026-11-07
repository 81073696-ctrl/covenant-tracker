"""数据包加载与校验。

设计目标
--------
1. **零硬编码**：干员、盟约、商店规则、收入曲线全部来自磁盘上的数据包；
2. **可版本化**：一个赛季一个目录，``pack.json`` 里带 ``schema`` 与来源；
3. **可容错**：缺字段就给默认值并在 ``Pack.warnings`` 里记一笔，绝不因为
   新赛季多了一个字段就崩掉；
4. **可热替换**：``Pack.reload()`` 重新读盘，UI 不需要重启。

一个数据包目录长这样::

    packs/act2autochess/
        pack.json          # 模式、经济、池子、来源
        operators.json     # 干员
        bonds.json         # 盟约
        layout.json        # 可选：识别布局（由「教它认界面」工具生成）

新增赛季时，复制一份目录、改 ``pack.json`` 里的 ``id``/``season``/数值即可，
代码一行不用动。文档见 ``docs/DATA_PACK.md``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..version import PACK_SCHEMA

# 置信度等级，从高到低
CONF_DATA = "data"          # 客户端数据表里直接读到的
CONF_WIKI = "wiki"          # 官方说明 / PRTS 等
CONF_COMMUNITY = "community"  # 社区实测
CONF_ASSUMED = "assumed"    # 推测，随时可能被推翻

CONF_ORDER = {CONF_DATA: 3, CONF_WIKI: 2, CONF_COMMUNITY: 1, CONF_ASSUMED: 0}


class PackError(ValueError):
    """数据包不合法。"""


@dataclass(frozen=True)
class Operator:
    """一名可在商店刷出的干员（基础形态）。

    ``golden_id`` 指向精锐形态；精锐不单独占卡池，只按 3 份计。
    """

    id: str
    name: str
    tier: int
    bonds: tuple[str, ...] = ()
    hidden: bool = False
    kind: str = "NORMAL"
    golden_id: str | None = None
    profession: str = ""
    sub: str = ""
    rarity: int = 0
    price: int = 0          # 购买价（资金）
    sell: int = 1           # 出售所得
    merge_num: int = 3      # 几张合成精锐（风丸为 2）

    @property
    def shopable(self) -> bool:
        return not self.hidden


@dataclass(frozen=True)
class Bond:
    """一条盟约（羁绊）。"""

    id: str
    name: str
    category: str = "ADD_ON"     # CORE / ADD_ON
    activate_at: int = 2         # 激活所需「不同干员」数
    in_deck: bool = False        # 是否把整备区也计入
    power: bool = False          # 核心盟约
    desc: str = ""
    thresholds: tuple[int, ...] = ()  # 描述里出现的额外档位（如 6、9）
    effects: dict[str, Any] = field(default_factory=dict)  # 已核对的经济触发器

    @property
    def levels(self) -> tuple[int, ...]:
        """所有有意义的档位：激活线 + 描述里的额外阈值。"""
        return tuple(sorted({self.activate_at, *self.thresholds}))


@dataclass(frozen=True)
class ShopLevelRule:
    """某一商店等级的一行规则。"""

    level: int
    upgrade_price: int
    char_slots: int
    item_slots: int = 0

    @property
    def total_slots(self) -> int:
        return self.char_slots + self.item_slots


@dataclass(frozen=True)
class Mode:
    """一个玩法模式（入门/标准/险境/…，单人与多人分开）。"""

    id: str
    name: str
    code: str = ""
    mode_type: str = "SINGLE"
    difficulty: str = "FUNNY"
    rounds: int = 0
    shop_levels: tuple[ShopLevelRule, ...] = ()
    active_bonds: tuple[str, ...] = ()
    inactive_bonds: tuple[str, ...] = ()

    def rule(self, level: int) -> ShopLevelRule | None:
        for r in self.shop_levels:
            if r.level == level:
                return r
        return None

    def char_slots(self, level: int) -> int:
        r = self.rule(level)
        return r.char_slots if r else 3


@dataclass(frozen=True)
class Economy:
    """经济规则。多数数值有置信度，见 ``Pack.confidence``。"""

    refresh_cost: int = 1
    sell_price: int = 1
    income_base: int = 3          # income(r) = min(base + r, cap)
    income_cap: int = 12
    income_per_round_bonus: int = 0  # 某些模式每回合额外加成
    upgrade_decay_per_round: int = 1
    upgrade_reset_on_upgrade: bool = True
    board_max: int = 8
    bench_max: int = 10
    temp_max: int = 5
    max_shop_level: int = 6
    gold_carry_over: bool = False  # 回合结束后未花完的资金是否保留
    #: 等阶 → 购买价，用于干员条目缺 price 时兜底
    price_by_tier: tuple[tuple[int, int], ...] = (
        (1, 2), (2, 3), (3, 3), (4, 3), (5, 4), (6, 4),
    )

    def income(self, round_no: int, bonus: int = 0) -> int:
        """第 ``round_no`` 回合开始时获得的资金。"""
        gain = min(self.income_base + round_no, self.income_cap)
        return max(0, gain + self.income_per_round_bonus + bonus)

    def buy_price(self, tier: int) -> int:
        return dict(self.price_by_tier).get(tier, 0)


@dataclass
class Pack:
    """一个可用的数据包。"""

    path: Path
    schema: int
    id: str
    name: str
    season: str
    updated: str = ""
    rules_version: str = ""
    source_type: str = "community_mixed"
    limitations: list[str] = field(default_factory=list)
    default_mode: str = ""
    source: list[dict[str, Any]] = field(default_factory=list)
    confidence: dict[str, str] = field(default_factory=dict)
    economy: Economy = field(default_factory=Economy)
    pool_copies: dict[int, int] = field(default_factory=dict)
    golden_copies: int = 3
    modes: dict[str, Mode] = field(default_factory=dict)
    operators: dict[str, Operator] = field(default_factory=dict)
    bonds: dict[str, Bond] = field(default_factory=dict)
    bands: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 11/8 起「独立模拟」开局核心盟约二选一：每项是一组 bond id。空表示没有该机制。
    core_bond_groups: list[list[str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # -- 加载 -------------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path) -> Pack:
        p = Path(path)
        if p.is_file():
            p = p.parent
        meta_file = p / "pack.json"
        if not meta_file.exists():
            raise PackError(f"找不到数据包清单：{meta_file}")
        meta = _read_json(meta_file)

        schema = int(meta.get("schema", 0))
        if schema != PACK_SCHEMA:
            raise PackError(
                f"数据包 schema={schema}，本程序需要 {PACK_SCHEMA}；"
                f"请升级程序或转换数据包（见 docs/DATA_PACK.md）"
            )

        warnings: list[str] = []

        eco_raw = meta.get("economy", {}) or {}
        eco_kwargs = {k: v for k, v in eco_raw.items() if k in Economy.__annotations__}
        if isinstance(eco_kwargs.get("price_by_tier"), dict):
            eco_kwargs["price_by_tier"] = tuple(
                sorted((int(k), int(v)) for k, v in eco_kwargs["price_by_tier"].items())
            )
        economy = Economy(**eco_kwargs)

        pool_raw = meta.get("pool", {}) or {}
        copies = {
            int(k): int(v)
            for k, v in (pool_raw.get("copies_by_tier") or {}).items()
            if str(k).isdigit()
        }
        if not copies:
            warnings.append("pool.copies_by_tier 缺失，概率计算将退化为「所有等阶等权」")

        modes: dict[str, Mode] = {}
        for m in meta.get("modes", []) or []:
            rules = tuple(
                ShopLevelRule(
                    level=int(r["level"]),
                    upgrade_price=int(r.get("upgrade_price", 99)),
                    char_slots=int(r.get("char_slots", 3)),
                    item_slots=int(r.get("item_slots", 0)),
                )
                for r in (m.get("shop_levels") or [])
            )
            mode = Mode(
                id=m["id"],
                name=m.get("name", m["id"]),
                code=m.get("code", ""),
                mode_type=m.get("mode_type", "SINGLE"),
                difficulty=m.get("difficulty", "FUNNY"),
                rounds=int(m.get("rounds", 0)),
                shop_levels=rules,
                active_bonds=tuple(m.get("active_bonds") or ()),
                inactive_bonds=tuple(m.get("inactive_bonds") or ()),
            )
            modes[mode.id] = mode

        ops = _load_items(p / "operators.json", warnings, "operators")
        operators: dict[str, Operator] = {}
        for o in ops:
            try:
                op = Operator(
                    id=o["id"],
                    name=o.get("name", o["id"]),
                    tier=int(o.get("tier", 1)),
                    bonds=tuple(o.get("bonds") or ()),
                    hidden=bool(o.get("hidden", False)),
                    kind=o.get("kind", "NORMAL"),
                    golden_id=o.get("golden_id"),
                    profession=o.get("profession", ""),
                    sub=o.get("sub", ""),
                    rarity=int(o.get("rarity", 0)),
                    price=int(o.get("price", 0)),
                    sell=int(o.get("sell", 1)),
                    merge_num=int(o.get("merge_num", 3)),
                )
            except KeyError as exc:  # pragma: no cover - 数据错误
                warnings.append(f"干员条目缺字段 {exc}：{o!r}")
                continue
            operators[op.id] = op

        bonds: dict[str, Bond] = {}
        for b in _load_items(p / "bonds.json", warnings, "bonds"):
            try:
                bond = Bond(
                    id=b["id"],
                    name=b.get("name", b["id"]),
                    category=b.get("category", "ADD_ON"),
                    activate_at=int(b.get("activate_at", 2)),
                    in_deck=bool(b.get("in_deck", False)),
                    power=bool(b.get("power", False)),
                    desc=b.get("desc", ""),
                    thresholds=tuple(int(x) for x in (b.get("thresholds") or ())),
                    effects=dict(b.get("effects") or {}),
                )
            except KeyError as exc:  # pragma: no cover
                warnings.append(f"盟约条目缺字段 {exc}：{b!r}")
                continue
            bonds[bond.id] = bond

        default_mode = meta.get("default_mode") or next(iter(modes), "")
        if default_mode and default_mode not in modes:
            warnings.append(f"default_mode={default_mode} 不在 modes 中")

        bands: dict[str, dict[str, Any]] = {}
        for b in _load_items(p / "bands.json", warnings, "bands"):
            if "id" in b:
                bands[str(b["id"])] = b
        if not bands:
            warnings.append("没有 bands.json：开局策略/初始生命需要手填")

        return cls(
            path=p,
            schema=schema,
            id=meta.get("id", p.name),
            name=meta.get("name", p.name),
            season=meta.get("season", ""),
            updated=meta.get("updated", ""),
            rules_version=str(meta.get("rules_version") or meta.get("season") or ""),
            source_type=str(meta.get("source_type") or "community_mixed"),
            limitations=[str(x) for x in (meta.get("limitations") or [])],
            default_mode=default_mode,
            source=meta.get("source", []) or [],
            confidence=meta.get("confidence", {}) or {},
            economy=economy,
            pool_copies=copies,
            golden_copies=int(pool_raw.get("golden_copies", 3)),
            core_bond_groups=[
                [str(x) for x in group]
                for group in (meta.get("core_bond_groups") or [])
                if isinstance(group, (list, tuple))
            ],
            modes=modes,
            operators=operators,
            bonds=bonds,
            bands=bands,
            raw=meta,
            warnings=warnings,
        )

    def reload(self) -> Pack:
        """热重载：重新读盘，返回新对象。"""
        return Pack.load(self.path)

    # -- 查询 -------------------------------------------------------------
    def mode(self, mode_id: str | None = None) -> Mode:
        mid = mode_id or self.default_mode
        if mid in self.modes:
            return self.modes[mid]
        if self.modes:
            return next(iter(self.modes.values()))
        return Mode(id="unknown", name="未知模式")

    def op(self, op_id: str) -> Operator | None:
        return self.operators.get(op_id)

    def bond(self, bond_id: str) -> Bond | None:
        return self.bonds.get(bond_id)

    def copies_for(self, op_id: str) -> int:
        op = self.operators.get(op_id)
        if op is None:
            return 0
        return int(self.pool_copies.get(op.tier, 0))

    def available_operators(
        self, mode: Mode, *, extra_inactive: tuple[str, ...] | list[str] = ()
    ) -> list[Operator]:
        """该模式下可能出现在商店里的基础干员。

        规则：
        * 只看基础形态（精锐不单独刷出）；
        * 排除 ``hidden``；
        * 若模式给了 ``active_bonds``，则要求干员至少有一条盟约在该模式启用
          （官方模式表就是这么筛池子的）；
        * 有盟约的干员仅在全部盟约都被 ``inactive_bonds`` 禁用时才排除，
          无盟约干员不受禁用规则影响。
        """
        active = set(mode.active_bonds)
        inactive = set(mode.inactive_bonds) | {str(x) for x in extra_inactive}
        out: list[Operator] = []
        for op in self.operators.values():
            if op.hidden:
                continue
            if active and op.bonds and not (set(op.bonds) & active):
                continue
            if inactive and op.bonds and all(bond in inactive for bond in op.bonds):
                continue
            out.append(op)
        return out

    def operators_by_tier(self, mode: Mode) -> dict[int, list[Operator]]:
        by_tier: dict[int, list[Operator]] = {}
        for op in self.available_operators(mode):
            by_tier.setdefault(op.tier, []).append(op)
        for tier in by_tier:
            by_tier[tier].sort(key=lambda o: o.name)
        return by_tier

    def confidence_of(self, key: str) -> str:
        return self.confidence.get(key, CONF_ASSUMED)

    def summary(self) -> dict[str, Any]:
        """给 UI 的概要。"""
        return {
            "id": self.id,
            "name": self.name,
            "season": self.season,
            "updated": self.updated,
            "rules_version": self.rules_version,
            "source_type": self.source_type,
            "limitations": self.limitations,
            "schema": self.schema,
            "path": str(self.path),
            "modes": [
                {
                    "id": m.id,
                    "name": m.name,
                    "code": m.code,
                    "type": m.mode_type,
                    "difficulty": m.difficulty,
                    "rounds": m.rounds,
                    "char_slots": {r.level: r.char_slots for r in m.shop_levels},
                    "upgrade_price": {r.level: r.upgrade_price for r in m.shop_levels},
                }
                for m in self.modes.values()
            ],
            "operators": len(self.operators),
            "bonds": len(self.bonds),
            "bands": len(self.bands),
            "pool_copies": {str(k): v for k, v in sorted(self.pool_copies.items())},
            "warnings": self.warnings,
            "confidence": self.confidence,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise PackError(f"{path.name} 不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise PackError(f"{path.name} 顶层必须是对象")
    return data


def _load_items(path: Path, warnings: list[str], label: str) -> list[dict[str, Any]]:
    if not path.exists():
        warnings.append(f"缺少 {path.name}，{label} 将为空")
        return []
    data = _read_json(path)
    items = data.get("items")
    if not isinstance(items, list):
        warnings.append(f"{path.name} 里没有 items 数组")
        return []
    return [x for x in items if isinstance(x, dict)]


def _pack_sort_key(path: Path) -> tuple[str, float]:
    """优先按 pack.json 里的 updated/season 排序，取不到再退回文件时间。"""
    try:
        data = _read_json(path / "pack.json")
        stamp = str(data.get("updated") or data.get("season") or "")
    except Exception:  # noqa: BLE001
        stamp = ""
    try:
        mtime = (path / "pack.json").stat().st_mtime
    except OSError:
        mtime = 0.0
    return stamp, mtime


def find_packs(root: str | Path) -> list[Path]:
    """列出所有可加载的数据包目录（按 updated 倒序，其次修改时间）。"""
    root = Path(root)
    if not root.exists():
        return []
    found = [p.parent for p in root.glob("*/pack.json")]
    found.sort(key=_pack_sort_key, reverse=True)
    return found


def load_latest(root: str | Path) -> Pack:
    """加载 ``packs/`` 下最新的一个数据包。"""
    packs = find_packs(root)
    if not packs:
        raise PackError(f"在 {root} 下没有找到任何数据包（需要 pack.json）")
    return Pack.load(packs[0])


def iter_bond_names(bonds: Iterable[Bond]) -> dict[str, str]:
    return {b.id: b.name for b in bonds}
