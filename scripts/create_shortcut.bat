@echo off
REM Qwen-TTS Worker 桌面快捷方式生成脚本
REM 在 Windows 上运行此脚本创建桌面快捷方式

set SHORTCUT=%USERPROFILE%\Desktop\Qwen-TTS-Worker.lnk
set TARGET=C:\qwen-tts-worker\run_worker.bat
set ICON=C:\qwen-tts-worker\run_worker.bat

powershell -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%SHORTCUT%'); $sc.TargetPath = '%TARGET%'; $sc.WorkingDirectory = 'C:\qwen-tts-worker'; $sc.Description = 'Qwen3-TTS GPU Worker'; $sc.IconLocation = '%ICON%,0'; $sc.Save()"

echo Desktop shortcut created: %SHORTCUT%
