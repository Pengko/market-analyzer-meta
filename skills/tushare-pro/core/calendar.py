#!/usr/bin/env python3
"""
主要作用:
- 提供交易日历获取、时间同步检查、交易日范围计算
- 供 `update_daily.py` 和 `auto_fill_data.py` 统一复用
"""

from datetime import datetime, timedelta
from pathlib import Path
from email.utils import parsedate_to_datetime

import pandas as pd
import urllib.request

from .logging_utils import log
from utils.paths import get_stock_data_dir


_trade_calendar_cache = None
_trade_calendar_cache_date = None
_LOCAL_TRADE_CAL_FILE = get_stock_data_dir() / "trade_cal" / "trade_cal_all.csv"


def get_network_time():
    """Fetch current time from an HTTP Date header."""
    try:
        request = urllib.request.Request("http://www.baidu.com", method="HEAD")
        request.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(request, timeout=5) as response:
            date_header = response.headers.get("Date")
            if not date_header:
                return None
            # HTTP Date is RFC 7231 GMT; keep tz-aware value to avoid local-time drift miscalculation.
            return parsedate_to_datetime(date_header)
    except Exception as exc:
        log(f"HTTP时间获取失败: {exc}", "DEBUG")
        return None


def verify_time_sync():
    """Check local time drift against network time."""
    log("正在验证时间同步...", "INFO")
    local_time = datetime.now().astimezone()
    network_time = get_network_time()
    if network_time is None:
        log("无法获取网络时间，使用本地时间", "WARNING")
        return True
    if network_time.tzinfo is None:
        network_time = network_time.replace(tzinfo=local_time.tzinfo)
    else:
        network_time = network_time.astimezone(local_time.tzinfo)

    diff_seconds = abs((local_time - network_time).total_seconds())
    diff_minutes = diff_seconds / 60
    log(f"本地时间: {local_time.strftime('%Y-%m-%d %H:%M:%S')}", "INFO")
    log(f"网络时间: {network_time.strftime('%Y-%m-%d %H:%M:%S')}", "INFO")
    log(f"时间差: {diff_minutes:.1f} 分钟", "INFO")
    if diff_seconds > 300:
        log(f"本地时间与网络时间偏差较大 ({diff_minutes:.1f} 分钟)", "WARNING")
        return False
    log("时间同步正常", "SUCCESS")
    return True


def _load_local_trade_calendar():
    """Load the persisted local trade calendar if available."""
    if not _LOCAL_TRADE_CAL_FILE.exists():
        return None
    try:
        df = pd.read_csv(
            _LOCAL_TRADE_CAL_FILE,
            usecols=["cal_date", "is_open"],
            low_memory=False,
        )
        if df.empty:
            return None
        df["cal_date"] = df["cal_date"].astype(str)
        return df
    except Exception as exc:
        log(f"读取本地交易日历失败: {exc}", "WARNING")
        return None


def _local_calendar_covers_today(df):
    """Check whether the local calendar is fresh enough for today's date."""
    if df is None or df.empty:
        return False
    today = datetime.now().strftime("%Y%m%d")
    return str(df["cal_date"].max()) >= today


def _extract_trade_dates(df, start_date=None, end_date=None):
    """Extract sorted open dates from a trade calendar frame."""
    if df is None or df.empty:
        return []
    working = df[df["is_open"].astype(str) == "1"].copy()
    working["cal_date"] = working["cal_date"].astype(str)
    if start_date is not None:
        working = working[working["cal_date"] >= str(start_date)]
    if end_date is not None:
        working = working[working["cal_date"] <= str(end_date)]
    return sorted(working["cal_date"].drop_duplicates().tolist())


def _refresh_trade_calendar_file(pro, start_date="20200101", end_date="20301231"):
    """Fetch a fresh trade calendar from remote and persist it locally."""
    frames = []
    for exchange in ("SSE", "SZSE"):
        df = pro.trade_cal(exchange=exchange, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            frames.append(df)
    if not frames:
        return None

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["exchange", "cal_date"], keep="last")
    combined["cal_date"] = combined["cal_date"].astype(str)
    output_dir = _LOCAL_TRADE_CAL_FILE.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    combined.to_csv(_LOCAL_TRADE_CAL_FILE, index=False)
    return combined


def get_trade_calendar(pro, force_refresh=False, lookback_days=365):
    """Return recent open trade dates with a one-day cache."""
    global _trade_calendar_cache, _trade_calendar_cache_date

    today = datetime.now().strftime("%Y%m%d")
    if not force_refresh and _trade_calendar_cache is not None:
        if _trade_calendar_cache_date == today:
            log("使用缓存的交易日历", "DEBUG")
            return _trade_calendar_cache

    local_df = None if force_refresh else _load_local_trade_calendar()
    if local_df is not None and _local_calendar_covers_today(local_df):
        trade_dates = _extract_trade_dates(
            local_df,
            start_date=(datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d"),
            end_date=today,
        )
        _trade_calendar_cache = trade_dates
        _trade_calendar_cache_date = today
        log("使用本地交易日历", "INFO")
        return trade_dates

    log("本地交易日历不可用或已过期，正在拉取新的交易日历...", "INFO")
    try:
        df = _refresh_trade_calendar_file(pro)
        if df is None or df.empty:
            log("交易日历返回空数据", "WARNING")
            return []
        start_date = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y%m%d")
        trade_dates = _extract_trade_dates(df, start_date=start_date, end_date=today)
        _trade_calendar_cache = trade_dates
        _trade_calendar_cache_date = today
        log(
            f"获取到 {len(trade_dates)} 个交易日 ({trade_dates[0]} 至 {trade_dates[-1]})",
            "SUCCESS",
        )
        return trade_dates
    except Exception as exc:
        log(f"获取交易日历失败: {exc}", "ERROR")
        return []


def get_trade_dates(pro, start_date=None, end_date=None):
    """Return trade dates within a range."""
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

    local_df = _load_local_trade_calendar()
    if local_df is not None and _local_calendar_covers_today(local_df):
        return _extract_trade_dates(local_df, start_date=start_date, end_date=end_date)

    log("本地交易日历不可用或已过期，正在拉取新的交易日历...", "INFO")
    try:
        df = _refresh_trade_calendar_file(pro)
        if df is None or df.empty:
            log("交易日历返回空数据", "WARNING")
            return []
        return _extract_trade_dates(df, start_date=start_date, end_date=end_date)
    except Exception as exc:
        log(f"获取交易日历失败: {exc}", "ERROR")
        return []
