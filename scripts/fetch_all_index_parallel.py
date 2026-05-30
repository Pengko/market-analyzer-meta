#!/usr/bin/env python3
"""Parallel fetch full history for all index codes."""

import sys
import os
import time
import json
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.stdout = os.fdopen(sys.stdout.fileno(), "w", 1)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from utils.tushare_client import create_pro_api

INDEX_BASIC = "/Users/penghongming/quant-data/tushare/指数数据/index_basic/index_basic_all.parquet"
DATA_DIR = "/Users/penghongming/quant-data/tushare/指数数据"
CHECKPOINT = "/Users/penghongming/quant-data/tushare/指数数据/.fetch_index_checkpoint_parallel.json"
NUM_WORKERS = 8

INTERFACES = {
    "index_daily": {"page_limit": 8000, "subdir": "index_daily"},
    "index_weekly": {"page_limit": 2000, "subdir": "index_weekly"},
    "index_monthly": {"page_limit": 2000, "subdir": "index_monthly"},
}

BATCH_SLEEP = 0.5
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


def worker_fetch(worker_id, codes_chunk):
    """Each worker processes a chunk of codes."""
    pro = create_pro_api(timeout=30)
    cp = load_checkpoint()
    stats = {"done": 0, "empty": 0, "error": 0}

    for i, ts_code in enumerate(codes_chunk, 1):
        for interface_name, cfg in INTERFACES.items():
            key = f"{ts_code}::{interface_name}"
            if cp.get(key):
                continue

            # Determine start date
            subdir = cfg["subdir"]
            path = Path(DATA_DIR) / subdir / f"{ts_code}.parquet"
            if path.exists():
                try:
                    existing = pd.read_parquet(path)
                    if not existing.empty and "trade_date" in existing.columns:
                        max_d = existing["trade_date"].max()
                        min_d = existing["trade_date"].min()
                        if max_d >= datetime.now().strftime("%Y%m%d"):
                            cp[key] = True
                            continue
                        is_full = min_d < "20250101" or len(existing) >= 10
                        start_dt = max_d if is_full else "19900101"
                    else:
                        start_dt = "19900101"
                except:
                    start_dt = "19900101"
            else:
                start_dt = "19900101"

            # Fetch
            action = "delta" if start_dt != "19900101" else "FULL"
            print(f"[W{worker_id}] [{i}/{len(codes_chunk)}] {ts_code} [{interface_name}] {action} ... ", end="", flush=True)

            all_rows = []
            offset = 0
            while True:
                try:
                    df = getattr(pro, interface_name)(
                        ts_code=ts_code,
                        start_date=start_dt,
                        end_date=datetime.now().strftime("%Y%m%d"),
                        limit=cfg["page_limit"],
                        offset=offset,
                    )
                except Exception as e:
                    msg = str(e)
                    if "速度过快" in msg:
                        print(f"RATE... ")
                        time.sleep(RATE_LIMIT_SLEEP)
                        continue
                    print(f"ERROR: {e}")
                    time.sleep(5)
                    break
                if df is None or df.empty:
                    break
                all_rows.append(df)
                offset += len(df)
                time.sleep(BATCH_SLEEP)

            if not all_rows:
                print("EMPTY")
                cp[key] = True
                stats["empty"] += 1
                save_checkpoint(cp)
                continue

            data = pd.concat(all_rows, ignore_index=True)
            for col in data.columns:
                if str(col).endswith("_date") or col in {"trade_date", "cal_date"}:
                    data[col] = data[col].astype(str).str.replace("-", "", regex=False)

            # Write
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

            cp[key] = True
            stats["done"] += 1
            save_checkpoint(cp)
            print(f"→ {len(data)} rows")
            time.sleep(BATCH_SLEEP)

    return stats


def main():
    codes = get_ts_codes()
    cp = load_checkpoint()
    total_tasks = len(codes) * len(INTERFACES)
    completed = sum(1 for v in cp.values() if v)

    print(f"[START] Codes: {len(codes)}, Tasks: {total_tasks}, Done: {completed}")
    print(f"[START] Workers: {NUM_WORKERS}, Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Split codes into chunks
    chunk_size = len(codes) // NUM_WORKERS
    chunks = []
    for i in range(NUM_WORKERS):
        start = i * chunk_size
        end = (i + 1) * chunk_size if i < NUM_WORKERS - 1 else len(codes)
        chunks.append(codes[start:end])

    with ProcessPoolExecutor(max_workers=NUM_WORKERS) as executor:
        futures = {executor.submit(worker_fetch, i, chunk): i for i, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            wid = futures[future]
            try:
                stats = future.result()
                print(f"\n[W{wid}] Done: {stats}")
            except Exception as e:
                print(f"\n[W{wid}] Error: {e}")

    done = sum(1 for v in load_checkpoint().values() if v)
    print(f"\n[DONE] Total: {done}/{total_tasks}")
    print(f"[DONE] Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
