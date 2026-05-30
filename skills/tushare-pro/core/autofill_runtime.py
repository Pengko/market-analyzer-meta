#!/usr/bin/env python3
"""Runtime helpers for auto_fill_data orchestration."""

import json
import multiprocessing
import re
import signal
import socket
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from utils.tushare_client import classify_api_error, diagnose_api_connection
from core.calendar import get_trade_dates as shared_get_trade_dates
from core.files import append_to_csv as shared_append_to_csv
from core.files import deduplicate_file as shared_deduplicate_file
from core.files import fast_merge_to_file as shared_fast_merge_to_file
from core.files import get_latest_date_fast as shared_get_latest_date_fast
from core.files import prune_date_partitioned_history as shared_prune_date_partitioned_history
from core.files import write_multi_format_bundle as shared_write_multi_format_bundle
from core.health import check_interface_by_date as shared_check_interface_by_date
from core.health import get_local_latest_date as shared_get_local_latest_date
from core.health import get_missing_trade_dates as shared_get_missing_trade_dates
from core.health import get_root_dir as shared_get_root_dir
from core.health import is_tolerable_trailing_gap as shared_is_tolerable_trailing_gap
from core.health import scan_incomplete_records as shared_scan_incomplete_records
from core.logging_utils import finish_live_progress as shared_finish_live_progress
from core.logging_utils import live_progress as shared_live_progress
from core.logging_utils import log as shared_log
from core.logging_utils import start_live_spinner as shared_start_live_spinner
from core.logging_utils import stop_live_spinner as shared_stop_live_spinner
from core.logging_utils import update_live_spinner as shared_update_live_spinner
from core.registry import INTERFACE_CONFIG

DATA_DIR = None
INDEX_DIR = None
FINANCIAL_DIR = None
pro = None
LOG_FN = None
THEME_HANDLERS = {}
WHITELIST_PATH = None
DEFAULT_WHITELIST_PATH = Path(__file__).resolve().parent.parent / "logs" / "autofill_interface_whitelist.json"


def initialize_runtime(*, pro_api, data_dir, index_dir, financial_dir, log_fn=None, theme_handlers=None):
    global pro, DATA_DIR, INDEX_DIR, FINANCIAL_DIR, LOG_FN, THEME_HANDLERS, WHITELIST_PATH
    pro = pro_api
    DATA_DIR = data_dir
    INDEX_DIR = index_dir
    FINANCIAL_DIR = financial_dir
    LOG_FN = log_fn
    THEME_HANDLERS = dict(theme_handlers or {})
    WHITELIST_PATH = DEFAULT_WHITELIST_PATH


def get_interface_whitelist_path():
    """Return the effective whitelist file path used by runtime."""
    return WHITELIST_PATH or DEFAULT_WHITELIST_PATH


def log(msg, level="INFO"):
    """打印日志"""
    if LOG_FN is not None:
        LOG_FN(msg, level)
    else:
        shared_log(msg, level)
    sys.stdout.flush()


def live_progress(msg, level="INFO"):
    shared_live_progress(msg, level)
    sys.stdout.flush()


def finish_live_progress(msg=None, level="INFO"):
    shared_finish_live_progress(msg, level)
    sys.stdout.flush()


def start_live_spinner(msg):
    shared_start_live_spinner(msg)
    sys.stdout.flush()


def update_live_spinner(msg):
    shared_update_live_spinner(msg)
    sys.stdout.flush()


def stop_live_spinner(msg=None, level="INFO"):
    shared_stop_live_spinner(msg, level)
    sys.stdout.flush()


def format_duration(seconds):
    total = max(0, int(seconds or 0))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}小时{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def log_interface_banner(interface_name, market_label):
    log(f"\n{'='*60}")
    log(f"🚀 开始补全 {interface_name} ({market_label})")
    log('='*60)


def log_interface_summary(success_count=None, empty_count=None, error_count=None, *, updated=None, no_data=None, errors=None, resumed_skipped=None, duration_seconds=None):
    duration_text = f" | 耗时 {format_duration(duration_seconds)}" if duration_seconds is not None else ""
    if success_count is not None:
        log(
            f"📦 完成汇总: 成功 {success_count} | 空数据 {empty_count} | 错误 {error_count}{duration_text}",
            "INFO",
        )
        return
    log(
        f"📦 完成汇总: 有更新 {updated} | 无数据 {no_data} | 错误 {errors} | 断点跳过 {resumed_skipped}{duration_text}",
        "INFO",
    )


def _is_likely_network_error(exc):
    category, _ = classify_api_error(exc)
    return category in {"dns_error", "connect_error", "timeout"}


def _normalize_date_series(series):
    normalized = series.astype(str).str.strip().str.replace(".0", "", regex=False)
    iso_mask = normalized.str.len().ge(10) & normalized.str.match(r"^\d{4}-\d{2}-\d{2}")
    normalized.loc[iso_mask] = (
        normalized.loc[iso_mask].str.slice(0, 10).str.replace("-", "", regex=False)
    )
    return normalized


def _normalize_frame_date_column(frame, date_col):
    if date_col in frame.columns:
        frame[date_col] = _normalize_date_series(frame[date_col])
    return frame


def get_stock_code_list(config=None):
    """Return the default stock pool for by-code auto-fill tasks.

    Stock interfaces intentionally use the locally maintained non-ST pool by
    default.  A config must opt in with include_st_codes=True to request the
    full listed stock pool.
    """
    config = config or {}
    if config.get("include_st_codes") is True:
        stock_basic = pro.stock_basic(exchange="", list_status="L", fields="ts_code")
        code_list = stock_basic["ts_code"].tolist()
        log(f"使用全量上市股票池: {len(code_list)} 只（显式包含 ST）")
        return code_list

    stock_basic_path = DATA_DIR / "stock_basic" / "stock_basic_non_st.csv"
    stock_basic = pd.read_csv(stock_basic_path, usecols=["ts_code"])
    code_list = stock_basic["ts_code"].dropna().astype(str).tolist()
    log(f"使用非 ST 股票池: {len(code_list)} 只")
    return code_list


def get_index_code_list(config=None):
    """优先使用本地 index_basic_all.csv，并进一步收敛到本地活跃指数池。"""
    config = config or {}
    local_index_basic = INDEX_DIR / "index_basic" / "index_basic_all.csv"
    if local_index_basic.exists():
        try:
            df_basic = pd.read_csv(local_index_basic, usecols=["ts_code"], low_memory=False)
            code_list = (
                df_basic["ts_code"]
                .dropna()
                .astype(str)
                .loc[lambda series: series.str.endswith((".SH", ".SZ"))]
                .drop_duplicates()
                .tolist()
            )
            active_codes = None
            path_name = config.get("path")
            prefix = config.get("prefix")
            if path_name and prefix:
                interface_dir = INDEX_DIR / path_name
                if interface_dir.exists():
                    active_codes = {
                        file.stem[len(prefix):]
                        for file in interface_dir.rglob(f"{prefix}*.csv")
                        if file.stem.startswith(prefix)
                    }
                    active_codes = {
                        code for code in active_codes
                        if isinstance(code, str) and code.endswith((".SH", ".SZ"))
                    }
            if active_codes:
                filtered = [code for code in code_list if code in active_codes]
                if filtered:
                    log(f"使用本地指数活跃代码池: {len(filtered)}/{len(code_list)} 个（仅 SH/SZ）")
                    return filtered
            log(f"使用本地指数代码池: {len(code_list)} 个（仅 SH/SZ）")
            return code_list
        except Exception as exc:
            log(f"读取本地指数代码池失败，回退远端 index_basic: {str(exc)[:80]}", "WARNING")

    df_basic = pro.index_basic()
    code_list = (
        df_basic["ts_code"]
        .dropna()
        .astype(str)
        .loc[lambda series: series.str.endswith((".SH", ".SZ"))]
        .drop_duplicates()
        .tolist()
    )
    log(f"使用远端指数代码池: {len(code_list)} 个（仅 SH/SZ）", "WARNING")
    return code_list


def get_latest_date_fast(filepath, chunk_size=32768, date_col="trade_date"):
    """快速读取CSV文件最后一行的日期列，避免读取整个大文件。"""
    return shared_get_latest_date_fast(filepath, chunk_size=chunk_size, date_col=date_col)


def append_to_csv(filepath, df):
    """追加DataFrame到CSV文件，不读取整个文件"""
    shared_append_to_csv(filepath, df)

def get_root_dir(config):
    """根据配置获取根目录"""
    return shared_get_root_dir(config, DATA_DIR, INDEX_DIR, financial_dir=FINANCIAL_DIR)

def get_trade_dates(start_date=None, end_date=None):
    """获取交易日列表"""
    return shared_get_trade_dates(pro, start_date=start_date, end_date=end_date)

def deduplicate_file(filepath, subset_cols, keep='last'):
    """清除文件中的重复数据"""
    return shared_deduplicate_file(filepath, subset_cols, keep=keep)

def check_interface_by_date(interface_name, config, sample_size=50):
    """检查按日期接口的数据完整性"""
    return shared_check_interface_by_date(
        interface_name,
        config,
        stock_dir=DATA_DIR,
        index_dir=INDEX_DIR,
        financial_dir=FINANCIAL_DIR,
        sample_size=sample_size,
    )


def get_local_latest_date(interface_name, config):
    """Return the actual latest local date for a configured interface."""
    actual_local_latest = shared_get_local_latest_date(
        interface_name,
        config,
        stock_dir=DATA_DIR,
        index_dir=INDEX_DIR,
        financial_dir=FINANCIAL_DIR,
    )
    whitelist_record = _get_interface_whitelist_record(interface_name)
    whitelist_latest = None
    if isinstance(whitelist_record, dict):
        candidates = [
            str(whitelist_record.get("latest_date")) if whitelist_record.get("latest_date") else None,
            str(whitelist_record.get("validated_end_date")) if whitelist_record.get("validated_end_date") else None,
        ]
        candidates = [item for item in candidates if item]
        if candidates:
            whitelist_latest = max(candidates)
    if whitelist_latest and actual_local_latest:
        return max(str(whitelist_latest), str(actual_local_latest))
    return whitelist_latest or actual_local_latest


def get_missing_trade_dates(interface_name, config, trade_calendar, default_lookback=30):
    """基于交易日历判断单接口缺失日期"""
    return shared_get_missing_trade_dates(
        interface_name,
        config,
        trade_calendar,
        stock_dir=DATA_DIR,
        index_dir=INDEX_DIR,
        financial_dir=FINANCIAL_DIR,
        default_lookback=default_lookback,
    )


def is_tolerable_trailing_gap(config, missing_dates):
    """判断剩余尾部缺口是否属于允许的晚出数空窗。"""
    return shared_is_tolerable_trailing_gap(config, missing_dates)


def scan_incomplete_records(interface_name, config, calendar_dates=None):
    """扫描单接口本地坏行/缺参行"""
    progress_interval = int(config.get("health_scan_log_interval", 1000) or 0)
    return shared_scan_incomplete_records(
        interface_name,
        config,
        stock_dir=DATA_DIR,
        index_dir=INDEX_DIR,
        financial_dir=FINANCIAL_DIR,
        progress_fn=log,
        progress_interval=progress_interval,
        calendar_dates=calendar_dates,
    )


def _load_interface_whitelist():
    path = get_interface_whitelist_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save_interface_whitelist(payload):
    path = get_interface_whitelist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def get_interface_whitelist_status(interface_names):
    """Return canonical whitelist counts for the given autofill interfaces."""
    payload = _load_interface_whitelist()
    names = list(dict.fromkeys(interface_names))
    whitelisted = [
        name
        for name in names
        if isinstance(payload.get(name), dict) and payload[name].get("enabled")
    ]
    not_whitelisted = [name for name in names if name not in set(whitelisted)]
    unknown_entries = sorted(set(payload) - set(names))
    return {
        "whitelist_path": str(get_interface_whitelist_path()),
        "total_count": len(names),
        "whitelisted_count": len(whitelisted),
        "not_whitelisted_count": len(not_whitelisted),
        "whitelisted": whitelisted,
        "not_whitelisted": not_whitelisted,
        "unknown_entries": unknown_entries,
    }


def _is_interface_whitelisted(interface_name):
    payload = _load_interface_whitelist()
    record = payload.get(interface_name)
    if isinstance(record, dict) and bool(record.get("enabled")):
        return True
    # 检查 registry 中是否标记为默认白名单
    config = INTERFACE_CONFIG.get(interface_name, {})
    return bool(config.get("default_whitelist"))


def _get_interface_whitelist_record(interface_name):
    payload = _load_interface_whitelist()
    record = payload.get(interface_name)
    if isinstance(record, dict) and bool(record.get("enabled")):
        return record
    # 默认白名单接口返回一个虚拟记录
    config = INTERFACE_CONFIG.get(interface_name, {})
    if config.get("default_whitelist"):
        return {"enabled": True, "default": True}
    return None


def _calendar_window_covered_by_whitelist(record, calendar_dates):
    """Return True when the validated whitelist range fully covers the health window."""
    if not record or not calendar_dates:
        return False
    return not _calendar_dates_not_covered_by_whitelist(record, calendar_dates)


def _get_validated_intervals(record):
    intervals = []
    for item in record.get("validated_intervals") or []:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        if start and end:
            intervals.append((str(start), str(end)))
    if not intervals:
        validated_start = record.get("validated_start_date")
        validated_end = record.get("validated_end_date") or record.get("latest_date")
        if validated_start and validated_end:
            intervals.append((str(validated_start), str(validated_end)))
    return intervals


def _calendar_dates_not_covered_by_whitelist(record, calendar_dates):
    intervals = _get_validated_intervals(record or {})
    uncovered = []
    for date_value in calendar_dates or []:
        date_text = str(date_value)
        if not any(start <= date_text <= end for start, end in intervals):
            uncovered.append(date_text)
    return uncovered


def _merge_validated_intervals(intervals):
    normalized = sorted(
        ({"start": str(item["start"]), "end": str(item["end"])} for item in intervals if item.get("start") and item.get("end")),
        key=lambda item: (item["start"], item["end"]),
    )
    if not normalized:
        return []

    merged = [normalized[0]]
    for item in normalized[1:]:
        current = merged[-1]
        if item["start"] <= current["end"]:
            current["end"] = max(current["end"], item["end"])
        else:
            merged.append(item)
    return merged


def _calendar_dates_to_intervals(calendar_dates):
    dates = [str(d) for d in calendar_dates or []]
    if not dates:
        return []
    intervals = []
    start = dates[0]
    previous = dates[0]
    for date_value in dates[1:]:
        try:
            previous_dt = datetime.strptime(previous, "%Y%m%d")
            current_dt = datetime.strptime(date_value, "%Y%m%d")
            delta_days = (current_dt - previous_dt).days
            is_weekend_bridge = (
                delta_days == 3
                and previous_dt.weekday() == 4
                and current_dt.weekday() == 0
            )
            continuous = delta_days == 1 or is_weekend_bridge
        except Exception:
            continuous = False
        if continuous:
            previous = date_value
            continue
        intervals.append({"start": start, "end": previous})
        start = date_value
        previous = date_value
    intervals.append({"start": start, "end": previous})
    return intervals


def _clean_calendar_intervals_for_whitelist(calendar_dates, missing_dates, health_report):
    if not calendar_dates:
        return []
    if health_report.get("empty_codes"):
        return []

    bad_dates = set(str(d) for d in missing_dates or [])
    bad_dates.update(str(d) for d in health_report.get("dates", []) or [])
    bad_dates.update(str(d) for d in (health_report.get("codes_by_date") or {}).keys())

    intervals = []
    start = None
    end = None
    for date_value in [str(d) for d in calendar_dates]:
        if date_value in bad_dates:
            if start and end:
                intervals.append({"start": start, "end": end})
            start = None
            end = None
            continue
        if start is None:
            start = date_value
        end = date_value
    if start and end:
        intervals.append({"start": start, "end": end})
    return intervals


def _local_year_file_date_intervals_for_whitelist(interface_name, config, calendar_dates, health_report=None):
    if config.get("root") != "financial" or config.get("save_granularity") != "year":
        return []
    if not calendar_dates:
        return []

    root_dir = get_root_dir(config)
    path = root_dir / config["path"]
    if not path.exists():
        return []

    date_col = config.get("date_col", "trade_date")
    prefix = config.get("prefix", f"{interface_name}_")
    calendar_values = [str(d) for d in calendar_dates]
    calendar_set = set(calendar_values)
    target_years = {d[:4] for d in calendar_values}
    bad_dates = set(str(d) for d in (health_report or {}).get("dates", []) or [])
    bad_dates.update(str(d) for d in ((health_report or {}).get("codes_by_date") or {}).keys())

    present_dates = set()
    for csv_file in sorted(path.glob(f"{prefix}*.csv")):
        if "_metadata" in csv_file.name:
            continue
        match = re.search(rf"{re.escape(prefix)}(\d{{4}})\.csv$", csv_file.name)
        if match and match.group(1) not in target_years:
            continue
        try:
            header = pd.read_csv(csv_file, nrows=0)
            if date_col not in header.columns:
                continue
            frame = pd.read_csv(csv_file, usecols=[date_col], low_memory=False)
        except Exception:
            continue
        if frame.empty:
            continue
        values = (
            frame[date_col]
            .dropna()
            .astype(str)
            .str.replace("-", "", regex=False)
            .str.replace(r"\.0$", "", regex=True)
        )
        present_dates.update(
            value
            for value in values
            if re.fullmatch(r"\d{8}", value or "")
            and value in calendar_set
            and value not in bad_dates
        )

    intervals = []
    start = None
    end = None
    for date_value in calendar_values:
        if date_value not in present_dates:
            if start and end:
                intervals.append({"start": start, "end": end})
            start = None
            end = None
            continue
        if start is None:
            start = date_value
        end = date_value
    if start and end:
        intervals.append({"start": start, "end": end})
    return intervals


def _call_api_with_timeout(api_func, timeout_sec=30, **kwargs):
    """Call a Tushare API with alarm and socket timeouts to avoid stuck reads."""
    timeout_sec = max(1, int(timeout_sec or 30))

    def _timeout_handler(signum, frame):
        raise TimeoutError(f"API timeout>{timeout_sec}s")

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    old_socket_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout_sec)
    signal.alarm(timeout_sec)
    try:
        return api_func(**kwargs)
    finally:
        signal.alarm(0)
        socket.setdefaulttimeout(old_socket_timeout)
        signal.signal(signal.SIGALRM, old_handler)


def _is_whitelist_eligible(config, *, bypass_whitelist=False):
    """Return True when an interface may use the validated-complete fast path."""
    return not bypass_whitelist and config.get("whitelist_eligible", True)


def _mark_interface_whitelisted(interface_name, *, latest_date=None, mode=None, calendar_dates=None, validated_intervals=None):
    payload = _load_interface_whitelist()
    record = dict(payload.get(interface_name) or {})
    if validated_intervals or calendar_dates:
        new_intervals = (
            [{"start": str(item["start"]), "end": str(item["end"])} for item in validated_intervals]
            if validated_intervals
            else _calendar_dates_to_intervals(calendar_dates)
        )
        existing_intervals = [
            {"start": start, "end": end}
            for start, end in _get_validated_intervals(record)
        ]
        record["validated_intervals"] = _merge_validated_intervals([*existing_intervals, *new_intervals])
        window_start = min(item["start"] for item in new_intervals)
        window_end = max(item["end"] for item in new_intervals)
        previous_start = record.get("validated_start_date")
        previous_end = record.get("validated_end_date")
        record["validated_start_date"] = min(
            [d for d in [previous_start, window_start] if d],
            default=window_start,
        )
        record["validated_end_date"] = max(
            [d for d in [previous_end, window_end] if d],
            default=window_end,
        )
    latest_candidates = [
        str(record.get("latest_date")) if record.get("latest_date") else None,
        str(latest_date) if latest_date else None,
        str(record.get("validated_end_date")) if record.get("validated_end_date") else None,
    ]
    latest_candidates = [item for item in latest_candidates if item]
    record.update({
        "enabled": True,
        "latest_date": max(latest_candidates) if latest_candidates else None,
        "validated_mode": mode,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    payload[interface_name] = record
    _save_interface_whitelist(payload)


def _clean_calendar_dates_for_whitelist(calendar_dates, missing_dates, health_report):
    if not calendar_dates:
        return []
    if health_report.get("empty_codes"):
        return []
    bad_dates = set(str(d) for d in missing_dates or [])
    bad_dates.update(str(d) for d in health_report.get("dates", []) or [])
    bad_dates.update(str(d) for d in (health_report.get("codes_by_date") or {}).keys())
    return [str(d) for d in calendar_dates if str(d) not in bad_dates]


def _remove_interface_whitelist(interface_name):
    payload = _load_interface_whitelist()
    if interface_name in payload:
        payload.pop(interface_name, None)
        _save_interface_whitelist(payload)


def _code_resume_state_path(interface_name, start_date, end_date):
    return Path(__file__).resolve().parent.parent / "logs" / "autofill_code_state" / f"{interface_name}_{start_date}_{end_date}.json"


def _load_code_resume_state(interface_name, start_date, end_date):
    path = _code_resume_state_path(interface_name, start_date, end_date)
    if not path.exists():
        return {"codes": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("codes"), dict):
            return payload
    except Exception:
        pass
    return {"codes": {}}


def _save_code_resume_state(interface_name, start_date, end_date, payload):
    path = _code_resume_state_path(interface_name, start_date, end_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "interface": interface_name,
        "start_date": start_date,
        "end_date": end_date,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "codes": payload.get("codes", {}),
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _mark_code_resume_state(payload, code, status):
    payload.setdefault("codes", {})[str(code)] = {
        "status": status,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _resolve_code_storage_file(output_dir, config, code, target_date):
    save_granularity = config.get('save_granularity', 'stock')
    if save_granularity == 'ymd_stock':
        year = str(target_date)[:4]
        month = str(target_date)[4:6]
        day = str(target_date)[6:8]
        return output_dir / year / month / day / f"{config['prefix']}{code}.csv"
    if save_granularity == 'year_stock':
        year = str(target_date)[:4]
        return output_dir / year / f"{config['prefix']}{code}.csv"
    return output_dir / f"{config['prefix']}{code}.csv"


def _get_api_name(interface_name, config=None):
    if config and config.get("api"):
        return str(config["api"])
    return str(interface_name)


def _get_api_func(interface_name, config=None):
    return getattr(pro, _get_api_name(interface_name, config))


def _api_process_worker(queue, api_name, kwargs):
    try:
        api_func = getattr(pro, api_name)
        frame = api_func(**kwargs)
        queue.put(("ok", frame))
    except KeyboardInterrupt:
        # 外部中断时，避免子进程打印一大段 traceback。
        try:
            queue.put(("error", "KeyboardInterrupt: API process interrupted"))
        except Exception:
            pass
    except Exception as exc:
        try:
            queue.put(("error", f"{type(exc).__name__}: {exc}"))
        except Exception:
            pass


def _call_api_in_process(api_name, timeout_sec=30, **kwargs):
    timeout_sec = max(1, int(timeout_sec or 30))
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue(maxsize=1)
    proc = ctx.Process(target=_api_process_worker, args=(queue, api_name, kwargs))
    proc.start()
    proc.join(timeout_sec)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        raise TimeoutError(f"API process timeout>{timeout_sec}s")
    if queue.empty():
        if proc.exitcode == 0:
            return None
        raise RuntimeError(f"API process exited with code {proc.exitcode}")
    status, payload = queue.get()
    if status == "ok":
        return payload
    raise RuntimeError(payload)

def fill_by_date_interface(interface_name, config, trade_dates):
    """补全按日期获取的接口数据（支持分页拉取）"""
    interface_started_at = time.monotonic()
    root_dir = get_root_dir(config)
    log_interface_banner(interface_name, '指数' if config.get('root') == 'index' else '股票')
    
    output_dir = root_dir / config['path']
    output_dir.mkdir(parents=True, exist_ok=True)
    api_func = _get_api_func(interface_name, config)
    partition_by_year_dir = bool(config.get('partition_by_year_dir', False))
    fixed_file_name = config.get('fixed_file_name')

    def _resolve_output_file(trade_date):
        if fixed_file_name:
            return output_dir / fixed_file_name
        save_granularity = config.get('save_granularity', 'date')
        if save_granularity == 'ymd_date':
            year = str(trade_date)[:4]
            month = str(trade_date)[4:6]
            day = str(trade_date)[6:8]
            ymd_dir = output_dir / year / month / day
            ymd_dir.mkdir(parents=True, exist_ok=True)
            return ymd_dir / f"{config['prefix']}{trade_date}.csv"
        if save_granularity == 'year_date':
            year_dir = output_dir / str(trade_date)[:4]
            year_dir.mkdir(parents=True, exist_ok=True)
            return year_dir / f"{config['prefix']}{trade_date}.csv"
        if save_granularity == 'year':
            return output_dir / f"{config['prefix']}{str(trade_date)[:4]}.csv"
        if config.get('partition_by_year_dir', False):
            year_dir = output_dir / str(trade_date)[:4]
            year_dir.mkdir(parents=True, exist_ok=True)
            return year_dir / f"{config['prefix']}{trade_date}.csv"
        return output_dir / f"{config['prefix']}{trade_date}.csv"

    def _resolve_code_output_file(code, trade_date):
        if partition_by_year_dir:
            year_dir = output_dir / str(trade_date)[:4]
            year_dir.mkdir(parents=True, exist_ok=True)
            return year_dir / f"{config['prefix']}{code}.csv"
        return output_dir / f"{config['prefix']}{code}.csv"

    def _write_csv_and_agent(target_file, frame):
        frame.to_csv(target_file, index=False)
        shared_write_multi_format_bundle(target_file, frame, interface_name=interface_name)

    def _refresh_multi_format(target_file):
        try:
            snapshot = pd.read_csv(target_file, low_memory=False)
            shared_write_multi_format_bundle(target_file, snapshot, interface_name=interface_name)
        except Exception:
            pass

    def _merge_and_write(target_file, frame, date_col):
        payload = _normalize_frame_date_column(frame.copy(), date_col)
        if target_file.exists() and target_file.stat().st_size > 0:
            existing = pd.read_csv(target_file, low_memory=False)
            existing = _normalize_frame_date_column(existing, date_col)
            merged = pd.concat([existing, payload], ignore_index=True)
        else:
            merged = payload

        dedup_cols = [
            c for c in [date_col, 'ts_code', 'index_code', 'con_code', 'name', 'hm_name', 'update_date']
            if c in merged.columns
        ]
        if dedup_cols:
            merged = merged.drop_duplicates(subset=dedup_cols, keep='last')
        else:
            merged = merged.drop_duplicates(keep='last')
        if date_col in merged.columns:
            merged = _normalize_frame_date_column(merged, date_col)
            merged = merged.sort_values(date_col)
        _write_csv_and_agent(target_file, merged)
    
    success_count = 0
    empty_count = 0
    error_count = 0
    save_granularity = config.get('save_granularity', 'date')

    # 大文件接口使用快速预扫描+追加策略（避免读取整个文件）
    use_fast_append = (
        interface_name in ['stk_factor_pro', 'daily', 'daily_basic', 'moneyflow']
        and not config.get('force_by_date', False)
        and save_granularity != 'ymd_date'
    )
    latest_date_map = {}
    if use_fast_append:
        log("  预扫描本地文件最新日期...")
        file_iter = output_dir.rglob(f"{config['prefix']}*.csv") if partition_by_year_dir else output_dir.glob(f"{config['prefix']}*.csv")
        for f in file_iter:
            code = f.name[len(config['prefix']):-4]
            latest = get_latest_date_fast(f)
            if not latest:
                continue
            prev = latest_date_map.get(code)
            if (prev is None) or (str(latest) > str(prev)):
                latest_date_map[code] = str(latest)
        log(f"  已扫描 {len(latest_date_map)} 个文件")
    
    if config.get('use_date_range_fetch', False) and trade_dates:
        date_col = config.get('date_col', 'trade_date')
        ranges = []
        if config.get('fetch_full_years', False):
            current_year = datetime.now().year
            start_year = int(config.get('year_fetch_start_year', current_year))
            start_year = min(start_year, current_year)
            for year in range(start_year, current_year + 1):
                start_date = f"{year}0101"
                end_date = f"{year}1231" if year < current_year else datetime.now().strftime('%Y%m%d')
                ranges.append((start_date, end_date))
        else:
            ranges.append((min(trade_dates), max(trade_dates)))

        all_frames = []
        range_fetch_stock_codes = None
        if config.get('date_fetch_with_stock_pool'):
            try:
                range_fetch_stock_codes = get_stock_code_list(config)
                log(f"区间请求附带非 ST 股票池: {len(range_fetch_stock_codes)} 只")
            except Exception as e:
                log(f"无法获取区间请求股票池: {str(e)[:80]}", "ERROR")
                return False
        for start_date, end_date in ranges:
            log(f"  🔄 区间拉取: {start_date} ~ {end_date}")
            try:
                request_kwargs = {"start_date": start_date, "end_date": end_date}
                if range_fetch_stock_codes:
                    request_kwargs[config.get('code_param', 'ts_code')] = ','.join(range_fetch_stock_codes)
                df = api_func(**request_kwargs)
                if df is None or df.empty:
                    continue
                if date_col not in df.columns:
                    log(f"  ⚪ 区间返回缺少 {date_col} 列")
                    continue
                df = _normalize_frame_date_column(df, date_col)
                all_frames.append(df)
            except Exception as e:
                error_count += 1
                log(f"  ❌ {start_date}~{end_date}: {str(e)[:80]}")

        if all_frames:
            merged = pd.concat(all_frames, ignore_index=True).drop_duplicates(keep='last')
            if save_granularity == 'year':
                merged['_year'] = merged[date_col].str[:4]
                for year, part in merged.groupby('_year'):
                    filepath = output_dir / f"{config['prefix']}{year}.csv"
                    part = part.drop(columns=['_year'])
                    sort_cols = [c for c in [date_col, 'ts_code', 'index_code'] if c in part.columns]
                    if sort_cols:
                        part = part.sort_values(sort_cols)
                    _write_csv_and_agent(filepath, part)
                log(f"  ✅ 区间写入 {len(merged)} 条，覆盖 {merged['_year'].nunique()} 个年份")
            else:
                if fixed_file_name:
                    filepath = _resolve_output_file(trade_dates[0])
                    _merge_and_write(filepath, merged, date_col)
                else:
                    if not config.get('fetch_full_years', False):
                        trade_set = set(trade_dates)
                        merged = merged[merged[date_col].isin(trade_set)]
                    for trade_date, part in merged.groupby(date_col):
                        filepath = _resolve_output_file(trade_date)
                        _write_csv_and_agent(filepath, part)
                log(f"  ✅ 区间写入 {len(merged)} 条，覆盖 {merged[date_col].nunique() if date_col in merged.columns else 1} 个交易日")
            success_count += 1
        elif error_count == 0:
            empty_count += 1
            if ranges:
                log(f"  ⚪ {ranges[0][0]}~{ranges[-1][1]}: 无数据")

        retention_days = config.get('retention_days')
        if retention_days:
            removed = shared_prune_date_partitioned_history(output_dir, config['prefix'], retention_days)
            if removed:
                log(f"  🧹 清理超期历史文件 {removed} 个 (>{retention_days} 天)")
        log_interface_summary(
            success_count,
            empty_count,
            error_count,
            duration_seconds=time.monotonic() - interface_started_at,
        )
        return success_count > 0

    date_col_param = config.get('date_col', 'trade_date')
    request_retry_count = max(1, int(config.get("date_request_retry_count", 1) or 1))
    request_retry_sleep_sec = float(config.get("date_request_retry_sleep_sec", 0.5) or 0.5)
    request_timeout_sec = int(config.get("api_timeout_sec", 30) or 30)
    network_error_dates = []
    date_fetch_stock_codes = None
    if config.get('date_fetch_with_stock_pool'):
        try:
            date_fetch_stock_codes = get_stock_code_list(config)
            log(f"日期请求附带非 ST 股票池: {len(date_fetch_stock_codes)} 只")
        except Exception as e:
            log(f"无法获取日期请求股票池: {str(e)[:80]}", "ERROR")
            return False
    total_trade_dates = len(trade_dates)
    for trade_index, trade_date in enumerate(trade_dates, start=1):
        try:
            start_live_spinner(f"{interface_name}: 正在拉取 {trade_date} ({trade_index}/{total_trade_dates})")
            all_rows = []
            had_page_error = False
            use_pagination = config.get('use_pagination')
            request_kwargs = {date_col_param: trade_date}
            if date_fetch_stock_codes:
                request_kwargs[config.get('code_param', 'ts_code')] = ','.join(date_fetch_stock_codes)
            if use_pagination is True:
                page_limit = int(config.get('page_limit', 5000))
                max_pages = int(config.get('max_pages', 3))
                for page_idx in range(max_pages):
                    offset = page_idx * page_limit
                    page_success = False
                    for retry_idx in range(request_retry_count):
                        try:
                            df_page = _call_api_with_timeout(
                                api_func,
                                timeout_sec=request_timeout_sec,
                                **request_kwargs,
                                limit=page_limit,
                                offset=offset,
                            )
                            page_success = True
                            if df_page is None or df_page.empty:
                                break
                            all_rows.append(df_page)
                            if len(df_page) < page_limit:
                                break
                            break
                        except Exception as e:
                            if retry_idx + 1 < request_retry_count:
                                stop_live_spinner(
                                    f"  ⚠️ {trade_date} offset={offset} 第 {retry_idx + 1}/{request_retry_count} 次失败: {str(e)[:60]}，准备重试"
                                )
                                time.sleep(request_retry_sleep_sec * (retry_idx + 1))
                                continue
                            error_count += 1
                            had_page_error = True
                            if _is_likely_network_error(e):
                                network_error_dates.append(trade_date)
                            stop_live_spinner(f"  ❌ {trade_date} offset={offset} 失败: {str(e)[:60]}", "ERROR")
                    if not page_success or (all_rows and len(all_rows[-1]) < page_limit):
                        if not page_success:
                            break
                        break
                    time.sleep(0.3)
            elif use_pagination is False:
                # 显式禁用分页：只传业务参数，不传 limit/offset（某些接口传 limit 会改变返回行为）
                for retry_idx in range(request_retry_count):
                    try:
                        df_page = _call_api_with_timeout(
                            api_func,
                            timeout_sec=request_timeout_sec,
                            **request_kwargs,
                        )
                        if df_page is not None and not df_page.empty:
                            all_rows.append(df_page)
                        break
                    except Exception as e:
                        if retry_idx + 1 < request_retry_count:
                            stop_live_spinner(
                                f"  ⚠️ {trade_date} 第 {retry_idx + 1}/{request_retry_count} 次失败: {str(e)[:60]}，准备重试"
                            )
                            time.sleep(request_retry_sleep_sec * (retry_idx + 1))
                            continue
                        error_count += 1
                        had_page_error = True
                        if _is_likely_network_error(e):
                            network_error_dates.append(trade_date)
                        stop_live_spinner(f"  ❌ {trade_date} 失败: {str(e)[:60]}", "ERROR")
                time.sleep(0.3)
            else:
                # 默认两页
                for offset in [0, 5000]:
                    page_success = False
                    for retry_idx in range(request_retry_count):
                        try:
                            df_page = _call_api_with_timeout(
                                api_func,
                                timeout_sec=request_timeout_sec,
                                **request_kwargs,
                                limit=5000,
                                offset=offset,
                            )
                            page_success = True
                            if df_page is not None and not df_page.empty:
                                all_rows.append(df_page)
                            break
                        except Exception as e:
                            if retry_idx + 1 < request_retry_count:
                                stop_live_spinner(
                                    f"  ⚠️ {trade_date} offset={offset} 第 {retry_idx + 1}/{request_retry_count} 次失败: {str(e)[:60]}，准备重试"
                                )
                                time.sleep(request_retry_sleep_sec * (retry_idx + 1))
                                continue
                            error_count += 1
                            had_page_error = True
                            if _is_likely_network_error(e):
                                network_error_dates.append(trade_date)
                            stop_live_spinner(f"  ❌ {trade_date} offset={offset} 失败: {str(e)[:60]}", "ERROR")
                    if not page_success:
                        break
                    time.sleep(0.3)
            
            if not all_rows:
                if had_page_error:
                    stop_live_spinner(f"  ⚠️ {trade_date}: 请求失败，未将其记为无数据", "WARNING")
                    continue
                empty_count += 1
                stop_live_spinner(f"  ⚪ {trade_date}: 无数据")
                continue
            
            df = pd.concat(all_rows, ignore_index=True)
            dup_cols = config.get('dedup_cols')
            if not dup_cols:
                dup_cols = [config['date_col']]
                if 'ts_code' in df.columns:
                    dup_cols.append('ts_code')
                elif 'index_code' in df.columns:
                    dup_cols.append('index_code')
            dup_cols = [col for col in dup_cols if col in df.columns]
            if dup_cols:
                df = df.drop_duplicates(subset=dup_cols, keep='last')
            else:
                df = df.drop_duplicates(keep='last')
            
            if df is not None and not df.empty:
                min_rows = int(config.get('min_rows_per_date', 0) or 0)
                if min_rows > 0 and len(df) < min_rows:
                    error_count += 1
                    stop_live_spinner(
                        f"  ⚠️ {trade_date}: 仅返回 {len(df)} 条，低于最低阈值 {min_rows}，疑似 API 截断，跳过",
                        "WARNING",
                    )
                    continue
                df = _normalize_frame_date_column(df, config['date_col'])
                code_col = 'ts_code' if 'ts_code' in df.columns else 'index_code'
                if code_col in df.columns and not config.get('force_by_date', False):
                    updated = 0
                    skipped = 0
                    for code in df[code_col].unique():
                        item_df = df[df[code_col] == code].copy()
                        item_df = _normalize_frame_date_column(item_df, config['date_col'])
                        
                        filepath = _resolve_code_output_file(code, trade_date)
                        
                        if use_fast_append:
                            latest = latest_date_map.get(code)
                            # Only skip when the exact target date is already present.
                            # If latest > trade_date, this is a backfill scenario and should merge.
                            if latest and latest == trade_date:
                                skipped += 1
                                continue
                            if latest and latest < trade_date:
                                append_to_csv(filepath, item_df)
                                _refresh_multi_format(filepath)
                                latest_date_map[code] = trade_date
                                updated += 1
                            else:
                                action = shared_fast_merge_to_file(filepath, item_df, date_col=config['date_col'])
                                if action in {"merged", "appended", "created"}:
                                    _refresh_multi_format(filepath)
                                    latest_date_map[code] = max(str(latest or ""), str(trade_date))
                                    updated += 1
                                else:
                                    skipped += 1
                        else:
                            if filepath.exists():
                                existing = pd.read_csv(filepath)
                                if config['date_col'] in existing.columns:
                                    existing = _normalize_frame_date_column(existing, config['date_col'])
                                    existing = existing[existing[config['date_col']] != trade_date]
                                combined = pd.concat([existing, item_df], ignore_index=True)
                                combined = combined.drop_duplicates(subset=[config['date_col']], keep='last')
                                combined = combined.sort_values(config['date_col'])
                            else:
                                combined = item_df.sort_values(config['date_col'])
                            _write_csv_and_agent(filepath, combined)
                    if use_fast_append:
                        stop_live_spinner(f"  ✅ {trade_date}: {len(df)} 条, 更新 {updated}, 跳过 {skipped}", "SUCCESS")
                    else:
                        stop_live_spinner(f"  ✅ {trade_date}: {len(df)} 条", "SUCCESS")
                else:
                    # 按日期或按年份保存
                    save_granularity = config.get('save_granularity', 'date')
                    if save_granularity == 'year':
                        year = str(trade_date)[:4]
                        filepath = output_dir / f"{config['prefix']}{year}.csv"
                        frame = _normalize_frame_date_column(df.copy(), config['date_col'])
                        if filepath.exists():
                            existing = pd.read_csv(filepath, low_memory=False)
                            existing = _normalize_frame_date_column(existing, config['date_col'])
                            combined = pd.concat([existing, frame], ignore_index=True)
                            dedup_cols = [c for c in [config['date_col'], 'ts_code', 'index_code'] if c in combined.columns]
                            if dedup_cols:
                                combined = combined.drop_duplicates(subset=dedup_cols, keep='last')
                            sort_cols = [c for c in [config['date_col'], 'ts_code', 'index_code'] if c in combined.columns]
                            if sort_cols:
                                combined = combined.sort_values(sort_cols)
                            _write_csv_and_agent(filepath, combined)
                        else:
                            _write_csv_and_agent(filepath, frame)
                    else:
                        filepath = _resolve_output_file(trade_date)
                        if fixed_file_name:
                            _merge_and_write(filepath, df, config['date_col'])
                        else:
                            _write_csv_and_agent(filepath, df)
                    stop_live_spinner(f"  ✅ {trade_date}: {len(df)} 条", "SUCCESS")
                
                success_count += 1
            else:
                empty_count += 1
                stop_live_spinner(f"  ⚪ {trade_date}: 无数据")
                
        except Exception as e:
            error_count += 1
            if _is_likely_network_error(e):
                network_error_dates.append(trade_date)
            stop_live_spinner(f"  ❌ {trade_date}: {str(e)[:80]}", "ERROR")
        
        time.sleep(0.3)
    
    retention_days = config.get('retention_days')
    if retention_days:
        removed = shared_prune_date_partitioned_history(output_dir, config['prefix'], retention_days)
        if removed:
            log(f"  🧹 清理超期历史文件 {removed} 个 (>{retention_days} 天)")

    log_interface_summary(
        success_count,
        empty_count,
        error_count,
        duration_seconds=time.monotonic() - interface_started_at,
    )
    unique_network_error_dates = sorted(set(network_error_dates))
    if unique_network_error_dates:
        log(
            f"🌐 疑似网络/中转端波动：{interface_name} 本轮有 {len(unique_network_error_dates)} 个交易日请求失败 "
            f"({_format_short_list(unique_network_error_dates)})",
            "WARNING",
        )
        log(
            f"⚠️ {interface_name}: 本接口本轮存在未成功日期，失败日期未记为无数据，可在下轮继续重试",
            "WARNING",
        )
    covered_target_date = False
    if trade_dates:
        target_trade_date = str(max(trade_dates))
        if fixed_file_name:
            covered_target_date = success_count > 0
        elif save_granularity == 'year':
            covered_target_date = any(str(item).startswith(target_trade_date[:4]) for item in trade_dates)
            covered_target_date = covered_target_date and success_count > 0
        else:
            target_filepath = _resolve_output_file(target_trade_date)
            covered_target_date = target_filepath.exists()
    return {
        "ok": success_count > 0,
        "covered_target_date": covered_target_date,
        "success_count": success_count,
        "empty_count": empty_count,
        "error_count": error_count,
    }


def fill_disclosure_date_by_announcement_date(config, trade_dates):
    """按公告日逐交易日批量拉取 disclosure_date，并按年聚合保存。"""
    root_dir = get_root_dir(config)
    output_dir = root_dir / config["path"]
    output_dir.mkdir(parents=True, exist_ok=True)
    date_col = config.get("date_col", "ann_date")
    unique_trade_dates = sorted(set(str(item) for item in trade_dates))

    log(f"\n{'=' * 60}")
    log("补全 disclosure_date (股票)")
    log("=" * 60)
    log(f"公告日范围: {unique_trade_dates[0]} ~ {unique_trade_dates[-1]}")
    log(f"公告日数量: {len(unique_trade_dates)}")
    stock_codes = get_stock_code_list(config)
    batch_size = int(config.get("batch_initial_code_chunk_size", 3000) or 3000)
    batch_size = max(1, batch_size)
    log(f"股票池批量模式: {len(stock_codes)} 只非 ST，分批大小 {batch_size}")

    frames = []
    success_count = 0
    empty_count = 0
    error_count = 0

    for index, trade_date in enumerate(unique_trade_dates, start=1):
        try:
            log(f"  公告日进度: {index}/{len(unique_trade_dates)} | {trade_date}")
            rows = []
            for start_idx in range(0, len(stock_codes), batch_size):
                batch_codes = stock_codes[start_idx:start_idx + batch_size]
                df_page = pro.disclosure_date(
                    ann_date=trade_date,
                    ts_code=",".join(batch_codes),
                    limit=3000,
                    offset=0,
                )
                if df_page is None or df_page.empty:
                    continue
                rows.append(df_page)

            if not rows:
                empty_count += 1
                continue

            df = pd.concat(rows, ignore_index=True)
            if date_col in df.columns:
                df[date_col] = df[date_col].astype(str).str.replace('-', '', regex=False)
            frames.append(df)
            success_count += 1
            log(f"  ✅ {trade_date}: {len(df)} 条")
        except Exception as exc:
            error_count += 1
            log(f"  ❌ {trade_date}: {str(exc)[:80]}", "ERROR")

    if not frames:
        log(f"\n完成: 成功 {success_count}, 空数据 {empty_count}, 错误 {error_count}")
        return success_count > 0

    merged = pd.concat(frames, ignore_index=True).drop_duplicates(keep="last")
    if date_col in merged.columns:
        merged[date_col] = merged[date_col].astype(str).str.replace('-', '', regex=False)

    for year, part in merged.groupby(merged[date_col].astype(str).str[:4]):
        filepath = output_dir / f"{config['prefix']}{year}.csv"
        if filepath.exists() and filepath.stat().st_size > 0:
            existing = pd.read_csv(filepath, low_memory=False)
            combined = pd.concat([existing, part], ignore_index=True)
        else:
            combined = part.copy()
        dedup_cols = config.get("dedup_cols")
        if not dedup_cols:
            dedup_cols = [c for c in [date_col, "ts_code", "end_date", "pre_date", "actual_date", "modify_date"] if c in combined.columns]
        if dedup_cols:
            combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
        sort_cols = [c for c in [date_col, "ts_code", "end_date"] if c in combined.columns]
        if sort_cols:
            combined = combined.sort_values(sort_cols)
        combined.to_csv(filepath, index=False)
        shared_write_multi_format_bundle(filepath, combined, interface_name="disclosure_date")
        log(f"  ✅ 年文件 {year}: 新增 {len(part)} 条，落盘后 {len(combined)} 条")

    log(f"\n完成: 成功 {success_count}, 空数据 {empty_count}, 错误 {error_count}")
    return True


def fill_pledge_stat_by_end_date(config, trade_dates):
    """按 end_date 逐日期批量拉取 pledge_stat，并按年/个股保存。"""
    root_dir = get_root_dir(config)
    output_dir = root_dir / config["path"]
    output_dir.mkdir(parents=True, exist_ok=True)
    date_col = config.get("date_col", "end_date")
    unique_trade_dates = sorted(set(str(item) for item in trade_dates))

    log(f"\n{'=' * 60}")
    log("补全 pledge_stat (股票)")
    log("=" * 60)
    log(f"结束日范围: {unique_trade_dates[0]} ~ {unique_trade_dates[-1]}")
    log(f"结束日数量: {len(unique_trade_dates)}")
    stock_codes = get_stock_code_list(config)
    batch_size = int(config.get("batch_initial_code_chunk_size", 1000) or 1000)
    batch_size = max(1, batch_size)
    log(f"股票池批量模式: {len(stock_codes)} 只非 ST，分批大小 {batch_size}")

    success_count = 0
    empty_count = 0
    error_count = 0
    covered_target_date = False

    for index, trade_date in enumerate(unique_trade_dates, start=1):
        try:
            log(f"  结束日进度: {index}/{len(unique_trade_dates)} | {trade_date}")
            rows = []
            for start_idx in range(0, len(stock_codes), batch_size):
                batch_codes = stock_codes[start_idx:start_idx + batch_size]
                df_page = pro.pledge_stat(
                    end_date=trade_date,
                    ts_code=",".join(batch_codes),
                )
                if df_page is None or df_page.empty:
                    continue
                rows.append(df_page)

            if not rows:
                empty_count += 1
                continue

            df = pd.concat(rows, ignore_index=True)
            if date_col in df.columns:
                df[date_col] = df[date_col].astype(str).str.replace('-', '', regex=False)

            for ts_code, code_part in df.groupby("ts_code"):
                code_part = code_part.copy()
                code_part[date_col] = code_part[date_col].astype(str)
                for year, year_part in code_part.groupby(code_part[date_col].str[:4]):
                    year_dir = output_dir / str(year)
                    year_dir.mkdir(parents=True, exist_ok=True)
                    filepath = year_dir / f"{config['prefix']}{ts_code}.csv"
                    if filepath.exists() and filepath.stat().st_size > 0:
                        existing = pd.read_csv(filepath, low_memory=False)
                        if date_col in existing.columns:
                            existing[date_col] = existing[date_col].astype(str).str.replace('-', '', regex=False)
                        merged = pd.concat([existing, year_part], ignore_index=True)
                    else:
                        merged = year_part.copy()
                    dedup_cols = [c for c in [date_col, "ts_code"] if c in merged.columns]
                    if dedup_cols:
                        merged = merged.drop_duplicates(subset=dedup_cols, keep="last")
                    else:
                        merged = merged.drop_duplicates(keep="last")
                    sort_cols = [c for c in [date_col, "ts_code"] if c in merged.columns]
                    if sort_cols:
                        merged = merged.sort_values(sort_cols)
                    merged.to_csv(filepath, index=False)
                    shared_write_multi_format_bundle(filepath, merged, interface_name="pledge_stat")

            success_count += 1
            if trade_date == unique_trade_dates[-1]:
                covered_target_date = True
            log(f"  ✅ {trade_date}: {len(df)} 条")
        except Exception as exc:
            error_count += 1
            log(f"  ❌ {trade_date}: {str(exc)[:80]}", "ERROR")

    log(f"\n完成: 成功 {success_count}, 空数据 {empty_count}, 错误 {error_count}")
    return {
        "ok": success_count > 0,
        "covered_target_date": covered_target_date,
        "success_count": success_count,
        "empty_count": empty_count,
        "error_count": error_count,
    }


def _resolve_express_vip_periods(target_date):
    target_dt = datetime.strptime(str(target_date), "%Y%m%d")
    year = target_dt.year
    periods = []
    if target_dt.month <= 4:
        periods.append(f"{year - 1}1231")
    if target_dt.month == 4:
        periods.append(f"{year}0331")
    elif target_dt.month in {7, 8}:
        periods.append(f"{year}0630")
    elif target_dt.month == 10:
        periods.append(f"{year}0930")
    return periods


def _split_time_window_if_near_limit(fetch_once, *, start_dt, end_dt, row_limit, log_prefix, min_window_minutes=15):
    """防止分钟接口因单次返回接近上限而静默截断。"""
    frame = fetch_once(start_dt, end_dt)
    if frame is None or frame.empty:
        return []

    effective_limit = int(row_limit or 0)
    if effective_limit <= 0 or len(frame) < int(effective_limit * 0.95):
        return [frame]

    window_minutes = int((end_dt - start_dt).total_seconds() // 60)
    if window_minutes <= min_window_minutes:
        log(
            f"{log_prefix}返回 {len(frame)} 条，已接近阈值 {effective_limit} 且无法继续缩小时间窗，请人工确认是否截断",
            "WARNING",
        )
        return [frame]

    midpoint = start_dt + (end_dt - start_dt) / 2
    midpoint = midpoint.replace(second=0, microsecond=0)
    if midpoint <= start_dt:
        midpoint = start_dt + timedelta(minutes=max(1, window_minutes // 2))
    if midpoint >= end_dt:
        midpoint = end_dt - timedelta(minutes=max(1, window_minutes // 2))
    if midpoint <= start_dt or midpoint >= end_dt:
        log(
            f"{log_prefix}返回 {len(frame)} 条，已接近阈值 {effective_limit} 且分窗失败，请人工确认是否截断",
            "WARNING",
        )
        return [frame]

    log(
        f"{log_prefix}返回 {len(frame)} 条，接近阈值 {effective_limit}，自动拆分时间窗重试",
        "WARNING",
    )
    left_end = midpoint
    right_start = midpoint + timedelta(minutes=1)
    parts = _split_time_window_if_near_limit(
        fetch_once,
        start_dt=start_dt,
        end_dt=left_end,
        row_limit=effective_limit,
        log_prefix=log_prefix,
        min_window_minutes=min_window_minutes,
    )
    if right_start <= end_dt:
        parts.extend(
            _split_time_window_if_near_limit(
                fetch_once,
                start_dt=right_start,
                end_dt=end_dt,
                row_limit=effective_limit,
                log_prefix=log_prefix,
                min_window_minutes=min_window_minutes,
            )
        )
    return parts


def fill_stk_mins_single_stock(config, trade_dates):
    """补全 stk_mins 分钟数据（受限接口：单股、freq 必填）"""
    root_dir = get_root_dir(config)
    output_dir = root_dir / config["path"]
    output_dir.mkdir(parents=True, exist_ok=True)

    target_trade_dates = sorted(set(str(item) for item in trade_dates))
    if not target_trade_dates:
        return {"ok": False, "covered_target_date": False, "updated": 0}

    target_trade_date = max(target_trade_dates)
    target_date_fmt = f"{target_trade_date[:4]}-{target_trade_date[4:6]}-{target_trade_date[6:8]}"
    start_dt = datetime.strptime(f"{target_date_fmt} 09:30:00", "%Y-%m-%d %H:%M:%S")
    end_dt = datetime.strptime(f"{target_date_fmt} 15:00:00", "%Y-%m-%d %H:%M:%S")
    freq = str(config.get("freq", "1min"))
    max_rows_per_call = int(config.get("max_rows_per_call", 8000) or 8000)
    stock_codes = get_stock_code_list(config)

    log(f"\n{'=' * 60}")
    log("补全 stk_mins (分钟数据)")
    log("=" * 60)
    log(f"目标日期: {target_trade_date}")
    if config.get("include_st_codes") is True:
        log(f"使用全量上市股票池: {len(stock_codes)} 只（含 ST）")
    else:
        log(f"使用非 ST 股票池: {len(stock_codes)} 只")
    log(f"分钟频率: {freq}")
    log("⚠️ 受限接口：当前按单股逐只请求")

    successful_codes = 0
    empty_codes = 0
    error_codes = 0
    total_codes = len(stock_codes)

    for idx, ts_code in enumerate(stock_codes, start=1):
        update_live_spinner(
            f"stk_mins: 正在拉取 | {idx}/{total_codes} | 更新 {successful_codes} 无数据 {empty_codes} 错误 {error_codes}"
        )
        try:
            def _fetch_once(window_start, window_end):
                return _call_api_in_process(
                    "stk_mins",
                    timeout_sec=int(config.get("api_process_timeout_sec", 20) or 20),
                    ts_code=ts_code,
                    freq=freq,
                    start_date=window_start.strftime("%Y-%m-%d %H:%M:%S"),
                    end_date=window_end.strftime("%Y-%m-%d %H:%M:%S"),
                )

            frames = _split_time_window_if_near_limit(
                _fetch_once,
                start_dt=start_dt,
                end_dt=end_dt,
                row_limit=max_rows_per_call,
                log_prefix=f"  stk_mins {ts_code}: ",
            )
            if not frames:
                empty_codes += 1
            else:
                frame = pd.concat(frames, ignore_index=True).drop_duplicates().reset_index(drop=True)
                frame["ts_code"] = frame["ts_code"].astype(str)
                frame["trade_time"] = frame["trade_time"].astype(str)
                code_short = ts_code.split(".")[0]
                target_dir = output_dir / target_trade_date[:4] / target_trade_date[4:6] / target_trade_date[6:8] / code_short
                target_dir.mkdir(parents=True, exist_ok=True)
                filepath = target_dir / f"{freq}.csv"
                frame = frame.sort_values("trade_time").reset_index(drop=True)
                frame.to_csv(filepath, index=False)
                successful_codes += 1
        except Exception as exc:
            error_codes += 1
            log(f"  ❌ stk_mins {ts_code}: {str(exc)[:120]}", "ERROR")

        if idx % int(config.get("progress_log_interval", 10) or 10) == 0:
            log(
                f"进度: {idx}/{total_codes} | 有更新: {successful_codes} | 无数据: {empty_codes} | 错误: {error_codes}"
            )

    stop_live_spinner()
    log(
        f"📦 完成汇总: 有更新 {successful_codes} | 无数据 {empty_codes} | 错误 {error_codes}",
        "INFO",
    )
    return {
        "ok": bool(successful_codes),
        "covered_target_date": bool(successful_codes),
        "updated": successful_codes,
    }


def fill_express_vip_by_period(config, trade_dates):
    """按财报发布时间窗口，用 express_vip 拉全市场业绩快报。"""
    root_dir = get_root_dir(config)
    output_dir = root_dir / config["path"]
    output_dir.mkdir(parents=True, exist_ok=True)
    target_date = max(str(item) for item in trade_dates)
    periods = _resolve_express_vip_periods(target_date)

    log(f"\n{'=' * 60}")
    log("补全 express (VIP 批量版)")
    log("=" * 60)
    log(f"目标日期: {target_date}")

    if not periods:
        log("  ⏭️ 未处于业绩快报发布时间窗口，跳过该接口")
        return {
            "ok": True,
            "covered_target_date": True,
            "success_count": 0,
            "empty_count": 0,
            "error_count": 0,
        }

    success_count = 0
    empty_count = 0
    error_count = 0
    frames = []

    for period in periods:
        try:
            df = pro.express_vip(period=period)
            if df is None or df.empty:
                empty_count += 1
                log(f"  ⚪ period={period}: 无数据")
                continue
            if "ann_date" in df.columns:
                df["ann_date"] = df["ann_date"].astype(str).str.replace('-', '', regex=False)
            if "end_date" in df.columns:
                df["end_date"] = df["end_date"].astype(str).str.replace('-', '', regex=False)
            frames.append(df)
            success_count += 1
            log(f"  ✅ period={period}: {len(df)} 条")
        except Exception as exc:
            error_count += 1
            log(f"  ❌ period={period}: {str(exc)[:80]}", "ERROR")

    if not frames:
        log(f"\n完成: 成功 {success_count}, 空数据 {empty_count}, 错误 {error_count}")
        return {
            "ok": success_count > 0,
            "covered_target_date": success_count > 0,
            "success_count": success_count,
            "empty_count": empty_count,
            "error_count": error_count,
        }

    merged = pd.concat(frames, ignore_index=True)
    dedup_cols = [c for c in ["ts_code", "ann_date", "end_date"] if c in merged.columns]
    if dedup_cols:
        merged = merged.drop_duplicates(subset=dedup_cols, keep="last")
    else:
        merged = merged.drop_duplicates(keep="last")

    date_col = config.get("date_col", "ann_date")
    if date_col in merged.columns:
        for year, part in merged.groupby(merged[date_col].astype(str).str[:4]):
            filepath = output_dir / f"{config['prefix']}{year}.csv"
            if filepath.exists() and filepath.stat().st_size > 0:
                existing = pd.read_csv(filepath, low_memory=False)
                for col in [date_col, "end_date"]:
                    if col in existing.columns:
                        existing[col] = existing[col].astype(str).str.replace('-', '', regex=False)
                combined = pd.concat([existing, part], ignore_index=True)
            else:
                combined = part.copy()
            dedup_cols = [c for c in [date_col, "ts_code", "end_date"] if c in combined.columns]
            if dedup_cols:
                combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            sort_cols = [c for c in [date_col, "ts_code", "end_date"] if c in combined.columns]
            if sort_cols:
                combined = combined.sort_values(sort_cols)
            combined.to_csv(filepath, index=False)
            shared_write_multi_format_bundle(filepath, combined, interface_name="express")
            log(f"  ✅ 年文件 {year}: 落盘后 {len(combined)} 条")

    log(f"\n完成: 成功 {success_count}, 空数据 {empty_count}, 错误 {error_count}")
    return {
        "ok": success_count > 0,
        "covered_target_date": success_count > 0,
        "success_count": success_count,
        "empty_count": empty_count,
        "error_count": error_count,
    }


def fill_by_code_interface(interface_name, config, trade_dates, code_list=None, code_type='stock'):
    """补全按代码获取的接口数据（股票或指数）"""
    interface_started_at = time.monotonic()
    root_dir = get_root_dir(config)
    log_interface_banner(interface_name, '指数' if code_type == 'index' else '股票')
    
    output_dir = root_dir / config['path']
    output_dir.mkdir(parents=True, exist_ok=True)
    date_col = config.get('date_col', 'trade_date')
    save_granularity = config.get('save_granularity', 'stock')
    
    explicit_code_list = code_list is not None
    # 获取代码列表
    if code_list is None:
        try:
            if code_type == 'index':
                code_list = get_index_code_list(config)
            else:
                code_list = get_stock_code_list(config)
        except:
            log("无法获取代码列表", "ERROR")
            return False
    
    start_date = min(trade_dates)
    end_date = max(trade_dates)
    code_param = config.get('code_param')
    if not code_param:
        code_param = 'index_code' if code_type == 'index' else 'ts_code'
    if config.get("batch_per_trade_date_request"):
        unique_dates = sorted(set(str(d) for d in trade_dates))
        chunk_windows = [(date_text, date_text) for date_text in unique_dates]
    else:
        chunk_windows = build_fetch_windows(
            trade_dates,
            granularity=config.get('fetch_granularity', 'daily'),
            max_rows_per_call=config.get('max_rows_per_call'),
        )
    
    log(f"日期范围: {start_date} ~ {end_date}")
    log(f"代码数量: {len(code_list)}")
    log(f"请求分段: {len(chunk_windows)} 段")
    api_name = _get_api_name(interface_name, config)
    
    updated = 0
    no_data = 0
    errors = 0
    resumed_skipped = 0
    api_timeout_sec = int(config.get('api_timeout_sec', 30) or 30)
    api_process_timeout_sec = config.get('api_process_timeout_sec')
    progress_interval = max(1, int(config.get('progress_log_interval', 500) or 500))
    active_code_log_interval = int(config.get('active_code_log_interval', 0) or 0)
    live_progress_include_code = bool(config.get('live_progress_include_code', True))
    resume_enabled = bool(config.get('resume_code_state')) and not explicit_code_list
    resume_state = _load_code_resume_state(interface_name, start_date, end_date) if resume_enabled else {"codes": {}}
    resume_done_statuses = {"updated"}
    if resume_enabled:
        done_count = sum(
            1 for item in resume_state.get("codes", {}).values()
            if isinstance(item, dict) and item.get("status") in resume_done_statuses
        )
        if done_count:
            log(f"断点状态: 已记录 {done_count} 个完成代码，将跳过重复请求")
    
    batch_request_enabled = (
        bool(config.get("batch_stock_pool_request"))
        and not explicit_code_list
        and code_type == "stock"
        and len(code_list) > 1
    )
    batch_df = None
    if batch_request_enabled:
        try:
            truncation_limit = config.get("batch_row_limit")
            initial_batch_size = config.get("batch_initial_code_chunk_size") or len(code_list)
            initial_batch_size = max(1, min(int(initial_batch_size), len(code_list)))
            api_func = _get_api_func(interface_name, config)

            def _fetch_one_batch(codes_list, win_start, win_end):
                """请求一批股票的一个窗口。"""
                kwargs = {code_param: ','.join(codes_list), 'start_date': win_start, 'end_date': win_end}
                df = _call_api_with_timeout(api_func, timeout_sec=api_timeout_sec, **kwargs)
                return df if df is not None and not df.empty else None

            def _fetch_codes_recursive(codes_list, depth=0):
                batch_rows = []
                for window_start, window_end in chunk_windows:
                    df_part = _fetch_one_batch(codes_list, window_start, window_end)
                    if df_part is not None:
                        batch_rows.append(df_part)
                if not batch_rows:
                    return []
                batch_df_part = pd.concat(batch_rows, ignore_index=True)
                if truncation_limit and len(batch_df_part) >= int(int(truncation_limit) * 0.95) and len(codes_list) > 1:
                    log(
                        f"  批量股票池返回 {len(batch_df_part)} 条，接近阈值 {truncation_limit}，自动拆半重试 ({len(codes_list)} 只)",
                        "WARNING",
                    )
                    half = len(codes_list) // 2
                    return _fetch_codes_recursive(codes_list[:half], depth + 1) + _fetch_codes_recursive(codes_list[half:], depth + 1)
                return [batch_df_part]

            all_dfs = []
            total_batches = (len(code_list) + initial_batch_size - 1) // initial_batch_size
            log(
                f"批量股票池模式: {len(code_list)} 只分 {total_batches} 批，每批约 {initial_batch_size} 只"
                + (f" (行数阈值 {truncation_limit})" if truncation_limit else "")
            )
            batch_idx = 0
            start_idx = 0
            while start_idx < len(code_list):
                end_idx = min(start_idx + initial_batch_size, len(code_list))
                batch_codes = code_list[start_idx:end_idx]
                try:
                    batch_parts = _fetch_codes_recursive(batch_codes)
                except Exception as e:
                    if config.get("batch_split_on_error") and len(batch_codes) > 1:
                        log(f"  第 {batch_idx + 1} 批失败，自动拆半重试: {str(e)[:80]}", "WARNING")
                        half = len(batch_codes) // 2
                        batch_parts = []
                        for sub_codes in (batch_codes[:half], batch_codes[half:]):
                            if not sub_codes:
                                continue
                            try:
                                batch_parts.extend(_fetch_codes_recursive(sub_codes, 1))
                            except Exception as sub_exc:
                                log(f"    子批失败: {str(sub_exc)[:80]}", "ERROR")
                    else:
                        raise
                if batch_parts:
                    all_dfs.extend(batch_parts)
                    batch_rows_total = sum(len(item) for item in batch_parts)
                    log(f"  第 {batch_idx + 1} 批返回 {batch_rows_total} 条")
                else:
                    log(f"  第 {batch_idx + 1} 批无数据")
                batch_idx += 1
                start_idx = end_idx
            if all_dfs:
                batch_df = pd.concat(all_dfs, ignore_index=True)
                log(f"批量请求完成: 共返回 {len(batch_df)} 条，涉及 {batch_df[code_param].nunique()} 只股票")
            else:
                log("批量请求无数据，回退逐只请求")
                batch_df = None
        except Exception as e:
            log(f"批量请求失败，回退单只请求: {e}", "WARNING")
            batch_df = None

    # 年聚合模式：批量请求后直接按年写入，跳过单只循环
    if batch_df is not None and save_granularity == 'year':
        if not batch_df.empty:
            if date_col in batch_df.columns:
                batch_df[date_col] = batch_df[date_col].astype(str).str.replace('-', '', regex=False)
            for year, part in batch_df.groupby(batch_df[date_col].astype(str).str[:4]):
                filepath = output_dir / f"{config['prefix']}{year}.csv"
                if filepath.exists() and filepath.stat().st_size > 0:
                    existing = pd.read_csv(filepath, low_memory=False)
                    combined = pd.concat([existing, part], ignore_index=True)
                else:
                    combined = part.copy()
                dedup_cols = config.get('dedup_cols')
                if not dedup_cols:
                    dedup_cols = [c for c in [date_col, 'ts_code', 'end_date', 'f_ann_date', 'bz_item'] if c in combined.columns]
                if dedup_cols:
                    combined = combined.drop_duplicates(subset=dedup_cols, keep='last')
                sort_cols = [c for c in [date_col, 'ts_code', 'end_date', 'f_ann_date', 'bz_item'] if c in combined.columns]
                if sort_cols:
                    combined = combined.sort_values(sort_cols)
                combined.to_csv(filepath, index=False)
                shared_write_multi_format_bundle(filepath, combined, interface_name=interface_name)
                log(f"  ✅ 年文件 {year}: {len(part)} 条")
        log(f"📦 完成汇总: 年聚合写入完成 | 耗时 {format_duration(time.monotonic() - interface_started_at)}")
        return True

    for i, code in enumerate(code_list):
        if resume_enabled:
            record = resume_state.get("codes", {}).get(str(code))
            status = record.get("status") if isinstance(record, dict) else None
            if status == "in_progress":
                _mark_code_resume_state(resume_state, code, "timeout")
                _save_code_resume_state(interface_name, start_date, end_date, resume_state)
                status = "timeout"
            if status in resume_done_statuses:
                code_file = _resolve_code_storage_file(output_dir, config, code, end_date)
                latest_for_code = get_latest_date_fast(code_file, date_col=date_col) if code_file.exists() else None
                if not latest_for_code or str(latest_for_code) < str(end_date):
                    status = None
                    resume_state.get("codes", {}).pop(str(code), None)
                    _save_code_resume_state(interface_name, start_date, end_date, resume_state)
                else:
                    resumed_skipped += 1
                    if (i + 1) % progress_interval == 0:
                        log(
                            f"进度: {i+1}/{len(code_list)} | 有更新: {updated} | 无数据: {no_data} | "
                            f"错误: {errors} | 断点跳过: {resumed_skipped}"
                        )
                    continue
            _mark_code_resume_state(resume_state, code, "in_progress")
            _save_code_resume_state(interface_name, start_date, end_date, resume_state)
        if active_code_log_interval and (i == 0 or (i + 1) % active_code_log_interval == 0):
            log(
                f"{interface_name} | 请求代码: {i+1}/{len(code_list)} {code} | 有更新: {updated} | "
                f"无数据: {no_data} | 错误: {errors} | 断点跳过: {resumed_skipped}"
            )
        try:
            if live_progress_include_code:
                spinner_message = (
                    f"{interface_name}: 正在拉取 {code} ({i+1}/{len(code_list)}) | "
                    f"更新 {updated} 无数据 {no_data} 错误 {errors}"
                )
            else:
                spinner_message = (
                    f"{interface_name}: 正在拉取 | {i+1}/{len(code_list)} | "
                    f"更新 {updated} 无数据 {no_data} 错误 {errors}"
                )
            start_live_spinner(spinner_message)
            if batch_df is not None:
                code_rows = batch_df[batch_df[code_param] == code]
                df = code_rows.copy() if not code_rows.empty else None
            else:
                all_rows = []
                api_func = _get_api_func(interface_name, config)
                for window_start, window_end in chunk_windows:
                    request_kwargs = {
                        code_param: code,
                        'start_date': window_start,
                        'end_date': window_end,
                    }
                    if api_process_timeout_sec:
                        df_part = _call_api_in_process(
                            api_name,
                            timeout_sec=api_process_timeout_sec,
                            **request_kwargs,
                        )
                    else:
                        df_part = _call_api_with_timeout(
                            api_func,
                            timeout_sec=api_timeout_sec,
                            **request_kwargs,
                        )
                    if df_part is not None and not df_part.empty:
                        all_rows.append(df_part)
                    time.sleep(0.03)
                df = pd.concat(all_rows, ignore_index=True) if all_rows else None
            
            if df is not None and not df.empty:
                if date_col in df.columns:
                    df[date_col] = df[date_col].astype(str).str.replace('-', '', regex=False)

                if save_granularity == 'ymd_stock' and date_col in df.columns:
                    for ymd_date, part in df.groupby(date_col):
                        year = str(ymd_date)[:4]
                        month = str(ymd_date)[4:6]
                        day = str(ymd_date)[6:8]
                        day_dir = output_dir / year / month / day
                        day_dir.mkdir(parents=True, exist_ok=True)
                        filepath = day_dir / f"{config['prefix']}{code}.csv"

                        if filepath.exists() and filepath.stat().st_size > 0:
                            existing = pd.read_csv(filepath, low_memory=False)
                            merged = pd.concat([existing, part], ignore_index=True)
                        else:
                            merged = part.copy()

                        dedup_cols = config.get('dedup_cols')
                        if not dedup_cols:
                            dedup_cols = [c for c in [date_col, 'ts_code', 'end_date', 'f_ann_date', 'bz_item'] if c in merged.columns]
                        if dedup_cols:
                            merged = merged.drop_duplicates(subset=dedup_cols, keep='last')
                        else:
                            merged = merged.drop_duplicates(keep='last')
                        sort_cols = [c for c in [date_col, 'end_date', 'f_ann_date', 'bz_item'] if c in merged.columns]
                        if sort_cols:
                            merged = merged.sort_values(sort_cols)

                        merged.to_csv(filepath, index=False)
                        shared_write_multi_format_bundle(filepath, merged, interface_name=interface_name)
                elif save_granularity == 'year_stock' and date_col in df.columns:
                    for year, part in df.groupby(df[date_col].astype(str).str[:4]):
                        year_dir = output_dir / str(year)
                        year_dir.mkdir(parents=True, exist_ok=True)
                        filepath = year_dir / f"{config['prefix']}{code}.csv"

                        if filepath.exists() and filepath.stat().st_size > 0:
                            existing = pd.read_csv(filepath, low_memory=False)
                            merged = pd.concat([existing, part], ignore_index=True)
                        else:
                            merged = part.copy()

                        dedup_cols = config.get('dedup_cols')
                        if not dedup_cols:
                            dedup_cols = [c for c in [date_col, 'ts_code', 'price'] if c in merged.columns]
                        if dedup_cols:
                            merged = merged.drop_duplicates(subset=dedup_cols, keep='last')
                        else:
                            merged = merged.drop_duplicates(keep='last')
                        sort_cols = [c for c in [date_col, 'ts_code', 'price'] if c in merged.columns]
                        if sort_cols:
                            merged = merged.sort_values(sort_cols)

                        merged.to_csv(filepath, index=False)
                        shared_write_multi_format_bundle(filepath, merged, interface_name=interface_name)
                else:
                    filepath = output_dir / f"{config['prefix']}{code}.csv"
                    if filepath.exists():
                        existing = pd.read_csv(filepath)
                        if date_col in existing.columns and date_col in df.columns:
                            existing[date_col] = existing[date_col].astype(str).str.replace('-', '', regex=False)
                            existing = existing[~existing[date_col].isin(df[date_col].unique())]
                        combined = pd.concat([existing, df], ignore_index=True)
                        dedup_cols = config.get('dedup_cols') or [date_col]
                        dedup_cols = [c for c in dedup_cols if c in combined.columns]
                        if dedup_cols:
                            combined = combined.drop_duplicates(subset=dedup_cols, keep='last')
                        else:
                            combined = combined.drop_duplicates(keep='last')
                        if date_col in combined.columns:
                            combined = combined.sort_values(date_col)
                    else:
                        combined = df.sort_values(date_col) if date_col in df.columns else df

                    combined.to_csv(filepath, index=False)
                    shared_write_multi_format_bundle(filepath, combined, interface_name=interface_name)
                updated += 1
                if resume_enabled:
                    _mark_code_resume_state(resume_state, code, "updated")
            else:
                no_data += 1
                if resume_enabled:
                    _mark_code_resume_state(resume_state, code, "no_data")
                
        except Exception as e:
            errors += 1
            if resume_enabled:
                _mark_code_resume_state(resume_state, code, "error")
            stop_live_spinner(f"  ❌ {interface_name} {code}: {str(e)[:80]}", "ERROR")
        
        if (i + 1) % progress_interval == 0:
            stop_live_spinner()
            if resume_enabled:
                _save_code_resume_state(interface_name, start_date, end_date, resume_state)
            log(
                f"进度: {i+1}/{len(code_list)} | 有更新: {updated} | 无数据: {no_data} | "
                f"错误: {errors} | 断点跳过: {resumed_skipped}"
            )
        
        time.sleep(0.15)
        _save_code_resume_state(interface_name, start_date, end_date, resume_state)
    stop_live_spinner()
    log_interface_summary(
        updated=updated,
        no_data=no_data,
        errors=errors,
        resumed_skipped=resumed_skipped,
        duration_seconds=time.monotonic() - interface_started_at,
    )
    return updated > 0


def build_fetch_windows(trade_dates, granularity='daily', max_rows_per_call=None):
    """Build date windows that respect API row limits while keeping request count low."""
    if not trade_dates:
        return []

    unique_dates = sorted(set(str(d) for d in trade_dates))
    if len(unique_dates) == 1:
        date_text = unique_dates[0]
        return [(date_text, date_text)]

    if max_rows_per_call is None:
        max_rows_per_call = 8000 if granularity == 'daily' else 1000

    safety_margin = 0.85
    max_dates_per_call = max(1, int(max_rows_per_call * safety_margin))
    if granularity == 'weekly':
        max_dates_per_call = max(1, min(max_dates_per_call, 800))
    elif granularity == 'monthly':
        max_dates_per_call = max(1, min(max_dates_per_call, 480))

    windows = []
    for start_idx in range(0, len(unique_dates), max_dates_per_call):
        chunk = unique_dates[start_idx:start_idx + max_dates_per_call]
        windows.append((chunk[0], chunk[-1]))
    return windows


def _is_missing_metric(value):
    if pd.isna(value):
        return True
    return str(value).strip().lower() in {"", "nan", "none", "null"}


def _is_valid_yyyymmdd(value):
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        return False
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return False
    return True


def fill_cyq_chips_by_stock(config, trade_dates):
    """补全 cyq_chips 数据（按股票并行增量补齐）"""
    import subprocess as sp
    import json
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed

    root_dir = get_root_dir(config)
    output_dir = root_dir / config['path']
    output_dir.mkdir(parents=True, exist_ok=True)
    target_trade_dates = sorted(set(str(item) for item in trade_dates))
    end_date = max(target_trade_dates)
    log(f"\n{'='*60}")
    log(f"补全 cyq_chips (并行增量版)")
    log('='*60)
    log(f"目标日期: {end_date}")

    missing = []
    for ts_code in get_stock_code_list(config):
        try:
            code_file = _resolve_code_storage_file(output_dir, config, ts_code, end_date)
            if not code_file.exists():
                missing.append({'ts_code': ts_code, 'trade_dates': list(target_trade_dates)})
                continue
            latest = get_latest_date_fast(code_file, date_col=config['date_col'])
            if not latest:
                missing.append({'ts_code': ts_code, 'trade_dates': list(target_trade_dates)})
                continue
            latest = str(latest)
            if latest < end_date:
                gap_trade_dates = get_trade_dates(start_date=latest, end_date=end_date)
                gap_trade_dates = [str(item) for item in gap_trade_dates if str(item) > latest]
                if gap_trade_dates:
                    missing.append({'ts_code': ts_code, 'trade_dates': gap_trade_dates})
        except Exception:
            missing.append({'ts_code': ts_code, 'trade_dates': list(target_trade_dates)})

    if not missing:
        log("✅ cyq_chips: 已全部是最新")
        return True

    log(f"需要补齐的股票: {len(missing)}")

    script_template = '''
import sys, json
sys.path.insert(0, "/Users/penghongming/agent-skills/custom/tushare_pro")
from utils.tushare_client import create_pro_api
from utils.paths import get_stock_data_dir
from core.files import write_multi_format_bundle
import pandas as pd
from pathlib import Path

batch = json.loads(sys.argv[1])
end_date = sys.argv[2]
prefix = sys.argv[3]
page_limit = int(sys.argv[4])
max_pages = int(sys.argv[5])
retry_per_trade_date = int(sys.argv[6])
pro = create_pro_api(timeout=15)
import time
stock_root = Path(get_stock_data_dir())
requested_codes = [item["ts_code"] for item in batch]
requested_trade_dates_by_code = {item["ts_code"]: item.get("trade_dates") or [] for item in batch}
trade_date_to_codes = {}
for item in batch:
    ts_code = item["ts_code"]
    for trade_date in item.get("trade_dates") or []:
        trade_date_to_codes.setdefault(str(trade_date), []).append(ts_code)

code_frames = {}
try:
    for trade_date, codes in trade_date_to_codes.items():
        unique_codes = sorted(set(codes))
        day_df = None
        for retry_idx in range(max(1, retry_per_trade_date)):
            page_parts = []
            for page_idx in range(max(1, max_pages)):
                offset = page_idx * max(1, page_limit)
                part = pro.cyq_chips(
                    ts_code=",".join(unique_codes),
                    trade_date=trade_date,
                    limit=max(1, page_limit),
                    offset=offset,
                )
                if part is None or part.empty:
                    break
                page_parts.append(part)
                if len(part) < max(1, page_limit):
                    break
                time.sleep(0.05)
            if page_parts:
                day_df = pd.concat(page_parts, ignore_index=True)
                break
            time.sleep(0.2 * (retry_idx + 1))
        if day_df is None or day_df.empty:
            continue
        day_df["trade_date"] = day_df["trade_date"].astype(str)
        for ts_code, code_part in day_df.groupby("ts_code"):
            code_frames.setdefault(ts_code, []).append(code_part.copy())

    for ts_code in requested_codes:
        parts = code_frames.get(ts_code, [])
        if not parts:
            print(f"{ts_code}:EMPTY")
            continue
        df = pd.concat(parts, ignore_index=True)
        df["trade_date"] = df["trade_date"].astype(str)
        requested_dates = set(requested_trade_dates_by_code.get(ts_code, []))
        if requested_dates:
            df = df[df["trade_date"].isin(requested_dates)]
        if df.empty:
            print(f"{ts_code}:EMPTY")
            continue
        df = df.drop_duplicates(subset=["ts_code", "trade_date", "price"], keep="last")
        for year, part in df.groupby(df["trade_date"].astype(str).str[:4]):
            year_dir = stock_root / "cyq_chips" / str(year)
            year_dir.mkdir(parents=True, exist_ok=True)
            fp = year_dir / f"{prefix}{ts_code}.csv"
            if fp.exists() and fp.stat().st_size > 0:
                ex = pd.read_csv(fp)
            else:
                ex = None
            if ex is not None:
                ex["trade_date"] = ex["trade_date"].astype(str)
                ex = ex[~ex["trade_date"].isin(part["trade_date"].unique())]
                combined = pd.concat([ex, part], ignore_index=True)
            else:
                combined = part.copy()
            combined = combined.drop_duplicates(subset=["ts_code", "trade_date", "price"], keep="last")
            combined = combined.sort_values(["trade_date", "price"]).reset_index(drop=True)
            combined.to_csv(fp, index=False)
            write_multi_format_bundle(str(fp), combined, interface_name="cyq_chips")
        print(f"{ts_code}:OK")
except Exception as e:
    for ts_code in requested_codes:
        print(f"{ts_code}:ERR:{str(e)[:40]}")
'''

    page_limit = int(config.get("page_limit", 2000))
    page_limit = max(1, min(page_limit, 2000))
    max_pages = int(config.get("max_pages", 8))
    max_pages = max(1, max_pages)
    configured_batch_size = int(config.get("batch_size", 0) or 0)
    estimated_rows_per_code = int(config.get("estimated_rows_per_code", 100) or 100)
    estimated_rows_per_code = max(1, estimated_rows_per_code)
    target_pages_per_batch = int(config.get("target_pages_per_batch", 1) or 1)
    target_pages_per_batch = max(1, target_pages_per_batch)
    if configured_batch_size > 0:
        batch_size = configured_batch_size
        batch_reason = f"固定 batch_size={batch_size}"
    else:
        row_budget = max(1, page_limit * target_pages_per_batch)
        batch_size = max(1, row_budget // estimated_rows_per_code)
        batch_reason = (
            f"按预估 {estimated_rows_per_code} 行/股自动拆批 "
            f"(page_limit={page_limit}, target_pages={target_pages_per_batch})"
        )
    batch_size = min(len(missing), max(1, batch_size))
    batch_timeout = int(config.get("batch_timeout_sec", 600))
    retry_timeout = int(config.get("retry_timeout_sec", 180))
    retry_per_trade_date = int(config.get("retry_per_trade_date", 2))
    retry_per_trade_date = max(1, retry_per_trade_date)
    parallel_batches = int(config.get("parallel_batches", 4))
    parallel_batches = max(1, parallel_batches)
    batches = [missing[i:i+batch_size] for i in range(0, len(missing), batch_size)]
    log(f"批次策略: 每批 {batch_size} 只 | 共 {len(batches)} 批 | {batch_reason}")
    total_ok = total_empty = total_err = 0
    retry_items = []
    processed = 0

    def _run_batch(batch):
        payload = json.dumps(batch)
        try:
            result = sp.run(
                [
                    'python3',
                    '-c',
                    script_template,
                    payload,
                    end_date,
                    config['prefix'],
                    str(page_limit),
                    str(max_pages),
                    str(retry_per_trade_date),
                ],
                capture_output=True,
                text=True,
                timeout=batch_timeout,
            )
            return {"timeout": False, "batch": batch, "stdout": result.stdout}
        except sp.TimeoutExpired:
            return {"timeout": True, "batch": batch, "stdout": ""}

    for wave_start in range(0, len(batches), parallel_batches):
        wave = batches[wave_start: wave_start + parallel_batches]
        with ThreadPoolExecutor(max_workers=len(wave)) as executor:
            future_map = {executor.submit(_run_batch, batch): batch for batch in wave}
            for future in as_completed(future_map):
                result = future.result()
                batch = result["batch"]
                processed += len(batch)
                if result["timeout"]:
                    retry_items.extend(batch)
                    total_err += len(batch)
                    log(
                        f"进度: {processed}/{len(missing)} | 本批超时(>{batch_timeout}s), "
                        f"转入逐只重试 {len(batch)} 只 | 累计 OK:{total_ok} EMPTY:{total_empty} ERR:{total_err}"
                    )
                    continue

                ok = empty = err = 0
                err_codes = []
                for line in result["stdout"].strip().split('\n'):
                    line = line.strip()
                    if not line or ':' not in line or line.startswith('['):
                        continue
                    parts = line.split(':', 2)
                    status = parts[1] if len(parts) >= 2 else ''
                    if status == 'OK':
                        ok += 1
                    elif status == 'EMPTY':
                        empty += 1
                    else:
                        err += 1
                        if len(parts) >= 1:
                            err_codes.append(parts[0])
                if err_codes:
                    for item in batch:
                        if item['ts_code'] in err_codes:
                            retry_items.append(item)

                total_ok += ok
                total_empty += empty
                total_err += err
                log(
                    f"进度: {processed}/{len(missing)} | 本批 OK:{ok} EMPTY:{empty} ERR:{err} | "
                    f"累计 OK:{total_ok} EMPTY:{total_empty} ERR:{total_err}"
                )

        if wave_start + parallel_batches < len(batches):
            time.sleep(0.2)

    if retry_items:
        log(f"开始重试 {len(retry_items)} 只失败股票...")
        for item in retry_items:
            payload = json.dumps([item])
            try:
                result = sp.run(
                    [
                        'python3',
                        '-c',
                        script_template,
                        payload,
                        end_date,
                        config['prefix'],
                        str(page_limit),
                        str(max_pages),
                        str(retry_per_trade_date),
                    ],
                    capture_output=True, text=True, timeout=retry_timeout
                )
            except sp.TimeoutExpired:
                continue
            for line in result.stdout.strip().split('\n'):
                line = line.strip()
                if not line or ':' not in line or line.startswith('['):
                    continue
                parts = line.split(':', 2)
                status = parts[1] if len(parts) >= 2 else ''
                if status == 'OK':
                    total_ok += 1
                    total_err = max(0, total_err - 1)
                elif status == 'EMPTY':
                    total_empty += 1
                    total_err = max(0, total_err - 1)
            time.sleep(1)
        log(f"重试后累计 OK:{total_ok} EMPTY:{total_empty} ERR:{total_err}")

    log(f"\n完成: OK:{total_ok} EMPTY:{total_empty} ERR:{total_err}")
    return total_ok > 0


def fill_cyq_perf_hybrid(config, trade_dates):
    """补全 cyq_perf 数据 - 混合策略（按日期批量分页拉取 + 逐只补齐）"""
    root_dir = get_root_dir(config)
    output_dir = root_dir / config['path']
    output_dir.mkdir(parents=True, exist_ok=True)

    log(f"\n{'='*60}")
    log(f"补全 cyq_perf (混合策略: 按日期批量+逐只补齐)")
    log('='*60)

    # 预扫描所有股票文件的已有日期
    log("预扫描本地文件...")
    stock_dates_map = {}
    for f in output_dir.glob(f"{config['prefix']}*.csv"):
        code = f.name[len(config['prefix']):-4]
        try:
            df = pd.read_csv(f, usecols=[config['date_col']])
            stock_dates_map[code] = set(df[config['date_col']].astype(str).tolist())
        except Exception:
            stock_dates_map[code] = set()

    all_codes = set(stock_dates_map.keys())
    updated_total = 0
    empty_total = 0

    for trade_date in trade_dates:
        missing_codes = [code for code, dates in stock_dates_map.items() if trade_date not in dates]
        if not missing_codes:
            log(f"✅ {trade_date}: 所有股票已是最新")
            continue

        log(f"🔄 {trade_date}: 缺失 {len(missing_codes)} 只股票")

        # 1. 批量拉取: offset=0 和 offset=5000
        all_rows = []
        for offset in [0, 5000]:
            try:
                df = pro.cyq_perf(trade_date=trade_date, limit=5000, offset=offset)
                if df is not None and not df.empty:
                    df[config['date_col']] = df[config['date_col']].astype(str)
                    all_rows.append(df)
                time.sleep(0.5)
            except Exception as e:
                log(f"  ❌ 批量获取 {trade_date} offset={offset} 失败: {e}")
                break

        if not all_rows:
            log(f"  ⚠️ {trade_date}: 无数据返回")
            continue

        bulk_df = pd.concat(all_rows, ignore_index=True)
        bulk_df = bulk_df.drop_duplicates(subset=['ts_code', config['date_col']], keep='last')
        fetched_codes = set(bulk_df['ts_code'].unique())

        # 2. 合并批量数据到各股票文件
        day_updated = 0
        for ts_code, rows in bulk_df.groupby('ts_code'):
            if ts_code not in all_codes:
                filepath = output_dir / f"{config['prefix']}{ts_code}.csv"
                rows = rows.sort_values([config['date_col'], 'ts_code']).reset_index(drop=True)
                rows.to_csv(filepath, index=False)
                shared_write_multi_format_bundle(filepath, rows, interface_name="cyq_perf")
                day_updated += 1
                stock_dates_map[ts_code] = {trade_date}
            else:
                filepath = output_dir / f"{config['prefix']}{ts_code}.csv"
                try:
                    if filepath.exists() and filepath.stat().st_size == 0:
                        rows = rows.sort_values([config['date_col'], 'ts_code']).reset_index(drop=True)
                        rows.to_csv(filepath, index=False)
                        shared_write_multi_format_bundle(filepath, rows, interface_name="cyq_perf")
                    else:
                        existing = pd.read_csv(filepath)
                        existing[config['date_col']] = existing[config['date_col']].astype(str)
                        existing = existing[existing[config['date_col']] != trade_date]
                        combined = pd.concat([existing, rows], ignore_index=True)
                        combined = combined.drop_duplicates(subset=[config['date_col']], keep='last')
                        combined = combined.sort_values(config['date_col'])
                        combined.to_csv(filepath, index=False)
                        shared_write_multi_format_bundle(filepath, combined, interface_name="cyq_perf")
                    day_updated += 1
                    stock_dates_map[ts_code].add(trade_date)
                except Exception as e:
                    log(f"  ❌ 合并 {ts_code} 失败: {e}")

        # 3. 逐只补全未被覆盖的缺失股票
        remaining = [c for c in missing_codes if c not in fetched_codes]
        if remaining:
            log(f"  🔄 逐只补全 {len(remaining)} 只股票...")
            single_updated = 0
            single_empty = 0
            for code in remaining:
                try:
                    df = pro.cyq_perf(ts_code=code, start_date=trade_date, end_date=trade_date)
                    if df is not None and not df.empty:
                        df[config['date_col']] = df[config['date_col']].astype(str)
                        filepath = output_dir / f"{config['prefix']}{code}.csv"
                        if filepath.exists() and filepath.stat().st_size == 0:
                            df = df.sort_values([config['date_col'], 'ts_code']).reset_index(drop=True)
                            df.to_csv(filepath, index=False)
                            shared_write_multi_format_bundle(filepath, df, interface_name="cyq_perf")
                        else:
                            if filepath.exists():
                                existing = pd.read_csv(filepath)
                                existing[config['date_col']] = existing[config['date_col']].astype(str)
                                existing = existing[existing[config['date_col']] != trade_date]
                                combined = pd.concat([existing, df], ignore_index=True)
                                combined = combined.drop_duplicates(subset=[config['date_col']], keep='last')
                                combined = combined.sort_values(config['date_col'])
                            else:
                                combined = df.sort_values(config['date_col'])
                            combined.to_csv(filepath, index=False)
                            shared_write_multi_format_bundle(filepath, combined, interface_name="cyq_perf")
                        single_updated += 1
                        stock_dates_map[code].add(trade_date)
                    else:
                        single_empty += 1
                except Exception:
                    pass
                time.sleep(0.2)
            day_updated += single_updated
            empty_total += single_empty
            log(f"  逐只补: 更新 {single_updated}, 无数据 {single_empty}")

        updated_total += day_updated
        log(f"  ✅ 当日更新 {day_updated}/{len(missing_codes)} 只")
        time.sleep(0.3)

    log(f"\n完成: 更新 {updated_total}, 无数据 {empty_total}")
    return updated_total > 0


def fill_margin_detail_by_date(config, trade_dates):
    """
    优化版 margin_detail 填充 - 使用按日期全量获取（而非逐个股票）
    Tushare的margin_detail接口支持trade_date参数返回当天所有股票数据
    """
    root_dir = get_root_dir(config)
    output_dir = root_dir / config['path']
    output_dir.mkdir(parents=True, exist_ok=True)
    
    log(f"\n{'='*60}")
    log(f"补全 margin_detail (优化版 - 按日期全量获取)")
    log('='*60)
    
    total_updated = 0
    successful_dates = set()
    stock_code_set = set(get_stock_code_list(config))
    
    for target_date in trade_dates:
        log(f"\n📅 处理日期: {target_date}")
        
        try:
            # 分页拉取该日期的所有股票数据
            all_rows = []
            for offset in [0, 6000]:
                try:
                    df_page = pro.margin_detail(trade_date=target_date, limit=6000, offset=offset)
                    if df_page is not None and not df_page.empty:
                        all_rows.append(df_page)
                except Exception as e:
                    log(f"  ❌ 批量获取 {target_date} offset={offset} 失败: {e}")
                    break
                time.sleep(0.3)
            
            if not all_rows:
                log(f"  ⚪ 该日期无数据")
                continue
            
            df_all = pd.concat(all_rows, ignore_index=True)
            if 'ts_code' in df_all.columns:
                df_all = df_all[df_all['ts_code'].astype(str).isin(stock_code_set)]
            df_all = df_all.drop_duplicates(subset=['ts_code', config['date_col']], keep='last')
            
            log(f"  ✅ API返回 {len(df_all)} 条数据")
            
            # 按股票代码分组保存
            updated = 0
            skipped = 0
            
            for ts_code, group in df_all.groupby('ts_code'):
                group = group.copy()
                group[config['date_col']] = group[config['date_col']].astype(str)
                year = str(target_date)[:4]
                year_dir = output_dir / year
                year_dir.mkdir(parents=True, exist_ok=True)
                filepath = year_dir / f"{config['prefix']}{ts_code}.csv"

                if filepath.exists() and filepath.stat().st_size > 0:
                    existing = pd.read_csv(filepath, low_memory=False)
                    existing[config['date_col']] = existing[config['date_col']].astype(str)
                    if str(target_date) in set(existing[config['date_col']].astype(str)):
                        skipped += 1
                        continue
                    combined = pd.concat([existing, group], ignore_index=True)
                else:
                    combined = group

                dedup_cols = config.get('dedup_cols') or [
                    c for c in [config['date_col'], 'ts_code', 'rzye', 'rqye'] if c in combined.columns
                ]
                combined = combined.drop_duplicates(subset=dedup_cols, keep='last')
                combined = combined.sort_values(config['date_col'])
                combined.to_csv(filepath, index=False)
                shared_write_multi_format_bundle(filepath, combined, interface_name='margin_detail')
                updated += 1
            
            log(f"  ✅ 更新: {updated}, 跳过(已有): {skipped}")
            total_updated += updated
            if updated > 0 or skipped > 0:
                successful_dates.add(str(target_date))
            
        except Exception as e:
            log(f"  ❌ 错误: {str(e)[:80]}", "ERROR")
            # 如果全量获取失败，降级到逐个获取
            log(f"  🔄 降级到逐个股票获取...")
            fill_by_code_interface('margin_detail', config, [target_date])
    
    log(f"\n完成: 总共更新 {total_updated} 只股票")
    target_trade_date = str(max(trade_dates)) if trade_dates else None
    return {
        "ok": bool(successful_dates),
        "covered_target_date": bool(target_trade_date and target_trade_date in successful_dates),
        "updated": total_updated,
    }

def fill_index_weight(config, trade_dates):
    """补全指数成分权重 - 按日期保存为总文件"""
    root_dir = get_root_dir(config)
    output_dir = root_dir / config['path']
    output_dir.mkdir(parents=True, exist_ok=True)
    
    log(f"\n{'='*60}")
    log(f"补全 index_weight (指数)")
    log('='*60)
    
    success_count = 0
    empty_count = 0
    error_count = 0
    
    for trade_date in trade_dates:
        try:
            df = pro.index_weight(trade_date=trade_date)
            
            if df is not None and not df.empty:
                filepath = output_dir / f"index_weight_{trade_date}.csv"
                
                if filepath.exists():
                    existing = pd.read_csv(filepath)
                    combined = pd.concat([existing, df], ignore_index=True)
                    combined = combined.drop_duplicates(subset=['index_code', 'con_code', 'trade_date'], keep='last')
                    combined = combined.sort_values(['index_code', 'con_code'])
                else:
                    combined = df.sort_values(['index_code', 'con_code'])
                
                combined.to_csv(filepath, index=False)
                shared_write_multi_format_bundle(filepath, combined, interface_name="index_weight")
                success_count += 1
                log(f"  ✅ {trade_date}: {len(df)} 条")
            else:
                empty_count += 1
                log(f"  ⚪ {trade_date}: 无数据")
                
        except Exception as e:
            error_count += 1
            log(f"  ❌ {trade_date}: {str(e)[:80]}")
        
        time.sleep(0.3)
    
    log(f"\n完成: 成功 {success_count}, 空数据 {empty_count}, 错误 {error_count}")
    return success_count > 0

def deduplicate_interface(interface_name, config, calendar_dates=None):
    """去重接口数据"""
    root_dir = get_root_dir(config)
    log(f"\n去重 {interface_name}...")
    
    path = root_dir / config['path']
    if not path.exists():
        return 0
    
    file_iter = (
        path.rglob(f"{config['prefix']}*.csv")
        if config.get("partition_by_year_dir", False)
        else path.glob(f"{config['prefix']}*.csv")
    )
    files = list(file_iter)
    files = [f for f in files if '_metadata' not in f.name]
    if calendar_dates:
        years = {str(d)[:4] for d in calendar_dates}
        filtered_files = []
        for f in files:
            try:
                relative_parts = f.relative_to(path).parts
            except Exception:
                relative_parts = ()
            if relative_parts and relative_parts[0] in years:
                filtered_files.append(f)
            elif not relative_parts:
                filtered_files.append(f)
        if filtered_files and len(filtered_files) < len(files):
            log(f"  ⚡ 去重范围按日期缩小: {len(filtered_files)}/{len(files)} 个文件")
            files = filtered_files
    
    total_removed = 0
    checked = 0
    skipped = 0
    allowed_dates = {str(d) for d in calendar_dates} if calendar_dates else None
    
    dedup_cols = config.get('dedup_cols')
    if not dedup_cols:
        if config.get("date_col"):
            dedup_cols = [config["date_col"]]
        elif interface_name == "ths_index":
            dedup_cols = ["ts_code"]
        elif interface_name == "ths_member":
            dedup_cols = ["ts_code", "con_code"]
        elif config.get("fixed_file_name"):
            dedup_cols = ["ts_code", "index_code", "con_code", "name"]
        else:
            dedup_cols = ["ts_code"]
    for f in files:
        if allowed_dates and not _csv_file_may_contain_dates(f, config, allowed_dates):
            skipped += 1
            continue
        removed = deduplicate_file(f, dedup_cols, keep='last')
        if removed > 0:
            try:
                refreshed = pd.read_csv(f, low_memory=False)
                shared_write_multi_format_bundle(f, refreshed, interface_name=interface_name)
            except Exception as exc:
                log(f"  ⚠️ {f.name}: parquet 刷新失败: {str(exc)[:80]}", "WARNING")
        total_removed += removed
        checked += 1
        
        if checked % 1000 == 0:
            log(f"  进度: {checked}/{len(files)}")
    
    if skipped:
        log(f"  ⚡ 去重跳过无目标日期文件: {skipped}/{len(files)}")
    log(f"  ✅ 检查 {checked} 个文件，去重 {total_removed} 条")
    return total_removed


def _csv_file_may_contain_dates(filepath, config, allowed_dates):
    date_col = config.get("date_col", "trade_date")
    prefix = config.get("prefix", "")
    file_date = None
    if prefix:
        match = re.search(rf"{re.escape(prefix)}(\d{{8}})", filepath.name)
        if match:
            file_date = match.group(1)
    if file_date:
        return file_date in allowed_dates
    try:
        latest = get_latest_date_fast(filepath)
    except Exception:
        return True
    if latest is None:
        return True
    latest = str(latest)
    try:
        header = pd.read_csv(filepath, nrows=0)
        if date_col not in header.columns:
            return True
        first = pd.read_csv(filepath, usecols=[date_col], nrows=1)
        if first.empty:
            return True
        first_date = str(first[date_col].iloc[0])
        return any(first_date <= date_value <= latest for date_value in allowed_dates)
    except Exception:
        return True


def _format_short_list(items, limit=8):
    values = list(items)
    if not values:
        return "-"
    if len(values) <= limit:
        return ", ".join(map(str, values))
    head = ", ".join(map(str, values[:limit]))
    return f"{head} ... (+{len(values) - limit})"


def _log_health_report(interface_name, report):
    incomplete_dates = report.get("dates", [])
    empty_codes = report.get("empty_codes", [])
    codes_by_date = report.get("codes_by_date", {})
    if not incomplete_dates and not empty_codes:
        log(f"  ✅ {interface_name}: 未发现缺参/坏行")
        return

    log(
        f"  ⚠️ {interface_name}: 不完整日期 {len(incomplete_dates)} 个, 空文件/坏文件代码 {len(empty_codes)} 个",
        "WARNING",
    )
    if incomplete_dates:
        log(f"    日期: {_format_short_list(incomplete_dates)}", "WARNING")
    if empty_codes:
        log(f"    代码: {_format_short_list(empty_codes)}", "WARNING")
    sample_pairs = []
    for trade_date, codes in codes_by_date.items():
        if not codes:
            continue
        sample_pairs.append(f"{trade_date}:{len(codes)}")
    if sample_pairs:
        log(f"    按日期缺参代码数: {_format_short_list(sample_pairs)}", "WARNING")


def _dispatch_fill(interface_name, config, trade_dates, code_list=None, code_type=None):
    if not trade_dates and not code_list:
        return False

    if code_type == "index":
        if interface_name == "index_weight":
            return fill_index_weight(config, trade_dates)
        return fill_by_code_interface(
            interface_name,
            config,
            trade_dates,
            code_list=code_list,
            code_type="index",
        )

    # cyq_perf 的旧 hybrid 逻辑仅用于历史“扁平按股票”模式。
    # 年/个股与年/月/日模式统一走通用 by_date 写入路径，避免写回根目录扁平文件。
    if interface_name == "cyq_perf" and config.get("save_granularity") == "stock":
        return fill_cyq_perf_hybrid(config, trade_dates)
    if interface_name == "dc_concept":
        handler = THEME_HANDLERS.get("dc_concept")
        return handler(config, trade_dates) if handler else False
    if interface_name == "kpl_concept_cons":
        handler = THEME_HANDLERS.get("kpl_concept_cons")
        return handler(config, trade_dates) if handler else False
    if interface_name == "dc_concept_cons":
        handler = THEME_HANDLERS.get("dc_concept_cons")
        return handler(config, trade_dates) if handler else False
    if interface_name == "ths_index":
        handler = THEME_HANDLERS.get("ths_index")
        return handler(config, trade_dates) if handler else False
    if interface_name == "ths_member":
        handler = THEME_HANDLERS.get("ths_member")
        return handler(config, trade_dates) if handler else False
    if interface_name == "ths_daily":
        handler = THEME_HANDLERS.get("ths_daily")
        return handler(config, trade_dates) if handler else False
    if interface_name == "dc_daily":
        handler = THEME_HANDLERS.get("dc_daily")
        return handler(config, trade_dates) if handler else False
    if interface_name == "dc_index":
        handler = THEME_HANDLERS.get("dc_index")
        return handler(config, trade_dates) if handler else False
    if interface_name == "dc_member":
        handler = THEME_HANDLERS.get("dc_member")
        return handler(config, trade_dates) if handler else False
    if interface_name == "margin_detail":
        return fill_margin_detail_by_date(config, trade_dates)
    if interface_name == "stk_mins":
        return fill_stk_mins_single_stock(config, trade_dates)
    if interface_name == "disclosure_date":
        return fill_disclosure_date_by_announcement_date(config, trade_dates)
    if interface_name == "express":
        return fill_express_vip_by_period(config, trade_dates)
    if interface_name == "pledge_stat":
        return fill_pledge_stat_by_end_date(config, trade_dates)
    if interface_name == "cyq_chips":
        return fill_cyq_chips_by_stock(config, trade_dates)
    if code_type == "stock":
        return fill_by_code_interface(
            interface_name,
            config,
            trade_dates,
            code_list=code_list,
            code_type="stock",
        )
    return fill_by_date_interface(interface_name, config, trade_dates)


def resolve_stable_trade_date(config, trade_calendar):
    """Choose a safer anchor date so late-publishing interfaces do not chase today's empty data."""
    if not trade_calendar:
        return None
    lag_days = int(config.get('stable_lag_trade_days', 0))
    lag_days = max(0, lag_days)
    if lag_days >= len(trade_calendar):
        lag_days = len(trade_calendar) - 1
    return trade_calendar[-1 - lag_days]


def get_report_target_date(config, trade_calendar):
    """Only daily-granularity interfaces should be compared to the stable trade date."""
    if not config.get("calendar_aligned", True):
        return None
    granularity = config.get("fetch_granularity", "daily")
    if granularity in {"weekly", "monthly"}:
        return None
    return resolve_stable_trade_date(config, trade_calendar)


def resolve_run_target_trade_date(trade_calendar, now=None, market_close_target_hour=15):
    """
    Resolve the trade date this run should chase.

    We should only chase today's trade date when:
    - today is actually a trade date in the calendar, and
    - current time is at or after the configured market close cutoff.

    Otherwise, target the previous completed trade day from the trade calendar.
    """
    if not trade_calendar:
        return None

    now = now or datetime.now()
    latest_trade_date = trade_calendar[-1]
    today_str = now.strftime("%Y%m%d")
    is_today_trade_date = today_str in set(trade_calendar)

    if (
        is_today_trade_date
        and latest_trade_date == today_str
        and (now.hour, now.minute, now.second) < (market_close_target_hour, 0, 0)
        and len(trade_calendar) > 1
    ):
        return trade_calendar[-2]
    return latest_trade_date


def _limit_health_report_to_calendar(report, calendar_dates):
    """Limit health report to dates inside the given trade-calendar slice."""
    if not report:
        return report
    allowed = set(calendar_dates or [])
    if not allowed:
        return report

    limited = dict(report)
    limited_dates = [d for d in report.get("dates", []) if d in allowed]
    codes_by_date = {
        d: codes
        for d, codes in report.get("codes_by_date", {}).items()
        if d in allowed
    }
    limited["dates"] = sorted(set(limited_dates))
    limited["codes_by_date"] = dict(sorted(codes_by_date.items()))
    return limited


def _collect_code_latest_dates(config):
    """Build {ts_code: latest_trade_date} for code-based interfaces."""
    root_dir = get_root_dir(config)
    path = root_dir / config["path"]
    code_cache_subdir = config.get("code_cache_subdir")
    if code_cache_subdir:
        path = path / code_cache_subdir
    fallback_path = root_dir / config["path"]
    if not path.exists() and not fallback_path.exists():
        return {}

    if path.exists():
        file_iter = (
            path.rglob(f"{config['prefix']}*.csv")
            if config.get("partition_by_year_dir", False)
            else path.glob(f"{config['prefix']}*.csv")
        )
    else:
        file_iter = []
    if fallback_path.exists():
        file_iter = list(file_iter) + list(fallback_path.glob(f"{config['prefix']}*.csv"))
    latest_by_code = {}
    prefix = config["prefix"]
    for f in file_iter:
        if "_metadata" in f.name:
            continue
        if not f.name.startswith(prefix) or not f.name.endswith(".csv"):
            continue
        ts_code = f.name[len(prefix) : -4]
        if re.fullmatch(r"\d{8}", ts_code or ""):
            # date-partitioned files are not code cache files
            continue
        latest = get_latest_date_fast(f)
        latest = str(latest) if latest is not None else None
        if not latest or latest.lower() == "nan":
            latest = None
        latest_by_code[ts_code] = latest
    return latest_by_code


def _fetch_untradable_dates(ts_code, dates):
    """
    Return dates where symbol should not be expected to have cyq_chips rows.
    Rule:
    1) suspend_d with suspend_type == 'S'
    2) daily has no row on that date (treat as no-trade fallback)
    """
    if not dates:
        return set()
    dates = sorted(set(str(d) for d in dates))
    start_date, end_date = dates[0], dates[-1]
    untradable = set()
    wanted = set(dates)

    try:
        suspend_df = pro.suspend_d(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if suspend_df is not None and not suspend_df.empty and "trade_date" in suspend_df.columns:
            suspend_df["trade_date"] = suspend_df["trade_date"].astype(str)
            if "suspend_type" in suspend_df.columns:
                for _, row in suspend_df.iterrows():
                    d = str(row.get("trade_date", ""))
                    if d in wanted and str(row.get("suspend_type", "")).upper() == "S":
                        untradable.add(d)
            else:
                # Older schemas may not expose suspend_type; treat returned rows as suspended trade dates.
                untradable.update(d for d in suspend_df["trade_date"].tolist() if d in wanted)
    except Exception as exc:
        log(f"  ⚠️ suspend_d 校验失败({ts_code}): {str(exc)[:80]}", "WARNING")

    try:
        daily_df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        traded = set()
        if daily_df is not None and not daily_df.empty and "trade_date" in daily_df.columns:
            traded = set(daily_df["trade_date"].astype(str).tolist())
        untradable.update(d for d in wanted if d not in traded)
    except Exception as exc:
        log(f"  ⚠️ daily 校验失败({ts_code}): {str(exc)[:80]}", "WARNING")

    return untradable


def _suppress_cyq_chips_non_trading_gaps(config, missing_dates, health_report, calendar_dates=None):
    """
    For cyq_chips only: suppress gaps that are explained by suspend_d/daily no-trade.
    This reduces false lag alerts for suspended or non-trading symbols.
    """
    if not missing_dates:
        return missing_dates, health_report, []

    max_codes = int(config.get("suspend_check_max_codes", 200))
    max_codes = max(1, max_codes)
    latest_by_code = _collect_code_latest_dates(config)
    if not latest_by_code:
        return missing_dates, health_report, []

    candidates = []
    missing_set = set(missing_dates)
    for ts_code, latest in latest_by_code.items():
        if latest is None or latest.lower() == "nan":
            gap_dates = sorted(missing_set)
        else:
            gap_dates = [d for d in missing_dates if d > latest]
        if gap_dates:
            candidates.append((ts_code, latest, gap_dates))

    if not candidates:
        return missing_dates, health_report, []
    if len(candidates) > max_codes:
        log(
            f"  ℹ️ cyq_chips 候选代码 {len(candidates)} 超过校验上限 {max_codes}，跳过停牌校验",
            "INFO",
        )
        return missing_dates, health_report, []

    required_count_by_date = defaultdict(int)
    exempt_count_by_date = defaultdict(int)
    exempt_codes = set()
    exempt_by_date = defaultdict(set)

    for ts_code, _, gap_dates in candidates:
        for d in gap_dates:
            required_count_by_date[d] += 1
        untradable = _fetch_untradable_dates(ts_code, gap_dates)
        if set(gap_dates).issubset(untradable):
            exempt_codes.add(ts_code)
            for d in gap_dates:
                exempt_count_by_date[d] += 1
                exempt_by_date[d].add(ts_code)

    filtered_missing = []
    suppressed_dates = []
    for d in missing_dates:
        required = required_count_by_date.get(d, 0)
        exempted = exempt_count_by_date.get(d, 0)
        if required > 0 and required == exempted:
            suppressed_dates.append(d)
        else:
            filtered_missing.append(d)

    filtered_report = dict(health_report or {})
    empty_exempt_codes = set()
    empty_codes = list((health_report or {}).get("empty_codes", []) or [])
    check_dates_for_empty = list(calendar_dates or [])
    if check_dates_for_empty:
        check_dates_for_empty = check_dates_for_empty[-5:]
    if empty_codes and check_dates_for_empty:
        for code in empty_codes:
            untradable = _fetch_untradable_dates(code, check_dates_for_empty)
            if set(check_dates_for_empty).issubset(untradable):
                empty_exempt_codes.add(code)
                exempt_codes.add(code)

    if health_report:
        empty_codes = [c for c in health_report.get("empty_codes", []) if c not in exempt_codes]
        codes_by_date = {}
        for d, codes in health_report.get("codes_by_date", {}).items():
            filtered_codes = [c for c in codes if c not in exempt_by_date.get(d, set())]
            if filtered_codes:
                codes_by_date[d] = filtered_codes
        filtered_report["empty_codes"] = empty_codes
        filtered_report["codes_by_date"] = codes_by_date
        filtered_report["dates"] = [d for d in health_report.get("dates", []) if d not in suppressed_dates]

    return filtered_missing, filtered_report, sorted(exempt_codes)


def _suppress_empty_codes_by_suspend(interface_name, config, health_report, calendar_dates=None):
    """
    Generic guard for stock code-based interfaces:
    if empty code is suspended/no-trade on recent dates, suppress from alert list.
    """
    if not health_report:
        return health_report, []
    empty_codes = list(health_report.get("empty_codes", []) or [])
    if not empty_codes:
        return health_report, []

    dates_to_check = list(calendar_dates or [])
    if not dates_to_check:
        return health_report, []
    dates_to_check = dates_to_check[-5:]
    if not dates_to_check:
        return health_report, []

    max_codes = int(config.get("suspend_check_max_codes", 200))
    max_codes = max(1, max_codes)
    if len(empty_codes) > max_codes:
        log(
            f"  ℹ️ {interface_name}: 空代码 {len(empty_codes)} 超过停牌校验上限 {max_codes}，跳过校验",
            "INFO",
        )
        return health_report, []

    exempt_codes = set()
    for code in empty_codes:
        untradable = _fetch_untradable_dates(code, dates_to_check)
        if set(dates_to_check).issubset(untradable):
            exempt_codes.add(code)

    if not exempt_codes:
        return health_report, []

    filtered = dict(health_report)
    filtered["empty_codes"] = [c for c in empty_codes if c not in exempt_codes]
    filtered["codes_by_date"] = {
        d: [c for c in codes if c not in exempt_codes]
        for d, codes in (health_report.get("codes_by_date", {}) or {}).items()
        if [c for c in codes if c not in exempt_codes]
    }
    return filtered, sorted(exempt_codes)


def _repair_single_interface(
    interface_name,
    config,
    trade_calendar,
    latest_trade_date,
    code_type=None,
    max_rounds=3,
    execution_mode="full",
    bypass_whitelist=None,
    inspect_only=False,
    repair_only=False,
    initial_health_result=None,
):
    log(f"\n{'#' * 70}")
    log(f"接口闭环修复: {interface_name}")
    log(f"{'#' * 70}")

    if config.get("default_whitelist"):
        log(f"  ⚡ {interface_name}: 默认白名单接口，跳过体检")
        _mark_interface_whitelisted(
            interface_name,
            latest_date=latest_trade_date,
            mode=execution_mode,
            calendar_dates=trade_calendar,
        )
        return True

    stable_trade_date = resolve_stable_trade_date(config, trade_calendar)
    effective_calendar = [d for d in trade_calendar if stable_trade_date is None or d <= stable_trade_date]
    health_recent_trade_days = int(config.get("health_recent_trade_days", 0) or 0)
    if health_recent_trade_days > 0 and len(effective_calendar) > health_recent_trade_days:
        health_calendar = effective_calendar[-health_recent_trade_days:]
    else:
        health_calendar = effective_calendar
    log(
        f"稳定出数日期: {stable_trade_date} (最新交易日: {latest_trade_date})"
    )
    if health_recent_trade_days > 0 and health_calendar:
        log(
            f"体检窗口: 最近 {len(health_calendar)} 个交易日 "
            f"({health_calendar[0]} ~ {health_calendar[-1]})"
        )

    if bypass_whitelist is None:
        bypass_whitelist = execution_mode == "full"
    whitelist_eligible = _is_whitelist_eligible(config, bypass_whitelist=bypass_whitelist)
    whitelist_record = _get_interface_whitelist_record(interface_name) if whitelist_eligible else None
    if whitelist_record and _calendar_window_covered_by_whitelist(whitelist_record, health_calendar):
        log(
            f"  ⚡ {interface_name}: 命中白名单已验证区间 "
            f"{whitelist_record.get('validated_start_date')} ~ {whitelist_record.get('validated_end_date')}，"
            "跳过本地缺口扫描",
            "INFO",
        )
        if inspect_only:
            return {"complete": True, "whitelisted": True}
        return True
    if whitelist_record:
        uncovered_dates = _calendar_dates_not_covered_by_whitelist(whitelist_record, health_calendar)
        if uncovered_dates and len(uncovered_dates) < len(health_calendar):
            log(
                f"  ⚡ {interface_name}: 白名单已覆盖 {len(health_calendar) - len(uncovered_dates)}/"
                f"{len(health_calendar)} 个交易日，本轮仅体检未覆盖日期 "
                f"{_format_short_list(uncovered_dates)}",
                "INFO",
            )
            health_calendar = uncovered_dates
    if whitelist_record and not inspect_only:
        log(f"  ⚡ {interface_name}: 命中白名单，先查缺口，仅补缺失日期（仅显式 full 绕过）", "INFO")
        initial_missing_dates = get_missing_trade_dates(interface_name, config, health_calendar)
        if initial_missing_dates and not is_tolerable_trailing_gap(config, initial_missing_dates):
            log(f"  🔄 白名单缺失日期: {_format_short_list(initial_missing_dates)}")
            _dispatch_fill(interface_name, config, initial_missing_dates, code_type=code_type)
        final_missing_dates = get_missing_trade_dates(interface_name, config, health_calendar)
        tolerated_tail_only = is_tolerable_trailing_gap(config, final_missing_dates)
        if not final_missing_dates or tolerated_tail_only:
            latest_local = get_local_latest_date(interface_name, config)
            _mark_interface_whitelisted(
                interface_name,
                latest_date=latest_local,
                mode=execution_mode,
                calendar_dates=health_calendar,
            )
            if tolerated_tail_only and final_missing_dates:
                log(
                    f"  ℹ️ {interface_name}: 白名单轻体检后仅剩尾部容忍缺口 {_format_short_list(final_missing_dates)}",
                    "INFO",
                )
            log(f"  ✅ {interface_name}: 白名单轻体检通过")
            return True
        log(
            f"  ↩️ {interface_name}: 白名单轻体检仍有缺口 {_format_short_list(final_missing_dates)}，回退完整闭环",
            "WARNING",
        )

    if inspect_only:
        log(f"\n[{interface_name}] 批量体检：只检查缺日期和完整性，暂不修复", "INFO")
        deduplicate_interface(interface_name, config, calendar_dates=health_calendar)
        missing_dates = get_missing_trade_dates(interface_name, config, health_calendar)
        health_report = scan_incomplete_records(interface_name, config, calendar_dates=health_calendar)
        health_report = _limit_health_report_to_calendar(health_report, health_calendar)

        if code_type == "stock":
            health_report, suspend_exempt_codes = _suppress_empty_codes_by_suspend(
                interface_name,
                config,
                health_report,
                calendar_dates=health_calendar,
            )
            if suspend_exempt_codes:
                log(
                    f"  ℹ️ {interface_name}: 停牌/无交易豁免空代码 {len(suspend_exempt_codes)} 只",
                    "INFO",
                )

        if interface_name == "cyq_chips":
            missing_dates, health_report, exempt_codes = _suppress_cyq_chips_non_trading_gaps(
                config,
                missing_dates,
                health_report,
                calendar_dates=health_calendar,
            )
            if exempt_codes:
                log(
                    f"  ℹ️ cyq_chips: 停牌/无交易豁免 {len(exempt_codes)} 只代码",
                    "INFO",
                )
        _log_health_report(interface_name, health_report)

        incomplete_dates = sorted(set(health_report.get("dates", [])))
        empty_codes = list(health_report.get("empty_codes", []))
        if config.get("whitelist_on_date_gap_only"):
            complete = not missing_dates or is_tolerable_trailing_gap(config, missing_dates)
        else:
            complete = (
                (not missing_dates or is_tolerable_trailing_gap(config, missing_dates))
                and not incomplete_dates
                and not empty_codes
            )
        result = {
            "complete": complete,
            "missing_dates": missing_dates,
            "health_report": health_report,
            "health_calendar": health_calendar,
        }
        local_year_intervals = _local_year_file_date_intervals_for_whitelist(
            interface_name,
            config,
            health_calendar,
            health_report=health_report,
        )
        if complete:
            _mark_interface_whitelisted(
                interface_name,
                latest_date=get_local_latest_date(interface_name, config),
                mode=execution_mode,
                validated_intervals=local_year_intervals or None,
                calendar_dates=None if local_year_intervals else health_calendar,
            )
            log(f"  ✅ {interface_name}: 批量体检通过，已加入白名单")
        else:
            clean_intervals = local_year_intervals or _clean_calendar_intervals_for_whitelist(
                health_calendar,
                missing_dates,
                health_report,
            )
            if clean_intervals:
                clean_interval_labels = [
                    f"{item['start']}~{item['end']}" for item in clean_intervals
                ]
                _mark_interface_whitelisted(
                    interface_name,
                    latest_date=get_local_latest_date(interface_name, config),
                    mode=execution_mode,
                    validated_intervals=clean_intervals,
                )
                log(
                    f"  ✅ {interface_name}: 干净子区间已加入白名单 "
                    f"{_format_short_list(clean_interval_labels)}",
                    "INFO",
                )
            log(f"  🧺 {interface_name}: 批量体检发现问题，加入统一修复队列", "WARNING")
        return result

    if repair_only:
        log(f"\n[{interface_name}] 批量修复：使用上一轮体检结果，暂不复检", "INFO")
        health_result = initial_health_result or {}
        missing_dates = list(health_result.get("missing_dates", []))
        health_report = dict(health_result.get("health_report") or {})
        incomplete_dates = sorted(set(health_report.get("dates", [])))
        empty_codes = list(health_report.get("empty_codes", []))
        codes_by_date = {
            trade_date: codes
            for trade_date, codes in health_report.get("codes_by_date", {}).items()
            if codes
        }
        repair_dates = sorted(set(missing_dates) | set(incomplete_dates))
        if not repair_dates and not codes_by_date and not empty_codes:
            log(f"  ✅ {interface_name}: 无需修复，等待统一复检")
            return True

        if repair_dates:
            log(f"  🔄 批量修复日期: {_format_short_list(repair_dates)}")
            _dispatch_fill(interface_name, config, repair_dates, code_type=code_type)

        if code_type in {"stock", "index"}:
            per_date_codes = {}
            for trade_date, codes in codes_by_date.items():
                per_date_codes.setdefault(tuple(codes), []).append(trade_date)
            for codes_tuple, dates in per_date_codes.items():
                codes = list(codes_tuple)
                log(
                    f"  🔄 批量定向修复代码 {len(codes)} 只, 日期 {len(dates)} 个: "
                    f"codes={_format_short_list(codes)}, dates={_format_short_list(dates)}"
                )
                _dispatch_fill(
                    interface_name,
                    config,
                    sorted(dates),
                    code_list=codes,
                    code_type=code_type,
                )
            if empty_codes:
                fallback_dates = [stable_trade_date] if stable_trade_date else repair_dates
                if fallback_dates:
                    log(
                        f"  🔄 批量空文件代码定向重拉 {len(empty_codes)} 只: {_format_short_list(empty_codes)}"
                    )
                    _dispatch_fill(
                        interface_name,
                        config,
                        sorted(set(fallback_dates)),
                        code_list=empty_codes,
                        code_type=code_type,
                    )
        return True

    previous_signature = None
    same_signature_count = 0
    last_health_report = None
    last_missing_dates = None
    reuse_last_health_result = False

    for round_index in range(1, max_rounds + 1):
        log(f"\n[{interface_name}] 第 {round_index} 轮体检：先查缺日期和完整性", "INFO")
        if round_index == 1 and initial_health_result:
            log(f"  ℹ️ {interface_name}: 复用批量体检结果，直接进入统一修复判断", "INFO")
            missing_dates = list(initial_health_result.get("missing_dates", []))
            health_report = dict(initial_health_result.get("health_report") or {})
        else:
            deduplicate_interface(interface_name, config, calendar_dates=health_calendar)
            missing_dates = get_missing_trade_dates(interface_name, config, health_calendar)
            health_report = scan_incomplete_records(interface_name, config, calendar_dates=health_calendar)
            health_report = _limit_health_report_to_calendar(health_report, health_calendar)
        last_missing_dates = missing_dates
        last_health_report = health_report

        # 全局规则：按个股拉取接口遇到空代码时，先做停牌/无交易校验，避免误报。
        if code_type == "stock":
            health_report, suspend_exempt_codes = _suppress_empty_codes_by_suspend(
                interface_name,
                config,
                health_report,
                calendar_dates=health_calendar,
            )
            if suspend_exempt_codes:
                log(
                    f"  ℹ️ {interface_name}: 停牌/无交易豁免空代码 {len(suspend_exempt_codes)} 只",
                    "INFO",
                )

        if interface_name == "cyq_chips":
            missing_dates, health_report, exempt_codes = _suppress_cyq_chips_non_trading_gaps(
                config,
                missing_dates,
                health_report,
                calendar_dates=health_calendar,
            )
            if exempt_codes:
                log(
                    f"  ℹ️ cyq_chips: 停牌/无交易豁免 {len(exempt_codes)} 只代码",
                    "INFO",
                )
        _log_health_report(interface_name, health_report)

        incomplete_dates = sorted(set(health_report.get("dates", [])))
        empty_codes = list(health_report.get("empty_codes", []))
        codes_by_date = {
            trade_date: codes
            for trade_date, codes in health_report.get("codes_by_date", {}).items()
            if codes
        }

        signature = (
            tuple(missing_dates),
            tuple(incomplete_dates),
            tuple(empty_codes),
            tuple((trade_date, tuple(codes)) for trade_date, codes in sorted(codes_by_date.items())),
        )
        if signature == previous_signature:
            same_signature_count += 1
            if (
                is_tolerable_trailing_gap(config, missing_dates)
                and not incomplete_dates
                and not empty_codes
            ):
                log(
                    f"  ℹ️ {interface_name}: 尾部剩余 {_format_short_list(missing_dates)} 仍为空，按晚出数接口容忍口径结束",
                    "INFO",
                )
                return True
            if same_signature_count >= 1:
                log(f"  ⚠️ {interface_name}: 连续两轮体检结果一致，停止重复补拉", "WARNING")
                log(f"  ⚠️ {interface_name}: 本轮修复无效，保留为残留问题", "WARNING")
                return False
        else:
            same_signature_count = 0
        previous_signature = signature

        if not any(signature):
            _mark_interface_whitelisted(
                interface_name,
                latest_date=get_local_latest_date(interface_name, config),
                mode=execution_mode,
                calendar_dates=health_calendar,
            )
            log(f"  ✅ {interface_name}: 已完整")
            return True

        repair_dates = sorted(set(missing_dates) | set(incomplete_dates))
        log(f"  🔧 {interface_name}: 第 {round_index} 轮检查完成，开始统一修复", "INFO")
        if repair_dates:
            log(f"  🔄 修复日期: {_format_short_list(repair_dates)}")
            _dispatch_fill(interface_name, config, repair_dates, code_type=code_type)

        if code_type in {"stock", "index"}:
            # 对按代码接口，缺参日期优先按代码定向重拉，避免整库全量重跑。
            per_date_codes = {}
            for trade_date, codes in codes_by_date.items():
                per_date_codes.setdefault(tuple(codes), []).append(trade_date)
            for codes_tuple, dates in per_date_codes.items():
                codes = list(codes_tuple)
                log(
                    f"  🔄 定向修复代码 {len(codes)} 只, 日期 {len(dates)} 个: "
                    f"codes={_format_short_list(codes)}, dates={_format_short_list(dates)}"
                )
                _dispatch_fill(
                    interface_name,
                    config,
                    sorted(dates),
                    code_list=codes,
                    code_type=code_type,
                )
            if empty_codes:
                fallback_dates = [stable_trade_date] if stable_trade_date else repair_dates
                if fallback_dates:
                    log(
                        f"  🔄 空文件代码定向重拉 {len(empty_codes)} 只: {_format_short_list(empty_codes)}"
                    )
                    _dispatch_fill(
                        interface_name,
                        config,
                        sorted(set(fallback_dates)),
                        code_list=empty_codes,
                        code_type=code_type,
                    )

    if reuse_last_health_result and last_health_report is not None and last_missing_dates is not None:
        log(f"  ℹ️ {interface_name}: 复用上一轮体检结果，跳过重复最终全量扫描", "INFO")
        final_report = last_health_report
        final_missing_dates = last_missing_dates
    else:
        deduplicate_interface(interface_name, config, calendar_dates=health_calendar)
        final_report = scan_incomplete_records(interface_name, config, calendar_dates=health_calendar)
        final_report = _limit_health_report_to_calendar(final_report, health_calendar)
        final_missing_dates = get_missing_trade_dates(interface_name, config, health_calendar)
    if config.get("whitelist_on_date_gap_only"):
        tolerated_tail_only = is_tolerable_trailing_gap(config, final_missing_dates)
        is_complete = not final_missing_dates or tolerated_tail_only
    else:
        tolerated_tail_only = (
            is_tolerable_trailing_gap(config, final_missing_dates)
            and not final_report.get("dates")
            and not final_report.get("empty_codes")
        )
        is_complete = (
            (not final_missing_dates or tolerated_tail_only)
            and not final_report.get("dates")
            and not final_report.get("empty_codes")
        )
    if is_complete:
        local_year_intervals = _local_year_file_date_intervals_for_whitelist(
            interface_name,
            config,
            health_calendar,
            health_report=final_report,
        )
        _mark_interface_whitelisted(
            interface_name,
            latest_date=get_local_latest_date(interface_name, config),
            mode=execution_mode,
            validated_intervals=local_year_intervals or None,
            calendar_dates=None if local_year_intervals else health_calendar,
        )
        if tolerated_tail_only and final_missing_dates:
            log(
                f"  ℹ️ {interface_name}: 尾部剩余 {_format_short_list(final_missing_dates)} 为空，按晚出数接口容忍口径视为完成",
                "INFO",
            )
        log(f"  ✅ {interface_name}: 闭环修复完成")
    else:
        local_year_intervals = _local_year_file_date_intervals_for_whitelist(
            interface_name,
            config,
            health_calendar,
            health_report=final_report,
        )
        clean_intervals = local_year_intervals or _clean_calendar_intervals_for_whitelist(
            health_calendar,
            final_missing_dates,
            final_report,
        )
        if clean_intervals:
            clean_interval_labels = [
                f"{item['start']}~{item['end']}" for item in clean_intervals
            ]
            _mark_interface_whitelisted(
                interface_name,
                latest_date=get_local_latest_date(interface_name, config),
                mode=execution_mode,
                validated_intervals=clean_intervals,
            )
            log(
                f"  ✅ {interface_name}: 残留问题之外的干净子区间已加入白名单 "
                f"{_format_short_list(clean_interval_labels)}",
                "INFO",
            )
        log(f"  ⚠️ {interface_name}: 仍有残留缺口，需人工关注", "WARNING")
        if final_missing_dates:
            log(f"    缺失日期: {_format_short_list(final_missing_dates)}", "WARNING")
        _log_health_report(interface_name, final_report)
    return is_complete


def preflight_api_health_check():
    """Fail fast with clear diagnostics before iterating every interface."""
    log("\n🔐 Tushare 健康检查")
    log("=" * 60)
    result = diagnose_api_connection(pro=pro)
    log(f"API URL: {result['api_url']}")
    log(f"TUSHARE_TOKEN: {'已设置' if result['has_token'] else '未设置'}")
    if result["ok"]:
        log(f"✅ {result['message']}")
        return True

    level = "ERROR"
    if result["category"] in {"timeout", "empty_response"}:
        level = "WARNING"
    log(f"❌ 健康检查失败: {result['message']}", level)

    hints = {
        "missing_token": "请先 export TUSHARE_TOKEN=... 再运行脚本",
        "token_expired": "请更新 utils/tushare_bootstrap.py 里的 token，再重新运行脚本",
        "invalid_token": "请检查 TUSHARE_TOKEN 是否正确，或是否需要配置合法的 TUSHARE_API_URL",
        "dns_error": "请检查当前网络 DNS，或确认 TUSHARE_API_URL 是否填写正确",
        "connect_error": "请检查 API 地址、端口和网络连通性",
        "timeout": "可以稍后重试，或在 create_pro_api 中提高 timeout",
    }
    hint = hints.get(result["category"])
    if hint:
        log(f"提示: {hint}", level)
    return False
