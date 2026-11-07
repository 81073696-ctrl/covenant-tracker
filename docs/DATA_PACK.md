# 数据包说明

数据包 = 这一赛季的全部游戏数值。**代码不认识任何具体数值**，只认 `packs/` 下最新的那个目录
（按目录修改时间排序，`covenant.data.pack.load_latest`）。

当前有两个包：

| 目录 | 来源 | 规模 | 用途 |
|---|---|---|---|
| `packs/act2autochess-private/` | 社区复刻 `data/*.json`（Stronghold-Protocol 整合包） | 219 干员 / 23 盟约 / 43 策略 / 11 模式 | **默认使用**，`source_type=community_replica`，不是官方客户端数据 |
| `packs/act2autochess/` | GitHub 研究文档与社区资料 | 133 干员 / 23 盟约 / 40 策略 / 9 模式 | 混合来源对照包，`source_type=community_mixed` |

```
packs/<id>/
├─ pack.json        模式、商店等级表、经济、卡池
├─ operators.json   干员（items 数组，基础形态；精锐用 golden_id 指向）
├─ bonds.json       盟约：激活线、阈值、是否算整备区
└─ bands.json       开局策略（分队）：生命、效果
```

改完任意文件后，在面板里点「重载数据包」（`POST /api/reload-pack`），不用重启。

---

## 1. `pack.json`

```jsonc
{
  "schema": 1,                       // 必须等于 covenant.version.PACK_SCHEMA
  "id": "act2autochess",
  "name": "卫戍协议：盟约（私服数据包）",
  "season": "2026-03-priv",          // 会写进每条战绩的 patch 字段
  "updated": "2026-11-07",
  "rules_version": "2026-lower-half-community-audit-1",
  "source_type": "community_replica", // 或 community_mixed；不能冒称官方数据
  "limitations": ["装备、机变与多人联防未纳入概率模型"],
  "default_mode": "mode_single_funny",
  "source": [ { "name": "...", "url": "...", "confidence": "community" } ],
  "confidence": { "operators": "community", "odds_model": "community" },
  "economy": {
    "refresh_cost": 1,
    "sell_price": 1,
    "income_base": 3,                // income(r) = min(base + r, cap)
    "income_cap": 12,
    "upgrade_decay_per_round": 1,
    "board_max": 8, "bench_max": 10, "temp_max": 5,
    "gold_carry_over": false,        // 私服 leftoverFundsLost=true
    "price_by_tier": { "1": 2, "2": 3, "3": 3, "4": 3, "5": 4, "6": 4 }
  },
  "pool": {
    "copies_by_tier": { "1": 12, "2": 14, "3": 18, "4": 16, "5": 8, "6": 5 },
    "golden_copies": 3
  },
  "modes": [ ... ]                   // 每个模式一行，见下
}
```

`modes[]` 每一项：

```jsonc
{
  "id": "mode_single_funny",
  "name": "标准模拟",
  "code": "AC-1",
  "mode_type": "SINGLE",             // SINGLE | MULTI | LOCAL
  "difficulty": "FUNNY",
  "rounds": 9,                       // 私服 config 的 lastRound
  "shop_levels": [
    {"level":1,"upgrade_price":1,"char_slots":3,"item_slots":1},
    ...
    {"level":6,"upgrade_price":99,"char_slots":5,"item_slots":1}
  ],
  "active_bonds": ["yanShip", ...],  // 本模式启用的盟约
  "inactive_bonds": ["lateranoShip", ...]
}
```

> `shop_levels` 是**按模式分表**的，不能混用。标准模拟的升级价从 1 开始，
> 险境/绝境/终极是 5/8/11/12/13，入门协议是 4/4/6/9/12。改数值前先确认 `modeId`。
>
> 第 6 级不可再升，惯例写 `upgrade_price: 99`。

---

## 2. `operators.json`

```jsonc
{
  "items": [
    {
      "id": "chess_char_1_01_a",
      "name": "隐现",
      "tier": 1,
      "bonds": ["lateranoShip", "swiftShip"],
      "hidden": false,               // true = 不进商店（剧情/特殊单位）
      "kind": "PRESET",              // PRESET | NORMAL | DIY
      "golden_id": "chess_char_1_01_b",
      "profession": "SNIPER",
      "sub": "速射手",
      "rarity": 5,
      "price": 2,                    // 购买价
      "sell": 1,                     // 出售返还
      "merge_num": 3                 // 几张合成精锐（风丸是 2）
    }
  ]
}
```

`available_operators(mode)` 的筛选规则：排除 `hidden`；若模式给了 `active_bonds`，
则要求干员至少有一条盟约在启用集合里（对应私服的「所有盟约都被禁则该棋不出现」）。

---

## 3. `bonds.json`

```jsonc
{
  "items": [
    {
      "id": "visiShip",
      "name": "远见",
      "category": "ADD_ON",          // CORE | ADD_ON
      "activate_at": 2,              // 激活所需不同干员数
      "in_deck": true,               // 是否把整备区也计入
      "power": false,                // 核心盟约
      "thresholds": [2],             // 描述里的额外档位
      "desc": "获得资金；达到指定层数后永久降低购买价……",
      "effects": {                  // 可选：只写已核对的结构化触发器
        "layer_rewards": {"every": 10, "gold": 2},
        "purchase_discounts": [
          {"threshold": 80, "amount": 1, "scope": "bond"},
          {"threshold": 150, "amount": 1, "scope": "all"}
        ]
      }
    }
  ]
}
```

层数（`bond_layers`）是每局的独立计数器，跨回合不清零；界面右侧点数字可手改。
`effects.layer_rewards` 在每达到 `every` 层时发放一次 `gold`；`purchase_discounts` 在首次达到
`threshold` 后永久生效，`scope` 为 `bond`（本盟约干员）或 `all`（全商店），扩展范围不重复叠加。
奇迹另用 `free_refresh_chance: {"base_percent": 18, "per_layer_percent": 0.3}` 展示概率，
程序不会据此自动掷出免费刷新。领取／解锁标记存放在本局 `rules.effect_milestones`，新局会清空。

---

## 4. `bands.json`

```jsonc
{
  "items": [
    {
      "id": "band_cannot",
      "name": "坎诺特",
      "hp": 27,                      // 开局生命
      "effect_name": "利滚利",
      "desc": "剩余资金可继承……",
      "effects": {
        "economy": {"gold_carry_over": true, "remaining_gold_threshold": 5, "income_bonus": 1}
      },
      "modes": ["SINGLE", "MULTI"],
      "victor_count": 3
    }
  ]
}
```

开局时选策略会自动带出生命；也能手改。`rules.gold_carry_over` 人工覆盖优先于策略效果，但不会带入新局。
刷新策略使用 `effects.refresh`：`{"scope": "all"}` 表示全部特殊，`{"per_round": 2}` 表示每回合前两次，
`{"on_levels": [2, 4, 6], "charges": 1}` 表示升级到指定等级时授予次数。特殊刷新仍按经济表付费。

---

## 5. 重新生成

```bash
# 社区复刻数据包（默认）
py -3 scripts/build_pack_private.py
py -3 scripts/build_pack_private.py --refs _refs/private --out packs/act2autochess-private

# GitHub 研究包（旧）
py -3 scripts/build_pack.py --refs _refs/stronghold --out packs/act2autochess
```

> 生成器当前只重建原始字段，不会自动保留人工审核的 `effects`、`rules_version` 等覆盖信息。
> 重新生成前请备份并迁移这些字段，生成后运行 `tests.test_rule_effects` 与完整测试，避免规则静默丢失。

生成后 `python -m covenant selftest` 会打印：可用干员数、回合数、池量、最可能刷到的干员、升级价。
测试用 `python -m unittest discover -s tests`。

---

## 6. 新赛季 / 官方更新后

1. 复制 `packs/act2autochess-private` 为 `packs/<新赛季>`；
2. 改 `pack.json` 的 `id` / `season` / `updated` / `economy` / `modes`；
3. 改 `operators.json`（新增/删除干员、改价、改 `merge_num`）；
4. 改 `bonds.json`（新盟约、阈值）；
5. 面板「重载数据包」，或重启 `serve`。

数据包只影响「算得对不对」，不影响「认不认得出」；识别布局在 `var/layout.json`，由 `/teach` 生成。

---

## 7. 置信度约定

| 值 | 含义 |
|---|---|
| `data` | 客户端/私服数据表里直接读到 |
| `wiki` | 官方说明或 PRTS |
| `community` | 社区实测 |
| `assumed` | 推测，随时可能被推翻 |

商店抽卡模型（独立格、按剩余份数加权、允许同店重复）参考社区复刻的 `server/match/pool.js`，
统一标 `community`；它只能说明复刻实现，不能证明官方客户端一致，仍需真实对局核对。

---

## 8. 常见错误

| 现象 | 原因 |
|---|---|
| `找到不数据包` / `schema=0` | `pack.json` 缺 `schema` 或不在目录里 |
| 面板显示「未知模式」 | `default_mode` 写了不存在的 id |
| 概率全是 0 | `pool.copies_by_tier` 缺等阶，或干员 `hidden` 全被过滤 |
| 买牌不扣钱 | 干员 `price` 为 0（旧包靠 `price_by_tier` 兜底，新包直接写价） |
| 商店格数不对 | `shop_levels` 用错了模式表 |
