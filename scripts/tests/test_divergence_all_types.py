#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
背离检测 - 全类型对比测试

测试 RSI / MACD / KDJ 背离的命中率
"""

import sys
import os
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import json
import random

_DEFAULT_TUSHARE_ROOT = Path.home() / "quant-data" / "tushare"
STOCK_DATA_ROOT = Path(
    os.environ.get("STOCK_DATA_ROOT")
    or (_DEFAULT_TUSHARE_ROOT / "股票数据")
)


def load_daily_data(ts_code: str, days: int = 120) -> pd.DataFrame:
    daily_file = STOCK_DATA_ROOT / f"daily/daily_{ts_code}.csv"
    if not daily_file.exists():
        return pd.DataFrame()
    df = pd.read_csv(daily_file)
    df = df.sort_values("trade_date")
    if len(df) > days:
        df = df.tail(days)
    return df.reset_index(drop=True)


def get_future_returns(df: pd.DataFrame, signal_date: int, days: list) -> dict:
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
            pct_chg = (future_price - signal_price) / signal_price * 100
            returns[f"+{d}d"] = round(pct_chg, 2)
        else:
            returns[f"+{d}d"] = None
    return returns


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["close"].values
    high = df["high"].values if "high" in df.columns else close
    low = df["low"].values if "low" in df.columns else close
    volume = df["vol"].values

    df["ma5"] = pd.Series(close).rolling(window=5, min_periods=1).mean()
    df["ma10"] = pd.Series(close).rolling(window=10, min_periods=1).mean()
    df["ma20"] = pd.Series(close).rolling(window=20, min_periods=1).mean()
    df["ma60"] = (
        pd.Series(close).rolling(window=60, min_periods=1).mean()
        if len(close) >= 60
        else df["ma20"]
    )

    delta = pd.Series(close).diff()
    gain = delta.where(delta > 0, 0).rolling(window=14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=1).mean()
    rs = gain / (loss + 0.001)
    df["rsi"] = 100 - (100 / (1 + rs))

    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean()
    df["macd_dif"] = ema12 - ema26
    df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()

    low_n = pd.Series(low).rolling(window=9, min_periods=1).min()
    high_n = pd.Series(high).rolling(window=9, min_periods=1).max()
    rsv = (close - low_n) / (high_n - low_n + 0.001) * 100
    df["kdj_k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    df["kdj_d"] = df["kdj_k"].ewm(alpha=1 / 3, adjust=False).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]

    df["vol_dif"] = (
        pd.Series(volume).rolling(window=5, min_periods=1).mean()
        - pd.Series(volume).rolling(window=10, min_periods=1).mean()
    )

    df["high20"] = pd.Series(close).rolling(window=20, min_periods=1).max()
    df["low20"] = pd.Series(close).rolling(window=20, min_periods=1).min()
    df["price_near_high"] = (df["high20"] - close) / (
        df["high20"] - df["low20"] + 0.001
    )
    df["price_near_low"] = (close - df["low20"]) / (df["high20"] - df["low20"] + 0.001)

    return df


def detect_trend_position(df: pd.DataFrame, i: int, lookback: int = 20) -> dict:
    if i < lookback:
        return {"trend": "unknown", "position": "unknown"}

    ma5 = df.iloc[i]["ma5"]
    ma20 = df.iloc[i]["ma20"]
    ma60 = df.iloc[i]["ma60"]
    close = df["close"].values

    if ma5 > ma20 > ma60:
        trend = "上升"
    elif ma5 < ma20 < ma60:
        trend = "下降"
    else:
        trend = "震荡"

    recent_high = max(close[max(0, i - lookback) : i + 1])
    recent_low = min(close[max(0, i - lookback) : i + 1])
    range_size = recent_high - recent_low

    if range_size < 0.001:
        position = "中间"
    else:
        price_pos = (close[i] - recent_low) / range_size
        if price_pos < 0.25:
            position = "回调低点"
        elif price_pos > 0.75:
            position = "反弹高点"
        elif ma5 > ma20:
            position = "上升中继"
        else:
            position = "下降中继"

    return {"trend": trend, "position": position}


def detect_all_divergences(df: pd.DataFrame) -> list:
    if len(df) < 30:
        return []

    df = df.copy()
    df = df.sort_values("trade_date").tail(120).reset_index(drop=True)
    df = calculate_indicators(df)

    close = df["close"].values
    signals = []

    for i in range(20, len(df)):
        trade_date = int(df.iloc[i]["trade_date"])
        trend_pos = detect_trend_position(df, i)

        # RSI 顶背离
        if close[i] > close[i - 5 : i].max() * 0.98:
            price_high_idx = np.argmax(close[i - 10 : i])
            if df.iloc[i]["rsi"] < df.iloc[i - 10 + price_high_idx]["rsi"] - 5:
                signals.append(
                    {
                        "type": "rsi_top_div",
                        "date": trade_date,
                        "trend": trend_pos["trend"],
                        "position": trend_pos["position"],
                    }
                )

        # RSI 底背离
        if close[i] < close[i - 5 : i].min() * 1.02:
            price_low_idx = np.argmin(close[i - 5 : i])
            if df.iloc[i]["rsi"] > df.iloc[i - 5 + price_low_idx]["rsi"] + 5:
                signals.append(
                    {
                        "type": "rsi_bottom_div",
                        "date": trade_date,
                        "trend": trend_pos["trend"],
                        "position": trend_pos["position"],
                    }
                )

        # MACD 顶背离
        if close[i] > close[i - 5 : i].max() * 0.98:
            prev_10_dif = df.iloc[i - 10 : i]["macd_dif"].values
            if len(prev_10_dif) > 0 and prev_10_dif.max() > 0:
                if df.iloc[i]["macd_dif"] < prev_10_dif.max() * 0.95:
                    signals.append(
                        {
                            "type": "macd_top_div",
                            "date": trade_date,
                            "trend": trend_pos["trend"],
                            "position": trend_pos["position"],
                        }
                    )

        # MACD 底背离
        if close[i] < close[i - 5 : i].min() * 1.02:
            prev_10_dif = df.iloc[i - 10 : i]["macd_dif"].values
            if len(prev_10_dif) > 0 and prev_10_dif.min() < 0:
                if df.iloc[i]["macd_dif"] > prev_10_dif.min() * 0.95:
                    signals.append(
                        {
                            "type": "macd_bottom_div",
                            "date": trade_date,
                            "trend": trend_pos["trend"],
                            "position": trend_pos["position"],
                        }
                    )

        # KDJ 顶背离
        if close[i] > close[i - 5 : i].max() * 0.98:
            prev_10_k = df.iloc[i - 10 : i]["kdj_k"].values
            if len(prev_10_k) > 0 and prev_10_k.max() > 0:
                if (
                    df.iloc[i]["kdj_k"] < prev_10_k.max() * 0.9
                    or df.iloc[i]["kdj_j"] < df.iloc[i]["kdj_k"]
                ):
                    signals.append(
                        {
                            "type": "kdj_top_div",
                            "date": trade_date,
                            "trend": trend_pos["trend"],
                            "position": trend_pos["position"],
                        }
                    )

        # KDJ 底背离
        if close[i] < close[i - 5 : i].min() * 1.02:
            prev_10_k = df.iloc[i - 10 : i]["kdj_k"].values
            if len(prev_10_k) > 0:
                if (
                    df.iloc[i]["kdj_k"] > prev_10_k.min() * 1.1
                    or df.iloc[i]["kdj_j"] > df.iloc[i]["kdj_k"]
                ):
                    signals.append(
                        {
                            "type": "kdj_bottom_div",
                            "date": trade_date,
                            "trend": trend_pos["trend"],
                            "position": trend_pos["position"],
                        }
                    )

    return signals


def main():
    import argparse

    parser = argparse.ArgumentParser(description="RSI/MACD/KDJ 背离对比测试")
    parser.add_argument("--all", "-a", action="store_true", help="测试所有股票")
    parser.add_argument("--sample", "-n", type=int, default=500, help="采样股票数")
    parser.add_argument("--output", "-o", help="输出文件")
    args = parser.parse_args()

    daily_dir = STOCK_DATA_ROOT / "daily"
    files = list(daily_dir.glob("daily_*.csv"))

    if args.all and len(files) > args.sample:
        files = random.sample(files, args.sample)

    print(f"测试 {len(files)} 只股票...")

    type_names = {
        "rsi_top_div": "RSI顶背离",
        "rsi_bottom_div": "RSI底背离",
        "macd_top_div": "MACD顶背离",
        "macd_bottom_div": "MACD底背离",
        "kdj_top_div": "KDJ顶背离",
        "kdj_bottom_div": "KDJ底背离",
    }

    all_signals = {t: [] for t in type_names.keys()}

    for idx, f in enumerate(files):
        ts_code = f.name.replace("daily_", "").replace(".csv", "")
        if idx % 100 == 0:
            print(f"  进度: {idx}/{len(files)}")

        df = load_daily_data(ts_code)
        if df.empty:
            continue

        signals = detect_all_divergences(df)

        for sig in signals:
            future = get_future_returns(df, sig["date"], [1, 3, 5])
            sig_data = {
                "trend": sig["trend"],
                "position": sig["position"],
                "returns": {p: future.get(p) for p in ["+1d", "+3d", "+5d"]},
            }
            all_signals[sig["type"]].append(sig_data)

    # 统计
    print("\n" + "=" * 80)
    print("背离类型对比测试结果")
    print("=" * 80)

    for div_type, type_name in type_names.items():
        is_top = "top" in div_type
        signals = all_signals[div_type]

        if not signals:
            continue

        all_returns = {p: [] for p in ["+1d", "+3d", "+5d"]}
        for sig in signals:
            for p in ["+1d", "+3d", "+5d"]:
                if sig["returns"].get(p) is not None:
                    all_returns[p].append(sig["returns"][p])

        print(f"\n【{type_name}】 (共 {len(signals)} 个信号)")

        direction = "跌" if is_top else "涨"
        print(f"{'周期':<6} {'命中率':<10} {'样本':<8} {'均收益':<10}")
        print("-" * 40)

        for period in ["+1d", "+3d", "+5d"]:
            returns = all_returns[period]
            if returns:
                if is_top:
                    hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
                else:
                    hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
                avg_return = sum(returns) / len(returns)
                flag = "✓✓" if hit_rate > 55 else ("✓" if hit_rate > 50 else "")
                print(
                    f"{period:<6} {hit_rate:>5.1f}%({direction}) {len(returns):<8} {avg_return:>+6.2f}% {flag}"
                )

    # 按趋势位置分组
    print("\n" + "=" * 80)
    print("\n最优趋势位置场景")
    print("-" * 70)

    summary = []

    for div_type, type_name in type_names.items():
        is_top = "top" in div_type
        signals = all_signals[div_type]
        if not signals:
            continue

        # 计算全部命中率
        all_returns = []
        for sig in signals:
            ret = sig["returns"].get("+5d")
            if ret is not None:
                all_returns.append(ret)

        if is_top:
            baseline = (
                sum(1 for r in all_returns if r < 0) / len(all_returns) * 100
                if all_returns
                else 50
            )
        else:
            baseline = (
                sum(1 for r in all_returns if r > 0) / len(all_returns) * 100
                if all_returns
                else 50
            )

        # 按趋势位置分组
        groups = {}
        for sig in signals:
            key = f"{sig['trend']}_{sig['position']}"
            if key not in groups:
                groups[key] = []
            ret = sig["returns"].get("+5d")
            if ret is not None:
                groups[key].append(ret)

        best_scenario = None
        best_improvement = 0

        for group_name, returns in groups.items():
            if len(returns) < 30:
                continue
            if is_top:
                hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
            else:
                hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
            improvement = hit_rate - baseline
            if improvement > best_improvement:
                best_improvement = improvement
                best_scenario = (group_name, hit_rate, len(returns))

        if best_scenario:
            scenario_name, hit_rate, count = best_scenario
            flag = (
                "✓✓" if best_improvement > 5 else ("✓" if best_improvement > 0 else "")
            )
            print(
                f"{type_name:<14} 最佳: {scenario_name:<25} {hit_rate:>5.1f}%({count:>4}样) 提升:{best_improvement:>+5.1f}% {flag}"
            )
            summary.append(
                {
                    "type": type_name,
                    "best_scenario": scenario_name,
                    "hit_rate": hit_rate,
                    "count": count,
                    "improvement": round(best_improvement, 1),
                }
            )

    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "config": {"stocks_tested": len(files)},
            "summary": summary,
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
