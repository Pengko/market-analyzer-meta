"""Migrate moneyflow_data parquet to final flat layout.

Per-stock interfaces (individual/tushare, individual/ths):
  yearly/*.parquet -> {root}/{ts_code}.parquet (deduped, sorted)

Combined interfaces (market/*, sector/*):
  {interface}_*.parquet -> {root}/{interface}.parquet (deduped, sorted)
"""
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA_ROOT = Path("/Users/penghongming/quant-data/tushare/股票数据/moneyflow_data")


def _normalize_dates(df):
    for col in df.columns:
        if str(col).endswith("_date") or str(col) in {"trade_date", "cal_date"}:
            df[col] = df[col].astype(str).str.replace("-", "", regex=False)
    return df


def _coerce_types(df):
    """Handle mixed-type columns by converting object cols that should be numeric."""
    for col in df.columns:
        if col in ("trade_date", "cal_date") or str(col).endswith("_date"):
            continue
        if df[col].dtype == "object":
            try:
                inferred = df[col].astype("float64", errors="raise")
                df[col] = inferred
            except (ValueError, TypeError):
                pass
    return df


def _merge_flat_pq(pq_path, df):
    """Merge df into existing flat parquet with dedup."""
    pq_path.parent.mkdir(parents=True, exist_ok=True)
    df = _normalize_dates(df)
    df = _coerce_types(df)
    if pq_path.exists():
        existing = pd.read_parquet(pq_path)
        dedup_cols = [c for c in ["trade_date", "ts_code"] if c in existing.columns and c in df.columns]
        combined = pd.concat([existing, df], ignore_index=True)
        if dedup_cols:
            combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
        if "trade_date" in combined.columns:
            combined = combined.sort_values("trade_date")
        combined.to_parquet(pq_path, index=False)
    else:
        df.to_parquet(pq_path, index=False)


def migrate_individual(src_dir: Path, interface_name: str):
    """Read yearly parquet shards, split by ts_code, write per-stock flat parquet."""
    files = sorted(src_dir.rglob("*.parquet"))
    if not files:
        print(f"  no files found")
        return
    print(f"  reading {len(files)} yearly shards...")
    chunks = []
    for f in files:
        df = pd.read_parquet(f)
        chunks.append(df)
    full = pd.concat(chunks, ignore_index=True)
    print(f"  total rows: {len(full)}, unique stocks: {full['ts_code'].nunique()}")
    full = _normalize_dates(full)
    full = _coerce_types(full)

    for col in full.columns:
        if "int" in str(full[col].dtype) and full[col].isna().any():
            full[col] = full[col].astype("float64")

    for ts_code, group in full.groupby("ts_code"):
        stock_path = src_dir / f"{ts_code}.parquet"
        _merge_flat_pq(stock_path, group)

    print(f"  done — {full['ts_code'].nunique()} per-stock parquets")


def migrate_combined(src_dir: Path, interface_name: str, glob_pattern: str = "*.parquet"):
    """Read flat per-date parquets, merge into one combined parquet."""
    files = sorted(src_dir.glob(glob_pattern))
    if not files:
        print(f"  no files matching {glob_pattern}")
        return
    print(f"  reading {len(files)} flat parquets...")
    chunks = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            chunks.append(df)
        except Exception as e:
            print(f"  skipping {f.name}: {e}")
    full = pd.concat(chunks, ignore_index=True)
    print(f"  total rows: {len(full)}")
    full = _normalize_dates(full)

    dest = src_dir / f"{interface_name}.parquet"
    _merge_flat_pq(dest, full)
    print(f"  written {dest.name} ({len(full)} rows)")


def main():
    # ── Step 1: individual/tushare — per-stock ──
    print("=" * 60)
    print("individual/tushare (moneyflow) → per-stock")
    tushare_dir = DATA_ROOT / "individual/tushare"
    # Remove the partial-migration combined file
    combined = tushare_dir / "moneyflow.parquet"
    if combined.exists():
        combined.unlink()
        print("  removed partial combined moneyflow.parquet")
    migrate_individual(tushare_dir, "moneyflow")

    # ── Step 2: individual/ths — per-stock ──
    print("=" * 60)
    print("individual/ths (moneyflow_ths) → per-stock")
    ths_dir = DATA_ROOT / "individual/ths"
    migrate_individual(ths_dir, "moneyflow_ths")

    # ── Step 3: market/hsgt — combined ──
    print("=" * 60)
    print("market/hsgt → moneyflow_hsgt.parquet")
    hsgt_dir = DATA_ROOT / "market/hsgt"
    migrate_combined(hsgt_dir, "moneyflow_hsgt", "moneyflow_hsgt_*.parquet")

    # ── Step 4: market/dc — combined ──
    print("=" * 60)
    print("market/dc → moneyflow_mkt_dc.parquet")
    dc_dir = DATA_ROOT / "market/dc"
    migrate_combined(dc_dir, "moneyflow_mkt_dc", "moneyflow_dc_market_*.parquet")

    # ── Step 5: sector/ths_industry — combined ──
    print("=" * 60)
    print("sector/ths_industry → moneyflow_ind_ths.parquet")
    ind_dir = DATA_ROOT / "sector/ths_industry"
    migrate_combined(ind_dir, "moneyflow_ind_ths", "moneyflow_ind_ths_*.parquet")

    # ── Step 6: sector/ths_concept — combined ──
    # Files have mixed names: moneyflow_concept_ths_* and moneyflow_cnt_ths_*
    print("=" * 60)
    print("sector/ths_concept → moneyflow_cnt_ths.parquet")
    cnt_dir = DATA_ROOT / "sector/ths_concept"
    migrate_combined(cnt_dir, "moneyflow_cnt_ths", "moneyflow*.parquet")

    print("=" * 60)
    print("Migration complete.")


if __name__ == "__main__":
    main()
