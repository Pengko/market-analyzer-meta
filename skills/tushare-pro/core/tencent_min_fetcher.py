#!/usr/bin/env python3
"""
腾讯 API 实时分钟数据获取器

通过腾讯 minute/query 接口获取当日实时分时数据，
转换为标准 OHLCV 格式并保存到本地分钟数据目录。

数据 fallback 链：本地 parquet/CSV → Tushare stk_mins/rt_min（限额）→ 腾讯 API（免费无限制）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 当从 core/ 目录直接运行时，避免 core/calendar.py 覆盖 Python 标准库
_core_dir = str(Path(__file__).parent)
if _core_dir in sys.path:
    sys.path.remove(_core_dir)

import urllib.request
from datetime import datetime
from typing import Optional

import pandas as pd

# 数据根目录
DATA_ROOT = Path.home() / "quant-data" / "tushare" / "股票数据" / "分钟数据"


def _get_tencent_code(ts_code: str) -> str:
    """将 Tushare 代码格式转换为腾讯代码格式。"""
    if ts_code.endswith(".SH"):
        return f"sh{ts_code.replace('.SH', '')}"
    elif ts_code.endswith(".SZ"):
        return f"sz{ts_code.replace('.SZ', '')}"
    elif ts_code.endswith(".BJ"):
        return f"bj{ts_code.replace('.BJ', '')}"
    return ts_code


def fetch_tencent_minute(ts_code: str, timeout: int = 10) -> pd.DataFrame:
    """
    从腾讯 API 获取当日实时分钟数据。

    Args:
        ts_code: Tushare 格式代码，如 "600519.SH"
        timeout: 请求超时秒数

    Returns:
        DataFrame, columns=[datetime, open, close, high, low, volume, amount, avg]
        若当日无交易或 API 失败则返回空 DataFrame
    """
    tencent_code = _get_tencent_code(ts_code)
    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={tencent_code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"腾讯 API 请求失败 ({ts_code}): {exc}") from exc

    if payload.get("code") != 0:
        raise RuntimeError(f"腾讯 API 返回错误 ({ts_code}): {payload.get('msg')}")

    data_key = tencent_code
    raw_list = (
        payload.get("data", {})
        .get(data_key, {})
        .get("data", {})
        .get("data", [])
    )
    if not raw_list:
        return pd.DataFrame()

    # 解析原始字符串: "HHMM price cum_vol cum_amount"
    rows: list[dict] = []
    for line in raw_list:
        parts = line.split()
        if len(parts) < 4:
            continue
        rows.append(
            {
                "time_str": parts[0],
                "price": float(parts[1]),
                "cum_vol": float(parts[2]),
                "cum_amount": float(parts[3]),
            }
        )

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    # 计算分钟增量成交量/成交额
    df["volume"] = df["cum_vol"].diff().fillna(df["cum_vol"].iloc[0])
    df["amount"] = df["cum_amount"].diff().fillna(df["cum_amount"].iloc[0])

    # 腾讯 API 成交量单位为"手"，转换为"股"
    df["volume"] = (df["volume"] * 100).astype(int)
    df["amount"] = df["amount"].astype(int)

    # 构建 datetime（使用当天日期）
    today = datetime.now().strftime("%Y-%m-%d")
    df["datetime"] = pd.to_datetime(
        today + " " + df["time_str"].str[:2] + ":" + df["time_str"].str[2:]
    )

    # 腾讯只返回每分钟一个价格点，用该点近似 OHLC
    # open = 上一分钟收盘价（第一分钟用自身价格）
    df["open"] = df["price"].shift(1).fillna(df["price"])
    df["close"] = df["price"]
    df["high"] = df["price"]
    df["low"] = df["price"]
    df["avg"] = df["price"]

    # 统一列顺序
    df = df[["datetime", "open", "close", "high", "low", "volume", "amount", "avg"]]
    return df


def save_minute_data(
    df: pd.DataFrame,
    ts_code: str,
    trade_date: Optional[str] = None,
    data_root: Optional[Path] = None,
) -> Path:
    """
    将分钟 DataFrame 保存到本地标准路径。

    Args:
        df: 分钟数据 DataFrame
        ts_code: Tushare 格式代码
        trade_date: 交易日期，格式 YYYY-MM-DD，默认取当天
        data_root: 数据根目录，默认 ~/quant-data/tushare/股票数据/分钟数据

    Returns:
        保存的文件路径
    """
    if df.empty:
        raise ValueError("DataFrame 为空，无法保存")

    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    root = data_root or DATA_ROOT
    y, m, d = trade_date.split("-")
    out_dir = root / y / m / d / ts_code
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / "1m.csv"
    df.to_csv(out_path, index=False, encoding="utf-8")
    return out_path


def fetch_and_save(
    ts_code: str,
    trade_date: Optional[str] = None,
    data_root: Optional[Path] = None,
    timeout: int = 10,
) -> dict:
    """
    一步式获取并保存腾讯实时分钟数据。

    Returns:
        {"status": "success", "path": Path, "rows": int, "ts_code": str, "trade_date": str}
        或 {"status": "error", "error": str, "ts_code": str}
    """
    try:
        df = fetch_tencent_minute(ts_code, timeout=timeout)
        if df.empty:
            return {
                "status": "empty",
                "ts_code": ts_code,
                "trade_date": trade_date or datetime.now().strftime("%Y-%m-%d"),
                "reason": "腾讯 API 返回空数据",
            }

        # 若未指定 trade_date，从 DataFrame 推断
        inferred_date = df["datetime"].iloc[0].strftime("%Y-%m-%d")
        actual_date = trade_date or inferred_date

        path = save_minute_data(df, ts_code, trade_date=actual_date, data_root=data_root)
        return {
            "status": "success",
            "path": str(path),
            "rows": len(df),
            "ts_code": ts_code,
            "trade_date": actual_date,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
            "ts_code": ts_code,
        }


def batch_fetch_and_save(
    codes: list[str],
    trade_date: Optional[str] = None,
    data_root: Optional[Path] = None,
    timeout: int = 10,
    max_workers: int = 4,
) -> list[dict]:
    """
    批量获取并保存多只股票的腾讯实时分钟数据。

    Args:
        codes: 股票代码列表，Tushare 格式
        trade_date: 交易日期，默认当天
        data_root: 数据根目录
        timeout: 单次请求超时
        max_workers: 并发线程数

    Returns:
        每只股票的处理结果列表
    """
    import concurrent.futures

    results: list[dict] = []
    total = len(codes)
    success = 0
    empty = 0
    failed = 0

    print(f"开始批量获取 {total} 只股票的腾讯实时分钟数据...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_code = {
            executor.submit(
                fetch_and_save,
                code,
                trade_date=trade_date,
                data_root=data_root,
                timeout=timeout,
            ): code
            for code in codes
        }

        for idx, future in enumerate(concurrent.futures.as_completed(future_to_code), 1):
            result = future.result()
            results.append(result)

            if result["status"] == "success":
                success += 1
                if idx % 50 == 0 or idx == total:
                    print(f"  进度 {idx}/{total} | 成功 {success} | 空数据 {empty} | 失败 {failed}")
            elif result["status"] == "empty":
                empty += 1
            else:
                failed += 1
                if failed <= 5:
                    print(f"  ⚠️ {result['ts_code']}: {result.get('error', 'unknown')}")

    print(f"\n批量获取完成: 成功 {success} | 空数据 {empty} | 失败 {failed} | 总计 {total}")
    return results


def get_stock_code_list_from_local() -> list[str]:
    """从本地 stock_basic 获取非 ST 股票列表。"""
    stock_basic_path = DATA_ROOT.parent.parent / "stock_basic" / "stock_basic_non_st.csv"
    if not stock_basic_path.exists():
        # fallback: 尝试其他路径
        alt_path = Path.home() / "quant-data" / "tushare" / "股票数据" / "stock_basic" / "stock_basic_non_st.csv"
        if alt_path.exists():
            stock_basic_path = alt_path
        else:
            raise FileNotFoundError(f"本地股票列表不存在: {stock_basic_path}")

    df = pd.read_csv(stock_basic_path, usecols=["ts_code"])
    return df["ts_code"].dropna().astype(str).tolist()


def main():
    parser = argparse.ArgumentParser(description="腾讯 API 实时分钟数据获取")
    parser.add_argument("--symbol", default=None, help="单只股票代码，如 600519.SH")
    parser.add_argument("--symbols", default=None, help="多只股票代码，逗号分隔")
    parser.add_argument("--batch", action="store_true", help="批量获取本地非 ST 股票池")
    parser.add_argument("--batch-size", type=int, default=0, help="批量模式下最多获取多少只，0=全部")
    parser.add_argument("--date", default=None, help="交易日期 YYYY-MM-DD，默认当天")
    parser.add_argument("--data-root", default=None, help="数据根目录，默认 ~/quant-data/tushare/股票数据/分钟数据")
    parser.add_argument("--timeout", type=int, default=10, help="请求超时秒数")
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    args = parser.parse_args()

    root = Path(args.data_root) if args.data_root else None

    if args.symbol:
        # 单只股票模式
        result = fetch_and_save(args.symbol, trade_date=args.date, data_root=root, timeout=args.timeout)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if result.get("status") == "error":
            sys.exit(1)
    elif args.symbols:
        # 多只股票模式
        codes = [c.strip() for c in args.symbols.split(",") if c.strip()]
        results = batch_fetch_and_save(
            codes,
            trade_date=args.date,
            data_root=root,
            timeout=args.timeout,
            max_workers=args.workers,
        )
        # 统计
        success = sum(1 for r in results if r["status"] == "success")
        print(f"\n总计 {len(results)} 只: 成功 {success}")
    elif args.batch:
        # 批量本地股票池模式
        codes = get_stock_code_list_from_local()
        if args.batch_size > 0:
            codes = codes[: args.batch_size]
        print(f"本地股票池总共 {len(codes)} 只")
        batch_fetch_and_save(
            codes,
            trade_date=args.date,
            data_root=root,
            timeout=args.timeout,
            max_workers=args.workers,
        )
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
