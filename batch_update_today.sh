#!/bin/bash
# 主要作用:
# - 为人工值守场景提供一个“先拉最近交易日，再按需闭环补齐”的包装入口
# - 只调用当前保留的健康主链脚本
#
# 适用场景:
# - 收盘后手动执行一次完整主链
# - 需要把当天增量更新和缺口补齐串起来跑

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"
QUANT_DIR="${QUANT_DATA_DIR:-$HOME/quant-data}"
LOG_DIR="$QUANT_DIR/tushare/logs"
mkdir -p "$LOG_DIR"

RUN_DATE="$(date +%Y%m%d)"
LOG_FILE="$LOG_DIR/batch_update_today_${RUN_DATE}.log"

echo "=========================================="
echo "开始执行 Tushare Pro 主链批处理"
echo "项目目录: $PROJECT_DIR"
echo "开始时间: $(date)"
echo "日志文件: $LOG_FILE"
echo "=========================================="

cd "$PROJECT_DIR"

{
  echo "[STEP 1] 执行最近交易日更新"
  python3 auto_fill_data.py --mode latest --latest-trade-days 1

  echo
  echo "[STEP 2] 执行完整性检查与自动补齐"
  python3 auto_fill_data.py --mode auto
} 2>&1 | tee -a "$LOG_FILE"

echo "=========================================="
echo "执行完成: $(date)"
echo "=========================================="
