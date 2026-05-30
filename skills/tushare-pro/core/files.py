#!/usr/bin/env python3
"""
主要作用:
- 提供 CSV 大文件尾读、追加写入、快速合并、去重等文件级工具
- 让更新脚本不再各自维护一套文件处理逻辑
"""

from pathlib import Path
import re
from datetime import datetime, timedelta

import pandas as pd


def get_latest_date_fast(filepath, chunk_size=32768, date_col="trade_date"):
    """Read the last date_col value from a CSV tail without loading the full file."""
    filepath = Path(filepath)
    try:
        with filepath.open("r", encoding="utf-8", errors="ignore") as handle:
            header_line = handle.readline().strip()
            first_data_line = handle.readline().strip()
        if not header_line:
            return None
        header = [col.strip() for col in header_line.split(",")]
        date_idx = header.index(date_col) if date_col in header else 1
        candidates = []
        if first_data_line:
            parts = first_data_line.split(",")
            if date_idx < len(parts):
                value = parts[date_idx].strip()
                if re.match(r"^\d{8}$", value):
                    candidates.append(value)

        with filepath.open("rb") as handle:
            try:
                handle.seek(-chunk_size, 2)
            except OSError:
                handle.seek(0)
            lines = handle.read().decode("utf-8", errors="ignore").split("\n")
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                if date_idx < len(parts):
                    value = parts[date_idx].strip()
                    if re.match(r"^\d{8}$", value):
                        candidates.append(value)
                        break
        if candidates:
            return max(candidates)
    except Exception:
        return None
    return None


def append_to_csv(filepath, df):
    """Append rows to an existing CSV while preserving column order."""
    filepath = Path(filepath)
    if not filepath.exists() or filepath.stat().st_size == 0:
        df.to_csv(filepath, index=False)
        return

    with filepath.open("r", encoding="utf-8") as handle:
        header_line = handle.readline().strip()
    if not header_line:
        df.to_csv(filepath, index=False)
        return

    header = header_line.split(",")
    ordered_cols = [col for col in header if col in df.columns]
    ordered = df[ordered_cols] if ordered_cols else df

    with filepath.open("rb") as handle:
        try:
            handle.seek(-1, 2)
        except OSError:
            handle.seek(0)
        needs_newline = handle.read() != b"\n"
    with filepath.open("a", encoding="utf-8") as handle:
        if needs_newline:
            handle.write("\n")
        ordered.to_csv(handle, header=False, index=False)


def fast_merge_to_file(filepath, df, date_col="trade_date"):
    """Append or merge a single-date frame into a per-code CSV."""
    filepath = Path(filepath)
    frame = df.copy()
    frame[date_col] = frame[date_col].astype(str)

    if not filepath.exists() or filepath.stat().st_size == 0:
        frame.to_csv(filepath, index=False)
        return "created"

    latest = get_latest_date_fast(filepath)
    target = str(frame[date_col].iloc[0])

    if latest is not None:
        try:
            latest_cmp = str(int(float(latest)))
        except Exception:
            latest_cmp = str(latest)
        if latest_cmp == target:
            return "skipped"
        if latest_cmp < target:
            append_to_csv(filepath, frame)
            return "appended"
        # latest_cmp > target: backfill an older date, fall through to merge path.

    existing = pd.read_csv(filepath)
    existing[date_col] = existing[date_col].astype(str)
    existing = existing[existing[date_col] != target]
    combined = pd.concat([existing, frame], ignore_index=True)
    combined = combined.drop_duplicates(subset=[date_col], keep="last")
    combined = combined.sort_values(date_col)
    combined.to_csv(filepath, index=False)
    return "merged"


def deduplicate_file(filepath, subset_cols, keep="last"):
    """Drop duplicates from a CSV file in place."""
    filepath = Path(filepath)
    try:
        df = pd.read_csv(filepath, low_memory=False)
        original_len = len(df)
        if df.empty:
            return 0
        valid_cols = [col for col in subset_cols if col in df.columns]
        if not valid_cols:
            return 0
        df = df.drop_duplicates(subset=valid_cols, keep=keep)
        df = df.sort_values(by=valid_cols[0])
        removed = original_len - len(df)
        if removed > 0:
            df.to_csv(filepath, index=False)
        return removed
    except Exception:
        return 0


def prune_date_partitioned_history(directory, prefix, retention_days):
    """Delete date-partitioned CSV files older than retention_days."""
    directory = Path(directory)
    if retention_days is None or retention_days <= 0 or not directory.exists():
        return 0

    cutoff = (datetime.now() - timedelta(days=int(retention_days))).strftime("%Y%m%d")
    pattern = re.compile(rf"^{re.escape(prefix)}(\d{{8}})\.csv$")
    removed = 0

    for csv_file in directory.rglob(f"{prefix}*.csv"):
        match = pattern.match(csv_file.name)
        if not match:
            continue
        file_date = match.group(1)
        if file_date < cutoff:
            try:
                csv_file.unlink()
                removed += 1
            except Exception:
                continue
    return removed


_FLAT_PARQUET_INTERFACES = {
    "index_daily", "index_weekly", "index_monthly",
    "daily", "daily_basic",
    "cyq_chips", "cyq_perf",
    "hm_detail", "limit_cpt_list", "limit_list_d", "limit_list_ths", "limit_step",
    "margin", "margin_detail",
    "moneyflow", "moneyflow_ths", "moneyflow_hsgt", "moneyflow_mkt_dc",
    "moneyflow_ind_ths", "moneyflow_cnt_ths",
    "pledge_detail", "pledge_stat", "stk_auction_c", "stk_auction_o",
    "stk_factor_pro",
    "stk_nineturn", "stk_shock",
    # theme_data
    "ths_member", "ths_daily", "dc_daily",
    "dc_member", "kpl_list",
    # index
    "index_member",
    "index_weight",
}

# By-date interfaces: merge into one parquet per interface (not per ts_code)
_COMBINED_PARQUET_INTERFACES = {
    "hm_detail", "limit_cpt_list", "limit_list_d", "limit_list_ths", "limit_step",
    "moneyflow_hsgt", "moneyflow_mkt_dc",
    "stk_shock",
    "index_member",
    "index_weight",
}

# By-date interfaces that contain all stocks in one file: split by ts_code into per-stock parquet
_PER_STOCK_BY_DATE_INTERFACES = {
    "moneyflow", "moneyflow_ths",
    "moneyflow_ind_ths", "moneyflow_cnt_ths",
    "stk_nineturn",
    "ths_daily", "dc_daily",
    "sw_daily",
}

# Standalone interfaces stored in per-concept subdirs: flat parquet at {root}/{concept_name}.parquet
_CONCEPT_PARQUET_INTERFACES = set()

# By-date interfaces: merge into one parquet per year (not per date, not all-time combined)
_YEARLY_COMBINED_INTERFACES = {
    "kpl_list",
    "top_inst",
    "top_list",
    "top10_holders",
    "top10_floatholders",
    "block_trade",
}

# Interfaces: merge into one parquet+csv per month at {root}/{year}/{month}.parquet|csv
_MONTHLY_COMBINED_INTERFACES = {
    "dc_concept",
    "dc_concept_cons",
    "kpl_concept_cons",
}


def _normalize_dates(df):
    """Normalize date columns to YYYYMMDD strings."""
    df = df.copy()
    for col in df.columns:
        if str(col).endswith("_date") or str(col) in {"trade_date", "cal_date", "in_date", "out_date"}:
            df[col] = df[col].astype(str).str.replace("-", "", regex=False)
    return df


def _write_parquet(pq_path, pq_frame):
    """Write/merge parquet with dedup and sort."""
    pq_path.parent.mkdir(parents=True, exist_ok=True)
    pq_frame = _normalize_dates(pq_frame)
    if pq_path.exists():
        existing = pd.read_parquet(pq_path)
        for col in set(existing.columns) & set(pq_frame.columns):
            if pd.api.types.is_numeric_dtype(existing[col].dtype):
                pq_frame[col] = pd.to_numeric(pq_frame[col], errors="coerce")
            elif pd.api.types.is_numeric_dtype(pq_frame[col].dtype):
                existing[col] = pd.to_numeric(existing[col], errors="coerce")
        dedup_cols = [c for c in ["trade_date", "ts_code", "end_date", "holder_name", "index_code", "con_code", "in_date", "theme_code"] if c in existing.columns and c in pq_frame.columns]
        combined = pd.concat([existing, pq_frame], ignore_index=True)
        if dedup_cols:
            combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
        sort_cols = [c for c in ["trade_date", "end_date", "in_date", "index_code", "con_code", "ts_code"] if c in combined.columns][:3]
        if sort_cols:
            for _sc in sort_cols:
                combined[_sc] = combined[_sc].astype(str)
            combined = combined.sort_values(sort_cols)
        combined.to_parquet(pq_path, index=False)
    else:
        pq_frame.to_parquet(pq_path, index=False)


def _flat_parquet_path(csv_path, interface_name):
    """Resolve per-stock parquet path from a CSV path."""
    csv_path = Path(csv_path)
    stem = csv_path.stem
    parent = csv_path.parent
    root_dir = parent.parent if (parent.name.isdigit() and len(parent.name) == 4) else parent
    if interface_name in _COMBINED_PARQUET_INTERFACES:
        return root_dir / f"{interface_name}.parquet"
    prefix_map = {
        "index_daily": "index_daily_", "index_weekly": "index_weekly_", "index_monthly": "index_monthly_",
        "daily": "daily_", "daily_basic": "daily_basic_",
        "cyq_chips": "cyq_chips_", "cyq_perf": "cyq_perf_",
        "margin": "margin_", "margin_detail": "margin_detail_",
        "pledge_detail": "pledge_detail_", "pledge_stat": "pledge_stat_",
        "stk_auction_c": "stk_auction_c_", "stk_auction_o": "stk_auction_o_",
        "stk_factor_pro": "stk_factor_pro_",
    }
    prefix = prefix_map.get(interface_name, "")
    ts_code = stem[len(prefix):] if stem.startswith(prefix) else stem
    return root_dir / f"{ts_code}.parquet"


def write_multi_format_bundle(csv_path, frame, interface_name=None, write_parquet=False):
    """Write aggregated CSV files alongside per-date CSVs. Parquet is NOT written here — use rebuild_parquet.py."""
    csv_path = Path(csv_path)
    result = {"csv": str(csv_path)}
    if frame is None or frame.empty:
        return result

    for legacy_sidecar in (
        csv_path.with_name(f"{csv_path.stem}.agent.jsonl"),
        csv_path.with_name(f"{csv_path.stem}.agent.meta.json"),
    ):
        try:
            if legacy_sidecar.exists():
                legacy_sidecar.unlink()
        except Exception:
            pass

    if interface_name in _MONTHLY_COMBINED_INTERFACES:
        root_dir = csv_path.parent
        if root_dir.name.isdigit() and len(root_dir.name) == 4:
            root_dir = root_dir.parent
        _df = _normalize_dates(frame)
        if "trade_date" in _df.columns:
            _df["_ym"] = _df["trade_date"].astype(str).str[:6]
            for ym, grp in _df.groupby("_ym"):
                year = ym[:4]
                out_dir = root_dir / year
                out_dir.mkdir(parents=True, exist_ok=True)
                csv_path_monthly = out_dir / f"{ym}.csv"
                grp = grp.drop(columns=["_ym"], errors="ignore")
                if "trade_date" in grp.columns:
                    grp = grp.sort_values("trade_date")
                if csv_path_monthly.exists():
                    existing_csv = pd.read_csv(csv_path_monthly)
                    dedup_cols_csv = [c for c in ["trade_date", "ts_code", "end_date", "holder_name", "index_code", "con_code", "in_date", "theme_code"] if c in existing_csv.columns and c in grp.columns]
                    combined_csv = pd.concat([existing_csv, grp], ignore_index=True)
                    if dedup_cols_csv:
                        combined_csv = combined_csv.drop_duplicates(subset=dedup_cols_csv, keep="last")
                    if "trade_date" in combined_csv.columns:
                        combined_csv["trade_date"] = combined_csv["trade_date"].astype(str)
                        combined_csv = combined_csv.sort_values("trade_date")
                    combined_csv.to_csv(csv_path_monthly, index=False)
                else:
                    grp.to_csv(csv_path_monthly, index=False)

    if interface_name in _COMBINED_PARQUET_INTERFACES:
        parquet_path = _flat_parquet_path(csv_path, interface_name)
        csv_combined = parquet_path.with_suffix(".csv")
        df_write = _normalize_dates(frame)
        if csv_combined.exists():
            existing_csv = pd.read_csv(csv_combined)
            dedup_cols_csv = [c for c in ["trade_date", "ts_code", "end_date", "holder_name", "index_code", "con_code", "in_date", "theme_code"] if c in existing_csv.columns and c in df_write.columns]
            combined_csv = pd.concat([existing_csv, df_write], ignore_index=True)
            if dedup_cols_csv:
                combined_csv = combined_csv.drop_duplicates(subset=dedup_cols_csv, keep="last")
            combined_csv.to_csv(csv_combined, index=False)
        else:
            df_write.to_csv(csv_combined, index=False)

    return result
