#!/usr/bin/env python3
"""
主要作用:
- 提供“走官方 API”时的备用配置模板
- 当自定义代理不可用或不想使用代理时可参考此配置
"""

import os
from utils.tushare_bootstrap import get_tushare_token

# 官方 API Token（需要通过环境变量提供）
TOKEN = get_tushare_token()

# 官方 API 无需代理
PROXY_URL = ""

# 数据存储路径
from paths import get_tushare_dir
DATA_DIR = str(get_tushare_dir() / "股票数据")
STOCK_BASIC_FILE = f"{DATA_DIR}/stock_basic/stock_basic_non_st.csv"

# 官方 API 限速更严格
RATE_LIMIT_CALLS_PER_MINUTE = 60  # 官方 API 限速更低

# 默认日期范围
DEFAULT_START_DATE = "20200101"
