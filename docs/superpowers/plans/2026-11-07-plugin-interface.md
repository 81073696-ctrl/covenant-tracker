# 独立插件操作台界面 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将现有三列记牌器网页改造成商店与阵容居中的独立战术插件界面，同时保留全部功能和数据口径。

**Architecture:** 保留零构建前端和现有 API，只调整 `index.html` 的分组与语义容器，并用 `style.css` 建立统一视觉和响应式布局。`app.js` 继续操作原有元素 ID；以静态结构回归测试和真实浏览器验证共同防止界面改造破坏行为。

**Tech Stack:** HTML5、CSS3、原生 JavaScript、Node.js `node:test`／`assert`、Python `unittest`、现有本地 HTTP 服务。

## Global Constraints

- 不修改后端 API 字段、存储格式、游戏规则、识别模型、卡池计算或统计口径。
- 不新建 Electron、Tauri 或其他桌面壳工程，不引入外部字体、前端框架或构建依赖。
- 保留 `app.js` 依赖的全部元素 ID、表单字段、弹窗结构和事件语义。
- 炭黑、暖金、冷青视觉；中文正文清晰，状态不能只靠颜色表达。
- 宽屏为中央主操作区加右侧情报栏；小于 900 px 改单栏且不隐藏功能。
- 使用原生 `details`／`summary` 折叠情报模块，折叠不得中断数据刷新。
- 遵循 `prefers-reduced-motion`，表格自身可横向滚动，页面不得整体横向溢出。
- 当前任务禁止子代理和自行 Git 提交；由主会话内联执行。
- 同步原项目前重新比较并备份目标文件，保留 DeepSeek 并发改动。

---

### Task 1: 操作台语义结构与前端回归保护

**Files:**
- Create: `tests/test_plugin_interface.js`
- Modify: `covenant/ui/web/index.html:42-126`

**Interfaces:**
- Consumes: 现有全部元素 ID、`data-view`、`data-edit`、商店／阵容／弹窗事件。
- Produces: `#match-main > .board-panel`、`#match-main > .intel-panel`，以及 `.intel-module > details > summary` 的可测试结构；不新增 JavaScript 公共接口。

- [ ] **Step 1: 写静态结构失败测试**

创建 Node 测试，解析 HTML 文本并检查所有现有 ID 不丢失、不重复，新结构存在且情报模块默认展开：

```js
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const html = fs.readFileSync(path.join(__dirname, "../covenant/ui/web/index.html"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../covenant/ui/web/style.css"), "utf8");
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map((match) => match[1]);
assert.equal(new Set(ids).size, ids.length, "元素 ID 不得重复");
for (const id of ["match-main", "shop", "bench", "board", "odds", "bonds", "economy", "log", "stats-main", "settings", "picker", "newgame"]) {
  assert.ok(ids.includes(id), `缺少 #${id}`);
}
assert.match(html, /<main id="match-main">[\s\S]*<section class="panel board-panel">/);
assert.match(html, /<aside class="intel-panel" aria-label="对局情报">/);
assert.match(html, /<section class="panel intel-module odds-panel">\s*<details open>/);
assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
console.log("ok - 插件操作台结构、关键 ID 与默认展开状态");
```

样式断言确保减少动态效果规则不会在后续视觉修改中丢失。

- [ ] **Step 2: 运行测试并确认先失败**

Run: `node tests/test_plugin_interface.js`
Expected: FAIL，提示缺少 `aside.intel-panel` 或减少动态效果对应断言。

- [ ] **Step 3: 重排对局页，不改任何业务 ID**

保留 `#result-hint` 为 `#match-main` 的首个子元素；保留整个 `.board-panel` 的现有内容。用以下容器替代原 `.odds-panel` 的标题区，内部从 `#tiers` 到 `#refresh-history` 的原有节点保持原样：

```html
<aside class="intel-panel" aria-label="对局情报">
  <section class="panel intel-module odds-panel">
    <details open>
      <summary class="section-title">
        <h3>商店概率</h3><span class="hint" id="odds-meta"></span>
      </summary>
      <!-- 原有 #tiers、搜索、概率表、说明、本局刷新节点 -->
    </details>
  </section>
```

将原 `.side-panel` 改为情报栏中的三个独立模块，每个模块使用以下相同模式；`#bonds`、`#economy`、`#log` 分别放入对应 `details`：

```html
  <section class="panel intel-module">
    <details open>
      <summary class="section-title"><h3>盟约</h3><span class="hint" id="bond-meta"></span></summary>
      <div class="bonds" id="bonds"></div>
    </details>
  </section>
  <section class="panel intel-module">
    <details open>
      <summary class="section-title"><h3>经济</h3><span class="hint" id="eco-meta"></span></summary>
      <div class="economy" id="economy"></div>
    </details>
  </section>
  <section class="panel intel-module">
    <details open>
      <summary class="section-title"><h3>日志</h3><span class="hint">最近 40 条</span></summary>
      <div class="log" id="log"></div>
    </details>
  </section>
</aside>
```

为品牌增加不改变 ID 的终端副标题：

```html
<span class="brand-sub">TACTICAL CONSOLE</span>
```

- [ ] **Step 4: 暂时运行结构测试并记录预期状态**

Run: `node tests/test_plugin_interface.js`
Expected: 在 Task 2 加入减少动态效果规则前仍失败；HTML 的 ID 与结构断言不得失败。

### Task 2: 战术终端视觉系统与双栏工作区

**Files:**
- Modify: `covenant/ui/web/style.css:1-391`

**Interfaces:**
- Consumes: Task 1 的 `.intel-panel`、`.intel-module`、原生 `details` 和全部既有类名。
- Produces: 统一设计变量、双栏操作台、商店卡片层级、可折叠情报模块、统计及弹窗视觉；DOM 与 API 不变。

- [ ] **Step 1: 替换设计变量和全局基础规则**

使用以下完整变量，保留后续既有 tier 色变量：

```css
:root {
  --bg: #090c0f;
  --panel: #11161b;
  --panel-2: #171e24;
  --line: #2a333b;
  --text: #edf0ee;
  --dim: #96a0a6;
  --muted: #96a0a6;
  --accent: #e9b85f;
  --accent-2: #6bd5ca;
  --danger: #ff7168;
  --ok: #7ed6a0;
  font-size: 14px;
  color-scheme: dark;
}
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { display: flex; flex-direction: column; overflow: hidden; }
body, html {
  background: var(--bg);
  color: var(--text);
  font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
button, input, select, textarea { font: inherit; color: inherit; }
button, input, select, textarea, a { outline-offset: 3px; }
:focus-visible { outline: 2px solid var(--accent-2); }
```

- [ ] **Step 2: 建立紧凑指挥栏与双栏主工作区**

用以下完整布局规则替代旧 `header` 和 `main` 网格定义，保留其他组件规则：

```css
header {
  display: flex; align-items: center; flex-wrap: wrap;
  gap: 8px 16px; padding: 10px 18px;
  border-bottom: 1px solid var(--line);
  background: linear-gradient(180deg, #171d22, #0e1317);
  position: sticky; top: 0; z-index: 20;
}
.brand { display: flex; align-items: baseline; gap: 8px; white-space: nowrap; }
.brand-sub { color: var(--dim); font-size: 9px; letter-spacing: 1.6px; }
main {
  display: grid; grid-template-columns: minmax(0, 1fr);
  gap: 12px; padding: 12px; align-items: start;
  flex: 1 1 auto; min-height: 0;
}
#match-main {
  grid-template-columns: minmax(560px, 1fr) minmax(340px, 420px);
  overflow: hidden;
}
.board-panel { min-width: 0; }
.intel-panel {
  min-width: 0; min-height: 0; height: 100%; overflow: auto;
  display: flex; flex-direction: column; gap: 10px;
}
.intel-module { height: auto; overflow: visible; flex: 0 0 auto; }
.intel-module details > :not(summary) { margin-top: 8px; }
.intel-module summary { cursor: pointer; list-style: none; }
.intel-module summary::-webkit-details-marker { display: none; }
.intel-module summary::before { content: "▾"; color: var(--accent); }
.intel-module details:not([open]) > summary::before { content: "▸"; }
.result-hint { grid-column: 1 / -1; }
```

- [ ] **Step 3: 统一面板、状态带和主要操作**

用切角 `clip-path` 只装饰 `.panel`，不动输入框和表格；以下完整规则作为组件基准：

```css
.panel {
  position: relative; min-height: 0; padding: 14px;
  background: linear-gradient(145deg, #141a20, var(--panel));
  border: 1px solid var(--line); border-radius: 3px;
  clip-path: polygon(0 0, calc(100% - 12px) 0, 100% 12px, 100% 100%, 12px 100%, 0 calc(100% - 12px));
}
.board-panel { height: 100%; overflow: auto; }
.hud { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; }
.stat { background: #0d1216; border: 1px solid var(--line); border-top: 2px solid #3c464e; padding: 9px 11px; }
.stat.gold { border-top-color: var(--accent); background: linear-gradient(120deg, #211b11, #0d1216); }
.stat b, .kpi b, .eco-row b { font-variant-numeric: tabular-nums; }
.actions { display: flex; gap: 8px; margin: 12px 0 16px; flex-wrap: wrap; }
.actions button { min-height: 36px; }
button.primary { background: var(--accent); color: #171106; border-color: #f4cf8b; font-weight: 700; }
button:disabled { opacity: .5; cursor: not-allowed; }
.section-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.section-title h3 { letter-spacing: .08em; }
```

- [ ] **Step 4: 提升商店与阵容辨识度**

保留 tier 色与购买状态，替换商店卡片基准：

```css
.shop { display: grid; grid-template-columns: repeat(auto-fill, minmax(126px, 1fr)); gap: 10px; }
.card {
  position: relative; min-height: 112px; padding: 12px;
  display: flex; flex-direction: column; justify-content: space-between;
  background: linear-gradient(155deg, #1d252c, #11171c);
  border: 1px solid #38444d; border-left: 3px solid currentColor;
  border-radius: 2px; cursor: pointer; overflow: hidden;
  transition: border-color .15s, transform .15s, background .15s;
}
.card:hover { border-color: var(--accent); transform: translateY(-2px); }
.card .name { color: var(--text); font-size: 16px; }
.card .sub, .card .bonds { color: var(--dim); }
.card.bought { opacity: .45; }
.card.bought::after { content: "已购买"; position: absolute; right: 8px; bottom: 8px; color: var(--dim); }
.units { display: flex; flex-wrap: wrap; gap: 7px; }
.unit { background: #141b20; border: 1px solid var(--line); border-left: 3px solid #4b5963; padding: 7px 10px; }
.unit.empty { border-style: dashed; background: transparent; }
```

- [ ] **Step 5: 收敛情报、统计和弹窗样式**

保留现有表头固定、表格横向滚动、盟约、经济、日志、统计和弹窗功能，统一采用 `var(--panel)`、`var(--panel-2)`、`var(--line)`、`var(--dim)`。设置局部高度：

```css
.odds-panel .table-wrap { max-height: min(38vh, 360px); }
.odds-panel .table-wrap.short { max-height: 180px; }
.bonds { max-height: 260px; overflow: auto; }
.log { max-height: 180px; overflow: auto; }
.modal { padding: 24px; background: rgba(0, 0, 0, .78); }
.modal-box { width: min(680px, 100%); max-height: 82vh; overflow: auto; border-radius: 3px; }
.toast { border-radius: 3px; }
```

- [ ] **Step 6: 加入窄窗口和减少动态效果规则**

```css
@media (max-width: 1279px) {
  #match-main { grid-template-columns: minmax(480px, 1fr) minmax(320px, 360px); }
  .pack-info, .brand-sub { display: none; }
}
@media (max-width: 899px) {
  body { overflow: auto; }
  header { position: static; }
  main, #match-main, .stats-main { grid-template-columns: minmax(0, 1fr); height: auto; overflow: visible; }
  .board-panel, .intel-panel, .panel { height: auto; overflow: visible; }
  .hud { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .odds-panel .table-wrap { max-height: 420px; overflow: auto; }
  .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .rounds-grid { grid-template-columns: 1fr; }
  .modal { padding: 12px; }
}
@media (max-width: 520px) {
  main { padding: 8px; }
  .shop { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .hud { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .stat.phase { grid-column: 1 / -1; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; animation: none !important; }
  .card:hover { transform: none; }
}
```

- [ ] **Step 7: 运行前端结构与行为测试**

Run: `node tests/test_plugin_interface.js && node tests/test_frontend.js && node --check covenant/ui/web/app.js`
Expected: 两条 `ok`，语法检查无输出，退出码 0。

### Task 3: 全量回归、真实页面验收与安全同步

**Files:**
- Test: `tests/test_plugin_interface.js`
- Test: `tests/test_frontend.js`
- Verify: `covenant/ui/web/index.html`
- Verify: `covenant/ui/web/style.css`
- Sync after comparison: 原项目的上述两个界面文件与新测试、规格、计划文件

**Interfaces:**
- Consumes: Task 1–2 完成的界面；原项目 `python -m covenant serve --port 8766 --window Arknights`。
- Produces: 可复核的测试结果、桌面与窄窗口实际页面验收、保留并发修改的原项目同步。

- [ ] **Step 1: 检查并发差异并备份**

比较工作副本与原项目的 `index.html`、`style.css`、`app.js`；若原项目界面文件在基线后有额外改动，先合并这些改动再继续。将同步前原文件备份到当前工作区时间戳目录，不覆盖其他文件。

- [ ] **Step 2: 运行全量自动化回归**

Run: `python -m unittest discover -s tests -v`
Expected: 97 项或更多测试全部通过，`OK`。

Run: `node tests/test_plugin_interface.js && node tests/test_frontend.js && node --check covenant/ui/web/app.js`
Expected: 全部通过，退出码 0。

Run: `python -m covenant selftest`（若当前 CLI 的实际自检子命令名称不同，以 `--help` 输出为准并记录实际命令）。
Expected: 自检通过，无规则包或数据错误。

- [ ] **Step 3: 验证真实服务与关键交互**

通过现有 `8766` 服务刷新页面，不启动或关闭 `8765`、`8801`。以 DOM 快照及页面脚本检查：

```js
({
  board: !!document.querySelector("#match-main > .board-panel"),
  intel: !!document.querySelector("#match-main > .intel-panel"),
  duplicateIds: [...document.querySelectorAll("[id]")].map((el) => el.id).filter((id, i, all) => all.indexOf(id) !== i),
  horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
  reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches
})
```

Expected: `board`、`intel` 为 `true`，`duplicateIds` 为空，桌面及窄窗口均无意外 `horizontalOverflow`。

依次验证对局／统计切换、搜索、只看已拥有、情报折叠、设置弹窗、选人弹窗和刷新后的真实数据。涉及新局、购买、过回合等会改状态的操作使用可丢弃测试局，不污染用户真实对局。

- [ ] **Step 4: 同步并复核原项目**

仅同步已比较的界面文件、新测试和文档；保持桌面快捷方式不变。同步后重新运行前端测试并刷新 `8766`，确认页面来自原项目且没有 JavaScript 错误。

- [ ] **Step 5: 记录交付结果**

在最终回复列出实际修改、测试数量、已验证交互和仍受环境限制未验证的项目；不得把未截图或未点击的流程声称已验证。
