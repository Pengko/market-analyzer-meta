"""Parallel migration for stk_factor_pro: flatten year-dir per-stock parquets.

Skips stocks that already have a flat parquet.
"""
import sys, time, math
from pathlib import Path
from collections import defaultdict
from multiprocessing import Pool, cpu_count
import pandas as pd

DATA_ROOT = Path("/Users/penghongming/quant-data/tushare/股票数据")
INTERFACE = "stk_factor_pro"


def _normalize_dates(df):
    for col in df.columns:
        if str(col).endswith("_date") or col in {"trade_date", "cal_date"}:
            df[col] = df[col].astype(str).str.replace("-", "", regex=False)
    return df


def process_stock(args):
    ts_code, file_paths = args
    d = DATA_ROOT / INTERFACE
    dest = d / f"{ts_code}.parquet"

    # Skip if already done
    if dest.exists():
        return ts_code, 0, "skip"

    chunks = []
    for f in file_paths:
        try:
            df = pd.read_parquet(f)
            chunks.append(df)
        except Exception as e:
            pass
    if not chunks:
        return ts_code, 0, "empty"

    combined = pd.concat(chunks, ignore_index=True)
    combined = _normalize_dates(combined)

    for col in combined.columns:
        if combined[col].dtype == "object":
            try:
                combined[col] = combined[col].astype("float64", errors="raise")
            except (ValueError, TypeError):
                pass

    combined.to_parquet(dest, index=False)
    return ts_code, len(combined), "done"


def main():
    d = DATA_ROOT / INTERFACE
    print(f"Scanning {INTERFACE}...")

    # Build ts_code → files mapping from all year dirs
    by_code = defaultdict(list)
    for f in sorted(d.rglob(f"{INTERFACE}_*.parquet")):
        stem = f.stem
        prefix = f"{INTERFACE}_"
        ts_code = stem[len(prefix):] if stem.startswith(prefix) else stem
        by_code[ts_code].append(f)

    # Filter out already processed stocks
    total = len(by_code)
    remaining = {code: files for code, files in by_code.items()
                 if not (d / f"{code}.parquet").exists()}
    done_count = total - len(remaining)

    print(f"Total stocks: {total}, already done: {done_count}, remaining: {len(remaining)}")

    if not remaining:
        print("Nothing to do.")
        return

    # Sort by number of files (largest first = most years = most data)
    items = sorted(remaining.items(), key=lambda x: -len(x[1]))

    n_workers = min(cpu_count(), 4)
    print(f"Processing with {n_workers} workers...")

    t0 = time.time()
    with Pool(n_workers) as pool:
        for i, (code, rows, status) in enumerate(pool.imap_unordered(process_stock, items, chunksize=10)):
            if (i + 1) % 100 == 0:
                elapsed = time.time() - t0
                pct = (i + 1) / len(remaining) * 100
                eta = (elapsed / (i + 1)) * (len(remaining) - i - 1) / 60
                print(f"  {i+1}/{len(remaining)} ({pct:.0f}%) {elapsed:.0f}s, ~{eta:.0f}min remaining")

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.0f}s ({elapsed/60:.1f}min)")

    # Final count
    flat = len(list(d.glob("*.parquet")))
    print(f"Flat parquets: {flat}")


if __name__ == "__main__":
    main()
