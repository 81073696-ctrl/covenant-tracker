# 打包成 exe（预演）

> 目标：双击就能用，不用装 Python。当前是 **v1.2 预演**——单文件 exe 能跑通，
> 托盘与轻量设置页已经能用；自动更新还没做。

## 构建

```bat
py -3 -m pip install pyinstaller
py -3 scripts\build_exe.py            :: onefile，产物 dist\CovenantTracker.exe
py -3 scripts\build_exe.py --onedir   :: 调试用，启动更快
```

构建脚本做的事：

| 参数 | 作用 |
| --- | --- |
| `--onefile` | 打成单个 exe（首次启动会解压到临时目录，约 1–2 秒） |
| `--add-data covenant/ui/web` | 网页面板（HTML/CSS/JS） |
| `--add-data packs` | 内置数据包（`act2autochess` 与 `act2autochess-private`） |
| `--collect-submodules covenant` | 保证 `doctor`/`community` 等延迟导入的模块也在包里 |
| `--paths .` | 让入口脚本 import 到源码包 |

## 运行

```bat
dist\CovenantTracker.exe                  :: 默认 serve，自动开浏览器（8765）
dist\CovenantTracker.exe demo --port 8790 :: 无游戏演示
dist\CovenantTracker.exe selftest
dist\CovenantTracker.exe doctor
```

- 只读资源（网页、数据包）在 exe 内部，运行时解压到 `%TEMP%`；
- **可写数据在 exe 旁边**：`var/engine.json`、`var/matches.jsonl`、`var/events/`、`var/*.npz`；
- 想换数据包：在 exe 同目录放 `packs\<id>\pack.json`，会优先于内置包（按 `updated` 取最新）。

## 托盘

`serve` 加 `--tray` 会显示托盘图标；打包后的 exe 默认开启；不想要托盘就加 `--no-tray`。
双击图标 = 打开面板，右键 = 「打开面板 / 退出」。实现是 `covenant/tray.py`，只用了 ctypes：
隐藏窗口 + `Shell_NotifyIcon` + `TrackPopupMenu`，没有新增依赖。

## 设置

面板右上 `⚙ 设置`（或直接开 `#settings`）：自动识别间隔、是否随面板自动开始；
只读显示版本、数据包路径与数据目录。设置存在 `var/settings.json`，下次启动仍然生效。
改端口/换数据包仍然要改命令行参数或放 `packs/`——这两项刻意不进设置页，避免半途重启。

## 已知问题

**`Could not create temporary directory!`**
个别环境（受限的 TEMP、安全软件拦截）下 onefile 解压失败。把临时目录指到可写位置再运行：

```bat
set TEMP=%CD%
set TMP=%CD%
CovenantTracker.exe
```

或者改用 `--onedir` 构建，产物在 `dist\CovenantTracker\`，不再解压。

**体积**：onefile 约 27MB（numpy + Pillow + 静态资源）。换成 onedir 后单个目录约 60MB，但启动快。

**杀软误报**：PyInstaller 的单文件包常被启发式扫描拦。内部使用可以先加白名单；要发布就做代码签名，或改用 onedir + 安装器。

## 下一步（v1.2 正式）

1. ~~托盘图标~~（已完成，纯 Win32）；
2. ~~设置页~~（轻量版已完成：间隔/自动识别）；
3. 首次启动向导：一键跑 `doctor`，缺什么教什么；
4. 自动更新：检查 GitHub Release 的 `latest.json`（先不做增量）。
