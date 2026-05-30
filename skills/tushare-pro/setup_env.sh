#!/bin/bash
# Tushare Pro 环境变量设置脚本
# 将此文件中的配置复制到您的shell配置文件中（如 ~/.bashrc, ~/.zshrc, ~/.bash_profile）

echo "============================================================"
echo "Tushare Pro 环境变量配置"
echo "============================================================"
echo ""
echo "请将以下配置添加到您的shell配置文件中："
echo "（如 ~/.bashrc, ~/.zshrc, ~/.bash_profile）"
echo ""
echo "# ==================== Tushare Pro 配置 ===================="
echo "export TUSHARE_TOKEN=\"6be0552842c69a4c84636359df4028459ce14d13d092cdce491ce77d361ab5a6\""
echo "export TUSHARE_API_URL=\"http://124.220.22.110:8020/\""
echo "export TUSHARE_CACHE_DIR=\"$HOME/tushare_cache\""
echo "# ==========================================================="
echo ""
echo "配置说明："
echo "1. TUSHARE_TOKEN:     您的Tushare Pro token（必须）"
echo "2. TUSHARE_API_URL:   当前统一 relay 端点"
echo "3. TUSHARE_CACHE_DIR: 缓存目录（可选，默认: ./tushare_cache）"
echo ""
echo "设置完成后，运行以下命令使配置生效："
echo "  source ~/.bashrc  # 或 source ~/.zshrc"
echo ""
echo "验证配置："
echo "  python3 -c \"import os; print('Token:', os.getenv('TUSHARE_TOKEN', '未设置'))\""
echo ""

# 可选：自动设置环境变量（当前会话有效）
read -p "是否在当前会话中设置这些环境变量？(y/N): " -n 1 -r
echo ""
if [[ $REPLY =~ ^[Yy]$ ]]; then
    export TUSHARE_TOKEN="6be0552842c69a4c84636359df4028459ce14d13d092cdce491ce77d361ab5a6"
    export TUSHARE_API_URL="http://124.220.22.110:8020/"
    export TUSHARE_CACHE_DIR="$HOME/tushare_cache"
    
    echo "✅ 环境变量已设置（仅当前会话有效）"
    echo "   TUSHARE_TOKEN: ${TUSHARE_TOKEN:0:8}...${TUSHARE_TOKEN: -8}"
    echo "   TUSHARE_API_URL: $TUSHARE_API_URL"
    echo "   TUSHARE_CACHE_DIR: $TUSHARE_CACHE_DIR"
    echo ""
    echo "⚠️  注意：这些设置仅在当前终端会话中有效。"
    echo "   要永久生效，请将配置添加到shell配置文件中。"
fi

echo ""
echo "📝 下一步："
echo "1. 将上述配置添加到shell配置文件"
echo "2. 运行 'source ~/.bashrc' 使配置生效"
echo "3. 测试技能包：python3 test_env_config.py"
