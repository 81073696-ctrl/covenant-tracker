# 自动跟随游戏 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 连接采集源后，让插件用稳定、高置信度的画面观测自动跟随对局，并把手工动作改为纠错入口。

**Architecture:** 在采集服务前增加纯 Python 稳定帧门控，每帧只识别一次；可靠观测通过现有绝对状态处理器同步。刷新使用独立的 `refresh_observed` 审计事件，不调用有经济副作用的 reducer。界面只消费 `/api/capture` 的跟随状态和待确认项。

**Tech Stack:** Python 3、`unittest`、现有 `Engine`／`Observation`／事件账本、原生 HTML／CSS／JavaScript、Node.js 语法检查。

## Global Constraints

- 只读屏幕，不读写游戏内存，不自动点击，不代打。
- 高置信度且连续两次一致才自动同步；低置信度、缺失和 `unknown` 不得清空有效状态。
- 自动同步不得调用 `refresh`、`level_up` 或 `next_round` 动作 reducer。
- 不承诺首版完整自动归因购买、出售、合成、锁定及所有战斗事件。
- 测试只用临时目录与演示帧，不修改真实对局存档。
- 保留 DeepSeek 并发修改，不覆盖无关文件；按用户要求不执行 Git 提交。
- 不引入新依赖，不改变现有手工账本和经济公式。

---

## File Structure

- Create `covenant/services/follow.py`：只负责观测指纹、两帧稳定判断、场景失效和待确认信息。
- Modify `covenant/services/capture.py`：串行化抓帧批次、调用稳定帧门控、同步观测并暴露跟随状态。
- Modify `covenant/state/engine.py`：提供批量观测入口和只追加审计事件的自动刷新记录入口。
- Modify `covenant/state/events.py`：重放时跳过不改变游戏状态的 `refresh_observed`。
- Modify `covenant/stats/refreshes.py`：把可靠的自动刷新事件纳入实测样本。
- Modify `covenant/services/settings.py`、`covenant/ui/server.py`、`covenant/ui/routes/game.py`、`covenant/__main__.py`：连接源后默认启动、切换源时复位。
- Modify `covenant/ui/web/index.html`、`app.js`、`style.css`：跟随状态、待确认提示和手动纠错区。
- Modify `tests/test_services.py`、`test_events.py`、`test_ui.py`：覆盖新行为并更新默认设置断言。
- Modify `README.md`：说明自动跟随边界及验证命令。

### Task 1: 稳定帧与整帧观测同步

**Files:**
- Create: `covenant/services/follow.py`
- Modify: `covenant/services/capture.py`
- Modify: `covenant/state/engine.py`
- Test: `tests/test_services.py`

**Interfaces:**
- Produces: `FollowGate.accept(observations: list[Observation]) -> tuple[list[Observation], list[dict]]`。
- Produces: `FollowGate.reset() -> None`。
- Produces: `Engine.observe_batch(observations: list[Observation], *, min_confidence: float = 0.95) -> list[dict]`。
- Produces: `/api/capture` 新字段 `state: str`、`last_sync: float | None`、`pending: list[dict]`、`batch: int`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_services.py` 增加会依次返回观测的 `FakeVision`，并加入以下测试：第一次高置信度资金观测只进入等待；第二次相同观测才同步；0.80 置信度只产生 `pending`；场景变化后需重新积累两帧；相同状态不重复增加账本事件。

```python
from covenant.core.types import Observation, ObservationKind

def test_stable_high_confidence_observation_syncs_on_second_frame(self):
    obs = Observation(ObservationKind.GOLD, 9, confidence=0.99)
    self.vision.items = [obs]
    self.service.source = FakeSource()
    first = self.service.read()
    self.assertEqual(first["state"], "stabilizing")
    self.assertEqual(self.engine.state.gold, PACK.economy.income(1))
    second = self.service.read()
    self.assertEqual(self.engine.state.gold, 9)
    self.assertEqual(second["batch"], 2)

def test_low_confidence_does_not_clear_or_write(self):
    self.vision.items = [Observation(ObservationKind.GOLD, 0, confidence=0.80)]
    self.service.source = FakeSource()
    self.service.read()
    self.service.read()
    self.assertNotEqual(self.engine.state.gold, 0)
    self.assertEqual(self.service.status()["pending"][0]["kind"], ObservationKind.GOLD)
```

- [ ] **Step 2: 验证测试先失败**

Run: `python -m unittest tests.test_services.TestCaptureService.test_stable_high_confidence_observation_syncs_on_second_frame tests.test_services.TestCaptureService.test_low_confidence_does_not_clear_or_write -v`

Expected: FAIL，原因是 `read()` 当前第一帧就写入且没有 `state`、`batch`、`pending`。

- [ ] **Step 3: 实现纯稳定帧门控**

`follow.py` 使用 `json.dumps(obs.value, ensure_ascii=False, sort_keys=True)` 作为值指纹。每帧每种 `kind` 只保留最后一条；只有指纹与上一候选相同且 `confidence >= 0.95` 才输出该观测。低置信度项输出 `{kind, confidence, reason}`；`scene` 改变时先清空候选，再接受新场景候选。`reset()` 清空候选和待确认项。

```python
class FollowGate:
    def __init__(self, min_confidence: float = 0.95) -> None:
        self.min_confidence = min_confidence
        self._candidates: dict[str, tuple[str, Observation]] = {}
        self._scene: str | None = None

    def reset(self) -> None:
        self._candidates.clear()
        self._scene = None
```

- [ ] **Step 4: 串行化整帧同步并暴露状态**

在 `CaptureService` 增加 `_sync_lock = threading.RLock()`、`FollowGate`、`batch`、`last_sync` 和 `pending`。`read()` 在锁内完成一次 `grab()`、一次 `vision.read(frame)`、门控和 `engine.observe_batch()`。`Engine.observe_batch()` 在现有 `RLock` 内逐条调用 `observe()`，防止手工动作插入一个批次。状态无变化时为 `following`，首批或变化候选为 `stabilizing`，存在待确认项时为 `needs_confirmation`。`status()` 返回新字段，`start()` 先 `reset()`。

- [ ] **Step 5: 运行服务层回归**

Run: `python -m unittest tests.test_services tests.test_ui.TestSameAsState -v`

Expected: PASS；旧 `same_as_state` 去重行为不变。

### Task 2: 无副作用的自动刷新审计事件

**Files:**
- Modify: `covenant/state/engine.py`
- Modify: `covenant/state/events.py`
- Modify: `covenant/stats/refreshes.py`
- Modify: `covenant/services/capture.py`
- Test: `tests/test_events.py`
- Test: `tests/test_services.py`

**Interfaces:**
- Consumes: Task 1 的 `Engine.observe_batch()` 和稳定帧批次。
- Produces: `Engine.record_observed_refresh(*, expected_revision: int, gold_before: int, gold_after: int, shop: list[dict], evidence: str) -> dict`。
- Produces: 账本事件 `type="refresh_observed"`，包含 `gold_before`、`gold_after`、`shop_level`、`shop_after`、`action={"source":"vision"}` 和 `evidence`。

- [ ] **Step 1: 写刷新与重放失败测试**

构造同回合、同等级、阵容不变、资金恰好减少 `pack.economy.refresh_cost`、完整商店指纹变化的两帧稳定批次。断言只新增一条 `refresh_observed`，资金等于画面值，商店等于识别值，重复帧不新增。另构造资金减少但阵容同时变化的批次，断言没有刷新事件而有待确认提示。

```python
res = engine.record_observed_refresh(
    expected_revision=engine.revision,
    gold_before=10,
    gold_after=9,
    shop=[{"slot": 0, "op_id": "op_a", "known": True}],
    evidence="stable_shop_and_gold",
)
assert res["ok"] is True
assert engine.state.gold == 10
```

将事件交给 `replay_events()`，断言不会扣款、清空商店或要求 `action`  reducer 重放。

- [ ] **Step 2: 验证测试先失败**

Run: `python -m unittest tests.test_events tests.test_services -v`

Expected: FAIL，原因是没有 `record_observed_refresh`，重放器会把新事件当动作处理。

- [ ] **Step 3: 实现原子审计入口**

`record_observed_refresh()` 在 `Engine._lock` 内检查 `_ledger_error`、`expected_revision == self.revision`、金额差等于 `pack.economy.refresh_cost` 和商店列表有效。只追加 `refresh_observed`，不修改 `self.state`；成功后更新 `last_change` 与 `_rev`，失败返回 `{ok: False, error: "具体校验错误"}`。`replay_events()` 对 `match_end` 和 `refresh_observed` 都直接 `continue`。

- [ ] **Step 4: 接通保守归因和统计**

`CaptureService.read()` 保存批次前状态摘要与 revision，批次后仅在同 `match_id`、同回合、同等级、阵容不变、资金差等于刷新费用且商店完整指纹改变时调用 `record_observed_refresh()`。记录 `_last_refresh_key=(match_id, round, gold_before, gold_after, shop_fingerprint)` 防重。若只有部分证据则添加 `kind="refresh"` 的 pending，不写账本。

`refresh_history()` 将 `refresh_observed` 直接生成 `confirmed=True`、来源为 `vision` 的历史项；原有人工 `refresh` 配对逻辑保持原样。统计说明改为“实测包含人工确认及高置信度自动同步”。

- [ ] **Step 5: 验证幂等和旧账本兼容**

Run: `python -m unittest tests.test_events tests.test_services tests.test_stats -v`

Expected: PASS；自动事件只记一次，旧人工刷新样本仍正确，重放不改变状态。

### Task 3: 连接后默认跟随与生命周期复位

**Files:**
- Modify: `covenant/services/settings.py`
- Modify: `covenant/services/capture.py`
- Modify: `covenant/ui/server.py`
- Modify: `covenant/ui/routes/game.py`
- Modify: `covenant/__main__.py`
- Test: `tests/test_services.py`
- Test: `tests/test_ui.py`
- Test: `tests/test_routes.py`

**Interfaces:**
- Produces: `CaptureService.set_source(source: Any) -> None`，负责换源并清空稳定缓存。
- Consumes: 设置项 `auto_capture: bool = True`；已有用户的显式 `false` 仍保留。

- [ ] **Step 1: 写生命周期失败测试**

```python
self.assertTrue(SettingsService(path).data["auto_capture"])
app.frame_source = FakeSource()
self.assertTrue(app.capture_status()["on"])
app.capture_service.stop()
app.frame_source = FakeSource()
self.assertEqual(app.capture_status()["batch"], 0)
```

另测试持久化 `{"auto_capture": false}` 后连接源不会自动启动，无采集源时保持 `state="disconnected"`。

- [ ] **Step 2: 验证测试先失败**

Run: `python -m unittest tests.test_services.TestSettingsService tests.test_ui.TestSettings tests.test_routes -v`

Expected: FAIL，当前默认值为 `False` 且设置 `frame_source` 不启动线程。

- [ ] **Step 3: 实现连接即跟随**

将 `DEFAULTS["auto_capture"]` 改为 `True`，不改变已有配置文件里的显式值。`set_source()` 在采集服务锁内停止旧线程、替换 source、调用 `FollowGate.reset()` 并清零批次状态。`AppServer.frame_source` setter 改用 `set_source()`，随后仅在 `settings["auto_capture"]` 为真且源存在时启动。`cmd_serve()` 和 `cmd_demo()` 仅在尚未启动时启动，避免双线程。`set_frame_source()` 返回新窗口及 capture 状态。

- [ ] **Step 4: 运行启停和路由测试**

Run: `python -m unittest tests.test_services tests.test_ui tests.test_routes tests.test_http -v`

Expected: PASS；启动、暂停、换源和显式关闭自动跟随均可重复执行。

### Task 4: 跟随状态与手动纠错界面

**Files:**
- Modify: `covenant/ui/web/index.html`
- Modify: `covenant/ui/web/app.js`
- Modify: `covenant/ui/web/style.css`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: `/api/capture` 的 `on`、`source`、`state`、`last_sync`、`pending`、`error`。
- Produces: `#follow-status`、`#follow-detail`、`#follow-pending`、`#manual-actions`；保留原按钮 ID 和动作 API。

- [ ] **Step 1: 写界面结构失败测试**

在 `tests/test_ui.py` 读取 HTML 和 JS，断言存在跟随状态与 `<details id="manual-actions">`，三个原按钮位于该折叠区，JS 根据 `CAPTURE.on && CAPTURE.source` 切换 `follow-active`，并在自动跟随激活时屏蔽 `r/e/n` 手工快捷键。

```python
self.assertIn('id="follow-status"', html)
self.assertIn('id="manual-actions"', html)
self.assertIn("CAPTURE.on && CAPTURE.source", js)
```

- [ ] **Step 2: 验证测试先失败**

Run: `python -m unittest tests.test_ui -v`

Expected: FAIL，当前没有跟随状态元素和手动纠错容器。

- [ ] **Step 3: 实现界面**

把 `刷新／升级／过回合` 移入 `<details id="manual-actions"><summary>手动纠错</summary><div class="actions">三个原按钮</div></details>`；`锁定` 与 `摆一张` 保持原位。状态带下增加：

```html
<div class="follow-bar" id="follow-bar" role="status" aria-live="polite">
  <strong id="follow-status">未连接</strong>
  <span id="follow-detail"></span>
</div>
<div class="follow-pending hidden" id="follow-pending"></div>
```

`renderCapture()` 映射 `disconnected／stabilizing／following／needs_confirmation／paused` 为中文，显示最近同步时间和来源；正常跟随时隐藏“确认商店”按钮，暂停或未连接时展开手工区。低置信度 pending 仅显示字段与原因，不伪造操作成功。快捷键仅在没有自动跟随或手动纠错区展开时生效。

- [ ] **Step 4: 添加布局样式并验证**

`.follow-bar` 使用现有冷青状态色和暖金警示色；窄屏允许换行；`.follow-active .shop-confirm` 隐藏，但所有状态文本保留。运行：

Run: `python -m unittest tests.test_ui -v`

Run: `node --check covenant/ui/web/app.js`

Expected: PASS 和无语法错误。

### Task 5: 集成验证与使用说明

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-11-07-auto-follow-design.md`
- Test: `tests/test_services.py`
- Test: `tests/test_events.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: 前四项任务的公开接口。
- Produces: 可复核的测试证据和自动跟随使用／限制说明。

- [ ] **Step 1: 添加端到端临时账本测试**

使用 `TemporaryDirectory`、`Engine.attach_storage()`、 FakeSource 和有序 FakeVision 演示两帧同步与一次自动刷新。断言重新创建 Engine 后账本可重放，最终资金、回合、等级、商店及刷新历史一致；测试结束自动清理，不触碰项目 `var/`。

- [ ] **Step 2: 运行新增测试**

Run: `python -m unittest tests.test_services tests.test_events tests.test_ui -v`

Expected: PASS，无真实 `var/` 文件写入。

- [ ] **Step 3: 更新文档**

README 增加“连接窗口后默认自动跟随；两帧稳定和高置信度才同步；暂停后在手动纠错区操作；购买、出售、合成仍可能需要手工纠正”的说明。将设计文档状态改为“已实施并完成自动化验证；真实游戏识别覆盖率未验证”，不得声称真实游戏准确率已经实测。

- [ ] **Step 4: 全量快速验证**

Run: `python scripts/check_all.py --quick`

Expected: 编译、全部 Python 测试、3 个 JavaScript 语法检查全部通过。

- [ ] **Step 5: 演示源 HTTP 冒烟**

用独立临时 `--var` 启动演示服务，请求 `/api/health`、`/api/capture` 和 `/api/snapshot`，确认跟随状态推进且快照由真实引擎产生。验证后停止本次启动的演示服务，不关闭其他已有服务，不留下后台进程。
