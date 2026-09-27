# osu-radar 一键启动（Windows PowerShell）
#   .\start.ps1 stop    停止运行中的服务（按 data/server.pid）
#   $env:SKIP_INGEST=1  跳过增量导入，直接起服务
#   $env:NO_BROWSER=1   不自动打开浏览器
#   $env:PORT=25431     首选端口（被占用时自动顺延 +1）
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$PidFile = "data/server.pid"

# Python 入口：优先 uv（自动 .venv），缺失回退系统 python
function Invoke-Py {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv run python3 @args
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        python @args
    } else {
        throw "未找到 python（建议安装 uv: https://docs.astral.sh/uv/）"
    }
}

if ($args[0] -eq "stop") {
    if (Test-Path $PidFile) {
        $parts = (Get-Content $PidFile -First 1) -split " "
        $TargetPid = [int]$parts[0]
        try {
            Stop-Process -Id $TargetPid -ErrorAction Stop
            Write-Host "已停止 osu-radar (pid $TargetPid, port $($parts[1]))"
        } catch {
            Write-Host "pid $TargetPid 已不存在，清理 .pid"
        }
        Remove-Item $PidFile -ErrorAction SilentlyContinue
    } else {
        Write-Host "未发现 $PidFile（服务未在运行？）"
    }
    Invoke-Py -c "import tosu_ctl; tosu_ctl.stop()" | Out-Null
    exit 0
}

Invoke-Py -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) { throw "需要 Python >= 3.11" }

# .env 引导（不存在则从模板创建）
if (-not (Test-Path .env) -and (Test-Path .env.example)) {
    Copy-Item .env.example .env
    Write-Host "已创建 .env（可在 Web 配置页调整路径）"
}

# 增量导入：复制新 replay → 只分析没见过的（已分析过自动跳过，秒级）
if ($env:SKIP_INGEST -ne "1") {
    Invoke-Py ingest.py
}

# 前端产物检查
if (-not (Test-Path frontend/dist/index.html)) {
    Write-Warning "frontend/dist 缺失 —— cd frontend; npm install; npm run build"
}

Write-Host ""
Write-Host "osu-radar: http://127.0.0.1:$Port"

# 启动后自动打开浏览器 "/" 配置页（NO_BROWSER=1 时 server.py 跳过）
Invoke-Py server.py --port $Port
