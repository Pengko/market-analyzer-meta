#!/usr/bin/env python3
"""
主要作用:
- 提供按接口逐步探测的只读执行层
- 复用注册表定义，以接近真实更新的参数发起请求
- 输出终端摘要和成功结果 Markdown 报告
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

import pandas as pd

from .logging_utils import log
from .registry import GROUPS, INTERFACE_CONFIG
from utils.tushare_client import classify_api_error, diagnose_api_connection


DEFAULT_GROUP_PRIORITY = {
    "core": 0,
    "index": 1,
    "limit": 2,
    "block": 3,
    "shock": 4,
    "auction": 5,
    "theme": 6,
    "factor": 7,
    "margin": 8,
    "financial": 9,
}

PAGINATED_APIS = {
    "daily",
    "daily_basic",
    "moneyflow",
    "stk_factor_pro",
    "cyq_perf",
    "margin_detail",
    "dc_concept",
    "kpl_concept_cons",
    "dc_concept_cons",
}

MAIN_INDEX_CODES = [
    "000001.SH",
    "000002.SH",
    "000003.SH",
    "000004.SH",
    "000016.SH",
    "000300.SH",
    "000688.SH",
    "399001.SZ",
    "399002.SZ",
    "399006.SZ",
    "399673.SZ",
    "399005.SZ",
    "000010.SH",
    "000009.SH",
]

INDEX_MEMBER_CODES = ["000001.SH", "000300.SH", "000016.SH"]
INDEX_BASIC_MARKETS = ["SSE", "SZSE", "BSE", "CSI"]


def add_probe_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Attach probe-related CLI switches to an existing parser."""
    parser.add_argument("--probe", action="store_true", help="只读模式：逐步探测接口请求情况")
    parser.add_argument("--probe-date", help="探测日期 (YYYYMMDD)，覆盖默认稳定出数日")
    parser.add_argument("--sample-size", type=int, default=20, help="代码型接口样本数量，默认 20")
    parser.add_argument("--probe-report", help="探测成功报告输出路径")
    return parser


def resolve_stable_trade_date(trade_calendar: List[str], lag_days: int = 1) -> Optional[str]:
    """Use the previous trade day by default to avoid late-publishing interfaces."""
    if not trade_calendar:
        return None
    lag_days = max(0, int(lag_days))
    if lag_days >= len(trade_calendar):
        lag_days = len(trade_calendar) - 1
    return trade_calendar[-1 - lag_days]


def resolve_recent_period_end(
    trade_calendar: List[str], granularity: str, anchor_date: Optional[str] = None
) -> Optional[str]:
    """Return the latest completed weekly/monthly period end on or before the anchor."""
    if not trade_calendar:
        return None

    anchor_date = anchor_date or trade_calendar[-1]
    series = [datetime.strptime(str(d), "%Y%m%d") for d in trade_calendar]
    groups: List[tuple[str, str]] = []

    for trade_date, dt in zip(trade_calendar, series):
        if granularity == "weekly":
            group_key = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
        elif granularity == "monthly":
            group_key = dt.strftime("%Y-%m")
        else:
            return anchor_date
        groups.append((group_key, trade_date))

    end_dates: Dict[str, str] = {}
    for group_key, trade_date in groups:
        end_dates[group_key] = trade_date

    ordered_groups = []
    seen = set()
    for group_key, _ in groups:
        if group_key not in seen:
            seen.add(group_key)
            ordered_groups.append(group_key)

    anchor_group = None
    for group_key, trade_date in groups:
        if trade_date == anchor_date:
            anchor_group = group_key
            break
    if anchor_group is None:
        anchor_group = ordered_groups[-1]

    anchor_index = ordered_groups.index(anchor_group)
    anchor_end = end_dates[anchor_group]
    anchor_dt = datetime.strptime(anchor_date, "%Y%m%d")
    next_day = anchor_dt + timedelta(days=1)
    if granularity == "weekly":
        period_finished = next_day.isocalendar().week != anchor_dt.isocalendar().week or next_day.isocalendar().year != anchor_dt.isocalendar().year
    else:
        period_finished = next_day.month != anchor_dt.month or next_day.year != anchor_dt.year

    if anchor_date == anchor_end and period_finished:
        return anchor_end
    if anchor_index == 0:
        return anchor_end
    return end_dates[ordered_groups[anchor_index - 1]]


def choose_probe_trade_date(
    trade_calendar: List[str], config: dict, probe_date: Optional[str] = None
) -> Optional[str]:
    """Choose the default probe date for a given interface."""
    if probe_date:
        return probe_date

    stable_trade_date = resolve_stable_trade_date(trade_calendar, lag_days=1)
    granularity = config.get("fetch_granularity", "daily")
    if granularity in {"weekly", "monthly"}:
        return resolve_recent_period_end(trade_calendar, granularity, anchor_date=stable_trade_date)
    return stable_trade_date


def select_probe_interfaces(
    requested_interfaces: Optional[Iterable[str]] = None,
    group: Optional[str] = None,
    interface_config: Optional[dict] = None,
    groups: Optional[dict] = None,
) -> List[str]:
    """Resolve target interfaces while preserving the repo's existing ordering."""
    interface_config = interface_config or INTERFACE_CONFIG
    groups = groups or GROUPS

    if group:
        return list(groups.get(group, []))
    if requested_interfaces:
        return [name for name in requested_interfaces if name in interface_config]
    return sorted(
        interface_config.keys(),
        key=lambda name: DEFAULT_GROUP_PRIORITY.get(interface_config[name].get("group"), 99),
    )


def get_sample_stock_codes(pro, sample_size: int = 20) -> List[str]:
    """Reuse the real stock pool logic, but keep only a small front slice."""
    stocks = pro.stock_basic(exchange="", list_status="L")
    if stocks is None or stocks.empty:
        return []
    non_st_stocks = stocks[~stocks["name"].str.contains("ST", na=False)]
    return non_st_stocks["ts_code"].tolist()[: max(1, int(sample_size))]


def build_default_report_path(base_dir: Optional[Path] = None) -> Path:
    """Return the default markdown report path."""
    base_dir = base_dir or Path(__file__).resolve().parent.parent / "logs" / "probe_reports"
    base_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base_dir / f"{stamp}.md"


def write_probe_report(results: List[dict], report_path: Path) -> Path:
    """Persist only successful probe results to markdown."""
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    successes = [result for result in results if result["status"] == "success"]

    lines = [
        "# 接口探测成功报告",
        "",
        f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 成功接口数: {len(successes)}",
        "",
        "---",
        "",
    ]

    for result in successes:
        lines.extend(
            [
                f"## {result['interface']}",
                "",
                f"- 分组: {result['group']}",
                f"- 请求方式: {result['request_mode']}",
                f"- 参数: {result['params_summary']}",
                f"- 返回条数: {result['row_count']}",
                f"- 耗时(ms): {result['elapsed_ms']}",
                "",
            ]
        )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def _summarize_params(params: dict) -> str:
    parts = [f"{key}={value}" for key, value in params.items()]
    return ", ".join(parts)


def _empty_result(interface_name: str, config: dict, request_mode: str, params: dict, elapsed_ms: int) -> dict:
    return {
        "interface": interface_name,
        "group": config.get("group", "unknown"),
        "status": "empty",
        "request_mode": request_mode,
        "params_summary": _summarize_params(params),
        "row_count": 0,
        "elapsed_ms": elapsed_ms,
        "error_category": "",
        "error_message": "",
    }


def _success_result(
    interface_name: str,
    config: dict,
    request_mode: str,
    params: dict,
    row_count: int,
    elapsed_ms: int,
) -> dict:
    return {
        "interface": interface_name,
        "group": config.get("group", "unknown"),
        "status": "success",
        "request_mode": request_mode,
        "params_summary": _summarize_params(params),
        "row_count": int(row_count),
        "elapsed_ms": elapsed_ms,
        "error_category": "",
        "error_message": "",
    }


def _error_result(
    interface_name: str,
    config: dict,
    request_mode: str,
    params: dict,
    elapsed_ms: int,
    exc: Exception,
) -> dict:
    category, message = classify_api_error(exc)
    text = str(exc).strip()
    lowered = text.lower()
    if "上限" in text or "频繁" in text or "limit" in lowered:
        category = "rate_limit"
        message = text or "接口触发限流"
    return {
        "interface": interface_name,
        "group": config.get("group", "unknown"),
        "status": "error",
        "request_mode": request_mode,
        "params_summary": _summarize_params(params),
        "row_count": 0,
        "elapsed_ms": elapsed_ms,
        "error_category": category,
        "error_message": message,
    }


def _call_paged_by_date(
    api_func,
    trade_date: str,
    page_limit: int = 5000,
    max_pages: int = 2,
    offsets: Optional[List[int]] = None,
) -> pd.DataFrame:
    if offsets is None:
        page_limit = max(1, int(page_limit or 5000))
        max_pages = max(1, int(max_pages or 2))
        offsets = [page_limit * idx for idx in range(max_pages)]
    all_rows = []
    for offset in offsets:
        df_page = api_func(trade_date=trade_date, limit=page_limit, offset=offset)
        if df_page is None or df_page.empty:
            continue
        all_rows.append(df_page)
    if not all_rows:
        return pd.DataFrame()
    return pd.concat(all_rows, ignore_index=True)


def _call_weekly_monthly_like_updater(pro, interface_name: str, ts_code: str, period_end: str):
    """
    Mirror update_weekly_monthly.fetch_api_data behavior:
    first call pro.stk_weekly_monthly, then fallback to pro.weekly/pro.monthly.
    """
    freq = "W" if interface_name == "weekly" else "M"
    primary = pro.stk_weekly_monthly(
        ts_code=ts_code,
        freq=freq,
        start_date=period_end,
        end_date=period_end,
    )
    if primary is not None and not primary.empty:
        return primary, "stk_weekly_monthly"

    fallback_api = getattr(pro, interface_name)
    fallback = fallback_api(ts_code=ts_code, start_date=period_end, end_date=period_end)
    if fallback is not None and not fallback.empty:
        return fallback, interface_name
    return pd.DataFrame(), "empty"


def _probe_by_date_interface(interface_name: str, config: dict, pro, trade_date: str) -> dict:
    api_name = config.get("api", interface_name)
    api_func = getattr(pro, api_name)
    params = {"trade_date": trade_date}
    started = time.perf_counter()
    try:
        if api_name in PAGINATED_APIS:
            page_limit = int(config.get("page_limit", 5000))
            max_pages = int(config.get("max_pages", 2))
            frame = _call_paged_by_date(api_func, trade_date, page_limit=page_limit, max_pages=max_pages)
            params["limit"] = page_limit
            params["max_pages"] = max_pages
            request_mode = "by_date_paged"
        else:
            frame = api_func(trade_date=trade_date)
            request_mode = "by_date"
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if frame is None or frame.empty:
            return _empty_result(interface_name, config, request_mode, params, elapsed_ms)
        return _success_result(interface_name, config, request_mode, params, len(frame), elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _error_result(interface_name, config, request_mode="by_date", params=params, elapsed_ms=elapsed_ms, exc=exc)


def _probe_by_stock_interface(
    interface_name: str,
    config: dict,
    pro,
    trade_date: str,
    sample_codes: List[str],
) -> dict:
    api_name = config.get("api", interface_name)
    api_func = getattr(pro, api_name)
    params = {
        "start_date": trade_date,
        "end_date": trade_date,
        "sample_codes": len(sample_codes),
    }
    started = time.perf_counter()
    try:
        total_rows = 0
        request_mode = "by_stock"
        for code in sample_codes:
            frame = api_func(ts_code=code, start_date=trade_date, end_date=trade_date)
            if frame is not None and not frame.empty:
                total_rows += len(frame)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if total_rows == 0:
            return _empty_result(interface_name, config, request_mode, params, elapsed_ms)
        return _success_result(interface_name, config, request_mode, params, total_rows, elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _error_result(interface_name, config, request_mode="by_stock", params=params, elapsed_ms=elapsed_ms, exc=exc)


def _probe_standalone_interface(
    interface_name: str,
    config: dict,
    pro,
    trade_date: str,
    sample_size: int,
    sample_codes: List[str],
) -> dict:
    started = time.perf_counter()
    params: Dict[str, object] = {}
    try:
        if interface_name in {"daily", "daily_basic", "moneyflow", "stk_factor_pro", "cyq_perf"}:
            page_limit = int(config.get("page_limit", 5000))
            max_pages = int(config.get("max_pages", 2))
            frame = _call_paged_by_date(
                getattr(pro, interface_name),
                trade_date,
                page_limit=page_limit,
                max_pages=max_pages,
            )
            params = {"trade_date": trade_date, "limit": page_limit, "max_pages": max_pages}
            request_mode = "standalone_paged"
            row_count = len(frame)
        elif interface_name in {"dc_concept", "kpl_concept_cons", "dc_concept_cons", "dc_daily", "dc_index"}:
            page_limit = int(config.get("page_limit", 5000))
            max_pages = int(config.get("max_pages", 2))
            frame = _call_paged_by_date(
                getattr(pro, interface_name),
                trade_date,
                page_limit=page_limit,
                max_pages=max_pages,
            )
            params = {"trade_date": trade_date, "limit": page_limit, "max_pages": max_pages}
            request_mode = "standalone_theme_paged"
            row_count = len(frame)
        elif interface_name == "ths_index":
            frame = pro.ths_index()
            params = {}
            request_mode = "standalone_theme_full"
            row_count = len(frame) if frame is not None else 0
        elif interface_name == "ths_member":
            frame = pd.DataFrame()
            params = {}
            request_mode = "standalone_theme_member"
            row_count = 0
        elif interface_name == "ths_daily":
            frame = pro.ths_daily(trade_date=trade_date)
            params = {"trade_date": trade_date}
            request_mode = "standalone_theme_daily"
            row_count = len(frame) if frame is not None else 0
        elif interface_name == "dc_member":
            frame = pd.DataFrame()
            params = {}
            request_mode = "standalone_theme_member"
            row_count = 0
        elif interface_name == "trade_cal":
            frame = pro.trade_cal(exchange="SSE", start_date=trade_date, end_date=trade_date)
            params = {"exchange": "SSE", "start_date": trade_date, "end_date": trade_date}
            request_mode = "standalone_trade_cal"
            row_count = len(frame) if frame is not None else 0
        elif interface_name == "index_basic":
            frames = []
            for market in INDEX_BASIC_MARKETS:
                frame = pro.index_basic(market=market)
                if frame is not None and not frame.empty:
                    frames.append(frame)
            params = {"markets": ",".join(INDEX_BASIC_MARKETS)}
            request_mode = "standalone_index_basic"
            row_count = sum(len(frame) for frame in frames)
            frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        elif interface_name == "index_daily":
            row_count = 0
            for code in MAIN_INDEX_CODES[: max(1, sample_size)]:
                frame = pro.index_daily(ts_code=code, trade_date=trade_date)
                if frame is not None and not frame.empty:
                    row_count += len(frame)
            params = {"trade_date": trade_date, "sample_codes": min(len(MAIN_INDEX_CODES), max(1, sample_size))}
            request_mode = "standalone_index_daily"
            frame = pd.DataFrame(index=range(row_count))
        elif interface_name == "index_weight":
            frame = pro.index_weight(trade_date=trade_date)
            params = {"trade_date": trade_date}
            request_mode = "standalone_index_weight"
            row_count = len(frame) if frame is not None else 0
        elif interface_name == "index_weekly":
            period_end = trade_date
            row_count = 0
            for code in MAIN_INDEX_CODES[: min(4, max(1, sample_size))]:
                frame = pro.index_weekly(ts_code=code, start_date=period_end, end_date=period_end)
                if frame is not None and not frame.empty:
                    row_count += len(frame)
            params = {"period_end": period_end, "sample_codes": min(4, max(1, sample_size))}
            request_mode = "standalone_index_weekly"
            frame = pd.DataFrame(index=range(row_count))
        elif interface_name == "index_monthly":
            period_end = trade_date
            row_count = 0
            for code in MAIN_INDEX_CODES[: min(4, max(1, sample_size))]:
                frame = pro.index_monthly(ts_code=code, start_date=period_end, end_date=period_end)
                if frame is not None and not frame.empty:
                    row_count += len(frame)
            params = {"period_end": period_end, "sample_codes": min(4, max(1, sample_size))}
            request_mode = "standalone_index_monthly"
            frame = pd.DataFrame(index=range(row_count))
        elif interface_name == "index_global":
            frame = pro.index_global()
            params = {"scope": "all"}
            request_mode = "standalone_index_global"
            row_count = len(frame) if frame is not None else 0
        elif interface_name == "index_classify":
            frame = pro.index_classify()
            params = {"scope": "all"}
            request_mode = "standalone_index_classify"
            row_count = len(frame) if frame is not None else 0
        elif interface_name == "index_member":
            row_count = 0
            for code in INDEX_MEMBER_CODES[: min(len(INDEX_MEMBER_CODES), max(1, sample_size))]:
                frame = pro.index_member(index_code=code)
                if frame is not None and not frame.empty:
                    row_count += len(frame)
            params = {"sample_indices": min(len(INDEX_MEMBER_CODES), max(1, sample_size))}
            request_mode = "standalone_index_member"
            frame = pd.DataFrame(index=range(row_count))
        elif interface_name in {"weekly", "monthly"}:
            row_count = 0
            used_methods = set()
            for code in sample_codes:
                frame, used_method = _call_weekly_monthly_like_updater(
                    pro,
                    interface_name=interface_name,
                    ts_code=code,
                    period_end=trade_date,
                )
                used_methods.add(used_method)
                if frame is not None and not frame.empty:
                    row_count += len(frame)
            params = {
                "period_end": trade_date,
                "sample_codes": len(sample_codes),
                "api_shape": "primary+fallback",
                "used": ",".join(sorted(used_methods)),
            }
            request_mode = f"standalone_{interface_name}_api_shape"
            frame = pd.DataFrame({"_probe_rows": list(range(row_count))}) if row_count > 0 else pd.DataFrame()
        else:
            frame = getattr(pro, interface_name)(trade_date=trade_date)
            params = {"trade_date": trade_date}
            request_mode = "standalone_generic"
            row_count = len(frame) if frame is not None else 0

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if frame is None or frame.empty:
            return _empty_result(interface_name, config, request_mode, params, elapsed_ms)
        return _success_result(interface_name, config, request_mode, params, row_count, elapsed_ms)
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return _error_result(interface_name, config, request_mode="standalone", params=params, elapsed_ms=elapsed_ms, exc=exc)


def probe_interface(
    interface_name: str,
    config: dict,
    pro,
    trade_calendar: List[str],
    probe_date: Optional[str] = None,
    sample_size: int = 20,
    sample_codes: Optional[List[str]] = None,
) -> dict:
    """Probe a single interface without writing any business data."""
    effective_probe_date = choose_probe_trade_date(trade_calendar, config, probe_date=probe_date)
    interface_type = config.get("type", "by_date")
    if interface_type in {"by_stock", "standalone"} and sample_codes is None:
        sample_codes = get_sample_stock_codes(pro, sample_size=sample_size)

    if interface_type == "by_date":
        return _probe_by_date_interface(interface_name, config, pro, effective_probe_date)
    if interface_type == "by_stock":
        return _probe_by_stock_interface(interface_name, config, pro, effective_probe_date, sample_codes or [])
    return _probe_standalone_interface(
        interface_name,
        config,
        pro,
        effective_probe_date,
        sample_size=sample_size,
        sample_codes=sample_codes or [],
    )


def _log_group_summary(group_name: str, stats: dict) -> None:
    log(
        f"📦 分组 {group_name}: 成功 {stats['success']} | 空数据 {stats['empty']} | 失败 {stats['error']}",
        "INFO",
    )


def _log_result_line(result: dict) -> None:
    if result["status"] == "success":
        icon = "✅"
        detail = f"{result['row_count']} rows"
        level = "SUCCESS"
    elif result["status"] == "empty":
        icon = "⚪"
        detail = "empty"
        level = "WARNING"
    else:
        icon = "❌"
        detail = f"{result['error_category']}: {result['error_message']}"
        level = "ERROR"
    log(
        f"{icon} {result['interface']:<20} [{result['group']}] {result['request_mode']} | {detail} | {result['elapsed_ms']}ms | {result['params_summary']}",
        level,
    )


def run_probe_suite(
    pro,
    trade_calendar: List[str],
    interface_names: Optional[List[str]] = None,
    group: Optional[str] = None,
    probe_date: Optional[str] = None,
    sample_size: int = 20,
    report_path: Optional[Path] = None,
    interface_config: Optional[dict] = None,
    groups: Optional[dict] = None,
    diagnose_func: Optional[Callable] = None,
    sleep_func: Optional[Callable[[float], None]] = None,
    probe_func: Optional[Callable[..., dict]] = None,
) -> dict:
    """Run the read-only probe workflow and return structured results."""
    interface_config = interface_config or INTERFACE_CONFIG
    groups = groups or GROUPS
    diagnose_func = diagnose_func or diagnose_api_connection
    sleep_func = sleep_func or time.sleep
    probe_func = probe_func or probe_interface

    health = diagnose_func(pro=pro)
    if not health.get("ok"):
        log(f"❌ API 健康检查失败: {health.get('message')}", "ERROR")
        return {"ok": False, "health": health, "results": [], "report_path": None, "summary": {}}

    targets = select_probe_interfaces(
        requested_interfaces=interface_names,
        group=group,
        interface_config=interface_config,
        groups=groups,
    )
    sample_codes = get_sample_stock_codes(pro, sample_size=sample_size)
    report_path = Path(report_path) if report_path else build_default_report_path()

    log(f"🔎 开始接口探测，共 {len(targets)} 个接口")
    results = []
    summary = defaultdict(lambda: {"success": 0, "empty": 0, "error": 0})
    current_group = None

    for interface_name in targets:
        config = interface_config[interface_name]
        group_name = config.get("group", "unknown")
        if current_group is None:
            current_group = group_name
        elif current_group != group_name:
            _log_group_summary(current_group, summary[current_group])
            current_group = group_name

        result = probe_func(
            interface_name,
            config,
            pro,
            trade_calendar,
            probe_date=probe_date,
            sample_size=sample_size,
            sample_codes=sample_codes,
        )
        results.append(result)
        summary[group_name][result["status"]] += 1
        _log_result_line(result)

        if result["status"] == "error" and result["error_category"] == "rate_limit":
            sleep_func(10)
        else:
            sleep_func(0.2)

    if current_group is not None:
        _log_group_summary(current_group, summary[current_group])

    total = {"success": 0, "empty": 0, "error": 0}
    for stats in summary.values():
        for key in total:
            total[key] += stats.get(key, 0)
    failed = [result["interface"] for result in results if result["status"] == "error"]
    log(f"📊 总结: 成功 {total['success']} | 空数据 {total['empty']} | 失败 {total['error']}", "INFO")
    if failed:
        log(f"⚠️ 失败接口: {', '.join(failed)}", "WARNING")

    write_probe_report(results, report_path)
    log(f"📄 成功报告: {report_path}", "INFO")
    return {
        "ok": True,
        "health": health,
        "results": results,
        "report_path": report_path,
        "summary": dict(summary),
        "total": total,
    }
