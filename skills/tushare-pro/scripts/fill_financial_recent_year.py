#!/usr/bin/env python3
"""
Batch-fill financial interfaces for the recent one-year window.

Features:
- resume from prior successful/no-data codes
- modest concurrency to avoid hours-long strictly serial runs
"""

from __future__ import annotations

import argparse
import csv
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auto_fill_data as af
from utils.tushare_client import create_pro_api


FINANCIAL_INTERFACES = [
    "forecast",
    "express",
    "fina_mainbz",
    "disclosure_date",
]

THREAD_LOCAL = threading.local()


def parse_args():
    parser = argparse.ArgumentParser(description="Fill financial interfaces for the recent year")
    parser.add_argument("--start-date", default="20250423", help="Start date in YYYYMMDD")
    parser.add_argument("--end-date", default="20260423", help="End date in YYYYMMDD")
    parser.add_argument(
        "--interfaces",
        nargs="+",
        default=FINANCIAL_INTERFACES,
        help="Interfaces to fill",
    )
    parser.add_argument("--workers", type=int, default=4, help="Concurrent workers per interface")
    return parser.parse_args()


def get_pro():
    pro = getattr(THREAD_LOCAL, "pro", None)
    if pro is None:
        pro = create_pro_api(timeout=30)
        THREAD_LOCAL.pro = pro
    return pro


def load_code_list():
    stock_basic = pd.read_csv(af.DATA_DIR / "stock_basic" / "stock_basic_non_st.csv", usecols=["ts_code"])
    return stock_basic["ts_code"].dropna().astype(str).tolist()


def state_dir():
    path = REPO_ROOT / "logs" / "financial_fill_state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path(interface_name: str) -> Path:
    return state_dir() / f"{interface_name}.csv"


def load_state(interface_name: str):
    path = state_path(interface_name)
    done = {}
    if not path.exists():
        return done
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            ts_code = str(row.get("ts_code", "")).strip()
            status = str(row.get("status", "")).strip()
            if ts_code and status:
                done[ts_code] = status
    return done


def append_state(interface_name: str, rows):
    if not rows:
        return
    path = state_path(interface_name)
    exists = path.exists()
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ts_code", "status", "rows", "updated_at"])
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(row)


def infer_existing_success_codes(config):
    output_dir = af.get_root_dir(config) / config["path"]
    prefix = config["prefix"]
    if not output_dir.exists():
        return set()
    codes = set()
    for csv_file in output_dir.rglob(f"{prefix}*.csv"):
        if any(part.startswith("_") for part in csv_file.relative_to(output_dir).parts[:-1]):
            continue
        name = csv_file.name
        if name.startswith(prefix) and name.endswith(".csv"):
            code = name[len(prefix):-4]
            if code:
                codes.add(code)
    return codes


def write_frame(output_dir: Path, config: dict, code: str, frame: pd.DataFrame):
    date_col = config["date_col"]
    save_granularity = config.get("save_granularity", "year_stock")
    group_key = frame[date_col].astype(str).str[:4] if save_granularity == "year_stock" else frame[date_col]

    for item_key, part in frame.groupby(group_key):
        item_key = str(item_key)
        if save_granularity == "year_stock":
            output_dir_for_file = output_dir / item_key
            output_dir_for_file.mkdir(parents=True, exist_ok=True)
            output_file = output_dir_for_file / f"{config['prefix']}{code}.csv"
        else:
            year = item_key[:4]
            month = item_key[4:6]
            day = item_key[6:8]
            output_dir_for_file = output_dir / year / month / day
            output_dir_for_file.mkdir(parents=True, exist_ok=True)
            output_file = output_dir_for_file / f"{config['prefix']}{code}.csv"

        if output_file.exists() and output_file.stat().st_size > 0:
            existing = pd.read_csv(output_file, low_memory=False)
            merged = pd.concat([existing, part], ignore_index=True)
        else:
            merged = part.copy()

        dedup_cols = config.get("dedup_cols")
        if not dedup_cols:
            dedup_cols = [c for c in [date_col, "ts_code", "end_date", "f_ann_date", "bz_item"] if c in merged.columns]
        if dedup_cols:
            merged = merged.drop_duplicates(subset=dedup_cols, keep="last")
        else:
            merged = merged.drop_duplicates(keep="last")
        sort_cols = [c for c in [date_col, "end_date", "f_ann_date", "bz_item"] if c in merged.columns]
        if sort_cols:
            merged = merged.sort_values(sort_cols)

        merged.to_csv(output_file, index=False)
        af.shared_write_multi_format_bundle(output_file, merged, interface_name=config["api"])


def fetch_one_code(interface_name: str, config: dict, ts_code: str, start_date: str, end_date: str):
    pro = get_pro()
    api_func = getattr(pro, config["api"])
    try:
        df = api_func(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception as exc:
        return {"ts_code": ts_code, "status": "error", "rows": 0, "error": str(exc)[:200]}

    if df is None or df.empty:
        return {"ts_code": ts_code, "status": "no_data", "rows": 0}

    date_col = config["date_col"]
    if date_col in df.columns:
        df[date_col] = df[date_col].astype(str).str.replace("-", "", regex=False)

    output_dir = af.get_root_dir(config) / config["path"]
    output_dir.mkdir(parents=True, exist_ok=True)
    write_frame(output_dir, config, ts_code, df)
    return {"ts_code": ts_code, "status": "success", "rows": len(df)}


def run_interface(interface_name: str, config: dict, start_date: str, end_date: str, workers: int):
    existing_success = infer_existing_success_codes(config)
    state = load_state(interface_name)
    skipped = {code for code, status in state.items() if status in {"success", "no_data"}}
    skipped |= existing_success

    code_list = load_code_list()
    pending = [code for code in code_list if code not in skipped]
    af.log(
        f"{interface_name}: 股票总数 {len(code_list)}，已跳过 {len(skipped)}，待处理 {len(pending)}，并发 {workers}"
    )
    if not pending:
        return

    success = 0
    no_data = 0
    errors = 0
    processed = 0
    buffer = []

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {
            executor.submit(fetch_one_code, interface_name, config, code, start_date, end_date): code
            for code in pending
        }
        for future in as_completed(future_map):
            processed += 1
            result = future.result()
            status = result["status"]
            if status == "success":
                success += 1
            elif status == "no_data":
                no_data += 1
            else:
                errors += 1
                af.log(f"{interface_name} {result['ts_code']} 请求失败: {result.get('error', '')}", "WARNING")

            buffer.append(
                {
                    "ts_code": result["ts_code"],
                    "status": status,
                    "rows": result.get("rows", 0),
                    "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
            )
            if len(buffer) >= 50:
                append_state(interface_name, buffer)
                buffer = []

            if processed % 100 == 0:
                af.log(
                    f"{interface_name}: 进度 {processed}/{len(pending)} | success {success} | no_data {no_data} | error {errors}"
                )

    append_state(interface_name, buffer)
    af.log(
        f"{interface_name}: 完成 | success {success} | no_data {no_data} | error {errors}"
    )


def main():
    args = parse_args()
    trade_dates = af.get_trade_dates(start_date=args.start_date, end_date=args.end_date)
    if not trade_dates:
        raise SystemExit("No trade dates resolved for requested window")

    start_date = trade_dates[0]
    end_date = trade_dates[-1]
    af.log(f"财务数据近一年补全窗口: {start_date} ~ {end_date} ({len(trade_dates)} 个交易日)")
    for name in args.interfaces:
        config = af.STOCK_INTERFACE_CONFIG["by_stock"].get(name)
        if not config:
            af.log(f"跳过未注册财务接口: {name}", "WARNING")
            continue
        af.log("")
        af.log("#" * 70)
        af.log(f"开始财务接口补全: {name}")
        af.log("#" * 70)
        run_interface(name, config, start_date, end_date, args.workers)


if __name__ == "__main__":
    main()
