#!/usr/bin/env python3
"""优先拉取4个主要指数的全量历史数据（日线、周线、月线）"""

import sys
import os
import time

sys.stdout = os.fdopen(sys.stdout.fileno(), "w", 1)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from utils.tushare_client import create_pro_api

DATA_DIR = "/Users/penghongming/quant-data/tushare/指数数据"

# 4个主要指数
PRIORITY_CODES = ["000001.SH", "399001.SZ", "399006.SZ", "000688.SH"]

INTERFACES = {
    "index_daily": {"page_limit": 8000, "subdir": "index_daily"},
    "index_weekly": {"page_limit": 2000, "subdir": "index_weekly"},
    "index_monthly": {"page_limit": 2000, "subdir": "index_monthly"},
}

SLEEP_BETWEEN = 2.0  # 秒，避免限速


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
                print(f"  [RATE LIMIT] Waiting 10s...")
                time.sleep(10)
                continue
            print(f"  [!] Error: {e}")
            time.sleep(5)
            break
        if df is None or df.empty:
            break
        all_rows.append(df)
        offset += len(df)
        time.sleep(1)
    if not all_rows:
        return pd.DataFrame()
    result = pd.concat(all_rows, ignore_index=True)
    for col in result.columns:
        if str(col).endswith("_date") or col in {"trade_date", "cal_date"}:
            result[col] = result[col].astype(str).str.replace("-", "", regex=False)
    return result


def write_parquet(ts_code, interface_name, data):
    subdir = INTERFACES[interface_name]["subdir"]
    path = os.path.join(DATA_DIR, subdir, f"{ts_code}.parquet")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
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
    print(f"[START] 优先拉取 {len(PRIORITY_CODES)} 个主要指数")
    print(f"Codes: {PRIORITY_CODES}")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    total_tasks = len(PRIORITY_CODES) * len(INTERFACES)
    completed = 0

    for ts_code in PRIORITY_CODES:
        for interface_name, cfg in INTERFACES.items():
            completed += 1
            print(f"[{completed}/{total_tasks}] {ts_code} [{interface_name}] ... ", end="", flush=True)

            data = fetch_data(pro, interface_name, ts_code, "19900101", "20260520", cfg["page_limit"])
            if data.empty:
                print("EMPTY")
            else:
                write_parquet(ts_code, interface_name, data)
                print(f"→ {len(data)} rows ({data['trade_date'].min()}~{data['trade_date'].max()})")

            time.sleep(SLEEP_BETWEEN)

    print(f"\n[DONE] {completed}/{total_tasks} 完成")
    print(f"Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
