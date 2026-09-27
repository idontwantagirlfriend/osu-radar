#!/bin/sh
# osu-radar 一键启动（POSIX sh 兼容：sh/ash/bash 均可，覆盖 Linux / WSL / macOS / Git Bash）
#   SKIP_INGEST=1  跳过增量导入，直接起服务
#   NO_BROWSER=1   不自动打开浏览器
#   PORT=8000      服务端口
set -eu
cd "$(dirname "$0")"

PORT="${PORT:-8000}"

# Python 入口：优先 uv（首次运行会按 pyproject.toml 自动创建 .venv，无需手动 uv venv），
# 缺失则回退系统 python3（本项目零第三方依赖）。
# 注意用变量而非函数：末行 exec 只能执行外部命令，函数会 127
if command -v uv >/dev/null 2>&1; then
  PY_CMD="uv run python3"
elif command -v python3 >/dev/null 2>&1; then
  PY_CMD="python3"
else
  echo "错误：未找到 python3（建议安装 uv: https://docs.astral.sh/uv/）" >&2
  exit 1
fi

# shellcheck disable=SC2086
$PY_CMD -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "错误：需要 Python >= 3.10" >&2; exit 1; }

# .env 引导（不存在则从模板创建）
[ -f .env ] || { cp .env.example .env; echo "已创建 .env（可在 Web 配置页调整路径）"; }

# 增量导入：复制新 replay → 只分析没见过的（已分析过自动跳过，秒级）
if [ "${SKIP_INGEST:-0}" != "1" ]; then
  $PY_CMD ingest.py
fi

# 前端产物检查
if [ ! -f frontend/dist/index.html ]; then
  echo "提示：frontend/dist 缺失 —— cd frontend && npm install && npm run build" >&2
fi

echo ""
echo "osu-radar: http://127.0.0.1:${PORT}"

# 启动后自动打开浏览器（NO_BROWSER=1 关闭）
if [ "${NO_BROWSER:-0}" != "1" ]; then
  (xdg-open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true) &
fi

exec $PY_CMD server.py --port "${PORT}"
