# osu-radar 一键启动（Windows PowerShell）
#   $env:SKIP_INGEST=1  跳过增量导入，直接起服务
#   $env:NO_BROWSER=1   不自动打开浏览器
#   $env:PORT=8000      服务端口
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Port = if ($env:PORT) { $env:PORT } else { "8000" }

# Python 入口：优先 uv（首次运行会按 pyproject.toml 自动创建 .venv，无需手动 uv venv），
# 缺失则回退系统 python（本项目零第三方依赖）
function Invoke-Py {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv run python3 @args
    } elseif (Get-Command python -ErrorAction SilentlyContinue) {
        python @args
    } else {
        throw "未找到 python（建议安装 uv: https://docs.astral.sh/uv/）"
    }
}

Invoke-Py -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) { throw "需要 Python >= 3.10" }

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
