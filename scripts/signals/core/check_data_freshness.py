#!/usr/bin/env python3
"""
汇总个股分析常用本地数据的新鲜度状态。

示例：
  python3 check_data_freshness.py --symbol 002639.SZ --trade-date 2026-04-08
  python3 check_data_freshness.py --symbol 002639 --trade-date 2026-04-08 --format json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 确保能找到 scripts/data/ 模块
_scripts_dir = Path(__file__).resolve().parents[2]
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from data.config_loader import cfg
from data.data_access import load_yearly_or_flat_rows

LEGACY_SCRIPTS_DIR = Path(cfg.get('paths', 'external', 'stock_skill_scripts') or str(Path.home() / 'agent-skills' / 'custom' / 'stock-deep-analysis' / 'scripts'))
if str(LEGACY_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_SCRIPTS_DIR))

from common import STOCK_DATA_ROOT, normalize_symbol, normalize_trade_date


ROOT = STOCK_DATA_ROOT
TRADE_CAL_DIR_CANDIDATES = [
    cfg.paths('trade_cal_dir'),
    ROOT / "trade_cal",
]
MONEYFLOW_TUSHARE_DIR = cfg.paths('moneyflow_individual_tushare')


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="检查个股分析数据新鲜度")
    parser.add_argument("--symbol", required=True, help="如 002639 或 002639.SZ")
    parser.add_argument("--trade-date", required=True, help="格式 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def parse_trade_date(value: str) -> datetime.date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def trade_cal_path() -> Path | None:
    for root in TRADE_CAL_DIR_CANDIDATES:
        candidate = root / "trade_cal_all.csv"
        if candidate.exists():
            return candidate
    return None


def load_open_trade_days() -> list[datetime.date]:
    path = trade_cal_path()
    if not path:
        return []
    result: list[datetime.date] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if str(row.get("is_open") or "").strip() != "1":
                continue
            raw = str(row.get("cal_date") or "").strip()
            if len(raw) != 8 or not raw.isdigit():
                continue
            try:
                result.append(datetime.strptime(raw, "%Y%m%d").date())
            except ValueError:
                continue
    result.sort()
    return result


def weekly_trade_window(target_date: datetime.date) -> tuple[datetime.date | None, datetime.date | None]:
    open_days = [day for day in load_open_trade_days() if day <= target_date]
    if not open_days:
        return None, None
    latest_open = open_days[-1]
    week_start = latest_open - timedelta(days=latest_open.weekday())
    weekly_candidates = [day for day in open_days if week_start <= day <= latest_open]
    if not weekly_candidates:
        return latest_open, latest_open
    return weekly_candidates[0], latest_open


def weekly_info(path: Path, daily_path: Path, target_date: datetime.date) -> dict:
    weekly_meta = csv_info_from_root(path.parent, path.name, target_date, 7)
    daily_meta = csv_info_from_root(daily_path.parent, daily_path.name, target_date, 1)
    latest_date = (
        datetime.strptime(weekly_meta["latest_trade_date"], "%Y-%m-%d").date()
        if weekly_meta.get("latest_trade_date")
        else None
    )
    rows = int(weekly_meta.get("rows") or 0)
    daily_latest_date = (
        datetime.strptime(daily_meta["latest_trade_date"], "%Y-%m-%d").date()
        if daily_meta.get("latest_trade_date")
        else None
    )
    daily_rows = int(daily_meta.get("rows") or 0)
    week_first_open, latest_open = weekly_trade_window(target_date)
    if latest_date is None and daily_latest_date is None:
        status = "missing"
    elif latest_open is None:
        status = infer_status(latest_date or daily_latest_date, target_date, 7)
    elif daily_latest_date and daily_latest_date >= latest_open:
        status = "available"
    else:
        status = "stale"
    return {
        "path": weekly_meta["path"],
        "status": status,
        "latest_trade_date": latest_date.isoformat() if latest_date else None,
        "week_first_open": week_first_open.isoformat() if week_first_open else None,
        "latest_open_trade_date": latest_open.isoformat() if latest_open else None,
        "daily_latest_trade_date": daily_latest_date.isoformat() if daily_latest_date else None,
        "rows": rows,
        "daily_rows": daily_rows,
        "derived_from_daily": bool(daily_latest_date and latest_open and daily_latest_date >= latest_open),
        "matched_file_count": weekly_meta.get("matched_file_count", 0),
        "latest_file": weekly_meta.get("latest_file"),
        "storage_layout": weekly_meta.get("storage_layout"),
    }


def infer_status(latest_date: datetime.date | None, target_date: datetime.date, max_lag_days: int) -> str:
    if latest_date is None:
        return "missing"
    lag = (target_date - latest_date).days
    if lag <= max_lag_days:
        return "available"
    return "stale"


def _candidate_data_files(root_dir: Path, filename: str) -> list[Path]:
    candidates: list[Path] = []

    def _append(path: Path) -> None:
        if path.exists() and path not in candidates:
            candidates.append(path)

    flat_path = root_dir / filename
    _append(flat_path)

    alt_name = None
    if filename.endswith(".csv"):
        alt_name = filename[:-4] + ".parquet"
    elif filename.endswith(".parquet"):
        alt_name = filename[:-8] + ".csv"
    if alt_name:
        _append(root_dir / alt_name)

    if root_dir.exists():
        for year_dir in sorted(root_dir.iterdir(), reverse=True):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            _append(year_dir / filename)
            if alt_name:
                _append(year_dir / alt_name)
    return candidates


def _latest_trade_date_from_rows(
    rows: list[dict[str, str]], trade_date_field: str = "trade_date"
) -> tuple[datetime.date | None, int]:
    latest: datetime.date | None = None
    row_count = 0
    for row in rows:
        raw = str(row.get(trade_date_field) or "").strip()
        if not raw:
            continue
        try:
            current = datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            try:
                current = datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                continue
        row_count += 1
        if latest is None or current > latest:
            latest = current
    return latest, row_count


def read_latest_trade_date(path: Path, trade_date_field: str = "trade_date") -> tuple[datetime.date | None, int]:
    if not path.exists():
        return None, 0

    latest: datetime.date | None = None
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = (row.get(trade_date_field) or "").strip()
            if not raw:
                continue
            try:
                current = datetime.strptime(raw, "%Y%m%d").date()
            except ValueError:
                try:
                    current = datetime.strptime(raw, "%Y-%m-%d").date()
                except ValueError:
                    continue
            rows += 1
            if latest is None or current > latest:
                latest = current
    return latest, rows


def csv_info_from_root(
    root_dir: Path,
    filename: str,
    target_date: datetime.date,
    max_lag_days: int,
    trade_date_field: str = "trade_date",
) -> dict:
    matched_files = _candidate_data_files(root_dir, filename)
    rows = load_yearly_or_flat_rows(root_dir, filename)
    latest_date, row_count = _latest_trade_date_from_rows(rows, trade_date_field)

    return {
        "path": str(root_dir / filename),
        "status": infer_status(latest_date, target_date, max_lag_days),
        "latest_trade_date": latest_date.isoformat() if latest_date else None,
        "rows": row_count,
        "matched_file_count": len(matched_files),
        "latest_file": str(matched_files[0]) if matched_files else None,
        "storage_layout": "flat_or_yearly_partitioned",
    }


def moneyflow_info(full_symbol: str, target_date: datetime.date, max_lag_days: int = 3) -> dict:
    latest_date: datetime.date | None = None
    latest_file: Path | None = None
    rows = 0
    existing_files = 0

    open_days = [day for day in load_open_trade_days() if day <= target_date]
    for day in reversed(open_days):
        day_path = MONEYFLOW_TUSHARE_DIR / day.strftime("%Y") / day.strftime("%m") / day.strftime("%d") / f"moneyflow_{day.strftime('%Y%m%d')}.csv"
        if not day_path.exists():
            continue
        existing_files += 1
        try:
            with day_path.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if str(row.get("ts_code") or "").strip() != full_symbol:
                        continue
                    rows += 1
                    raw = str(row.get("trade_date") or "").strip()
                    if not raw:
                        continue
                    try:
                        current = datetime.strptime(raw, "%Y%m%d").date()
                    except ValueError:
                        try:
                            current = datetime.strptime(raw, "%Y-%m-%d").date()
                        except ValueError:
                            continue
                    if latest_date is None or current > latest_date:
                        latest_date = current
                        latest_file = day_path
        except Exception:
            continue
        if latest_date is not None:
            break

    return {
        "path": str(MONEYFLOW_TUSHARE_DIR),
        "status": infer_status(latest_date, target_date, max_lag_days),
        "latest_trade_date": latest_date.isoformat() if latest_date else None,
        "rows": rows,
        "matched_file_count": existing_files,
        "latest_file": str(latest_file) if latest_file else None,
        "storage_layout": "date_partitioned_market_table",
        "source_layout": "date_partitioned_by_day",
    }


def minute_file_info(pure_symbol: str, full_symbol: str, trade_date_text: str) -> dict:
    y, m, d = trade_date_text.split("-")

    # 新结构A：分钟数据/YYYY/MM/DD/{symbol}_{granularity}.csv
    new_base = ROOT / "分钟数据" / y / m / d
    new_candidates = [
        ("1m", new_base / f"{pure_symbol}_1m.csv"),
        ("5m", new_base / f"{pure_symbol}_5m.csv"),
        ("15m", new_base / f"{pure_symbol}_15m.csv"),
        ("30m", new_base / f"{pure_symbol}_30m.csv"),
        ("60m", new_base / f"{pure_symbol}_60m.csv"),
    ]

    # 新结构B：分钟数据/YYYY/MM/DD/{symbol}/1m.csv
    partitioned_candidates = [
        ("1m", new_base / full_symbol / "1m.csv"),
        ("5m", new_base / full_symbol / "5m.csv"),
        ("15m", new_base / full_symbol / "15m.csv"),
        ("30m", new_base / full_symbol / "30m.csv"),
        ("60m", new_base / full_symbol / "60m.csv"),
        ("1m", new_base / pure_symbol / "1m.csv"),
        ("5m", new_base / pure_symbol / "5m.csv"),
        ("15m", new_base / pure_symbol / "15m.csv"),
        ("30m", new_base / pure_symbol / "30m.csv"),
        ("60m", new_base / pure_symbol / "60m.csv"),
    ]

    # 旧结构 fallback：分钟数据/{symbol}/{date}/minute_kline[_5m].csv
    old_base = ROOT / "分钟数据" / pure_symbol / trade_date_text
    old_candidates = [
        ("1m", old_base / "minute_kline.csv"),
        ("5m", old_base / "minute_kline_5m.csv"),
        ("15m", old_base / "minute_kline_15m.csv"),
        ("30m", old_base / "minute_kline_30m.csv"),
        ("60m", old_base / "minute_kline_60m.csv"),
    ]

    candidates = new_candidates + partitioned_candidates + old_candidates
    granularity = None
    path = None
    for granularity, path in candidates:
        if path.exists():
            break
    else:
        return {"path": str(new_candidates[0][1]), "status": "missing"}

    def _parse_json_minute_payload(raw_text: str) -> tuple[int, datetime | None, datetime | None, list[str]]:
        try:
            payload = json.loads(raw_text)
        except Exception:
            return 0, None, None, []
        data_rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data_rows, list):
            return 0, None, None, []

        rows = 0
        first_dt = None
        last_dt = None
        time_points: list[str] = []
        for item in data_rows:
            if not isinstance(item, dict):
                continue
            raw_time = str(item.get("time") or "").strip()
            if len(raw_time) == 4 and raw_time.isdigit():
                hhmm = f"{raw_time[:2]}:{raw_time[2:]}"
            elif len(raw_time) == 5 and raw_time[2] == ":":
                hhmm = raw_time
            else:
                continue
            current = datetime.strptime(f"{trade_date_text} {hhmm}", "%Y-%m-%d %H:%M")
            rows += 1
            time_points.append(hhmm)
            if first_dt is None:
                first_dt = current
            last_dt = current
        return rows, first_dt, last_dt, time_points

    rows = 0
    first_dt = None
    last_dt = None
    time_points: list[str] = []
    raw_text = path.read_text(encoding="utf-8").strip()
    if raw_text.startswith("{") or raw_text.startswith("["):
        rows, first_dt, last_dt, time_points = _parse_json_minute_payload(raw_text)
    else:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                dt_text = str(row.get("datetime") or "").strip()
                if dt_text:
                    current = datetime.strptime(dt_text, "%Y-%m-%d %H:%M")
                else:
                    raw_time = str(row.get("time") or "").strip()
                    if len(raw_time) == 4 and raw_time.isdigit():
                        hhmm = f"{raw_time[:2]}:{raw_time[2:]}"
                    elif len(raw_time) == 5 and raw_time[2] == ":":
                        hhmm = raw_time
                    else:
                        continue
                    current = datetime.strptime(f"{trade_date_text} {hhmm}", "%Y-%m-%d %H:%M")
                rows += 1
                time_points.append(current.strftime("%H:%M"))
                if first_dt is None:
                    first_dt = current
                last_dt = current

    def _has_window(start: str, end: str) -> bool:
        return any(start <= t <= end for t in time_points)

    coverage = {
        "open_window": _has_window("09:30", "09:35"),
        "first_push_window": _has_window("09:48", "09:56"),
        "pre_noon_window": _has_window("11:25", "11:30"),
        "pm_open_window": _has_window("13:01", "13:30"),
        "pm_tail_window": _has_window("14:30", "15:00"),
    }

    status = "available"
    if rows == 0:
        status = "missing"
    elif granularity == "1m":
        if rows < 200:
            status = "stale"
        elif not all(coverage.values()):
            status = "stale"
        elif first_dt and first_dt.strftime("%H:%M") > "09:35":
            status = "stale"
        elif last_dt and last_dt.strftime("%H:%M") < "14:30":
            status = "stale"

    return {
        "path": str(path),
        "status": status,
        "granularity": granularity,
        "rows": rows,
        "first_dt": first_dt.isoformat(timespec="minutes") if first_dt else None,
        "last_dt": last_dt.isoformat(timespec="minutes") if last_dt else None,
        "coverage": coverage,
    }


def csv_info(path: Path, target_date: datetime.date, max_lag_days: int) -> dict:
    latest_date, rows = read_latest_trade_date(path)
    return {
        "path": str(path),
        "status": infer_status(latest_date, target_date, max_lag_days),
        "latest_trade_date": latest_date.isoformat() if latest_date else None,
        "rows": rows,
    }


def sqlite_info(full_symbol: str, target_date: datetime.date) -> dict:
    """Check SQLite warehouse freshness for the given symbol."""
    db_path = Path(__file__).resolve().parents[3] / "references" / "data" / "stock_analytics.db"
    if not db_path.exists():
        return {"path": str(db_path), "status": "missing", "latest_trade_date": None, "tables": {}}

    import sqlite3

    tables = {
        "daily_ohlcv": "trade_date",
        "daily_basic": "trade_date",
        "stk_factor_pro": "trade_date",
    }
    result: dict[str, dict] = {}
    overall_latest: datetime.date | None = None

    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        for table, date_col in tables.items():
            try:
                cur.execute(
                    f"SELECT MAX({date_col}) FROM {table} WHERE ts_code = ?",
                    (full_symbol,),
                )
                row = cur.fetchone()
                raw_date = row[0] if row else None
                if raw_date:
                    if isinstance(raw_date, str):
                        latest = datetime.strptime(raw_date, "%Y%m%d").date()
                    else:
                        latest = datetime.strptime(str(raw_date), "%Y%m%d").date()
                else:
                    latest = None
                result[table] = {
                    "latest_trade_date": latest.isoformat() if latest else None,
                    "status": infer_status(latest, target_date, 1),
                }
                if latest and (overall_latest is None or latest > overall_latest):
                    overall_latest = latest
            except Exception:
                result[table] = {"latest_trade_date": None, "status": "invalid"}
        conn.close()
    except Exception:
        return {"path": str(db_path), "status": "invalid", "latest_trade_date": None, "tables": {}}

    return {
        "path": str(db_path),
        "status": infer_status(overall_latest, target_date, 1),
        "latest_trade_date": overall_latest.isoformat() if overall_latest else None,
        "tables": result,
    }


def build_report(full_symbol: str, pure_symbol: str, trade_date_text: str) -> dict:
    target_date = parse_trade_date(trade_date_text)

    items = {
        "minute": minute_file_info(pure_symbol, full_symbol, trade_date_text),
        "open_auction_tushare": csv_info_from_root(ROOT / "stk_auction_o", f"stk_auction_o_{full_symbol}.csv", target_date, 1),
        "close_auction_tushare": csv_info_from_root(ROOT / "stk_auction_c", f"stk_auction_c_{full_symbol}.csv", target_date, 1),
        "daily": csv_info_from_root(ROOT / "daily", f"daily_{full_symbol}.csv", target_date, 1),
        "weekly": weekly_info(
            ROOT / "weekly" / f"weekly_{full_symbol}.csv",
            ROOT / "daily" / f"daily_{full_symbol}.csv",
            target_date,
        ),
        "monthly": csv_info_from_root(ROOT / "monthly", f"monthly_{full_symbol}.csv", target_date, 31),
        "moneyflow": moneyflow_info(full_symbol, target_date, 3),
        "cyq_perf": csv_info_from_root(ROOT / "cyq_perf", f"cyq_perf_{full_symbol}.csv", target_date, 7),
        "cyq_chips": csv_info_from_root(ROOT / "cyq_chips", f"cyq_chips_{full_symbol}.csv", target_date, 7),
        "stk_factor_pro": csv_info_from_root(ROOT / "stk_factor_pro", f"stk_factor_pro_{full_symbol}.csv", target_date, 3),
        "sqlite_warehouse": sqlite_info(full_symbol, target_date),
    }

    summary = {
        "available": sorted([name for name, item in items.items() if item["status"] == "available"]),
        "stale": sorted([name for name, item in items.items() if item["status"] == "stale"]),
        "missing": sorted([name for name, item in items.items() if item["status"] == "missing"]),
        "invalid": sorted([name for name, item in items.items() if item["status"] == "invalid"]),
    }

    return {
        "symbol": full_symbol,
        "trade_date": trade_date_text,
        "items": items,
        "summary": summary,
    }


def print_text(report: dict) -> None:
    print(f"symbol: {report['symbol']}")
    print(f"trade_date: {report['trade_date']}")
    print("summary:")
    for key in ("available", "stale", "missing", "invalid"):
        print(f"- {key}: {', '.join(report['summary'][key]) if report['summary'][key] else '-'}")
    print("items:")
    for name, item in report["items"].items():
        print(f"- {name}:")
        for key, value in item.items():
            print(f"  {key}: {value}")


def main() -> int:
    args = parse_args()
    _, trade_date_text = normalize_trade_date(args.trade_date)
    pure_symbol, full_symbol = normalize_symbol(args.symbol)
    report = build_report(full_symbol, pure_symbol, trade_date_text)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
