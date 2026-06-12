"""
财务数据读取器 —— 专门读财报相关数据。

职责：
1. 从本地 parquet 读取：业绩快报、财务指标、利润表、资产负债表、现金流
2. 支持按股票代码查询历史财务数据

谁用它：
- build_stock_report.py 调它获取财务数据
- 用于判断公司基本面
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from common import FINANCIAL_DATA_ROOT, STOCK_DATA_ROOT

FINANCIAL_ROOT = FINANCIAL_DATA_ROOT
STOCK_ROOT = STOCK_DATA_ROOT


def _read_parquet(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
        return df.to_dict(orient="records")
    except Exception:
        return []


def _match_symbol(rows: list[dict], symbol: str) -> list[dict]:
    return [r for r in rows if r.get("ts_code", "").startswith(symbol)]


def get_fundamental_express(symbol: str) -> list[dict]:
    """业绩快报 (~1157只, 覆盖最广)"""
    for path in sorted(FINANCIAL_ROOT.glob("express/express_*.parquet"), reverse=True):
        matched = _match_symbol(_read_parquet(path), symbol)
        if matched:
            return matched
    return []


def get_fundamental_indicator(symbol: str) -> list[dict]:
    """财务指标 (~26只)"""
    for path in sorted(FINANCIAL_ROOT.glob("fina_indicator/fina_indicator_*.parquet"), reverse=True):
        matched = _match_symbol(_read_parquet(path), symbol)
        if matched:
            return matched
    return []


def get_fundamental_income(symbol: str) -> list[dict]:
    """利润表 (~26只)"""
    for path in sorted(FINANCIAL_ROOT.glob("income/income_*.parquet"), reverse=True):
        matched = _match_symbol(_read_parquet(path), symbol)
        if matched:
            return matched
    return []


def get_fundamental_balancesheet(symbol: str) -> list[dict]:
    """资产负债表 (~63只)"""
    for path in sorted(FINANCIAL_ROOT.glob("balancesheet/balancesheet_*.parquet"), reverse=True):
        matched = _match_symbol(_read_parquet(path), symbol)
        if matched:
            return matched
    return []


def get_fundamental_cashflow(symbol: str) -> list[dict]:
    """现金流量表 (~27只)"""
    for path in sorted(FINANCIAL_ROOT.glob("cashflow/cashflow_*.parquet"), reverse=True):
        matched = _match_symbol(_read_parquet(path), symbol)
        if matched:
            return matched
    return []


def get_fundamental_mainbz(symbol: str) -> list[dict]:
    """主营业务构成 (~30只, 2025年数据)"""
    for path in sorted(FINANCIAL_ROOT.glob("fina_mainbz/*.parquet"), reverse=True):
        matched = _match_symbol(_read_parquet(path), symbol)
        if matched:
            return matched
    return []


def get_top10_holders(symbol: str) -> list[dict]:
    """前十大股东"""
    for suffix in (f"{symbol}.parquet",):
        rows = _read_parquet(STOCK_ROOT / "top10_holders" / suffix)
        if rows:
            return rows
    return []


def get_top10_floatholders(symbol: str) -> list[dict]:
    """前十大流通股东"""
    for suffix in (f"{symbol}.parquet",):
        rows = _read_parquet(STOCK_ROOT / "top10_floatholders" / suffix)
        if rows:
            return rows
    return []


def get_pledge_stat(symbol: str) -> list[dict]:
    """股权质押统计"""
    for suffix in (f"{symbol}.parquet",):
        rows = _read_parquet(STOCK_ROOT / "pledge_stat" / suffix)
        if rows:
            return rows
    return []


def get_pledge_detail(symbol: str) -> list[dict]:
    """股权质押明细"""
    for suffix in (f"{symbol}.parquet",):
        rows = _read_parquet(STOCK_ROOT / "pledge_detail" / suffix)
        if rows:
            return rows
    return []


def get_share_float(symbol: str) -> list[dict]:
    """限售股解禁"""
    for suffix in (f"{symbol}.parquet",):
        rows = _read_parquet(STOCK_ROOT / "share_float" / suffix)
        if rows:
            return rows
    return []


def get_repurchase(symbol: str) -> list[dict]:
    """股票回购"""
    for suffix in (f"{symbol}.parquet",):
        rows = _read_parquet(STOCK_ROOT / "repurchase" / suffix)
        if rows:
            return rows
    return []


def get_risk_info_from_search(stock_name: str, stock_code: str) -> dict:
    """获取风险搜索信息"""
    from data.risk_search import get_risk_info
    return get_risk_info(stock_name, stock_code)
