#!/usr/bin/env python3
"""Fetch full weekly history for all index codes and write to flat parquet files."""

import sys
import os
import time

# Force unbuffered output so progress prints immediately
sys.stdout = os.fdopen(sys.stdout.fileno(), "w", 1)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from utils.tushare_client import create_pro_api

WEEKLY_DIR = "/Users/penghongming/quant-data/tushare/指数数据/index_weekly"
DEFAULT_START = "19900101"
PAGE_LIMIT = 2000
BATCH_SLEEP = 0.5
REPORT_INTERVAL = 10


def get_ts_codes_from_csv() -> list[str]:
    """Extract unique ts_codes from existing weekly CSV files."""
    codes = set()
    for root, _dirs, files in os.walk(WEEKLY_DIR):
        for f in files:
            if f.endswith(".csv"):
                df = pd.read_csv(os.path.join(root, f))
                codes.update(df["ts_code"].unique())
    return sorted(codes)


def load_existing_parquet(ts_code: str) -> pd.DataFrame | None:
    path = os.path.join(WEEKLY_DIR, f"{ts_code}.parquet")
    if os.path.exists(path):
        df = pd.read_parquet(path)
        if not df.empty:
            return df
    return None


def needs_fetch(ts_code: str) -> tuple[bool, str, str]:
    """Check if code needs fetching and determine date range."""
    existing = load_existing_parquet(ts_code)
    latest = "20260520"
    if existing is None or existing.empty:
        return True, DEFAULT_START, latest
    max_d = existing["trade_date"].max()
    min_d = existing["trade_date"].min()
    if max_d >= latest:
        return False, "", latest
    is_full_history = min_d < "20250101" or len(existing) >= 10
    if is_full_history:
        return True, max_d, latest
    return True, DEFAULT_START, latest


def fetch_weekly(pro, ts_code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch all weekly data with pagination."""
    all_rows = []
    offset = 0
    while True:
        try:
            df = pro.index_weekly(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                limit=PAGE_LIMIT,
                offset=offset,
            )
        except Exception as e:
            print(f"  [!] Error: {e}")
            time.sleep(2)
            break
        if df is None or df.empty:
            break
        all_rows.append(df)
        if len(df) < PAGE_LIMIT:
            break
        offset += PAGE_LIMIT
        time.sleep(BATCH_SLEEP)
    if not all_rows:
        return pd.DataFrame()
    result = pd.concat(all_rows, ignore_index=True)
    for col in result.columns:
        if str(col).endswith("_date") or col in {"trade_date", "cal_date"}:
            result[col] = result[col].astype(str).str.replace("-", "", regex=False)
    return result


def main():
    pro = create_pro_api(timeout=30)
    codes = get_ts_codes_from_csv()
    print(f"[START] Total codes to process: {len(codes)}")
    print(f"[START] Parquet dir: {WEEKLY_DIR}")
    print(f"[START] Starting at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("")

    stats = {"fetched": 0, "skipped": 0, "empty": 0, "error": 0}
    for i, ts_code in enumerate(codes, 1):
        need_fetch, start_dt, end_dt = needs_fetch(ts_code)
        if not need_fetch:
            stats["skipped"] += 1
            if i % REPORT_INTERVAL == 0:
                print(f"[{i:4d}/{len(codes)}] {ts_code}: skipped (latest)")
            continue

        action = "delta" if start_dt != DEFAULT_START else "FULL"
        print(f"[{i:4d}/{len(codes)}] {ts_code}: {action} {start_dt}~{end_dt} ... ", end="", flush=True)

        new_data = fetch_weekly(pro, ts_code, start_dt, end_dt)
        if new_data.empty:
            stats["empty"] += 1
            print("EMPTY")
            continue

        existing = load_existing_parquet(ts_code)
        if existing is not None:
            combined = pd.concat([existing, new_data], ignore_index=True)
            dedup_cols = [c for c in ["trade_date", "ts_code"] if c in combined.columns]
            if dedup_cols:
                combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            if "trade_date" in combined.columns:
                combined = combined.sort_values("trade_date")
        else:
            combined = new_data

        combined.to_parquet(os.path.join(WEEKLY_DIR, f"{ts_code}.parquet"), index=False)
        stats["fetched"] += 1
        print(f"→ {len(combined)} rows ({new_data['trade_date'].min()}~{new_data['trade_date'].max()})")
        time.sleep(BATCH_SLEEP)

    print(f"\n[DONE] Fetched: {stats['fetched']}, Skipped: {stats['skipped']}, "
          f"Empty: {stats['empty']}, Error: {stats['error']}")
    print(f"[DONE] Finished at: {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
