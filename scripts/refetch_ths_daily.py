"""Re-fetch all ths_daily data, write per-date CSVs (flat), then one migration pass to per-stock."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from collections import defaultdict
import pandas as pd
from utils.tushare_client import create_pro_api

pro = create_pro_api()
DATA_DIR = Path("/Users/penghongming/quant-data/tushare/股票数据/theme_data/ths_daily")
DATA_DIR.mkdir(parents=True, exist_ok=True)

PREFIX = "ths_daily_"
START = "20200102"
END = "20260521"


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


def fetch_all():
    cal = pro.trade_cal(start_date=START, end_date=END)
    cal = cal[cal["is_open"] == 1]
    trade_dates = sorted(cal["cal_date"].tolist())
    print(f"Trade dates to fetch: {len(trade_dates)}")

    t0 = time.time()
    existing = len(list(DATA_DIR.glob(f"{PREFIX}*.csv")))
    if existing > 0:
        print(f"{existing} CSVs already exist, will skip those")

    for i, date in enumerate(trade_dates):
        csv_path = DATA_DIR / f"{PREFIX}{date}.csv"
        if csv_path.exists():
            continue

        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            already = len(list(DATA_DIR.glob(f"{PREFIX}*.csv")))
            left = len(trade_dates) - already
            eta = (elapsed / max(1, already)) * max(0, left) / 60
            print(f"  {already}/{len(trade_dates)} fetched ({elapsed:.0f}s, ~{eta:.0f}min left)")

        try:
            df = pro.ths_daily(trade_date=date)
        except Exception as e:
            print(f"  ERROR {date}: {e}")
            continue

        if df is None or len(df) == 0:
            continue

        df.to_csv(csv_path, index=False)

    elapsed = time.time() - t0
    total_csv = len(list(DATA_DIR.glob(f"{PREFIX}*.csv")))
    print(f"Fetch done: {total_csv}/{len(trade_dates)} CSVs in {elapsed:.0f}s ({elapsed/60:.1f}min)")


def migrate_to_per_stock():
    """Read all CSVs, group by ts_code, write per-stock parquet+CSV."""
    csvs = sorted(DATA_DIR.glob(f"{PREFIX}*.csv"))
    print(f"\nMigrating {len(csvs)} CSVs to per-stock...")

    chunks = []
    for f in csvs:
        try:
            df = pd.read_csv(f)
            chunks.append(df)
        except:
            pass

    full = pd.concat(chunks, ignore_index=True)
    full = norm(full)
    full = coerce(full)
    print(f"  {len(full)} rows, {full['ts_code'].nunique()} stocks")

    t0 = time.time()
    n = full['ts_code'].nunique()
    for i, (code, group) in enumerate(full.groupby('ts_code')):
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{n} ({time.time()-t0:.0f}s)")
        group = group.drop_duplicates(subset=["trade_date", "ts_code"], keep="last").sort_values("trade_date")
        group.to_parquet(DATA_DIR / f"{code}.parquet", index=False)
        group.to_csv(DATA_DIR / f"{code}.csv", index=False)

    # Remove old per-date CSVs + any per-date parquets
    for f in list(DATA_DIR.glob(f"{PREFIX}*")):
        f.unlink()

    print(f"  done in {time.time()-t0:.0f}s")
    print(f"  final: {len(list(DATA_DIR.glob('*.parquet')))} pq, {len(list(DATA_DIR.glob('*.csv')))} csv")


if __name__ == "__main__":
    fetch_all()
    migrate_to_per_stock()
