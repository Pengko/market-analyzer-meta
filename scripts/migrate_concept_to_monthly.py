"""Migrate dc_concept, dc_concept_cons, kpl_concept_cons to monthly combined parquet+csv.

Old → New:
  dc_concept/{name}.parquet|csv       → concept_daily/{year}/{month}.parquet|csv
  dc_concept_cons/{stock}.parquet|csv  → concept_member_dc/{year}/{month}.parquet|csv
  kpl_concept_cons/{subdir}/*         → concept_member_kpl/{year}/{month}.parquet|csv
"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import pyarrow.parquet as pq


DATA_DIR = Path("/Users/penghongming/quant-data/tushare/股票数据/theme_data")


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


def migrate_dc_concept():
    """dc_concept: 674 per-concept parquet → concept_daily/ per-year per-month."""
    src = DATA_DIR / "dc_concept"
    dst = DATA_DIR / "concept_daily"
    print(f"\n=== dc_concept → concept_daily ===")
    pqs = sorted(src.glob("*.parquet"))
    print(f"  Reading {len(pqs)} per-concept parquets...")
    chunks = []
    for f in pqs:
        df = pd.read_parquet(f)
        chunks.append(df)
    full = pd.concat(chunks, ignore_index=True)
    full = norm(full)
    full = coerce(full)
    print(f"  Total rows: {len(full)}")

    # Group by year-month
    full["_ym"] = full["trade_date"].astype(str).str[:6]
    groups = sorted(full.groupby("_ym"))
    n = len(groups)
    t0 = time.time()
    for i, (ym, group) in enumerate(groups):
        year = ym[:4]
        month = ym[4:6]
        out_dir = dst / year
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ym}.parquet"
        csv_path = out_dir / f"{ym}.csv"
        group = group.drop_duplicates(subset=["trade_date", "theme_code"], keep="last").sort_values("trade_date")
        group = group.drop(columns=["_ym"])
        # Merge with existing if any
        if out_path.exists():
            existing = pd.read_parquet(out_path)
            group = pd.concat([existing, group], ignore_index=True)
            dedup_cols = [c for c in ["trade_date", "theme_code"] if c in group.columns]
            group = group.drop_duplicates(subset=dedup_cols, keep="last").sort_values("trade_date")
        group.to_parquet(out_path, index=False)
        group.to_csv(csv_path, index=False)
        if (i + 1) % 3 == 0:
            print(f"  {i+1}/{n} months ({time.time()-t0:.0f}s)")
    total_mb = sum(f.stat().st_size for f in dst.rglob("*.parquet")) / 1024 / 1024
    print(f"  Done: {n} monthly files, {total_mb:.1f}MB")


def migrate_dc_concept_cons():
    """dc_concept_cons: per-stock parquet → concept_member_dc/ per-year per-month."""
    src = DATA_DIR / "dc_concept_cons"
    dst = DATA_DIR / "concept_member_dc"
    print(f"\n=== dc_concept_cons → concept_member_dc ===")
    pqs = sorted(src.glob("*.parquet"))
    print(f"  Reading {len(pqs)} per-stock parquets...")
    chunks = []
    for f in pqs:
        df = pd.read_parquet(f)
        chunks.append(df)
    full = pd.concat(chunks, ignore_index=True)
    full = norm(full)
    full = coerce(full)
    print(f"  Total rows: {len(full)}")

    full["_ym"] = full["trade_date"].astype(str).str[:6]
    groups = sorted(full.groupby("_ym"))
    n = len(groups)
    t0 = time.time()
    for i, (ym, group) in enumerate(groups):
        year = ym[:4]
        month = ym[4:6]
        out_dir = dst / year
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ym}.parquet"
        csv_path = out_dir / f"{ym}.csv"
        group = group.drop_duplicates(subset=["trade_date", "ts_code", "theme_code"], keep="last").sort_values("trade_date")
        group = group.drop(columns=["_ym"])
        if out_path.exists():
            existing = pd.read_parquet(out_path)
            group = pd.concat([existing, group], ignore_index=True)
            dedup_cols = [c for c in ["trade_date", "ts_code", "theme_code"] if c in group.columns]
            group = group.drop_duplicates(subset=dedup_cols, keep="last").sort_values("trade_date")
        group.to_parquet(out_path, index=False)
        group.to_csv(csv_path, index=False)
        if (i + 1) % 3 == 0:
            print(f"  {i+1}/{n} months ({time.time()-t0:.0f}s)")
    total_mb = sum(f.stat().st_size for f in dst.rglob("*.parquet")) / 1024 / 1024
    print(f"  Done: {n} monthly files, {total_mb:.1f}MB")


def migrate_kpl_concept_cons():
    """kpl_concept_cons: by_stock/by_concept/by_date → concept_member_kpl/ per-year per-month.
    We use by_stock as primary data source (most complete).
    """
    src = DATA_DIR / "kpl_concept_cons"
    dst = DATA_DIR / "concept_member_kpl"
    print(f"\n=== kpl_concept_cons → concept_member_kpl ===")

    pqs = sorted((src / "by_stock").glob("*.parquet"))
    print(f"  Reading {len(pqs)} per-stock parquets...")
    chunks = []
    for f in pqs:
        df = pd.read_parquet(f)
        chunks.append(df)
    full = pd.concat(chunks, ignore_index=True)
    full = norm(full)
    full = coerce(full)
    print(f"  Total rows: {len(full)}")

    full["_ym"] = full["trade_date"].astype(str).str[:6]
    groups = sorted(full.groupby("_ym"))
    n = len(groups)
    t0 = time.time()
    for i, (ym, group) in enumerate(groups):
        year = ym[:4]
        month = ym[4:6]
        out_dir = dst / year
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{ym}.parquet"
        csv_path = out_dir / f"{ym}.csv"
        # dedup by trade_date + ts_code + con_code
        group = group.drop_duplicates(subset=["trade_date", "ts_code", "con_code"], keep="last").sort_values("trade_date")
        group = group.drop(columns=["_ym"])
        if out_path.exists():
            existing = pd.read_parquet(out_path)
            group = pd.concat([existing, group], ignore_index=True)
            dedup_cols = [c for c in ["trade_date", "ts_code", "con_code"] if c in group.columns]
            group = group.drop_duplicates(subset=dedup_cols, keep="last").sort_values("trade_date")
        group.to_parquet(out_path, index=False)
        group.to_csv(csv_path, index=False)
        if (i + 1) % 2 == 0:
            print(f"  {i+1}/{n} months ({time.time()-t0:.0f}s)")
    total_mb = sum(f.stat().st_size for f in dst.rglob("*.parquet")) / 1024 / 1024
    print(f"  Done: {n} monthly files, {total_mb:.1f}MB")


if __name__ == "__main__":
    migrate_dc_concept()
    migrate_dc_concept_cons()
    migrate_kpl_concept_cons()
    print("\nAll migrations complete.")
