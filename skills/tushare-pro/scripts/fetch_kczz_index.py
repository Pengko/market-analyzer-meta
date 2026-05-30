#!/usr/bin/env python3
"""补拉科创综指 000680.SH 三个接口"""

import sys
import os
import time

sys.stdout = os.fdopen(sys.stdout.fileno(), "w", 1)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from utils.tushare_client import create_pro_api

DATA_DIR = "/Users/penghongming/quant-data/tushare/指数数据"
TS_CODE = "000680.SH"

INTERFACES = {
    "index_daily": {"page_limit": 8000, "subdir": "index_daily"},
    "index_weekly": {"page_limit": 2000, "subdir": "index_weekly"},
    "index_monthly": {"page_limit": 2000, "subdir": "index_monthly"},
}


def fetch_data(pro, api_name, start_date, end_date, page_limit):
    all_rows = []
    offset = 0
    while True:
        try:
            df = getattr(pro, api_name)(
                ts_code=TS_CODE,
                start_date=start_date,
                end_date=end_date,
                limit=page_limit,
                offset=offset,
            )
        except Exception as e:
            print(f"  [!] Error: {e}")
            time.sleep(5)
            break
        if df is None or df.empty:
            break
        all_rows.append(df)
        if len(df) < page_limit:
            break
        offset += page_limit
        time.sleep(1)
    if not all_rows:
        return pd.DataFrame()
    result = pd.concat(all_rows, ignore_index=True)
    for col in result.columns:
        if str(col).endswith("_date") or col in {"trade_date", "cal_date"}:
            result[col] = result[col].astype(str).str.replace("-", "", regex=False)
    return result


def write_parquet(data, subdir):
    path = os.path.join(DATA_DIR, subdir, f"{TS_CODE}.parquet")
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
    print(f"[START] 补拉 {TS_CODE} (科创综指)")
    
    for interface_name, cfg in INTERFACES.items():
        print(f"[{interface_name}] fetching ... ", end="", flush=True)
        data = fetch_data(pro, interface_name, "19900101", "20260520", cfg["page_limit"])
        if data.empty:
            print("EMPTY")
        else:
            write_parquet(data, cfg["subdir"])
            print(f"→ {len(data)} rows ({data['trade_date'].min()}~{data['trade_date'].max()})")
        time.sleep(2)

if __name__ == "__main__":
    main()
