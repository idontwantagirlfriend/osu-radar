#!/usr/bin/env bash
# osu-radar 一键启动（Linux / WSL / macOS）
#   SKIP_INGEST=1  跳过增量导入，直接起服务
#   NO_BROWSER=1   不自动打开浏览器
#   PORT=8000      服务端口
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

# Python 入口：优先 uv（首次运行会按 pyproject.toml 自动创建 .venv，无需手动 uv venv），
# 缺失则回退系统 python3（本项目零第三方依赖）
if command -v uv >/dev/null 2>&1; then
  PY=(uv run python3)
elif command -v python3 >/dev/null 2>&1; then
  PY=(python3)
else
  echo "错误：未找到 python3（建议安装 uv: https://docs.astral.sh/uv/）" >&2
  exit 1
fi

"${PY[@]}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "错误：需要 Python >= 3.10" >&2; exit 1; }

# .env 引导（不存在则从模板创建）
[ -f .env ] || { cp .env.example .env && echo "已创建 .env（可在 Web 配置页调整路径）"; }

# 增量导入：复制新 replay → 只分析没见过的（已分析过自动跳过，秒级）
if [ "${SKIP_INGEST:-0}" != "1" ]; then
  "${PY[@]}" ingest.py
fi

# 前端产物检查
if [ ! -f frontend/dist/index.html ]; then
  echo "提示：frontend/dist 缺失 —— cd frontend && npm install && npm run build" >&2
fi

echo ""
echo "osu-radar: http://127.0.0.1:${PORT}"

# 启动后自动打开浏览器 "/" 配置页（NO_BROWSER=1 时 server.py 跳过）
exec "${PY[@]}" server.py --port "${PORT}"
