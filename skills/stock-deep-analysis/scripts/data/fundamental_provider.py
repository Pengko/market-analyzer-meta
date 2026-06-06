"""
财务数据 parquet 读取层。

数据源: ~/quant-data/tushare/财务数据/ 和 股票数据/top10_*
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
