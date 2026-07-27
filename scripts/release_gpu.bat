@echo off
REM ============================================
REM Qwen3-TTS Worker - 释放显存
REM 停止 Worker 进程，释放 GPU 显存
REM ============================================

echo ============================================
echo   Qwen3-TTS Worker - 释放显存
echo ============================================
echo.

REM 停止 Worker 进程
taskkill /f /im python.exe /fi "WINDOWTITLE eq QwenWorker*" 2>nul
if %errorlevel%==0 (
    echo [OK] Worker 进程已停止
) else (
    REM 如果标题匹配失败，按命令行特征杀
    for /f "tokens=2" %%i in ('tasklist /fi "imagename eq python.exe" /fo list ^| findstr "PID"') do (
        for /f "tokens=*" %%a in ('wmic process where "processid=%%i" get commandline /value 2^>nul ^| findstr "worker"') do (
            taskkill /f /pid %%i 2>nul
            echo [OK] Worker 进程 PID=%%i 已停止
        )
    )
)

REM 等待 2 秒让显存释放
timeout /t 2 /nobreak >nul

REM 显示 GPU 显存状态
echo.
echo [GPU 显存状态]
nvidia-smi --query-gpu=memory.used,memory.total,memory.free --format=csv,noheader 2>nul
echo.
echo [OK] 显存已释放
echo.
pause
