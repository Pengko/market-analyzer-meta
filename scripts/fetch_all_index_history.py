#!/usr/bin/env python3
"""
Fetch full history for all index codes from index_basic_all.parquet.
Supports checkpoint/resume. Processes index_daily, index_weekly, index_monthly.
"""

import sys
import os
import time
import json
from pathlib import Path

sys.stdout = os.fdopen(sys.stdout.fileno(), "w", 1)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from utils.tushare_client import create_pro_api

INDEX_BASIC = "/Users/penghongming/quant-data/tushare/指数数据/index_basic/index_basic_all.parquet"
DATA_DIR = "/Users/penghongming/quant-data/tushare/指数数据"
CHECKPOINT = "/Users/penghongming/quant-data/tushare/指数数据/.fetch_index_checkpoint.json"

# Interface configs
INTERFACES = {
    "index_daily": {"page_limit": 8000, "subdir": "index_daily"},
    "index_weekly": {"page_limit": 2000, "subdir": "index_weekly"},
    "index_monthly": {"page_limit": 2000, "subdir": "index_monthly"},
}

REPORT_INTERVAL = 20
BATCH_SLEEP = 1.5  # 增加间隔避免限速
RATE_LIMIT_SLEEP = 10


def load_checkpoint():
    if os.path.exists(CHECKPOINT):
        with open(CHECKPOINT) as f:
            return json.load(f)
    return {}


def save_checkpoint(cp):
    with open(CHECKPOINT, "w") as f:
        json.dump(cp, f, indent=2)


def get_ts_codes():
    df = pd.read_parquet(INDEX_BASIC)
    codes = sorted(df["ts_code"].dropna().unique().tolist())
    return codes


def fetch_data(pro, api_name, ts_code, start_date, end_date, page_limit):
    all_rows = []
    offset = 0
    while True:
        try:
            df = getattr(pro, api_name)(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                limit=page_limit,
                offset=offset,
            )
        except Exception as e:
            msg = str(e)
            if "速度过快" in msg or "too fast" in msg.lower():
                print(f"  [RATE LIMIT] Waiting {RATE_LIMIT_SLEEP}s...")
                time.sleep(RATE_LIMIT_SLEEP)
                continue
            print(f"  [!] API Error: {e}")
            time.sleep(5)
            break
        if df is None or df.empty:
            break
        all_rows.append(df)
        offset += len(df)
        time.sleep(BATCH_SLEEP)
    if not all_rows:
        return pd.DataFrame()
    result = pd.concat(all_rows, ignore_index=True)
    for col in result.columns:
        if str(col).endswith("_date") or col in {"trade_date", "cal_date"}:
            result[col] = result[col].astype(str).str.replace("-", "", regex=False)
    return result


def write_parquet(ts_code, interface_name, data):
    subdir = INTERFACES[interface_name]["subdir"]
    path = Path(DATA_DIR) / subdir / f"{ts_code}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, data], ignore_index=True)
        dedup = [c for c in ["trade_date", "ts_code"] if c in combined.columns]
        if dedup:
            combined = combined.drop_duplicates(subset=dedup, keep="last")
        if "trade_date" in combined.columns:
            combined = combined.sort_values("trade_date")
        combined.to_parquet(path, index=False)
    else:
        data.to_parquet(path, index=False)
    return path


def main():
    pro = create_pro_api(timeout=30)
    codes = get_ts_codes()
    cp = load_checkpoint()
    total_tasks = len(codes) * len(INTERFACES)
    completed = sum(1 for v in cp.values() if v)

    print(f"[START] Total codes: {len(codes)}, Total tasks: {total_tasks}, Already done: {completed}")
    print(f"[START] Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("")

    for i, ts_code in enumerate(codes, 1):
        for interface_name, cfg in INTERFACES.items():
            key = f"{ts_code}::{interface_name}"
            if cp.get(key):
                continue

            # Determine date range
            subdir = cfg["subdir"]
            path = Path(DATA_DIR) / subdir / f"{ts_code}.parquet"
            if path.exists():
                existing = pd.read_parquet(path)
                if not existing.empty and "trade_date" in existing.columns:
                    max_d = existing["trade_date"].max()
                    min_d = existing["trade_date"].min()
                    if max_d >= "20260520":
                        cp[key] = True
                        continue
                    is_full = min_d < "20250101" or len(existing) >= 10
                    if is_full:
                        start_dt = max_d
                    else:
                        start_dt = "19900101"
                else:
                    start_dt = "19900101"
                    existing = None
            else:
                start_dt = "19900101"
                existing = None

            action = "delta" if (existing is not None and start_dt != "19900101") else "FULL"
            print(f"[{i:4d}/{len(codes)}] {ts_code} [{interface_name}] {action} ... ", end="", flush=True)

            data = fetch_data(pro, interface_name, ts_code, start_dt, "20260520", cfg["page_limit"])
            if data.empty:
                print("EMPTY")
                cp[key] = True  # mark as done even if empty to avoid retry
                save_checkpoint(cp)
                continue

            write_parquet(ts_code, interface_name, data)
            cp[key] = True
            save_checkpoint(cp)
            print(f"→ {len(data)} rows")
            time.sleep(BATCH_SLEEP)

    done = sum(1 for v in cp.values() if v)
    print(f"\n[DONE] Completed: {done}/{total_tasks}")
    print(f"[DONE] Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
