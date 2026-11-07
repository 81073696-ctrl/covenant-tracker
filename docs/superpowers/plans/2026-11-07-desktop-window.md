# 独立桌面窗口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用系统 WebView2 将现有操作台交付为可保存状态、可置顶、可切换紧凑模式的独立 Windows 应用窗口。

**Architecture:** `desktop.py` 独立管理配置、窗口和 JS 桥接，现有 `AppServer` 与业务引擎不变。新增 `desktop.js` 管理两个桌面按钮和紧凑样式；只有显式 `--desktop` 才加载桌面依赖，失败时回退系统浏览器。

**Tech Stack:** Python 3.12、pywebview 6.x、Edge WebView2、原生 JavaScript、CSS、Node.js 测试、Python unittest、Windows 桌面实测。

## Global Constraints

- 不修改游戏规则、识别算法、API 数据结构和已有对局存档。
- 不做 exe、安装器、无边框窗口、透明覆盖层、内存读取或游戏注入。
- 首次完整尺寸 1280 × 840，紧凑尺寸 430 × 760，全局最小尺寸 400 × 500。
- 配置只写入当前 `--var` 目录下的 `desktop.json`，使用原子替换。
- 普通浏览器启动不 import `pywebview`，不显示桌面按钮。
- 桌面依赖仅安装到原项目实际使用的 Python 3.12 环境。
- 禁止子代理和自行 Git 提交；同步原项目前备份并比较并发修改。

---

### Task 1: 配置存储与窗口控制器

**Files:**
- Create: `covenant/ui/desktop.py`
- Create: `tests/test_desktop.py`
- Create: `requirements-desktop.txt`

**Interfaces:**
- Consumes: 当前 `--var` 路径、pywebview `Window.resize()`、`Window.move()`、`Window.on_top`。
- Produces: `DesktopStore.load() -> dict`、`DesktopStore.save(state) -> None`、`DesktopController.get_state() -> dict`、`set_compact(value: bool) -> dict`、`set_on_top(value: bool) -> dict`、`close() -> None`。

- [ ] **Step 1: 写配置与控制器失败测试**

```python
import json
import tempfile
import unittest
from pathlib import Path
from covenant.ui.desktop import DesktopController, DesktopStore

class FakeWindow:
    def __init__(self, state):
        self.width, self.height = state["width"], state["height"]
        self.x, self.y = state["x"] or 20, state["y"] or 20
        self.on_top = state["on_top"]
        self.calls = []
    def resize(self, width, height):
        self.width, self.height = width, height
        self.calls.append(("resize", width, height))
    def move(self, x, y):
        self.x, self.y = x, y
        self.calls.append(("move", x, y))

class DesktopTests(unittest.TestCase):
    def test_corrupt_config_falls_back_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "desktop.json"
            path.write_text("not-json", encoding="utf-8")
            store = DesktopStore(path)
            self.assertEqual(store.load()["width"], 1280)
            state = dict(store.load(), compact=True, on_top=True, width=430, height=760, x=40, y=50)
            store.save(state)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["width"], 430)
            self.assertFalse(path.with_suffix(".tmp").exists())
    def test_compact_topmost_and_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DesktopStore(Path(tmp) / "desktop.json")
            window = FakeWindow(store.load())
            closed = []
            controller = DesktopController(window, store, lambda: closed.append(True))
            state = controller.set_compact(True)
            self.assertEqual((state["width"], state["height"]), (430, 760))
            self.assertTrue(state["on_top"])
            self.assertIn(("resize", 430, 760), window.calls)
            state = controller.set_on_top(False)
            self.assertFalse(state["on_top"])
            controller.close()
            self.assertEqual(closed, [True])
            self.assertFalse(store.load()["on_top"])
```

- [ ] **Step 2: 验证测试因模块缺失而失败**

Run: `python -m unittest tests.test_desktop -v`
Expected: FAIL，`ModuleNotFoundError: covenant.ui.desktop`。

- [ ] **Step 3: 实现严格校验和原子存储**

`DesktopStore.DEFAULTS` 为 `{"compact": False, "on_top": False, "width": 1280, "height": 840, "x": None, "y": None}`。`load()` 对任何读取、JSON 或校验异常返回新默认字典；布尔字段必须严格为 `bool`，宽高分别限制在 400–3840、500–2160，坐标允许 `None` 或 -32768–32767 的整数且排除布尔值。`save()` 只输出六个字段，先写 `path.with_suffix(".tmp")` 再 `Path.replace()`。

- [ ] **Step 4: 实现可单测的窗口控制器**

```python
class DesktopController:
    def __init__(self, window, store, on_close):
        self.window, self.store, self.on_close = window, store, on_close
        self.state = store.load()
        self._full_size = (self.state["width"], self.state["height"])
        self._closed = False
    def get_state(self):
        self._remember_geometry()
        return dict(self.state)
    def _remember_geometry(self):
        self.state.update(width=int(self.window.width), height=int(self.window.height), x=int(self.window.x), y=int(self.window.y))
    def _save(self):
        self._remember_geometry()
        self.store.save(self.state)
        return dict(self.state)
    def set_compact(self, value):
        value = bool(value)
        if value == self.state["compact"]:
            return self.get_state()
        if value:
            self._full_size = (self.window.width, self.window.height)
            self.window.resize(430, 760)
            self.state.update(compact=True, on_top=True)
            self.window.on_top = True
        else:
            self.window.resize(*self._full_size)
            self.state["compact"] = False
        return self._save()
    def set_on_top(self, value):
        self.state["on_top"] = bool(value)
        self.window.on_top = self.state["on_top"]
        return self._save()
    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._save()
        finally:
            self.on_close()
```

另加 `remember_geometry()`，调用 `_save()` 供移动和缩放事件使用；窗口运行入口用 0.35 秒 `threading.Timer` 合并高频保存，保存异常只打印提示。

- [ ] **Step 5: 声明可选依赖并运行测试**

`requirements-desktop.txt` 内容为 `pywebview>=6,<7`。

Run: `python -m unittest tests.test_desktop -v`
Expected: 3 个测试通过。

### Task 2: 真实桌面创建、CLI 生命周期与安全回退

**Files:**
- Modify: `covenant/ui/desktop.py`
- Modify: `covenant/__main__.py:43-109,252-272`
- Modify: `tests/test_desktop.py`

**Interfaces:**
- Consumes: Task 1 的存储和控制器、现有 `AppServer.start()` 返回 URL、`threading.Event`。
- Produces: `run_desktop(url: str, var_dir: str, on_close: Callable[[], None]) -> None` 和 `serve --desktop`。

- [ ] **Step 1: 增加 CLI 失败测试**

```python
from covenant.__main__ import build_parser

def test_desktop_flag_only_on_serve(self):
    args = build_parser().parse_args(["serve", "--desktop", "--no-browser"])
    self.assertTrue(args.desktop)
    self.assertFalse(hasattr(build_parser().parse_args(["demo"]), "desktop"))
```

- [ ] **Step 2: 验证 CLI 测试失败**

Run: `python -m unittest tests.test_desktop.DesktopTests.test_desktop_flag_only_on_serve -v`
Expected: FAIL，`unrecognized arguments: --desktop`。

- [ ] **Step 3: 实现桌面创建**

`run_desktop()` 内部延迟 `import webview`。创建窗口时使用 `min_size=(400,500)`、`background_color="#090c0f"`、当前状态尺寸与坐标、`on_top=state["on_top"]`。若坐标超出主屏幕可用范围，将其钳制到 `0` 与 `screen.width - width`／`screen.height - height` 之间。绑定 `closed`、`resized`、`moved` 事件；`webview.start()` 返回后只清理定时器，不重复调用关闭回调。

- [ ] **Step 4: 接入 `cmd_serve` 生命周期**

在 `serve` 子命令增加：

```python
sp.add_argument("--desktop", action="store_true", help="用独立桌面窗口打开（需要 pywebview）")
```

在 `cmd_serve` 中保持服务与自动识别初始化顺序。`--desktop` 时调用 `run_desktop(url, args.var, done.set)`；发生任何桌面初始化异常时打印 `桌面窗口不可用（...），已回退浏览器。安装：python -m pip install -r requirements-desktop.txt`，然后仅在未指定 `--no-browser` 时调用 `webbrowser.open(url)` 并进入原有等待循环。桌面关闭后进入现有 `finally`，停止托盘和服务。

- [ ] **Step 5: 验证 CLI 与既有测试**

Run: `python -m unittest tests.test_desktop -v`
Expected: 全部通过。

Run: `python -m covenant serve --help`
Expected: 帮助包含 `--desktop`，且不启动服务。

### Task 3: 桌面按钮、紧凑布局与复杂流程恢复

**Files:**
- Create: `covenant/ui/web/desktop.js`
- Create: `tests/test_desktop_frontend.js`
- Modify: `covenant/ui/web/index.html`
- Modify: `covenant/ui/web/app.js`
- Modify: `covenant/ui/web/style.css`

**Interfaces:**
- Consumes: Python JS API `get_state()`、`set_compact(value)`、`set_on_top(value)`。
- Produces: 浏览器全局 `CovenantDesktop.ensureFull()`；按钮 `#btn-compact`、`#btn-topmost`；`body.compact` 布局。

- [ ] **Step 1: 写桥接失败测试**

用 `require("../covenant/ui/web/desktop.js")` 导出 `createDesktopController`。以内存元素和假 `pywebview.api` 验证：

```js
const api = {
  get_state: async () => ({ compact: false, on_top: false }),
  set_compact: async (value) => ({ compact: value, on_top: value }),
  set_on_top: async (value) => ({ compact: false, on_top: value }),
};
const controller = createDesktopController({ pywebview: { api } }, document, () => {});
await controller.init();
assert.equal(elements["#btn-compact"].hidden, false);
await controller.setCompact(true);
assert.equal(elements.body.classList.contains("compact"), true);
assert.equal(elements["#btn-compact"].textContent, "完整模式");
await controller.ensureFull();
assert.equal(elements.body.classList.contains("compact"), false);
await controller.setOnTop(true);
assert.equal(elements["#btn-topmost"].getAttribute("aria-pressed"), "true");
```

另测 `get_state()` 抛错时按钮保持隐藏并调用错误通知，不能产生未处理 Promise rejection。

- [ ] **Step 2: 验证前端测试失败**

Run: `node tests/test_desktop_frontend.js`
Expected: FAIL，桌面桥接模块不存在。

- [ ] **Step 3: 实现独立前端桥接**

`createDesktopController(win, doc, notify)` 返回 `{init, setCompact, setOnTop, ensureFull}`。`init()` 等待 API 的 `get_state()`，成功后移除 `.desktop-only` 的 `hidden`；`apply()` 同步 `body.compact`、按钮文字、`aria-pressed` 和 `.on`；所有桥接错误通过 `notify("桌面窗口操作失败：...")` 报告并恢复按钮可用。`ensureFull()` 仅在紧凑且桥接已就绪时调用 `setCompact(false)`，否则返回已 resolve 的 Promise。

浏览器入口监听一次 `pywebviewready`，将控制器保存为 `window.CovenantDesktop`；Node 环境通过 `module.exports` 仅导出工厂，不访问真实 DOM。

- [ ] **Step 4: 添加桌面按钮并加载桥接**

在 `#btn-new` 前加入：

```html
<button class="ghost desktop-only hidden" id="btn-compact" type="button">紧凑模式</button>
<button class="ghost desktop-only hidden" id="btn-topmost" type="button" aria-pressed="false">置顶</button>
```

在 `app.js` 前加入 `<script src="/static/desktop.js"></script>`。桌面按钮由桥接工厂绑定，不在 `app.js` 重复绑定。

- [ ] **Step 5: 复杂操作恢复完整模式**

在 `switchView("stats")`、`openPicker()`、`openSettings()`、`openRecord()`、`openUploader()` 的开头调用：

```js
window.CovenantDesktop?.ensureFull();
```

该调用不清空输入、不关闭已有弹窗、不改变当前业务状态。

- [ ] **Step 6: 添加紧凑布局**

```css
body.compact { overflow: hidden; }
body.compact #match-main { grid-template-columns: minmax(0, 1fr); overflow: hidden; }
body.compact .intel-panel, body.compact .brand-sub, body.compact .pack-info,
body.compact #btn-new, body.compact #btn-auto, body.compact .head-right a { display: none; }
body.compact .head-mid { order: initial; flex-basis: auto; }
body.compact .board-panel { height: 100%; overflow: auto; }
body.compact .hud { grid-template-columns: repeat(3, minmax(0, 1fr)); }
body.compact .shop { grid-template-columns: repeat(2, minmax(0, 1fr)); }
body.compact .stat.phase { grid-column: auto; }
```

保留紧凑按钮、置顶、对局／统计、编辑商店和设置入口；所有隐藏功能可通过恢复完整模式访问。

- [ ] **Step 7: 运行前端回归**

Run: `node tests/test_desktop_frontend.js && node tests/test_plugin_interface.js && node tests/test_frontend.js && node --check covenant/ui/web/desktop.js && node --check covenant/ui/web/app.js`
Expected: 全部通过，无未处理 Promise rejection。

### Task 4: 依赖安装、桌面实测、快捷方式与安全同步

**Files:**
- Modify: `README.md`
- Modify: 桌面快捷方式 `卫戍协议记牌器.lnk`
- Verify: 本任务全部新增／修改文件

**Interfaces:**
- Consumes: 前三个任务的桌面壳、原项目 Python 3.12、现有端口 8766。
- Produces: 可双击启动的独立应用、可复现依赖安装命令、实际窗口验收记录。

- [ ] **Step 1: 同步前备份与差异检查**

比较原项目与副本的 `__main__.py`、`app.js`、`index.html`、`style.css`。若存在基线外改动，先合并而非覆盖。备份所有将被替换的原文件到当前工作区时间戳目录。

- [ ] **Step 2: 安装桌面依赖**

Run: `py -3 -m pip install -r requirements-desktop.txt`
Expected: 安装 pywebview 6.x 成功；不安装 Electron 或其他前端依赖。

Run: 同一 Python `-c "import webview; print(webview.__version__)"`
Expected: 输出实际版本。

- [ ] **Step 3: 更新文档与快捷方式**

README 增加：

```bat
py -3 -m pip install -r requirements-desktop.txt
py -3 -m covenant serve --desktop --window Arknights
```

说明普通 `serve --window Arknights` 仍打开浏览器。快捷方式继续使用 `pythonw.exe`、工作目录为原项目、端口 8766，参数改为 `-m covenant serve --port 8766 --window Arknights --desktop`。

- [ ] **Step 4: 全量自动化回归**

Run: `python -m unittest discover -s tests -v`
Expected: 所有测试通过，`OK`。

Run: `node tests/test_desktop_frontend.js && node tests/test_plugin_interface.js && node tests/test_frontend.js && node --check covenant/ui/web/app.js && node --check covenant/ui/web/desktop.js && python -m covenant selftest`
Expected: 全部通过。

- [ ] **Step 5: 实际窗口验收**

用独立 `--var` 测试目录启动，避免污染真实对局。确认无地址栏；点击紧凑、置顶、恢复完整；打开统计、设置和选人；移动和缩放后关闭，再启动确认恢复。将损坏的 `desktop.json` 放入另一个测试目录，确认回退默认配置且服务可用。测试结束只关闭本次测试进程，保留用户 8766 正式窗口。

- [ ] **Step 6: 交付前复核**

确认桌面快捷方式指向原项目，普通浏览器模式仍能启动，临时测试服务与脚本已清理。最终报告实际依赖版本、测试数量、窗口实测结果和任何未覆盖环境。
