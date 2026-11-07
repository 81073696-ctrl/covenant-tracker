# 参与与接手

感谢你愿意看这个项目。当前最需要的是一位愿意持续跟进官方 UI、规则变化和识别校准的维护者；修单个问题、补截图诊断或完善文档同样欢迎。

## 先跑起来

要求：Windows、Python 3.10+、NumPy、Pillow；桌面窗口另需 `requirements-desktop.txt`。

```bash
python -m pip install numpy Pillow
python -m covenant selftest
python scripts/check_all.py --quick
python -m covenant demo
```

前端语法检查需要 Node.js。完整检查可运行：

```bash
python scripts/check_all.py --frames var/demo_frames
```

Screen Lens 是独立的辅助诊断工具，演示方式：

```bash
cd tools/screen-lens
python -m unittest discover -s tests -v
python -m screenlens serve --samples samples --layout layouts/mock.json --var var
```

## 提交改动

1. 开一个 Issue 说明问题、复现步骤和预期行为；小修也可直接发 PR。
2. 行为修改尽量先补回归测试，尤其是状态合并、资金归因和跨帧门控。
3. 不提交 `var/`、个人模板、真实对局存档、令牌、本机绝对路径或未经确认的私人数据。
4. 涉及识别错误时，附上诊断导出的 `frame.png` 与 `report.json`；先确认素材可以公开。
5. 完成后运行完整测试，并在 PR 中写明实际跑过的命令和结果。
6. 官方未公开的规则不能写成确定事实，请在数据包中保留来源与置信度。

## 优先级

- 官方客户端的布局与模板校准流程；
- 免费刷新、已售出商店、跨帧变化的可靠归因；
- 多单位、星级、装备和结算识别；
- 让未知格形成可标注队列，减少逐张手工教学；
- 可复现的端到端回放测试与 CI。

详细技术边界见 `docs/MAINTAINER_HANDOFF.md` 和 `docs/ARCHITECTURE.md`。

## 行为边界

本项目只读屏幕，不读内存、不注入、不修改游戏客户端。请不要提交绕过反作弊、自动操作正式服或其他可能危害用户账号的功能。
