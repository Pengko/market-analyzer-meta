"""
Data Layer — 交易日定位 + 数据 Slice 构建。

Phase 0: resolve_trade_date() — 定位最新交易日
Phase 1: slice_all() — 并行构建所有 DataSlice → 分发给 Agent
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as _pd

# 确保 scripts 目录在 Python 路径中
_scripts_dir = str(Path(__file__).parent.parent)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# ── 路径 ──────────────────────────────────────────────
STOCK_ROOT   = Path.home() / "quant-data" / "tushare" / "股票数据"
INDEX_ROOT   = Path.home() / "quant-data" / "tushare" / "指数数据"
THEME_ROOT   = Path.home() / "quant-data" / "tushare" / "股票数据" / "theme_data"
TRADE_CAL    = STOCK_ROOT / "trade_cal" / "trade_cal_all.csv"
DC_INDEX_CACHE = Path("/tmp/stock_deep_dc_index.json")


# ── Data Slices ───────────────────────────────────────

@dataclass
class MarketSlice:
    """个股日线 + 指数日线"""
    symbol: str
    trade_date: str
    # 个股
    daily_latest: dict[str, float] = field(default_factory=dict)
    daily_pcts: list[float] = field(default_factory=list)
    daily_basic: dict[str, float] = field(default_factory=dict)
    # 指数
    index_daily: dict[str, dict] = field(default_factory=dict)

@dataclass
class ConceptSlice:
    """概念→对标股数据"""
    names: list[str] = field(default_factory=list)
    stock_to_bk: dict[str, list[str]] = field(default_factory=dict)
    bk_to_name: dict[str, str] = field(default_factory=dict)
    bk_to_stocks: dict[str, list[str]] = field(default_factory=dict)
    sector_daily: dict[str, list[float]] = field(default_factory=dict)

@dataclass
class FinancialSlice:
    """财务数据"""
    has_financials: bool = False
    express: list[dict] = field(default_factory=list)
    income: list[dict] = field(default_factory=list)
    balancesheet: list[dict] = field(default_factory=list)
    cashflow: list[dict] = field(default_factory=list)
    mainbz: list[dict] = field(default_factory=list)
    holders: list[dict] = field(default_factory=list)

@dataclass
class MinuteSlice:
    """分钟数据"""
    stock: list[dict] = field(default_factory=list)
    indexes: dict[str, list[dict]] = field(default_factory=dict)
    sector: list[dict] = field(default_factory=list)
    sector_code: str = ""


# ── Phase 0: 交易日定位 ──────────────────────────────

def resolve_trade_date() -> str:
    """确定当前交易日。本地 trade_cal → tushare → 降级。
    9:30 前用 t-1。返回 YYYYMMDD 格式。"""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    today_str = now.strftime("%Y%m%d")

    # 9:30 前用昨天
    if now.time() < dtime(9, 30):
        yesterday = now.replace(hour=0, minute=0) - __import__('datetime').timedelta(days=1)
        today_str = yesterday.strftime("%Y%m%d")

    # 先查本地 trade_cal
    try:
        df = _pd.read_csv(TRADE_CAL)
        mask = (df["cal_date"] == int(today_str)) & (df["exchange"] == "SSE") & (df["is_open"] == 1)
        if not mask.any():
            # 非交易日 → 向前找最近交易日
            df_sse = df[(df["exchange"] == "SSE") & (df["is_open"] == 1)]
            before = df_sse[df_sse["cal_date"] < int(today_str)]
            if before.empty:
                return today_str
            return str(before["cal_date"].max())
        return today_str
    except Exception:
        pass

    # 本地没有 → tushare
    try:
        from data.tushare_client import pro
        cal = pro.trade_cal(exchange="SSE", start_date="20200101", end_date=today_str)
        open_days = cal[cal["is_open"] == 1]["cal_date"].astype(int).tolist()
        # 找最近的交易日
        for d in sorted(open_days, reverse=True):
            if d <= int(today_str):
                return str(d)
    except Exception:
        pass

    return today_str


# ── Phase 1: Slice 构建 ────────────────────────────────

def _safe_float(v: Any) -> float | None:
    if v is None: return None
    try: return float(v)
    except (ValueError, TypeError): return None


def _read_parquet(path: Path) -> list[dict] | None:
    if not path.exists(): return None
    try:
        df = _pd.read_parquet(path)
        return df.sort_values("trade_date").to_dict("records") if "trade_date" in df.columns else df.to_dict("records")
    except Exception:
        return None


def _fetch_daily_tushare(symbol: str, trade_date: str) -> list[dict]:
    """Tushare 补缺日线数据"""
    try:
        from data.tushare_client import pro
        import tushare as ts
        df = ts.pro_bar(api=pro, ts_code=symbol, start_date=trade_date, end_date=trade_date)
        if df is not None and len(df) > 0:
            return df.to_dict("records")
    except Exception:
        pass
    return []


def _build_market_slice(symbol: str, trade_date: str) -> MarketSlice:
    """构建个股日线 + 指数日线"""
    td = trade_date
    result = MarketSlice(symbol=symbol, trade_date=td)

    # 个股 daily
    rows = _read_parquet(STOCK_ROOT / "daily" / f"{symbol}.parquet")
    if rows:
        td_rows = [r for r in rows if str(r["trade_date"]) in (td, int(td))]
    else:
        td_rows = []
    
    # 本地缺失 → tushare 补
    if not td_rows:
        td_rows = _fetch_daily_tushare(symbol, td)
    
    if td_rows:
        latest = td_rows[-1]
        result.daily_latest = {
            "open": _safe_float(latest.get("open")),
            "close": _safe_float(latest.get("close")),
            "high": _safe_float(latest.get("high")),
            "low": _safe_float(latest.get("low")),
            "pct_chg": _safe_float(latest.get("pct_chg")),
            "amount": _safe_float(latest.get("amount")),
            "vol": _safe_float(latest.get("vol")),
        }
    result.daily_pcts = [_safe_float(r.get("pct_chg")) or 0.0 for r in rows[-20:]] if rows else []

    # daily_basic
    basic_rows = _read_parquet(STOCK_ROOT / "daily_basic" / f"{symbol}.parquet")
    if basic_rows:
        bs = [r for r in basic_rows if str(r["trade_date"]) in (td, int(td))]
        if bs:
            b = bs[-1]
            result.daily_basic = {
                "pe_ttm": _safe_float(b.get("pe_ttm")),
                "pb": _safe_float(b.get("pb")),
                "ps_ttm": _safe_float(b.get("ps_ttm")),
                "total_mv": _safe_float(b.get("total_mv")),
                "turnover_rate": _safe_float(b.get("turnover_rate")),
                "dv_ttm": _safe_float(b.get("dv_ttm")),
                "float_share": _safe_float(b.get("float_share")),
                "total_share": _safe_float(b.get("total_share")),
            }

    # 指数 (sh000001)
    for idx_name, idx_code in [("sh000001", "sh000001"), ("sz399001", "sz399001")]:
        i_rows = _read_parquet(INDEX_ROOT / "index_daily" / f"{idx_code}.parquet")
        if i_rows:
            idx_td = [r for r in i_rows if str(r.get("trade_date", "")) in (td, int(td))]
            if idx_td:
                ir = idx_td[-1]
                result.index_daily[idx_name] = {
                    "pct_chg": _safe_float(ir.get("pct_chg")),
                    "close": _safe_float(ir.get("close")),
                    "amount": _safe_float(ir.get("amount")),
            }
            result.index_daily[f"{idx_name}_pcts"] = [_safe_float(r.get("pct_chg")) or 0.0 for r in i_rows[-20:]]

    return result


def _build_concept_slice(symbol: str, trade_date: str) -> ConceptSlice:
    """构建概念→对标股数据"""
    result = ConceptSlice()

    # DC 索引缓存
    if DC_INDEX_CACHE.exists():
        try:
            idx = json.loads(DC_INDEX_CACHE.read_text())
            result.stock_to_bk = idx.get("s2c", {})
            result.bk_to_name = idx.get("c2n", {})
            result.bk_to_stocks = idx.get("c2s", {})
        except Exception:
            pass

    if not result.bk_to_stocks:
        return result

    # 获取个股概念
    my_codes = result.stock_to_bk.get(symbol, [])
    if not my_codes and "." in symbol:
        my_codes = result.stock_to_bk.get(symbol.split(".")[0], [])

    NOISE = ("昨日", "HS300_", "融资融券", "深股通", "沪股通", "AB股",
             "大盘", "周期股", "近期新", "百日新", "最近多", "行业龙头",
             "东方财富", "央国企", "破发", "破增", "富时罗素", "标准普尔",
             "MSCI", "深证10", "深成50", "深证30")
    for bk in my_codes:
        name = result.bk_to_name.get(bk)
        if not name: continue
        if any(name.startswith(p) for p in NOISE): continue
        if len(result.bk_to_stocks.get(bk, [])) < 5: continue
        result.names.append(name)

    # dc_daily 概念指数行情
    for cn in result.names:
        bk = {v: k for k, v in result.bk_to_name.items()}.get(cn)
        if not bk: continue
        dp = THEME_ROOT / "dc_daily" / f"{bk}.parquet"
        rows = _read_parquet(dp)
        if not rows: continue
        result.sector_daily[cn] = [_safe_float(r.get("pct_change")) or 0.0 for r in rows[-20:]]

    return result


def _build_minute_slice(symbol: str, trade_date: str, 
                         top_theme: str | None = None,
                         concept: ConceptSlice | None = None) -> MinuteSlice:
    """构建分钟数据"""
    result = MinuteSlice()
    td = trade_date
    y, m, d = td[:4], td[4:6], td[6:8]

    # 个股分钟
    for pattern in [
        STOCK_ROOT / "分钟数据" / y / m / d / symbol.replace(".SZ", "").replace(".SH", "") / "1min.csv",
        STOCK_ROOT / "分钟数据" / y / m / d / f"{symbol.replace('.SZ','').replace('.SH','')}_1m.csv",
    ]:
        if pattern.exists():
            rows = _read_csv_minute(pattern, td)
            if rows:
                result.stock = rows
                break

    # 大盘分钟 (Tencent API)
    try:
        from runtime.runtime_fetch import fetch_index_minutes
        for code in ["sh000001", "sz399001"]:
            data = fetch_index_minutes(code, f"{td[:4]}-{td[4:6]}-{td[6:8]}")
            if data:
                result.indexes[code] = [
                    {"dt": r["dt"], "close": r.get("close", r.get("price", 0))}
                    for r in data
                ]
    except Exception:
        pass

    # 板块分钟
    if concept and concept.names and top_theme:
        try:
            from runtime.runtime_fetch import resolve_sector_code, fetch_sector_minutes
            scode = resolve_sector_code(top_theme)
            if scode:
                data = fetch_sector_minutes(scode, f"{td[:4]}-{td[4:6]}-{td[6:8]}")
                if data:
                    result.sector = [
                        {"dt": r["dt"], "close": r.get("close", r.get("price", 0))}
                        for r in data
                    ]
                    result.sector_code = scode
        except Exception:
            pass

    return result


def _read_csv_minute(path: Path, trade_date: str) -> list[dict]:
    """读取分钟 CSV"""
    import csv as _csv
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for raw in _csv.DictReader(f):
            dt_str = raw.get("trade_time", raw.get("datetime", ""))
            tp = dt_str.split()[-1] if " " in dt_str else dt_str
            if tp.count(":") == 2:
                tp = ":".join(tp.split(":")[:2])
            rows.append({
                "dt": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]} {tp}",
                "open": float(raw.get("open", 0)),
                "close": float(raw.get("close", 0)),
                "high": float(raw.get("high", 0)),
                "low": float(raw.get("low", 0)),
                "volume": float(raw.get("vol", raw.get("volume", 0))),
                "amount": float(raw.get("amount", 0)),
            })
    return rows


def _build_financial_slice(symbol: str) -> FinancialSlice:
    """构建财务数据"""
    from data.fundamental_provider import (
        get_fundamental_express, get_fundamental_income,
        get_fundamental_balancesheet, get_fundamental_cashflow,
        get_fundamental_mainbz, get_top10_holders,
    )
    result = FinancialSlice()
    result.express = get_fundamental_express(symbol)
    result.has_financials = bool(result.express)
    if result.has_financials:
        result.income = get_fundamental_income(symbol)
        result.balancesheet = get_fundamental_balancesheet(symbol)
        result.cashflow = get_fundamental_cashflow(symbol)
        result.mainbz = get_fundamental_mainbz(symbol)
        result.holders = get_top10_holders(symbol)
    return result


# ── 统一入口 ──────────────────────────────────────────

def slice_all(symbol: str, trade_date: str | None = None,
              top_theme: str | None = None) -> dict[str, Any]:
    """构建所有 DataSlice。
    返回: {"market": MarketSlice, "concept": ConceptSlice, ...}
    """
    td = trade_date or resolve_trade_date()

    concept = _build_concept_slice(symbol, td)

    return {
        "trade_date": td,
        "symbol": symbol,
        "market": _build_market_slice(symbol, td),
        "concept": concept,
        "financial": _build_financial_slice(symbol),
        "minute": _build_minute_slice(symbol, td, top_theme, concept),
    }
