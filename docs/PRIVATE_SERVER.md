# 私服（Stronghold-Protocol）联调与识别训练

> 目的：官方 11/8 更新前，用同人复刻的网页版私服练「截图 → 状态」的识别链路。
> 私服不是官方客户端，UI 会有差异；它只用来**提前把管线、布局、模板、评测脚本跑通**，官方上线后用同样的流程重标一遍即可。

## 1. 启动私服

整合包解压到 `var/priv/Stronghold-Protocol`（需要 `server/ public/ shared/ node_modules/ scripts/ data/ tools/`）。
端口用环境变量，不要用命令行参数（`--port` 会被忽略）：

```powershell
cd var\priv\Stronghold-Protocol
$env:PORT='3210'; $env:HOST='127.0.0.1'; $env:SP_NO_BROWSER='1'
node server/index.js
```

- `/` 是游戏页面，`/healthz` 返回 `{"ok":true,...}`。
- 3000 常被 SnowLuma 占用；3100 可能被其他桥接程序占用，换 3210/3211 即可。
- 浏览器窗口建议 1600×900 或更大；记牌器的 `WindowCapture` 能直接抓 Edge 窗口（实测 PrintWindow 可用，包含浏览器边框）。

## 2. 自动走到对局（CDP）

`var/priv/Stronghold-Protocol/_attach.mjs` 是临时驱动脚本（依赖包内自带的 `ws`，不需要 puppeteer）：

```powershell
# 先开一个带调试端口的 Edge
msedge --headless=new --remote-debugging-port=9224 --window-size=1600,900 about:blank

node _attach.mjs "nav:http://127.0.0.1:3210/" "size:1600x900" "focus:input" "type:测试博士" "click:开始"
node _attach.mjs "size:1600x900" "xy:1296,814"          # 独立模拟
node _attach.mjs "click:开始模拟" "wait:3" "click:开始模拟" "wait:3"
node _attach.mjs "click:准备就绪" "wait:3" "click:确认选择" "wait:6"
```

要点：

- `Emulation.setDeviceMetricsOverride` 会在**离开 CDP 会话后失效**，所以「设窗口 → 点击 → 截图」要放在同一次调用里；
- 大厅的模式卡是真实 `<button class="hub-entry hub-entry--mint">`，点击坐标随窗口大小变，按截图量一次即可；
- 识别用的 DOM 标注（资金/等级/回合/生命、商店卡文本）直接从页面读，是**免费的真值**。

## 3. 采集带标注的帧

`_attach.mjs` 支持把一帧的截图和 DOM 标注一起导出：

```powershell
node _attach.mjs "size:1600x900" "shot:priv_f1.png" "eval:expr_labels.js" > f1.txt
node _attach.mjs "size:1600x900" "xy:1490,650" "wait:2" "shot:priv_f2.png" "eval:expr_labels.js" > f2.txt
```

`expr_labels.js` 会输出当前 HUD 的文本值、元素框（像素）和商店卡（名称/价格/位置）。把 `priv_f*.png` 和同名 `.txt` 放进 `var/fixtures/priv/`。

注意：PowerShell 的 `>` 重定向是 UTF-16，脚本里已按 BOM 自动解码。

## 4. 校准与回放

```powershell
py -3 scripts\calibrate_priv.py var\fixtures\priv   # 学字形 + 逐帧自检，模板存 var/ocr_priv.npz
py -3 scripts\replay_priv.py   var\fixtures\priv    # 截图 → VisionPipeline → Engine，和 DOM 真值比对
```

2026-11-07 实测（4 帧、1600×900、私服）：

- 字形：1/2/3/4/8 共 20 个样本，`learn()` 全部切分正确；
- 逐帧整串读取：16/16 = 100%；
- 回放：`round/hp/gold/level` 16/16 与 DOM 一致；
- 布局预设：`data/layouts/private-1600x900.json`（资金/等级/回合/生命/商店四格的比例坐标）。

```powershell
# 让面板用这套布局（会覆盖 var/layout.json，先备份）
copy data\layouts\private-1600x900.json var\layout.json
```

## 5. 官方客户端上线后的迁移

1. 打开游戏，`py -3 -m covenant serve --window Arknights`；
2. 浏览器进 `http://127.0.0.1:8765/teach`，把资金/等级/回合/生命/商店/整备区/战场重新框一遍；
3. 学数字：框住「4」点学习，框住「28」点学习（多学几种字号）；
4. 学干员：点商店格 → 选干员 → 学习，先学当前池里常见的 20~30 个；
5. 试识别，确认 `round/gold/level/hp` 与游戏一致，再开始记战绩。

私服的布局只能当参考，**不要直接用在官方客户端上**。

## 6. 商店卡识别（v1.0 结果）

`scripts/train_priv.py` 用 DOM 标注自动学商店卡模板，模板 key 是数据包的 `op_id`：

```powershell
py -3 scripts\train_priv.py var\fixtures\priv
py -3 scripts\replay_priv.py var\fixtures\priv
```

2026-11-07 实测（6 场、26 帧、1600×900、私服 `single_funny`）：

- HUD（round/hp/gold/level）：**120/120 = 100%**；
- 商店干员：**72/76 = 95% 命中，0 错认**；4 张是训练集里没出现过的干员（正确返回 unknown，不瞎猜）；
- 模板：13 个干员（T1 商店池）、12 个模板文件 `var/icons_priv.npz`；
- 关键设计：模板从**和线上完全相同的切格几何**里取（`slot_rects` + `params.art`），所以离线评测分数就是线上分数；
- 商店布局按等级分两套：`shop`（L1，3 干员+道具）与 `shop_l2`（L2+，4 干员+道具），由 `params.levels` 自动切换；官方客户端同样按等级各标一次；
- 识别阈值写在 `var/vision.json`（`icon_min_sim` = 0.90）：低于阈值返回 unknown，宁缺毋滥。

把私服模板装进面板（会覆盖 `var/layout.json`、`var/ocr_digits.npz`、`var/icons.npz`）：

```powershell
py -3 scripts\install_private_templates.py
```

调试时可以让面板直接回放一个截图目录（不需要开游戏）：

```powershell
py -3 -m covenant serve --frames var/demo_frames --port 8801
```

每次 `/api/vision/read`（或 /teach 页的「试识别」）会读下一帧。
