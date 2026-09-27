#!/bin/sh
# osu-radar 一键启动（POSIX sh 兼容：sh/ash/bash 均可，覆盖 Linux / WSL / macOS / Git Bash）
#   ./start.sh stop   停止运行中的服务（按 data/server.pid）
#   SKIP_INGEST=1     跳过增量导入，直接起服务
#   NO_BROWSER=1      不自动打开浏览器
#   PORT=25431        首选端口（被占用时自动顺延 +1；实际端口见启动横幅与 data/server.pid）
set -eu
cd "$(dirname "$0")"

PID_FILE="data/server.pid"

PORT="${PORT:-25431}"

# Python 入口：优先 uv（首次运行会按 pyproject.toml 自动创建 .venv 并装依赖），
# 缺失则回退系统 python3。
# 注意用变量而非函数：末行 exec 只能执行外部命令，函数会 127
if command -v uv >/dev/null 2>&1; then
  PY_CMD="uv run python3"
elif command -v python3 >/dev/null 2>&1; then
  PY_CMD="python3"
else
  echo "错误：未找到 python3（建议安装 uv: https://docs.astral.sh/uv/）" >&2
  exit 1
fi

# ---- stop：根据 .pid 关闭对应进程并删除 .pid（顺带停本工具拉起的 tosu）----
if [ "${1:-}" = "stop" ]; then
  if [ -f "$PID_FILE" ]; then
    PID=$(head -1 "$PID_FILE" | awk '{print $1}')
    PORT_USED=$(head -1 "$PID_FILE" | awk '{print $2}')
    if kill "$PID" 2>/dev/null; then
      echo "已停止 osu-radar (pid $PID, port $PORT_USED)"
    else
      echo "pid $PID 已不存在，清理 .pid"
    fi
    rm -f "$PID_FILE"
  else
    echo "未发现 $PID_FILE（服务未在运行？）"
  fi
  $PY_CMD -c "import tosu_ctl; tosu_ctl.stop()" >/dev/null 2>&1 || true
  exit 0
fi

# shellcheck disable=SC2086
$PY_CMD -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "错误：需要 Python >= 3.11" >&2; exit 1; }

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


exec $PY_CMD server.py
