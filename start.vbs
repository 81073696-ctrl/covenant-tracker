' 双击启动，不弹黑框；浏览器会自动打开面板。
' 想调试就改用 run.bat（保留控制台输出）。
Option Explicit
Dim sh, fso, here
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = here
sh.Run "cmd /c chcp 65001>nul & py -3 -m covenant serve --port 8765 --window Arknights", 0, False
