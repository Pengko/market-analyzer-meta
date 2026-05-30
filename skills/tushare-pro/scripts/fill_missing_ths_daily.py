"""Fetch 3 timed-out ths_daily dates and merge into per-stock parquets."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pyarrow.parquet as pq
from utils.tushare_client import create_pro_api

pro = create_pro_api()
DATA_DIR = Path("/Users/penghongming/quant-data/tushare/股票数据/theme_data/ths_daily")

MISSING = ['20250311', '20250821', '20251113']

def norm(df):
    for col in df.columns:
        if str(col).endswith("_date") or col in {"trade_date", "cal_date"}:
            df[col] = df[col].astype(str).str.replace("-", "", regex=False)
    return df

def coerce(df):
    for col in df.columns:
        if col in ("trade_date", "cal_date") or str(col).endswith("_date"):
            continue
        if df[col].dtype == "object":
            try:
                df[col] = df[col].astype("float64", errors="raise")
            except (ValueError, TypeError):
                pass
    return df

# Fetch missing dates with longer timeout
all_chunks = []
for date in MISSING:
    print(f"Fetching {date}...")
    try:
        # Retry with longer timeout
        for attempt in range(3):
            try:
                df = pro.ths_daily(trade_date=date)
                if df is not None and len(df) > 0:
                    break
            except Exception as e:
                print(f"  Attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    time.sleep(5)
                else:
                    raise
        if df is not None and len(df) > 0:
            print(f"  got {len(df)} rows")
            all_chunks.append(df)
        else:
            print(f"  no data")
    except Exception as e:
        print(f"  ERROR: {e}")

if not all_chunks:
    print("No data fetched, nothing to merge.")
    sys.exit(0)

full = pd.concat(all_chunks, ignore_index=True)
full = norm(full)
full = coerce(full)
print(f"\nTotal new rows: {len(full)}, unique stocks: {full['ts_code'].nunique()}")

# Merge into per-stock parquets
t0 = time.time()
groups = list(full.groupby('ts_code'))
n = len(groups)
for i, (code, new_data) in enumerate(groups):
    if (i + 1) % 200 == 0:
        print(f"  {i+1}/{n} ({time.time()-t0:.0f}s)")

    new_data = new_data.drop_duplicates(subset=["trade_date", "ts_code"], keep="last")

    parquet_path = DATA_DIR / f"{code}.parquet"
    csv_path = DATA_DIR / f"{code}.csv"

    if parquet_path.exists():
        existing = pq.read_table(parquet_path).to_pandas()
        combined = pd.concat([existing, new_data], ignore_index=True)
        combined = combined.drop_duplicates(subset=["trade_date", "ts_code"], keep="last").sort_values("trade_date")
    else:
        combined = new_data.sort_values("trade_date")

    combined.to_parquet(parquet_path, index=False)
    combined.to_csv(csv_path, index=False)

print(f"\nDone in {time.time()-t0:.0f}s")
print(f"Updated {n} stock parquets")
