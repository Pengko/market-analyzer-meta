#!/bin/bash
# 主要作用:
# - 清理旧 live 日志，只保留最新一份
# - 在当前终端前台运行 auto_fill_data.py

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
LOG_DIR="$PROJECT_DIR/logs"
LIVE_LOG="$LOG_DIR/auto_fill_data-live.log"

mkdir -p "$LOG_DIR"
rm -f "$LOG_DIR"/auto_fill_data-live*.log

{
  echo "=========================================="
  echo "启动实时日志模式"
  echo "项目目录: $PROJECT_DIR"
  echo "开始时间: $(date)"
  echo "实时日志: $LIVE_LOG"
  echo "查看命令: tail -f $LIVE_LOG"
  echo "=========================================="
  echo
} | tee "$LIVE_LOG"

cd "$PROJECT_DIR"

set +e
python3 auto_fill_data.py "$@" 2>&1 | tee -a "$LIVE_LOG"
exit_code=${PIPESTATUS[0]}
set -e

{
  echo
  echo "=========================================="
  echo "脚本结束时间: $(date)"
  echo "主脚本退出码: $exit_code"
  echo "=========================================="
} | tee -a "$LIVE_LOG"

exit "$exit_code"
