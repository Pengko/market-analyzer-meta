"""
本地数据路径聚合层 —— 所有本地 parquet 读取都走这里。

职责：
1. 统一管理本地数据路径（daily、daily_basic、index_daily、cyq_perf 等）
2. 提供简单的 get_xxx(symbol, trade_date) 接口
3. 不管 API 补数据，本地没有就返回 None

跟 data_access.py 的区别：
- data_provider：只读本地，简单快速
- data_access.py：本地没有会调 Tushare API 补数据

谁用它：
- 需要快速查本地数据的场景
"""
from typing import Any
import pandas as pd

from common import STOCK_DATA_ROOT, INDEX_DATA_ROOT

_STOCK_ROOT = STOCK_DATA_ROOT
_INDEX_ROOT = INDEX_DATA_ROOT


def _read_one(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(path)
    except Exception:
        return None


def get_daily(symbol: str, trade_date: str) -> dict[str, Any] | None:
    df = _read_one(_STOCK_ROOT / "daily" / f"{symbol}.parquet")
    if df is None:
        return None
    row = df[df["trade_date"] == trade_date]
    return row.iloc[0].to_dict() if not row.empty else None


def get_daily_rows(symbol: str, trade_date: str, limit: int = 10) -> list[dict[str, Any]]:
    df = _read_one(_STOCK_ROOT / "daily" / f"{symbol}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    rows = df[mask].sort_values("trade_date").tail(limit)
    return rows.to_dict("records") if not rows.empty else []


def get_daily_basic(symbol: str, trade_date: str) -> dict[str, Any] | None:
    df = _read_one(_STOCK_ROOT / "daily_basic" / f"{symbol}.parquet")
    if df is None:
        return None
    row = df[df["trade_date"] == trade_date]
    return row.iloc[0].to_dict() if not row.empty else None


def get_index_daily(index_code: str, trade_date: str) -> dict[str, Any] | None:
    df = _read_one(_INDEX_ROOT / "index_daily" / f"{index_code}.parquet")
    if df is None:
        return None
    row = df[df["trade_date"] == trade_date]
    return row.iloc[0].to_dict() if not row.empty else None


def get_index_daily_rows(index_code: str, trade_date: str, limit: int = 30) -> list[dict[str, Any]]:
    df = _read_one(_INDEX_ROOT / "index_daily" / f"{index_code}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    rows = df[mask].sort_values("trade_date").tail(limit)
    return rows.to_dict("records") if not rows.empty else []


def get_chips(symbol: str, trade_date: str) -> list[dict[str, Any]]:
    df = _read_one(_STOCK_ROOT / "cyq_chips" / f"{symbol}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    return df[mask].sort_values("trade_date").tail(10).to_dict("records")


def get_chips_perf(symbol: str, trade_date: str) -> list[dict[str, Any]]:
    df = _read_one(_STOCK_ROOT / "cyq_perf" / f"{symbol}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    return df[mask].sort_values("trade_date").tail(5).to_dict("records")


def get_factors(symbol: str, trade_date: str) -> dict[str, Any] | None:
    df = _read_one(_STOCK_ROOT / "stk_factor_pro" / f"{symbol}.parquet")
    if df is None:
        return None
    mask = df["trade_date"] <= trade_date
    rows = df[mask].sort_values("trade_date")
    if rows.empty:
        return None
    return rows.iloc[-1].to_dict()


def get_weekly(symbol: str, trade_date: str) -> list[dict[str, Any]]:
    df = _read_one(_STOCK_ROOT / "weekly" / f"{symbol}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    return df[mask].sort_values("trade_date").tail(20).to_dict("records")


def get_monthly(symbol: str, trade_date: str) -> list[dict[str, Any]]:
    df = _read_one(_STOCK_ROOT / "monthly" / f"{symbol}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    return df[mask].sort_values("trade_date").tail(12).to_dict("records")


def get_stock_concepts(symbol: str) -> list[str]:
    for year_pq in sorted(_STOCK_ROOT.glob("theme_data/kpl_concept_cons/20*.parquet"), reverse=True):
        df = _read_one(year_pq)
        if df is None or "con_code" not in df.columns:
            continue
        match = df[df["con_code"] == symbol]
        if not match.empty and "name" in match.columns:
            return match["name"].dropna().unique().tolist()
    return []


def get_theme_constituents(symbol: str, trade_date: str) -> list[dict[str, Any]]:
    for year_pq in sorted(_STOCK_ROOT.glob("theme_data/dc_concept_cons/20*.parquet"), reverse=True):
        df = _read_one(year_pq)
        if df is None:
            continue
        col = "ts_code" if "ts_code" in df.columns else "con_code"
        match = df[df[col] == symbol]
        if not match.empty:
            return match.to_dict("records")
    return []


def get_stock_basic(symbol: str) -> dict[str, Any] | None:
    df = _read_one(_STOCK_ROOT / "stock_basic" / f"{symbol}.parquet")
    if df is None:
        import csv
        path = _STOCK_ROOT / "stock_basic" / "stock_basic_all.csv"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("ts_code") == symbol:
                    return row
        return None
    return df.iloc[0].to_dict() if not df.empty else None
