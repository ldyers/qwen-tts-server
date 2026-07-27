# guard_worker.ps1 - Auto-start TTS Worker on demand, with health check
$ErrorActionPreference = "SilentlyContinue"

$WORKER_PORT = 8001
$WORKER_DIR = "C:\qwen-tts-worker"
$WORKER_SCRIPT = "$WORKER_DIR\run_worker.bat"
$MAX_RESTARTS_PER_HOUR = 5

$restartFile = "$WORKER_DIR\.restart_count"
$now = Get-Date

$count = 0
$resetTime = $now
if (Test-Path $restartFile) {
    $data = Get-Content $restartFile | ConvertFrom-Json
    $count = $data.Count
    $resetTime = [DateTime]$data.ResetTime
    if (($now - $resetTime).TotalHours -ge 1) {
        $count = 0
        $resetTime = $now
    }
}

function Test-WorkerHealth {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost:$WORKER_PORT/health" -TimeoutSec 5 -UseBasicParsing
        return $r.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Start-Worker {
    if ($count -ge $MAX_RESTARTS_PER_HOUR) {
        return
    }
    Write-Output "$($now.ToString('yyyy-MM-dd HH:mm:ss')): Starting Worker..."
    Start-Process -FilePath $WORKER_SCRIPT -WorkingDirectory $WORKER_DIR -WindowStyle Hidden
    $count++
    $data = @{ Count = $count; ResetTime = $resetTime.ToString('o') }
    $data | ConvertTo-Json | Set-Content $restartFile
}

$pythonProcs = Get-Process python* -ErrorAction SilentlyContinue

if ($pythonProcs) {
    if (Test-WorkerHealth) {
        # Worker is healthy, do nothing
    } else {
        Write-Output "$($now.ToString('yyyy-MM-dd HH:mm:ss')): Worker unhealthy, restarting..."
        $pythonProcs | Stop-Process -Force
        Start-Sleep -Seconds 3
        Start-Worker
    }
} else {
    Start-Worker
}
