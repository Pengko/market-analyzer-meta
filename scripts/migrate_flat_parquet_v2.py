"""Migrate per-stock parquet from year-dir shards to flat root dir.

Interfaces: pledge_detail, pledge_stat, stk_auction_c, stk_auction_o, stk_factor_pro
"""
import sys, time
from pathlib import Path
from collections import defaultdict

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA_ROOT = Path("/Users/penghongming/quant-data/tushare/股票数据")


def _normalize_dates(df):
    for col in df.columns:
        if str(col).endswith("_date") or str(col) in {"trade_date", "cal_date"}:
            df[col] = df[col].astype(str).str.replace("-", "", regex=False)
    return df


def migrate_interface(interface_name: str):
    """Flatten year-dir per-stock parquets into one per-stock flat parquet."""
    d = DATA_ROOT / interface_name
    if not d.exists():
        print(f"[{interface_name}] directory not found")
        return

    # Collect all year-dir parquet files grouped by ts_code
    ts_code_files = defaultdict(list)
    for f in sorted(d.rglob(f"{interface_name}_*.parquet")):
        # filename: {prefix}{ts_code}.parquet
        stem = f.stem  # e.g. "stk_factor_pro_600654.SH"
        prefix = f"{interface_name}_"
        ts_code = stem[len(prefix):] if stem.startswith(prefix) else stem
        ts_code_files[ts_code].append(f)

    total_codes = len(ts_code_files)
    print(f"[{interface_name}] {total_codes} unique stocks, {sum(len(v) for v in ts_code_files.values())} total files")

    t0 = time.time()
    for idx, (ts_code, files) in enumerate(sorted(ts_code_files.items())):
        if (idx + 1) % 500 == 0:
            elapsed = time.time() - t0
            print(f"  [{interface_name}] {idx+1}/{total_codes} ({elapsed:.0f}s)")

        dest = d / f"{ts_code}.parquet"
        chunks = []
        for f in files:
            try:
                df = pd.read_parquet(f)
                chunks.append(df)
            except Exception as e:
                print(f"  ERROR reading {f}: {e}")
        if not chunks:
            continue

        combined = pd.concat(chunks, ignore_index=True)
        combined = _normalize_dates(combined)

        # Handle type inconsistencies across years
        for col in combined.columns:
            if combined[col].dtype == "object":
                try:
                    combined[col] = combined[col].astype("float64", errors="raise")
                except (ValueError, TypeError):
                    pass

        # Merge with existing flat parquet (for resume safety)
        if dest.exists():
            existing = pd.read_parquet(dest)
            date_like = [c for c in combined.columns if c == "trade_date" or c == "ann_date" or c == "end_date"]
            dedup_cols = [c for c in date_like + ["ts_code"] if c in existing.columns and c in combined.columns]
            combined = pd.concat([existing, combined], ignore_index=True)
            if dedup_cols:
                combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            sort_col = next((c for c in ["trade_date", "ann_date", "end_date"] if c in combined.columns), None)
            if sort_col:
                combined = combined.sort_values(sort_col)

        combined.to_parquet(dest, index=False)

    elapsed = time.time() - t0
    print(f"[{interface_name}] done in {elapsed:.0f}s")


def main():
    interfaces = ["pledge_detail", "pledge_stat", "stk_auction_c", "stk_auction_o", "stk_factor_pro"]
    for name in interfaces:
        migrate_interface(name)


if __name__ == "__main__":
    main()
