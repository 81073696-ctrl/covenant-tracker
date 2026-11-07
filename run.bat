@echo off
rem 卫戍协议 · 记牌器 —— 一键启动（Windows）
rem 双击即可。默认自动找标题含 Arknights 的窗口；找不到也能用，只是要手工点。
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PYTHONIOENCODING=utf-8

where py >nul 2>nul
if %errorlevel%==0 (
  set PY=py -3
) else (
  set PY=python
)

%PY% -m covenant selftest
if errorlevel 1 (
  echo.
  echo 自检失败，先看上面的报错。
  pause
  exit /b 1
)

echo.
echo 正在启动面板（关掉这个窗口即结束）...
%PY% -m covenant serve --window Arknights
pause
