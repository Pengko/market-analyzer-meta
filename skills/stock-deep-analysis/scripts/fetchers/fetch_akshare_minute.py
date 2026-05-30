#!/usr/bin/env python3
"""
akshare 分钟线批量抓取器。

数据来源：Sina Finance via akshare
速率：~1-2s/请求，批量 8 股约 10-16 秒

用法：
    python3 fetchers/fetch_akshare_minute.py --symbol 000725.SZ --days 5
    python3 fetchers/fetch_akshare_minute.py --portfolio --days 5
    python3 fetchers/fetch_akshare_minute.py --symbols 000725.SZ,600667.SH --days 5
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import akshare as ak
except ImportError:
    print("请先安装 akshare: pip install akshare")
    sys.exit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import STOCK_DATA_ROOT, normalize_symbol


def _to_akshare_symbol(code: str) -> str:
    """标准代码 → akshare 格式（sz000725 / sh600667）"""
    pure, full = normalize_symbol(code)
    if full.endswith(".SH"):
        return f"sh{pure}"
    return f"sz{pure}"


def _exchange_from_code(code: str) -> str:
    pure, _ = normalize_symbol(code)
    return "SH" if pure.startswith(("6", "9")) else "SZ"


def _file_exists_ok(path: Path, min_rows: int = 200) -> bool:
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.reader(f)
            next(reader, None)  # skip header
            count = sum(1 for _ in reader)
        return count >= min_rows
    except Exception:
        return False


def _save_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fetch_minute_data(symbol: str, days: int = 5) -> dict[str, Any]:
    """获取单只股票的分钟线数据。

    Args:
        symbol: 股票代码，如 '000725' 或 '000725.SZ'
        days: 获取最近几天的数据（默认5天）

    Returns:
        {
            "status": "ok" | "error",
            "symbol": "000725.SZ",
            "rows": 1210,
            "days_fetched": 5,
            "saved_files": ["path/to/file1.csv", ...],
            "error": None | "error message"
        }
    """
    pure, full = normalize_symbol(symbol)
    ak_symbol = _to_akshare_symbol(full)
    exchange = _exchange_from_code(full)

    try:
        df = ak.stock_zh_a_minute(symbol=ak_symbol, period="1")
    except Exception as exc:
        return {
            "status": "error",
            "symbol": full,
            "rows": 0,
            "days_fetched": 0,
            "saved_files": [],
            "error": str(exc),
        }

    if df is None or df.empty:
        return {
            "status": "error",
            "symbol": full,
            "rows": 0,
            "days_fetched": 0,
            "saved_files": [],
            "error": "akshare returned empty DataFrame",
        }

    # 解析 day 列 → 分组
    df["day"] = df["day"].astype(str)
    df["_date"] = df["day"].str[:10]  # YYYY-MM-DD

    # 只取最近 N 个交易日
    unique_dates = sorted(df["_date"].unique())
    recent_dates = unique_dates[-days:] if len(unique_dates) > days else unique_dates

    fieldnames = ["datetime", "open", "high", "low", "close", "volume", "amount"]
    saved: list[str] = []
    total_rows = 0

    for date_str in recent_dates:
        day_df = df[df["_date"] == date_str]
        if day_df.empty:
            continue

        y, m, d = date_str.split("-")
        out_path = STOCK_DATA_ROOT / "分钟数据" / y / m / d / f"{pure}.{exchange}" / "1m.csv"

        if _file_exists_ok(out_path):
            total_rows += len(day_df)
            saved.append(str(out_path))
            continue

        rows = []
        for _, row in day_df.iterrows():
            rows.append({
                "datetime": str(row["day"]),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
                "amount": row["amount"],
            })

        _save_csv(out_path, rows, fieldnames)
        total_rows += len(rows)
        saved.append(str(out_path))

    return {
        "status": "ok",
        "symbol": full,
        "rows": total_rows,
        "days_fetched": len(recent_dates),
        "saved_files": saved,
        "error": None,
    }


def batch_fetch(symbols: list[str], days: int = 5) -> list[dict[str, Any]]:
    """批量获取多只股票的分钟线数据。"""
    results: list[dict[str, Any]] = []
    for i, sym in enumerate(symbols):
        if i > 0:
            time.sleep(1.5)  # 限速
        result = fetch_minute_data(sym, days=days)
        status_char = "✓" if result["status"] == "ok" else "✗"
        print(f"  [{status_char}] {result['symbol']}: {result['rows']} rows, {result['days_fetched']} days")
        if result["error"]:
            print(f"      error: {result['error']}")
        results.append(result)
    return results


def _load_portfolio_symbols() -> list[str]:
    from data.portfolio_loader import load_portfolio
    portfolio = load_portfolio()
    positions = portfolio.get("positions") or {}
    return [k for k, v in positions.items() if v.get("hold", 0) > 0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="akshare 分钟线批量抓取器")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--symbol", help="单只股票代码，如 000725.SZ")
    group.add_argument("--symbols", help="逗号分隔的股票代码列表，如 000725.SZ,600667.SH")
    group.add_argument("--portfolio", action="store_true", help="获取持仓股票")
    parser.add_argument("--days", type=int, default=5, help="获取最近几天的数据（默认5）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.symbol:
        symbols = [args.symbol]
    elif args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    elif args.portfolio:
        symbols = _load_portfolio_symbols()
        if not symbols:
            print("持仓为空，请先配置 portfolio.yaml")
            return 1
        print(f"持仓股票: {', '.join(symbols)}")
    else:
        print("请指定 --symbol、--symbols 或 --portfolio")
        return 1

    print(f"开始获取 {len(symbols)} 只股票的 {args.days} 天分钟数据...\n")
    t0 = time.time()

    results = batch_fetch(symbols, days=args.days)

    elapsed = time.time() - t0
    ok_count = sum(1 for r in results if r["status"] == "ok")
    total_rows = sum(r["rows"] for r in results)
    total_files = sum(len(r["saved_files"]) for r in results)

    print(f"\n完成: {ok_count}/{len(symbols)} 只股票, {total_rows} 行, {total_files} 个文件, {elapsed:.1f}s")

    return 0 if ok_count == len(symbols) else 1


if __name__ == "__main__":
    raise SystemExit(main())
