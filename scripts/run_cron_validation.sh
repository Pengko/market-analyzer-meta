#!/bin/bash
# 定时任务 Wrapper 脚本
# 用于股票深度分析定时验证和策略优化

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$SKILL_ROOT/logs"
DATE=$(date +%Y-%m-%d)
DATETIME=$(date +%Y-%m-%d_%H%M%S)

mkdir -p "$LOG_DIR"

echo "[$DATETIME] === 开始执行待验证报告验证 ===" >> "$LOG_DIR/cron_validation.log"

cd "$SCRIPT_DIR"

# 1. 运行验证脚本
python3 validate_pending_reports.py >> "$LOG_DIR/cron_validation.log" 2>&1

# 2. 验证完成后自动优化策略因子
echo "[$DATETIME] === 验证完成，开始策略优化 ===" >> "$LOG_DIR/cron_validation.log"
python3 optimize_strategy.py >> "$LOG_DIR/cron_optimization.log" 2>&1

echo "[$DATETIME] === 执行完成 ===" >> "$LOG_DIR/cron_validation.log"
