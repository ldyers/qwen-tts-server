@echo off
REM Qwen3-TTS GPU Worker - 启动脚本 (懒加载模式)
echo Starting Qwen3-TTS GPU Worker (lazy loading)...
call .venv\Scripts\activate.bat
python worker\start_worker.py --host 0.0.0.0 --port 8001 --device cuda:0
pause
