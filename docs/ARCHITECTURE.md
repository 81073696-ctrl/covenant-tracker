# 架构说明

> 一句话：把「屏幕上看得到的东西」变成「一份可以随时纠错的对局快照」，再在这份快照上算卡池、概率、经济和盟约。

```
 ┌──────────┐   ┌──────────┐   ┌─────────┐   ┌────────┐   ┌────────┐
 │ capture  │ → │  vision  │ → │  state  │ → │  calc  │ → │   ui   │
 │ 拿一帧图  │   │ 变成观测  │   │ 合并快照 │   │ 纯计算  │   │ 本机网页 │
 └──────────┘   └──────────┘   └─────────┘   └────────┘   └────────┘
       ↑              ↑              ↑
   窗口/回放/合成   比例 ROI + 模板   人工点按（永远可用）
```

每层只依赖它左边的一层。任何一层都可以整体换掉，而不影响其它层——这是「11/8 之后你还要大改」的前提。

---

## 1. 为什么不是「事件溯源」

炉石记牌器能靠日志精确重放，是因为游戏给了完整事件流。我们没有：只有像素。

所以状态模型选的是 **绝对快照 + 出处（provenance）**：

- `GameState` 永远表示「现在这一局长什么样」，不是「发生过什么」的推导结果；
- 每条字段都记着它是谁写的：`vision`（置信度多少）还是 `manual`；
- 人工值优先。同回合内，视觉观测低于 `0.95` 置信度不会覆盖你手填的值；
- 认错了、点错了，直接改字段即可，不需要「撤销一串事件」。

代价是没有完整的回放历史；收益是**永远能收敛到正确状态**。对噪声输入，这个取舍是值得的。

动作（买/卖/刷/升）仍然走 `Action` → reducer，因为这类操作要联动金币和卡池，必须原子地一起变。

---

## 2. 目录与职责

```
covenant-tracker/
├─ covenant/
│  ├─ core/types.py        Unit / ShopOffer / Observation / Action
│  ├─ capture/             采集源：WindowCapture（ctypes GDI）、FolderSource、SyntheticSource
│  ├─ vision/
│  │   ├─ roi.py           相对比例的区域布局（var/profiles/<id>/layout.json）
│  │   ├─ ocr.py           模板法数字识别（自己学字形，不依赖 tesseract）
│  │   ├─ match.py         头像/格子模板匹配（NCC）
│  │   ├─ profiles.py      多套识别配置（皮肤/分辨率各一份，var/profiles/）
│  │   └─ pipeline.py      一帧 → 一组 Observation
│  ├─ state/
│  │   ├─ state.py         GameState（可 JSON 序列化，含 match_id / timeline）
│  │   ├─ reducer.py       入口：apply / apply_observation（纯函数）
│  │   ├─ actions.py       动作 reducer：买/卖/刷/升/移动/过回合
│  │   ├─ errors.py        ActionError（各层共用，避免循环 import）
│  │   ├─ observers.py     纯函数：Observation → 新状态（手填优先、按 slot 合并）
│  │   ├─ events.py        逐局事件账本：严格 JSONL + fsync + 重放（var/events/*.jsonl）
│  │   └─ engine.py        线程安全的门面：act / observe / snapshot（先写事件再提交）
│  ├─ calc/
│  │   ├─ pool.py          卡池余量（精锐占 3 份、商店展示不占池）
│  │   ├─ odds.py          商店命中概率（每格独立、按剩余份数加权，允许同店重复）
│  │   ├─ economy.py       收入曲线、升级成本、攒钱回合
│  │   └─ bonds.py         盟约激活与阈值
│  ├─ stats/               战绩：记录模型、JSONL 存储、聚合（pick/win、按回合）
│  │   └─ refreshes.py     从事件账本还原刷新实测（命中率 / 命中成本）
│  ├─ community/           收集器（stdlib HTTP）+ 上传客户端
│  ├─ data/pack.py         数据包加载 + 校验
│  └─ ui/
│      ├─ server.py        stdlib HTTP，仅 127.0.0.1；快照 ETag 缓存
│      └─ web/             index.html / app.js / teach.html / teach.js（零构建）
├─ packs/act2autochess/    赛季数据包（pack / operators / bonds / bands）
├─ scripts/build_pack.py   从参考 JSON 生成数据包
├─ tests/                  169 个单元测试
└─ var/                    存档、布局、学到的模板、战绩（全部可删）
```

`state → calc` 之间没有反向依赖；`stats` 只读 `GameState` 的终局快照与时间线（刷新实测另从事件账本重算），
`community` 只依赖 `stats` 的记录模型，所以「本机统计」和「社区聚合」是同一套口径。

---

## 3. 识别为什么这么设计

11/8 之前没人知道新界面长什么样。所以：

1. **区域用比例，不用像素**。`gold` 就是「左上角那一小块」，在 1920×1080 标一次，1280×720 也能用。
2. **模板由你教，不内置美术资源**。`/teach` 里框一下、点一下，模板存进 `var/icons.npz`。
3. **数字用字形模板**。游戏 HUD 是固定字体，学一次 0–9 就够；没学过的字形会拒绝输出，而不是瞎猜。
4. **认不出就降级**：产出低置信度观测（被 reducer 丢弃），或干脆不产出；界面永远可以手点。

`/teach` 的五步：

| 步骤 | 做什么 | 存到哪 |
|---|---|---|
| 1 | 连接游戏窗口（标题模糊匹配） | 内存 |
| 2 | 拖框画区域：`gold` / `shop_level` / `round` / `hp` 用 `digits`；商店整排用 `shop`；整备区/战场用 `units` | `var/layout.json` |
| 3 | 每个 `digits` 区域学一次数字（点「学」） | `var/ocr_digits.npz` |
| 4 | 商店/整备区每格选一个干员「学这一格」 | `var/icons.npz` |
| 5 | 点「识别这一帧」看产出 | — |

> 提示：学数字时框要贴紧数字本身（不要带中文标签），否则切分数量对不上会拒绝学习。

---

## 4. 计算层的模型与假设

所有假设都写在代码注释和 `pack.json` 的 `confidence` 字段里，**发现不对就改数据包，不要改代码**。

### 卡池（`calc/pool.py`）

- 每名干员按等阶份数：I..VI = 12/14/18/16/8/5；
- **精锐占 3 份**（三合一），所以 2 星牌消耗 3 份；
- 商店里展示的副本不占池；买下来才占；
- 多人共享池：别人买的你看不见 → 用 `rules.pool_extra = {"op_id": 份数}` 手工补记。

### 商店概率（`calc/odds.py`）

当前模型 `uniform_copies`（每格独立、按剩余份数加权，**允许同一店里重复**）：

```
p(某格出现干员 i) = 剩余份数_i / 所有可用干员剩余份数之和
p(一次刷新至少出现一张) = 1 - (1 - p) ^ slots
期望刷新次数 = (1 - p) / p
```

依据是同人私服 `server/match/pool.js`：每一格单独 roll 一份剩余副本，所以同一刷
可能刷出两张同名卡；商店展示不占池，已展示的卡被买走时允许库存降到负数。

如果官方其实是「先按等阶 roll 权重，再在等阶内均匀」，实现 `MODEL_TIER_WEIGHTS` 并在数据包里切模型即可，UI 不用动。

### 经济（`calc/economy.py`）

- `income(r) = min(base + r, cap)`，默认 base=3、cap=12（私服 config 口径，数据包可改）；
- 升级价每回合 -1，最低 0，升级后重置为新档初始价；
- 资金是否跨回合保留是规则开关（`rules.gold_carry_over`），界面右下可切。

### 盟约（`calc/bonds.py`）

- 激活看**不同干员数**，同名两张只算 1；
- 默认只数战场；`in_deck` 的盟约把整备区也算上；
- 层数（layer）是独立计数器，跨回合保留、不下降，和「场上几个」无关。

---

## 5. 数据流细节

### 一次人工操作

```
界面点「刷新」
  → POST /api/action {type:"refresh"}
  → engine.act → reducer.apply（深拷贝一份新状态）
  → 扣 1 金、商店变未知、写日志、记 provenance
  → 返回完整 state；前端下一帧轮询拿到新快照
```

### 一次识别

```
POST /api/vision/read
  → capture.grab() 拿 RGB
  → pipeline.read() 按 layout 逐区域读（layout/模板来自当前 profiles 配置）
  → engine.observe(obs) 每条单独合并（低置信度被拒）
  → 返回每条观测的落库结果
```

### 多套识别配置（皮肤）

```
var/profiles/<id>/{layout.json, ocr_digits.npz, icons.npz, results.npz, vision.json}
var/active_profile.json         {"id": "..."}
```

- `vision/profiles.py:ProfileManager` 负责列出 / 新建 / 切换；旧版放在 `var/` 根下的模板
  首次启动自动 `move` 到 `profiles/default/`，避免两个来源打架。
- `AppServer.activate_profile()` 先把当前配置 `vision.save()` 落盘，再加载新配置，并把
  `CaptureService.vision` 指向新管线——切换后自动识别立即用新模板，不需要重启。
- `GET /api/profiles`、`POST /api/profiles/activate`、`POST /api/profiles/save`
  给前端做下拉/另存；`doctor` 也只看当前激活配置。

### 快照契约

`GET /api/snapshot` 返回：

```jsonc
{
  "pack":   {"id","name","season","warnings",...},
  "mode":   {"id","name","rounds","char_slots",...},
  "state":  { ...GameState 全量... },
  "pool":   {"total","consumed","remaining","by_tier","entries"},
  "odds":   {"shop_level","slots","per_tier","per_op"},
  "economy":{"income_now","income_next","income_curve","level_up_now","plans"},
  "bonds":  {"items":[...],"active_count"},
  "focus":  ["op_id", ...]
}
```

前端只认这个契约；换实现不影响界面。

---

## 6. 扩展点（按 11/8 之后的优先级）

1. **换识别后端**：实现 `FrameSource` 子类（采集卡、录屏文件、OBS 虚拟摄像头）即可，其它层不知道差别。
2. **加识别能力**：`vision/pipeline.py` 里加一个 `region.kind` 分支，产出新 `ObservationKind`，再让 reducer 认识它。
3. **加计算**：`calc/` 下新增纯函数模块，在 `engine.snapshot()` 里挂上去。
4. **置顶小窗**：`ui/overlay.py`（TODO）用 tkinter 画一个 always-on-top 的窄条，直接读同一个 `snapshot()`。
5. **多人协同**：目前 `pool_extra` 是手工补记；后续可以加一个「队友池子」面板，把队友买走的份数记进去。

---

## 7. 已知限制

- 商店格子只按「均分」切；如果实际 UI 格子不等宽，需要在 layout 里加自定义切分（`params.splits`，TODO）。
- 干员星级（精锐）目前识别不出来，默认按 1 星；需要额外学一个「精锐角标」模板。
- 装备格（`item`）还没有专门识别，先当未知格。
- 数字识别假定「亮字暗底」；反色界面需要 `invert=True`（管线里还没暴露开关）。
- 多人模式的队友操作完全不可见，只能手工补记。
