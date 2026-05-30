#!/bin/bash
# 一键快速分析脚本
# 用法: ./quick_analyze.sh 600103.SH 2026-04-24

set -e

SYMBOL="${1:-600103.SH}"
DATE="${2:-$(date +%Y-%m-%d)}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"

# 1. 预收集数据
echo "[1/3] 预收集数据..."
JSON_PATH=$(python3 "${SCRIPT_DIR}/fetchers/pre_collect_data.py" --symbol "$SYMBOL" --date "$DATE" 2>/dev/null)

if [ ! -f "$JSON_PATH" ]; then
    echo "错误: 预收集失败"
    exit 1
fi

echo "  → JSON已保存: $JSON_PATH"

# 2. 生成报告
echo "[2/3] 生成分析报告..."
REPORT=$(python3 "${SCRIPT_DIR}/fetchers/quick_analyze_from_precollected.py" "$JSON_PATH" 2>/dev/null)

# 3. 保存报告
echo "[3/3] 保存报告..."
CODE=$(echo "$SYMBOL" | sed 's/\.SH//;s/\.SZ//')
MARKET_PREFIX=$(echo "$SYMBOL" | grep -o '\.S[HZ]')
NAME=$(echo "$REPORT" | head -1 | sed 's/# //;s/ (.*//')

# 标准保存路径
SAVE_DIR="${SKILL_DIR}/references/pending-validations/${DATE}"
mkdir -p "$SAVE_DIR"
FILENAME="待验证-${CODE}${MARKET_PREFIX}-${NAME}-精简分析.md"
REPORT_PATH="${SAVE_DIR}/${FILENAME}"

echo "$REPORT" > "$REPORT_PATH"
echo "  → 报告已保存: $REPORT_PATH"

echo ""
echo "✅ 完成! 总耗时: ${SECONDS}s"
