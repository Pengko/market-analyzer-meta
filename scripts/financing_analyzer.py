#!/usr/bin/env python3
"""
融资融券分析 + 基本面构建 + 股票代码解析工具集。

从 build_stock_report.py 提取：
- safe_float: 通用浮点转换
- analyze_financing_context: 融资融券四层判定
- build_fundamental: daily_basic 基本面数据
- resolve_symbol: 中文名称→股票代码解析
"""

from __future__ import annotations

import re
import sys
from typing import Any

from data.data_access import (
    _read_single_parquet,
    _read_stock_parquet,
    load_browser_margin_signal as load_browser_margin_signal_impl,
    load_daily_basic_row as load_daily_basic_row_impl,
    load_margin_rows as load_margin_rows_impl,
)


def safe_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def load_browser_margin_signal(full_symbol: str) -> dict[str, Any]:
    return load_browser_margin_signal_impl(full_symbol)


def analyze_financing_context(full_symbol: str, trade_date_text: str) -> dict[str, Any]:
    trade_date_compact = trade_date_text.replace("-", "")
    # parquet only
    margin_detail_rows = _read_stock_parquet("margin_detail", full_symbol)

    browser_signal = load_browser_margin_signal(full_symbol)
    browser_eligibility = str(browser_signal.get("eligibility") or "unknown")

    md_latest: str | None = None
    if margin_detail_rows:
        md_dates = sorted(
            {
                d
                for d in (str(row.get("trade_date") or "").strip() for row in margin_detail_rows)
                if len(d) == 8 and d.isdigit() and d <= trade_date_compact
            }
        )
        if md_dates:
            md_latest = md_dates[-1]

    if md_latest:
        return {
            "status": "available",
            "is_margin_stock": True,
            "label": "融资标的",
            "summary": f"检测到融资融券明细，最新交易日 {md_latest}",
            "latest_margin_detail_trade_date": md_latest,
            "browser_eligibility": browser_eligibility,
            "browser_signal": browser_signal,
            "assumption": None,
        }

    margin_latest: str | None = None
    margin_rows = load_margin_rows_impl(full_symbol)
    if margin_rows:
        margin_dates = sorted(
            {
                d
                for d in (str(row.get("trade_date") or "").strip() for row in margin_rows)
                if len(d) == 8 and d.isdigit() and d <= trade_date_compact
            }
        )
        if margin_dates:
            margin_latest = margin_dates[-1]

    if browser_eligibility == "non_margin":
        note = "浏览器识别非融资标的，且 margin_detail 无数据，双重验证判定为非融资股"
        if margin_latest:
            note += f"（margin 汇总最新 {margin_latest}）"
        return {
            "status": "verified_non_margin",
            "is_margin_stock": False,
            "label": "非融资股（双重验证）",
            "summary": note,
            "latest_margin_detail_trade_date": None,
            "latest_margin_trade_date": margin_latest,
            "browser_eligibility": browser_eligibility,
            "browser_signal": browser_signal,
            "assumption": "browser_non_margin_and_no_margin_detail",
        }

    if margin_latest:
        note = "margin 汇总存在历史记录，但 margin_detail 为空，暂不能直接确认为融资股或非融资股"
        if browser_eligibility == "margin":
            note += "（浏览器侧显示疑似可融资）"
        elif browser_eligibility == "unknown":
            note += "（浏览器侧未给出明确结论）"
        note += f"（margin 汇总最新 {margin_latest}）"
        return {
            "status": "likely_margin",
            "is_margin_stock": None,
            "label": "疑似融资股",
            "summary": note,
            "latest_margin_detail_trade_date": None,
            "latest_margin_trade_date": margin_latest,
            "browser_eligibility": browser_eligibility,
            "browser_signal": browser_signal,
            "assumption": "margin_history_without_detail",
        }

    note = "未检测到 margin_detail，且当前缺少足够证据确认是否为融资股"
    if browser_eligibility == "margin":
        note += "（浏览器侧显示疑似可融资）"
    elif browser_eligibility == "unknown":
        note += "（浏览器侧未给出明确可融资结论）"
    return {
        "status": "unknown",
        "is_margin_stock": None,
        "label": "未知待确认",
        "summary": note,
        "latest_margin_detail_trade_date": None,
        "latest_margin_trade_date": None,
        "browser_eligibility": browser_eligibility,
        "browser_signal": browser_signal,
        "assumption": "insufficient_evidence",
    }


def build_fundamental(full_symbol: str, trade_date_compact: str) -> dict[str, Any]:
    row = load_daily_basic_row_impl(full_symbol, trade_date_compact)
    if not row:
        return {"status": "missing", "reason": "daily_basic 本地数据缺失"}
    return {
        "status": "available",
        "pe": safe_float(row.get("pe")),
        "pe_ttm": safe_float(row.get("pe_ttm")),
        "pb": safe_float(row.get("pb")),
        "total_mv": safe_float(row.get("total_mv")),
        "circ_mv": safe_float(row.get("circ_mv")),
    }


def resolve_symbol(symbol: str) -> str:
    """若传入中文股票名称，查 stock_basic_all.parquet 解析为 ts_code。

    若传入的已经是代码（纯数字或数字+后缀），直接原样返回。
    """
    raw = symbol.strip()
    # 如果是纯数字(6位)或数字+后缀，直接返回
    if re.match(r'^\d{6}(\.(SH|SZ))?$', raw, re.IGNORECASE):
        return raw
    # 如果是中文名称，查 stock_basic parquet
    try:
        rows = _read_single_parquet("stock_basic", "stock_basic_all.parquet")
        for row in rows:
            if row.get("name", "").strip() == raw:
                resolved = row["ts_code"].strip()
                print(
                    f"[financing_analyzer] 名称解析: '{raw}' → {resolved}",
                    file=sys.stderr,
                    flush=True,
                )
                return resolved
        # 模糊匹配：包含
        for row in rows:
            name = row.get("name", "").strip()
            if raw in name:
                resolved = row["ts_code"].strip()
                print(
                    f"[financing_analyzer] 名称解析(模糊): '{raw}' → {resolved}({name})",
                    file=sys.stderr,
                    flush=True,
                )
                return resolved
        print(
            f"[financing_analyzer] 警告: 未找到名称 '{raw}'，按原样使用",
            file=sys.stderr,
            flush=True,
        )
    except Exception as e:
        print(
            f"[financing_analyzer] 警告: 查询名称 '{raw}' 失败: {e}，按原样使用",
            file=sys.stderr,
            flush=True,
        )
    return raw
