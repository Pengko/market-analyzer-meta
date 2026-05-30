#!/usr/bin/env python3
"""
主要作用:
- 提供当前主链默认读取的基础配置
- 主要包括 token、API 端点、数据目录和限速参数
"""

import os

from paths import get_stock_data_dir
from utils.tushare_bootstrap import get_tushare_http_url, get_tushare_token

# Tushare Pro Token
# 当前主链统一从 tushare_bootstrap.py 初始化
TOKEN = get_tushare_token()

# 自定义 API 端点
# 当前主链统一从 tushare_bootstrap.py 初始化
PROXY_URL = get_tushare_http_url()

# 数据存储路径
DATA_DIR = str(get_stock_data_dir())
STOCK_BASIC_FILE = f"{DATA_DIR}/stock_basic/stock_basic_non_st.csv"

# API 限速配置
RATE_LIMIT_CALLS_PER_MINUTE = 300  # 每分钟最大调用次数

# 默认日期范围
DEFAULT_START_DATE = "20200101"
