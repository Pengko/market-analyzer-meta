#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量价背离命中率测试

测试逻辑：
1. 遍历历史日线数据，检测背离信号
2. 看背离信号后 1/3/5 个交易日的涨跌情况
3. 统计命中率
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import json

# 数据路径
_DEFAULT_TUSHARE_ROOT = Path.home() / "quant-data" / "tushare"
STOCK_DATA_ROOT = Path(
    os.environ.get("STOCK_DATA_ROOT")
    or (_DEFAULT_TUSHARE_ROOT / "股票数据")
)


def load_daily_data(ts_code: str, days: int = 120) -> pd.DataFrame:
    """加载日线数据"""
    daily_file = STOCK_DATA_ROOT / f"daily/daily_{ts_code}.csv"
    if not daily_file.exists():
        return pd.DataFrame()

    df = pd.read_csv(daily_file)
    df = df.sort_values("trade_date")

    # 取最近 N 天
    if len(df) > days:
        df = df.tail(days)

    return df


def detect_all_divergences(df: pd.DataFrame) -> list:
    """检测所有背离信号"""
    if len(df) < 30:
        return []

    df = df.copy()
    df = df.sort_values("trade_date").tail(120).reset_index(drop=True)

    close = df["close"].values
    volume = df["vol"].values

    # 成交量快慢线
    vol_fast = pd.Series(volume).rolling(window=5, min_periods=1).mean()
    vol_slow = pd.Series(volume).rolling(window=10, min_periods=1).mean()
    vol_dif = vol_fast - vol_slow

    # 价格快慢线
    price_fast = pd.Series(close).rolling(window=5, min_periods=1).mean()
    price_slow = pd.Series(close).rolling(window=10, min_periods=1).mean()

    # 计算 RSI(14)
    delta = pd.Series(close).diff()
    gain = delta.where(delta > 0, 0).rolling(window=14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=1).mean()
    rs = gain / (loss + 0.001)
    rsi = 100 - (100 / (1 + rs))

    signals = []

    for i in range(15, len(df)):
        trade_date = int(df.iloc[i]["trade_date"])

        # ===== 量价顶背离 =====
        if (
            close[i] > close[i - 3 : i].max() * 0.98
            and close[i] > price_fast.iloc[i]
            and vol_dif.iloc[i] < vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] < 0
        ):
            price_chg = (close[i] - close[i - 3]) / close[i - 3] * 100
            vol_chg = (vol_dif.iloc[i] - vol_dif.iloc[i - 3]) / (
                abs(vol_dif.iloc[i - 3]) + 0.001
            )

            signals.append(
                {
                    "type": "volume_top_div",
                    "date": trade_date,
                    "price": float(close[i]),
                    "price_change": round(price_chg, 2),
                    "strength": "strong" if abs(vol_chg) > 0.5 else "normal",
                }
            )

        # ===== 量价底背离 =====
        if (
            close[i] < close[i - 3 : i].min() * 1.02
            and close[i] < price_fast.iloc[i]
            and vol_dif.iloc[i] > vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] > 0
        ):
            price_chg = (close[i] - close[i - 3]) / close[i - 3] * 100
            vol_chg = (vol_dif.iloc[i] - vol_dif.iloc[i - 3]) / (
                abs(vol_dif.iloc[i - 3]) + 0.001
            )

            signals.append(
                {
                    "type": "volume_bottom_div",
                    "date": trade_date,
                    "price": float(close[i]),
                    "price_change": round(price_chg, 2),
                    "strength": "strong" if abs(vol_chg) > 0.5 else "normal",
                }
            )

        # ===== RSI 顶背离 =====
        if close[i] > close[i - 5 : i].max() * 0.98:
            price_high_idx = np.argmax(close[i - 10 : i])
            rsi_high_idx = np.argmax(rsi[i - 10 : i])

            if rsi.iloc[i] < rsi.iloc[i - 10 + price_high_idx] - 5:
                signals.append(
                    {
                        "type": "rsi_top_div",
                        "date": trade_date,
                        "price": float(close[i]),
                        "rsi": round(float(rsi.iloc[i]), 2),
                        "strength": "strong" if rsi.iloc[i] < 40 else "normal",
                    }
                )

        # ===== RSI 底背离 =====
        if close[i] < close[i - 5 : i].min() * 1.02:
            price_low_idx = np.argmin(close[i - 5 : i])
            rsi_low_idx = np.argmin(rsi[i - 5 : i])

            if rsi.iloc[i] > rsi.iloc[i - 5 + price_low_idx] + 5:
                signals.append(
                    {
                        "type": "rsi_bottom_div",
                        "date": trade_date,
                        "price": float(close[i]),
                        "rsi": round(float(rsi.iloc[i]), 2),
                        "strength": "strong" if rsi.iloc[i] > 60 else "normal",
                    }
                )

    return signals


def get_future_returns(df: pd.DataFrame, signal_date: int, days: list) -> dict:
    """获取信号后的未来收益"""
    # 重置索引确保位置连续
    df = df.reset_index(drop=True)

    idx_list = df[df["trade_date"] == signal_date].index.tolist()
    if len(idx_list) == 0:
        return {}

    idx = idx_list[0]
    returns = {}

    for d in days:
        future_idx = idx + d
        if future_idx < len(df):
            signal_price = df.iloc[idx]["close"]
            future_price = df.iloc[future_idx]["close"]
            future_date = int(df.iloc[future_idx]["trade_date"])
            pct_chg = (future_price - signal_price) / signal_price * 100
            returns[f"+{d}d"] = {
                "date": future_date,
                "price": float(future_price),
                "return": round(pct_chg, 2),
            }
        else:
            returns[f"+{d}d"] = None

    return returns


def test_divergence_hit_rate(ts_code: str, min_signals: int = 3) -> dict:
    """测试单只股票的背离命中率"""
    df = load_daily_data(ts_code)
    if df.empty:
        return None

    signals = detect_all_divergences(df)
    if len(signals) < min_signals:
        return None

    # 按类型分组
    result = {"ts_code": ts_code, "total_signals": len(signals), "by_type": {}}

    for div_type in [
        "volume_top_div",
        "volume_bottom_div",
        "rsi_top_div",
        "rsi_bottom_div",
    ]:
        type_signals = [s for s in signals if s["type"] == div_type]
        if not type_signals:
            continue

        type_result = {
            "count": len(type_signals),
            "returns": {f"+{d}d": [] for d in [1, 3, 5]},
        }

        for signal in type_signals:
            future = get_future_returns(df, signal["date"], [1, 3, 5])

            for d in [1, 3, 5]:
                ret_data = future.get(f"+{d}d")
                if ret_data:
                    type_result["returns"][f"+{d}d"].append(ret_data["return"])

        # 计算命中率
        # 顶背离：未来应该跌（return < 0）为正确
        # 底背离：未来应该涨（return > 0）为正确
        is_top = "top" in div_type

        hit_rates = {}
        for d in [1, 3, 5]:
            returns = type_result["returns"][f"+{d}d"]
            if returns:
                if is_top:
                    hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
                else:
                    hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100

                avg_return = sum(returns) / len(returns)
                hit_rates[f"+{d}d"] = {
                    "hit_rate": round(hit_rate, 1),
                    "avg_return": round(avg_return, 2),
                    "count": len(returns),
                }

        type_result["hit_rates"] = hit_rates
        result["by_type"][div_type] = type_result

    return result


def main():
    import argparse

    parser = argparse.ArgumentParser(description="量价背离命中率测试")
    parser.add_argument("--symbol", "-s", help="股票代码，如 000001.SZ")
    parser.add_argument("--min-signals", "-m", type=int, default=3, help="最少信号数")
    parser.add_argument("--all", "-a", action="store_true", help="测试所有有数据的股票")
    parser.add_argument("--output", "-o", help="输出结果到文件")

    args = parser.parse_args()

    results = []

    if args.all:
        # 遍历所有日线数据文件
        daily_dir = STOCK_DATA_ROOT / "daily"
        for f in daily_dir.glob("daily_*.csv"):
            ts_code = f.name.replace("daily_", "").replace(".csv", "")
            print(f"测试 {ts_code}...", end=" ")
            result = test_divergence_hit_rate(ts_code, args.min_signals)
            if result:
                results.append(result)
                print(f"✓ {result['total_signals']} 个信号")
            else:
                print("✗")
    elif args.symbol:
        result = test_divergence_hit_rate(args.symbol, args.min_signals)
        if result:
            results.append(result)
        else:
            print(f"❌ {args.symbol} 数据不足或无背离信号")
            return
    else:
        # 默认测试案例
        test_stocks = [
            "600110.SH",  # 诺德股份
            "000001.SZ",  # 平安银行
            "002806.SZ",  # 华锋
            "000815.SZ",  # 美丽云
            "605162.SH",  # 新中港
            "001896.SZ",  # 豫能控股
            "000823.SZ",  # 超声电子
            "002639.SZ",  # 雪人股份
        ]

        for ts_code in test_stocks:
            print(f"测试 {ts_code}...", end=" ")
            result = test_divergence_hit_rate(ts_code, args.min_signals)
            if result:
                results.append(result)
                print(f"✓ {result['total_signals']} 个信号")
            else:
                print("✗")

    # 汇总结果
    print("\n" + "=" * 70)
    print("背离命中率测试汇总")
    print("=" * 70)

    for result in results:
        print(f"\n📊 {result['ts_code']} ({result['total_signals']} 个信号)")

        for div_type, data in result["by_type"].items():
            type_name = {
                "volume_top_div": "量价顶背离",
                "volume_bottom_div": "量价底背离",
                "rsi_top_div": "RSI顶背离",
                "rsi_bottom_div": "RSI底背离",
            }.get(div_type, div_type)

            print(f"\n  【{type_name}】共 {data['count']} 个")

            for period, stats in data["hit_rates"].items():
                direction = "跌" if "top" in div_type else "涨"
                print(
                    f"    {period}: 命中率={stats['hit_rate']}% ({direction}), "
                    f"平均收益={stats['avg_return']}%, 样本={stats['count']}"
                )

    # 综合统计
    print("\n" + "=" * 70)
    print("综合统计")
    print("=" * 70)

    all_stats = {}
    for result in results:
        for div_type, data in result["by_type"].items():
            if div_type not in all_stats:
                all_stats[div_type] = {f"+{d}d": [] for d in [1, 3, 5]}

            for d in [1, 3, 5]:
                all_stats[div_type][f"+{d}d"].extend(data["returns"][f"+{d}d"])

    for div_type, periods in all_stats.items():
        type_name = {
            "volume_top_div": "量价顶背离",
            "volume_bottom_div": "量价底背离",
            "rsi_top_div": "RSI顶背离",
            "rsi_bottom_div": "RSI底背离",
        }.get(div_type, div_type)

        is_top = "top" in div_type
        direction = "跌" if is_top else "涨"

        print(f"\n【{type_name}】")
        for period, returns in periods.items():
            if returns:
                if is_top:
                    hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
                else:
                    hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100

                avg_return = sum(returns) / len(returns)
                print(
                    f"  {period}: 命中率={hit_rate:.1f}% ({direction}), "
                    f"平均收益={avg_return:.2f}%, 样本={len(returns)}"
                )

    # 保存结果
    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "results": results,
            "summary": {},
        }

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        print(f"\n✅ 结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
