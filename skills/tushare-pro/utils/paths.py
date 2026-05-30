"""
主要作用:
- 统一管理数据目录路径
- 以固定的 tushare 根目录为基准，拼装股票、指数、消息面等子目录
"""
from pathlib import Path


TUSHARE_ROOT_DIR = Path("/Users/penghongming/quant-data/tushare")


def get_data_dir() -> Path:
    """获取固定的 tushare 根目录。"""
    return TUSHARE_ROOT_DIR


def get_stock_data_dir() -> Path:
    """获取股票数据目录（原 investor 目录）。"""
    return TUSHARE_ROOT_DIR / "股票数据"


def get_index_data_dir() -> Path:
    """获取指数数据目录。"""
    return TUSHARE_ROOT_DIR / "指数数据"


def get_news_data_dir() -> Path:
    """获取消息面数据目录。"""
    return TUSHARE_ROOT_DIR / "消息面数据"


def get_financial_data_dir() -> Path:
    """获取财务数据目录。"""
    return TUSHARE_ROOT_DIR / "财务数据"


def get_tushare_dir() -> Path:
    """获取固定的 tushare 数据根目录。"""
    return TUSHARE_ROOT_DIR


# 向后兼容的常量
# 注意：DATA_DIR 现在指向股票数据目录，而非 tushare 根目录
DATA_DIR = str(get_stock_data_dir())
TUSHARE_DIR = str(TUSHARE_ROOT_DIR)
STOCK_DATA_DIR = str(get_stock_data_dir())
INDEX_DATA_DIR = str(get_index_data_dir())
NEWS_DATA_DIR = str(get_news_data_dir())
FINANCIAL_DATA_DIR = str(get_financial_data_dir())
