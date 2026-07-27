@echo off
REM Qwen3-TTS GPU Worker - 启动脚本 (预加载模式)
echo Starting Qwen3-TTS GPU Worker (preloading all models)...
call .venv\Scripts\activate.bat
python worker\start_worker.py --host 0.0.0.0 --port 8001 --device cuda:0 --preload
pause
