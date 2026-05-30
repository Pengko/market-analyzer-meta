#!/usr/bin/env python3
"""
主要作用:
- 提供数据完整性和健康检查能力
- 包括本地最新日期识别、缺失交易日计算、接口状态扫描
"""

import re
from collections import defaultdict
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

from .files import get_latest_date_fast
from .logging_utils import log


FAILURE_TEXT_MARKERS = {
    "",
    "nan",
    "none",
    "null",
    "missing",
    "error",
    "failed",
    "fail",
    "timeout",
}


def is_tolerable_trailing_gap(interface_config, missing_dates):
    """Return True when the remaining missing dates are an allowed empty tail."""
    if not missing_dates or not interface_config.get("expected_empty_ok"):
        return False
    tolerance = int(interface_config.get("max_trailing_gap_trade_days", 1) or 0)
    tolerance = max(0, tolerance)
    return len(missing_dates) <= tolerance


def get_root_dir(config, stock_dir, index_dir, financial_dir=None):
    """Return the correct root directory for a config."""
    root = config.get("root")
    if root == "index":
        return Path(index_dir)
    if root == "financial" and financial_dir is not None:
        return Path(financial_dir)
    return Path(stock_dir)


def _normalize_date_value(value):
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        text = text[:4] + text[5:7] + text[8:10]
    if not re.match(r"^\d{8}$", text):
        return None
    try:
        datetime.strptime(text, "%Y%m%d")
    except ValueError:
        return None
    return text


def _is_missing_scalar(value):
    if pd.isna(value):
        return True
    text = str(value).strip().lower()
    return text in FAILURE_TEXT_MARKERS


def _looks_like_date_partitioned_file(files, prefix):
    probe = list(files[: min(20, len(files))])
    if not probe:
        return False
    pattern = re.compile(rf"^{re.escape(prefix)}\d{{8}}")
    matched = sum(1 for csv_file in probe if pattern.match(csv_file.name))
    return matched / len(probe) >= 0.9


def _extract_code_from_filename(csv_file, prefix):
    name = csv_file.name
    if not name.startswith(prefix) or not name.endswith(".csv"):
        return None
    return name[len(prefix) : -4]


def _extract_date_from_filename(csv_file, prefix):
    match = re.search(rf"{re.escape(prefix)}(\d{{8}})", csv_file.name)
    return match.group(1) if match else None


def _iter_interface_csv_files(data_path, config, interface_name):
    fixed_file_name = config.get("fixed_file_name")
    if fixed_file_name:
        candidate = data_path / fixed_file_name
        return [candidate] if candidate.exists() else []

    prefix = config.get("prefix", f"{interface_name}_")
    save_granularity = config.get("save_granularity")
    recursive = bool(config.get("partition_by_year_dir", False)) or save_granularity in {
        "year",
        "year_date",
        "year_stock",
        "ymd_date",
        "ymd_stock",
    }
    file_iter = data_path.rglob(f"{prefix}*.csv") if recursive else data_path.glob(f"{prefix}*.csv")
    candidate_files = list(file_iter)
    if not candidate_files and save_granularity == "year":
        candidate_files = [
            csv_file for csv_file in data_path.glob("*.csv")
            if re.match(r"^\d{4}\.csv$", csv_file.name)
        ]

    code_cache_subdir = config.get("code_cache_subdir")
    files = []
    for csv_file in candidate_files:
        try:
            relative_parts = csv_file.relative_to(data_path).parts
        except Exception:
            relative_parts = csv_file.parts
        if code_cache_subdir and code_cache_subdir in relative_parts:
            continue
        if any(part.startswith("_") for part in relative_parts[:-1]):
            continue
        files.append(csv_file)
    return files


def _resolve_required_columns(config, columns):
    required = config.get("required_columns", [])
    return [col for col in required if col in columns]


def _resolve_nullable_columns(config, columns):
    nullable = config.get("nullable_columns", [])
    return [col for col in nullable if col in columns]


def _resolve_key_columns(config, columns):
    keys = set(config.get("dedup_cols", []))
    keys.add(config.get("date_col", "trade_date"))
    for col in ("ts_code", "index_code", "con_code", "name"):
        if col in columns:
            keys.add(col)
    return [col for col in columns if col in keys]


def _find_incomplete_dates_in_frame(interface_name, config, frame, allowed_dates=None):
    if frame.empty:
        return set()

    date_col = config.get("date_col", "trade_date")
    if date_col not in frame.columns:
        return set()

    normalized = frame.copy()
    normalized[date_col] = normalized[date_col].map(_normalize_date_value)
    normalized = normalized[normalized[date_col].notna()]
    if allowed_dates is not None:
        normalized = normalized[normalized[date_col].isin(allowed_dates)]
    if normalized.empty:
        return set()

    required_cols = _resolve_required_columns(config, list(normalized.columns))
    if required_cols:
        nullable_cols = set(_resolve_nullable_columns(config, list(normalized.columns)))
        effective_required = [col for col in required_cols if col not in nullable_cols]
        subset = normalized[effective_required] if effective_required else pd.DataFrame(index=normalized.index)
        if subset.empty:
            return set()
        missing_mask = subset.apply(lambda col: col.map(_is_missing_scalar))
        incomplete_mask = missing_mask.any(axis=1)
    else:
        key_cols = set(_resolve_key_columns(config, list(normalized.columns)))
        value_cols = [col for col in normalized.columns if col not in key_cols]
        if not value_cols:
            return set()
        subset = normalized[value_cols]
        missing_mask = subset.apply(lambda col: col.map(_is_missing_scalar))
        incomplete_mask = missing_mask.all(axis=1)

    return {
        value
        for value in normalized.loc[incomplete_mask, date_col].tolist()
        if value is not None
    }


def get_local_latest_date(interface_name, interface_config, stock_dir, index_dir, financial_dir=None):
    """Find the latest locally available trade date for an interface."""
    try:
        interface_type = interface_config.get("type", "by_date")
        target_date_col = interface_config.get("date_col", "trade_date")
        base_dir = get_root_dir(interface_config, stock_dir, index_dir, financial_dir=financial_dir)

        if interface_type == "by_date":
            data_path = base_dir / interface_config.get("path", interface_name)
            prefix = interface_config.get("prefix", f"{interface_name}_")
            if not data_path.exists():
                return None
            files = sorted(
                _iter_interface_csv_files(data_path, interface_config, interface_name)
            )
            if not files:
                return None
            # by_date 接口在本地可能是“按日期文件”或“按代码文件”，两种都要兼容。
            if _looks_like_date_partitioned_file(files, prefix):
                pattern = re.compile(rf"^{re.escape(prefix)}\d{{8}}")
                dates = []
                non_date_files = []
                for csv_file in files:
                    match = re.search(rf"{re.escape(prefix)}(\d{{8}})", csv_file.name)
                    if match and pattern.match(csv_file.name):
                        dates.append(match.group(1))
                    else:
                        non_date_files.append(csv_file)
                if not dates:
                    return None
                latest_dates = [max(dates)]
                for csv_file in non_date_files:
                    latest = get_latest_date_fast(csv_file, date_col=target_date_col)
                    if latest is None:
                        continue
                    normalized = _normalize_date_value(latest)
                    if normalized is not None:
                        latest_dates.append(normalized)
                return max(latest_dates)

            latest_dates = []
            for csv_file in files:
                latest = get_latest_date_fast(csv_file, date_col=target_date_col)
                if latest is None:
                    continue
                normalized = _normalize_date_value(latest)
                if normalized is not None:
                    latest_dates.append(normalized)
            return max(latest_dates) if latest_dates else None

        if interface_type in {"standalone", "by_stock"}:
            data_path = base_dir / interface_config.get("path", interface_name)
            if not data_path.exists():
                return None
            files = _iter_interface_csv_files(data_path, interface_config, interface_name)
            if not files:
                return None
            latest_dates = []
            for csv_file in files:
                latest = get_latest_date_fast(csv_file, date_col=target_date_col)
                if latest is None:
                    continue
                normalized = _normalize_date_value(latest)
                if normalized is not None:
                    latest_dates.append(normalized)
            if latest_dates:
                strategy = interface_config.get("latest_date_strategy", "max")
                if strategy == "mode":
                    counts = Counter(latest_dates)
                    # mode first, and then newer date as tie-breaker
                    top_count = max(counts.values())
                    candidates = [d for d, c in counts.items() if c == top_count]
                    return max(candidates)
                return max(latest_dates)
        return None
    except Exception as exc:
        log(f"获取 {interface_name} 本地日期失败: {exc}", "DEBUG")
        return None


def get_missing_trade_dates(
    interface_name,
    interface_config,
    trade_calendar,
    stock_dir,
    index_dir,
    financial_dir=None,
    default_lookback=30,
):
    """Compare local state to the trade calendar and return missing dates."""
    if not interface_config.get("calendar_aligned", True):
        return []
    if not trade_calendar:
        return []

    def _resolve_period_end_dates(calendar_values, granularity):
        if granularity not in {"weekly", "monthly"}:
            return calendar_values
        ends = []
        last_key = None
        last_date = None
        for d in calendar_values:
            dt = datetime.strptime(str(d), "%Y%m%d")
            if granularity == "weekly":
                key = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
            else:
                key = dt.strftime("%Y-%m")
            if last_key is None:
                last_key = key
                last_date = d
                continue
            if key != last_key:
                ends.append(last_date)
                last_key = key
            last_date = d
        # Only include fully closed periods.
        # The trailing period (current week/month) is still open and should not
        # be treated as a required sync target yet.
        return ends

    min_trade_date = interface_config.get("min_trade_date")
    if min_trade_date:
        trade_calendar = [d for d in trade_calendar if d >= str(min_trade_date)]
        if not trade_calendar:
            return []

    fetch_granularity = interface_config.get("fetch_granularity")
    target_calendar = _resolve_period_end_dates(trade_calendar, fetch_granularity)
    if not target_calendar:
        return []

    local_latest = get_local_latest_date(
        interface_name,
        interface_config,
        stock_dir=stock_dir,
        index_dir=index_dir,
        financial_dir=financial_dir,
    )
    if local_latest is None:
        log(
            f"{interface_name}: 本地无数据，需获取最近 {min(default_lookback, len(target_calendar))} 个交易日",
            "INFO",
        )
        return target_calendar[-default_lookback:]

    try:
        local_index = target_calendar.index(local_latest)
        missing = target_calendar[local_index + 1 :]
        if missing:
            if is_tolerable_trailing_gap(interface_config, missing):
                log(
                    f"{interface_name}: 尾部缺口 {len(missing)} 天在容忍范围内，但仍先尝试补拉可用日期",
                    "INFO",
                )
            log(
                f"{interface_name}: 本地最新 {local_latest}，缺失 {len(missing)} 天 ({missing[0]} 至 {missing[-1]})",
                "INFO",
            )
        else:
            log(f"{interface_name}: 已是最新 ({local_latest})", "SUCCESS")
        return missing
    except ValueError:
        if target_calendar:
            try:
                if str(local_latest) >= str(target_calendar[-1]):
                    log(f"{interface_name}: 已是最新 ({local_latest})", "SUCCESS")
                    return []
                if str(local_latest) < str(target_calendar[0]):
                    log(
                        f"{interface_name}: 本地最新 {local_latest}，当前仅检查窗口 {target_calendar[0]} 至 {target_calendar[-1]}",
                        "INFO",
                    )
                    return target_calendar[-default_lookback:]
            except Exception:
                pass
        log(f"{interface_name}: 本地日期 {local_latest} 不在当前交易日历中", "WARNING")
        return target_calendar[-default_lookback:]


def check_interface_by_date(interface_name, config, stock_dir, index_dir, financial_dir=None, sample_size=50):
    """Inspect file presence and latest date for a configured interface."""
    root_dir = get_root_dir(config, stock_dir, index_dir, financial_dir=financial_dir)
    path = root_dir / config["path"]
    if not path.exists():
        return {
            "name": interface_name,
            "exists": False,
            "files": 0,
            "root": config.get("root", "stock"),
        }

    file_iter = _iter_interface_csv_files(path, config, interface_name)
    files = [f for f in file_iter if "_metadata" not in f.name]
    if not files:
        return {
            "name": interface_name,
            "exists": True,
            "files": 0,
            "latest_date": None,
            "root": config.get("root", "stock"),
        }

    dates = []
    for csv_file in files[:sample_size]:
        try:
            date_col = config["date_col"]
            df = pd.read_csv(csv_file, usecols=[date_col], low_memory=False)
            if not df.empty and date_col in df.columns:
                dates.append(str(df[date_col].max()))
        except Exception:
            continue

    if not dates:
        return {
            "name": interface_name,
            "exists": True,
            "files": len(files),
            "latest_date": None,
            "root": config.get("root", "stock"),
        }

    latest_date = max(dates)
    coverage = dates.count(latest_date) / len(dates) * 100
    return {
        "name": interface_name,
        "exists": True,
        "files": len(files),
        "latest_date": latest_date,
        "coverage": coverage,
        "root": config.get("root", "stock"),
    }


def scan_incomplete_records(
    interface_name,
    config,
    stock_dir,
    index_dir,
    financial_dir=None,
    progress_fn=None,
    progress_interval=0,
    calendar_dates=None,
):
    """Scan local CSVs and find dates/codes whose rows still have missing critical fields."""
    root_dir = get_root_dir(config, stock_dir, index_dir, financial_dir=financial_dir)
    path = root_dir / config["path"]
    result = {
        "name": interface_name,
        "dates": [],
        "codes_by_date": {},
        "empty_codes": [],
        "scanned_files": 0,
        "date_partitioned": False,
        "root": config.get("root", "stock"),
    }
    if not path.exists():
        return result

    allowed_dates = {str(d) for d in calendar_dates} if calendar_dates else None
    allowed_years = {str(d)[:4] for d in allowed_dates} if allowed_dates else None

    file_iter = _iter_interface_csv_files(path, config, interface_name)
    files = sorted(f for f in file_iter if "_metadata" not in f.name)
    if not files:
        return result

    date_partitioned = _looks_like_date_partitioned_file(files, config["prefix"])
    if allowed_dates:
        if date_partitioned:
            files = [
                csv_file for csv_file in files
                if _extract_date_from_filename(csv_file, config["prefix"]) in allowed_dates
            ]
        elif config.get("partition_by_year_dir", False):
            filtered_files = []
            for csv_file in files:
                try:
                    relative_parts = csv_file.relative_to(path).parts
                except Exception:
                    relative_parts = ()
                if relative_parts and relative_parts[0] in allowed_years:
                    filtered_files.append(csv_file)
            files = filtered_files
    result["date_partitioned"] = date_partitioned

    bad_dates = set()
    codes_by_date = defaultdict(set)
    empty_codes = set()

    progress_interval = max(0, int(progress_interval or 0))
    total_files = len(files)
    if progress_fn and progress_interval and total_files >= progress_interval:
        progress_fn(f"  🔎 {interface_name}: 缺参扫描 0/{total_files}")

    for csv_file in files:
        result["scanned_files"] += 1
        if (
            progress_fn
            and progress_interval
            and result["scanned_files"] % progress_interval == 0
        ):
            progress_fn(
                f"  🔎 {interface_name}: 缺参扫描 {result['scanned_files']}/{total_files}"
            )
        try:
            header = pd.read_csv(csv_file, nrows=0)
            columns = list(header.columns)
        except pd.errors.EmptyDataError:
            columns = []
        except Exception:
            continue

        if not columns:
            if date_partitioned:
                file_date = _extract_date_from_filename(csv_file, config["prefix"])
                if file_date:
                    bad_dates.add(file_date)
            else:
                code = _extract_code_from_filename(csv_file, config["prefix"])
                if code:
                    empty_codes.add(code)
            continue

        required_cols = _resolve_required_columns(config, columns)
        date_col = config.get("date_col", "trade_date")
        usecols = list(dict.fromkeys([date_col, *required_cols])) if required_cols else None

        try:
            frame = pd.read_csv(csv_file, usecols=usecols, low_memory=False)
        except pd.errors.EmptyDataError:
            frame = pd.DataFrame()
        except Exception:
            continue

        if frame.empty:
            if date_partitioned:
                file_date = _extract_date_from_filename(csv_file, config["prefix"])
                if file_date:
                    bad_dates.add(file_date)
            else:
                code = _extract_code_from_filename(csv_file, config["prefix"])
                if code:
                    empty_codes.add(code)
            continue

        incomplete_dates = _find_incomplete_dates_in_frame(
            interface_name,
            config,
            frame,
            allowed_dates=allowed_dates,
        )
        if not incomplete_dates:
            continue

        if date_partitioned:
            bad_dates.update(incomplete_dates)
            continue

        code = _extract_code_from_filename(csv_file, config["prefix"])
        bad_dates.update(incomplete_dates)
        if not code:
            continue
        for trade_date in incomplete_dates:
            codes_by_date[trade_date].add(code)

    result["dates"] = sorted(bad_dates)
    result["codes_by_date"] = {
        trade_date: sorted(codes) for trade_date, codes in sorted(codes_by_date.items())
    }
    result["empty_codes"] = sorted(empty_codes)
    if progress_fn and progress_interval and total_files >= progress_interval:
        progress_fn(f"  🔎 {interface_name}: 缺参扫描完成 {result['scanned_files']}/{total_files}")
    return result
