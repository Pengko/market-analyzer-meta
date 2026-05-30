#!/usr/bin/env python3
"""
从 CSV 源数据批量重建 parquet 文件。
CSV 是主存储，parquet 是从 CSV 导出的只读副本。
"""

import os
import sys
import time
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

MAX_WORKERS = min(16, os.cpu_count() * 2 or 8)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.files import (
    _write_parquet,
    _normalize_dates,
    _FLAT_PARQUET_INTERFACES,
    _COMBINED_PARQUET_INTERFACES,
    _PER_STOCK_BY_DATE_INTERFACES,
    _YEARLY_COMBINED_INTERFACES,
    _MONTHLY_COMBINED_INTERFACES,
)

DATA_ROOT = Path("/Users/penghongming/quant-data/tushare/股票数据")
INDEX_ROOT = Path("/Users/penghongming/quant-data/tushare/指数数据")
FINANCIAL_ROOT = Path("/Users/penghongming/quant-data/tushare/财务数据")


def _read_csvs(chunks):
    """Concat a list of CSV DataFrames."""
    frames = [pd.read_csv(f, low_memory=False) for f in chunks if f.stat().st_size > 0]
    frames = [f for f in frames if not f.empty]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _rebuild_flat_per_stock(interface, root):
    """
    Per-stock flat stored as {root}/{year}/{prefix}{code}.csv → {root}/{code}.parquet.
    """
    prefix_map = {
        "daily": "daily_", "daily_basic": "daily_basic_",
        "cyq_chips": "cyq_chips_", "cyq_perf": "cyq_perf_",
        "margin": "margin_", "margin_detail": "margin_detail_",
        "pledge_detail": "pledge_detail_", "pledge_stat": "pledge_stat_",
        "stk_auction_c": "stk_auction_c_", "stk_auction_o": "stk_auction_o_",
        "stk_factor_pro": "stk_factor_pro_",
        "index_daily": "index_daily_", "index_weekly": "index_weekly_", "index_monthly": "index_monthly_",
    }
    prefix = prefix_map.get(interface, "")
    stock_files = defaultdict(list)
    for csv_file in sorted(root.rglob(f"{prefix}*.csv")):
        code = csv_file.stem[len(prefix):]
        stock_files[code].append(csv_file)

    lock = threading.Lock()
    count = 0

    def _process(code, files):
        combined = _read_csvs(files)
        if combined.empty:
            return 0
        pq_path = root / f"{code}.parquet"
        _write_parquet(pq_path, combined)
        return 1

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_process, code, files) for code, files in stock_files.items()}
        for f in as_completed(futs):
            with lock:
                count += f.result()
    return count


def _rebuild_combined(interface, root):
    """Combined flat: {root}/{interface}.csv → {root}/{interface}.parquet"""
    csv_path = root / f"{interface}.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path, low_memory=False)
        if not df.empty:
            pq_path = csv_path.with_suffix(".parquet")
            _write_parquet(pq_path, df)
            return 1
    return 0


def _rebuild_per_stock_by_date(interface, root):
    """Per-stock by date: {root}/{year}/{prefix}{date}_{code}.csv → {root}/{code}.parquet"""
    prefix_map = {
        "moneyflow": "moneyflow_", "moneyflow_ths": "moneyflow_ths_",
        "moneyflow_ind_ths": "moneyflow_ind_ths_", "moneyflow_cnt_ths": "moneyflow_cnt_ths_",
        "stk_nineturn": "stk_nineturn_",
        "ths_daily": "ths_daily_", "dc_daily": "dc_daily_",
        "sw_daily": "sw_daily_",
    }
    prefix = prefix_map.get(interface, "")
    stock_files = defaultdict(list)
    for csv_file in sorted(root.rglob(f"{prefix}*.csv")):
        stem = csv_file.stem[len(prefix):]
        code = stem[8:]  # after YYYYMMDD_
        stock_files[code].append(csv_file)

    lock = threading.Lock()
    count = 0

    def _process(code, files):
        combined = _read_csvs(files)
        if combined.empty:
            return 0
        pq_path = root / f"{code}.parquet"
        _write_parquet(pq_path, combined)
        return 1

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_process, code, files) for code, files in stock_files.items()}
        for f in as_completed(futs):
            with lock:
                count += f.result()
    return count


def _rebuild_yearly(interface, root):
    """Yearly combined: {root}/{year}/*.csv → {root}/{year}.parquet"""
    year_dirs = [d for d in sorted(root.iterdir()) if d.is_dir() and d.name.isdigit() and len(d.name) == 4]

    lock = threading.Lock()
    count = 0

    def _process(year_dir):
        combined = _read_csvs(sorted(year_dir.glob("*.csv")))
        if combined.empty:
            return 0
        pq_path = root / f"{year_dir.name}.parquet"
        _write_parquet(pq_path, combined)
        return 1

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, 6)) as pool:
        futs = {pool.submit(_process, d) for d in year_dirs}
        for f in as_completed(futs):
            with lock:
                count += f.result()
    return count


def _rebuild_monthly(interface, root):
    """Monthly combined: {root}/{year}/{month}.csv → {root}/{year}.parquet"""
    year_dirs = [d for d in sorted(root.iterdir()) if d.is_dir() and d.name.isdigit() and len(d.name) == 4]

    lock = threading.Lock()
    count = 0

    def _process(year_dir):
        combined = _read_csvs(sorted(year_dir.glob("*.csv")))
        if combined.empty:
            return 0
        pq_path = root / f"{year_dir.name}.parquet"
        _write_parquet(pq_path, combined)
        return 1

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, 6)) as pool:
        futs = {pool.submit(_process, d) for d in year_dirs}
        for f in as_completed(futs):
            with lock:
                count += f.result()
    return count


def _rebuild_combined_csv(interface, root):
    """Rebuild combined CSV from per-date CSVs for _COMBINED_PARQUET_INTERFACES."""
    csv_path = root / f"{interface}.csv"
    chunks = []
    for csv_file in sorted(root.rglob("*.csv")):
        if csv_file.name == csv_path.name:
            continue
        try:
            df = pd.read_csv(csv_file, low_memory=False)
            if not df.empty:
                chunks.append(df)
        except Exception:
            pass
    if not chunks:
        return
    combined = pd.concat(chunks, ignore_index=True)
    combined = _normalize_dates(combined)
    dedup_cols = [c for c in ["trade_date", "ts_code", "end_date", "holder_name", "index_code", "con_code", "in_date", "theme_code"] if c in combined.columns]
    if dedup_cols:
        combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
    combined.to_csv(csv_path, index=False)


def rebuild_interface(interface):
    """Rebuild all parquet for one interface."""
    roots = []
    if interface in _YEARLY_COMBINED_INTERFACES or interface in _MONTHLY_COMBINED_INTERFACES or interface in _PER_STOCK_BY_DATE_INTERFACES:
        roots.append(DATA_ROOT / interface)
    if interface not in _YEARLY_COMBINED_INTERFACES and interface not in _MONTHLY_COMBINED_INTERFACES:
        roots.append(DATA_ROOT / interface)

    theme_paths = {
        "dc_concept": DATA_ROOT / "theme_data/dc_concept",
        "dc_concept_cons": DATA_ROOT / "theme_data/dc_concept_cons",
        "kpl_concept_cons": DATA_ROOT / "theme_data/kpl_concept_cons",
        "kpl_list": DATA_ROOT / "theme_data/kpl_list",
        "ths_member": DATA_ROOT / "theme_data/ths_member",
        "ths_daily": DATA_ROOT / "theme_data/ths_daily",
        "dc_daily": DATA_ROOT / "theme_data/dc_daily",
        "dc_member": DATA_ROOT / "theme_data/dc_member",
        "ths_index": DATA_ROOT / "theme_data/ths_index",
        "dc_index": DATA_ROOT / "theme_data/dc_index",
    }
    if interface in theme_paths:
        roots.append(theme_paths[interface])

    index_interfaces = {"index_daily", "index_weekly", "index_monthly", "index_weight", "index_member"}
    if interface in index_interfaces:
        roots = [INDEX_ROOT / interface]

    financial_interfaces = {"forecast", "express", "fina_mainbz", "disclosure_date", "income", "balancesheet", "cashflow", "fina_indicator", "stk_holdernumber"}
    if interface in financial_interfaces:
        roots = [FINANCIAL_ROOT / interface]

    total = 0
    for root in set(roots):
        if not root.exists():
            continue
        if interface in _COMBINED_PARQUET_INTERFACES:
            total += _rebuild_combined(interface, root)
            _rebuild_combined_csv(interface, root)
        elif interface in _PER_STOCK_BY_DATE_INTERFACES:
            total += _rebuild_per_stock_by_date(interface, root)
        elif interface in _YEARLY_COMBINED_INTERFACES:
            total += _rebuild_yearly(interface, root)
        elif interface in _MONTHLY_COMBINED_INTERFACES:
            total += _rebuild_monthly(interface, root)
        elif interface in _FLAT_PARQUET_INTERFACES:
            total += _rebuild_flat_per_stock(interface, root)
        else:
            total += _rebuild_flat_per_stock(interface, root) + _rebuild_yearly(interface, root)
    return total


def main():
    import argparse
    parser = argparse.ArgumentParser(description="从 CSV 批量重建 parquet")
    parser.add_argument("--interfaces", default="", help="指定接口，逗号分隔；空=重建所有")
    parser.add_argument("--list", action="store_true", help="列出所有可重建的接口")
    parser.add_argument("--clean", action="store_true", help="重建前删除旧 parquet 文件")
    args = parser.parse_args()

    all_interfaces = sorted(
        _FLAT_PARQUET_INTERFACES
        | _COMBINED_PARQUET_INTERFACES
        | _PER_STOCK_BY_DATE_INTERFACES
        | _YEARLY_COMBINED_INTERFACES
        | _MONTHLY_COMBINED_INTERFACES
    )

    if args.list:
        for name in all_interfaces:
            cats = []
            if name in _YEARLY_COMBINED_INTERFACES: cats.append("yearly")
            if name in _MONTHLY_COMBINED_INTERFACES: cats.append("monthly")
            if name in _COMBINED_PARQUET_INTERFACES: cats.append("combined")
            if name in _PER_STOCK_BY_DATE_INTERFACES: cats.append("per_stock_by_date")
            if name in _FLAT_PARQUET_INTERFACES and name not in _COMBINED_PARQUET_INTERFACES: cats.append("flat")
            print(f"  {name} ({', '.join(cats)})")
        return

    selected = [s.strip() for s in args.interfaces.split(",") if s.strip()] if args.interfaces else all_interfaces
    unknown = [s for s in selected if s not in all_interfaces]
    if unknown:
        print(f"未知接口: {unknown}")
        return 1

    started = time.monotonic()
    for name in selected:
        print(f"\n{'=' * 50}")
        print(f"重建 {name} ...")
        if args.clean:
            import glob as _glob
            for root in [DATA_ROOT, INDEX_ROOT, FINANCIAL_ROOT]:
                for pq in _glob.glob(f"{root}/{name}/**/*.parquet", recursive=True):
                    Path(pq).unlink()
        t0 = time.monotonic()
        count = rebuild_interface(name)
        dt = time.monotonic() - t0
        print(f"  ✅ {name}: {count} parquet ({dt:.0f}s)")
    elapsed = time.monotonic() - started
    print(f"\n完成，总耗时 {elapsed:.0f}s")


if __name__ == "__main__":
    raise SystemExit(main())
