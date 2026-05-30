#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
背离组合测试

测试单指标 vs 双指标组合 vs 三指标组合的命中率
找出最优组合
"""

import sys
import os
from pathlib import Path
from datetime import datetime
import pandas as pd
import numpy as np
import json
import random
from itertools import combinations

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

    # MA
    df["ma5"] = pd.Series(close).rolling(window=5, min_periods=1).mean()
    df["ma10"] = pd.Series(close).rolling(window=10, min_periods=1).mean()
    df["ma20"] = pd.Series(close).rolling(window=20, min_periods=1).mean()
    df["ma60"] = (
        pd.Series(close).rolling(window=60, min_periods=1).mean()
        if len(close) >= 60
        else df["ma20"]
    )

    # RSI(14)
    delta = pd.Series(close).diff()
    gain = delta.where(delta > 0, 0).rolling(window=14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=1).mean()
    rs = gain / (loss + 0.001)
    df["rsi"] = 100 - (100 / (1 + rs))

    # MACD (12, 26, 9)
    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean()
    df["macd_dif"] = ema12 - ema26
    df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()

    # KDJ (9, 3, 3)
    low_n = pd.Series(low).rolling(window=9, min_periods=1).min()
    high_n = pd.Series(high).rolling(window=9, min_periods=1).max()
    rsv = (close - low_n) / (high_n - low_n + 0.001) * 100
    df["kdj_k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    df["kdj_d"] = df["kdj_k"].ewm(alpha=1 / 3, adjust=False).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]

    # CCI (14)
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma_tp = tp.rolling(window=14, min_periods=1).mean()
    mad = tp.rolling(window=14, min_periods=1).apply(
        lambda x: np.abs(x - x.mean()).mean()
    )
    df["cci"] = (tp - sma_tp) / (0.015 * mad + 0.001)

    # WR (14)
    high14 = df["high"].rolling(window=14, min_periods=1).max()
    low14 = df["low"].rolling(window=14, min_periods=1).min()
    df["wr"] = (high14 - close) / (high14 - low14 + 0.001) * 100

    # OBV
    obv_diff = np.where(df["close"].diff() > 0, volume, -volume)
    obv = pd.Series(obv_diff).cumsum()
    obv_ma = obv.rolling(window=10, min_periods=1).mean()
    df["obv_dif"] = obv - obv_ma

    # 价格位置
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


def detect_all_divergences(df: pd.DataFrame) -> dict:
    """检测所有背离，返回按日期组织的字典"""
    if len(df) < 30:
        return {}

    df = df.copy()
    df = df.sort_values("trade_date").tail(120).reset_index(drop=True)
    df = calculate_indicators(df)

    close = df["close"].values

    # 按日期记录背离类型
    date_divergences = {}

    for i in range(20, len(df)):
        trade_date = int(df.iloc[i]["trade_date"])
        trend_pos = detect_trend_position(df, i)

        div_types = []

        # RSI 背离
        if close[i] > close[i - 5 : i].max() * 0.98:
            price_high_idx = np.argmax(close[i - 10 : i])
            if df.iloc[i]["rsi"] < df.iloc[i - 10 + price_high_idx]["rsi"] - 5:
                div_types.append("rsi_top")
        if close[i] < close[i - 5 : i].min() * 1.02:
            price_low_idx = np.argmin(close[i - 5 : i])
            if df.iloc[i]["rsi"] > df.iloc[i - 5 + price_low_idx]["rsi"] + 5:
                div_types.append("rsi_bottom")

        # MACD 背离
        if close[i] > close[i - 5 : i].max() * 0.98:
            prev_10_dif = df.iloc[i - 10 : i]["macd_dif"].values
            if len(prev_10_dif) > 0 and prev_10_dif.max() > 0:
                if df.iloc[i]["macd_dif"] < prev_10_dif.max() * 0.95:
                    div_types.append("macd_top")
        if close[i] < close[i - 5 : i].min() * 1.02:
            prev_10_dif = df.iloc[i - 10 : i]["macd_dif"].values
            if len(prev_10_dif) > 0 and prev_10_dif.min() < 0:
                if df.iloc[i]["macd_dif"] > prev_10_dif.min() * 0.95:
                    div_types.append("macd_bottom")

        # KDJ 背离
        if close[i] > close[i - 5 : i].max() * 0.98:
            prev_10_k = df.iloc[i - 10 : i]["kdj_k"].values
            if len(prev_10_k) > 0 and prev_10_k.max() > 0:
                if (
                    df.iloc[i]["kdj_k"] < prev_10_k.max() * 0.9
                    or df.iloc[i]["kdj_j"] < df.iloc[i]["kdj_k"]
                ):
                    div_types.append("kdj_top")
        if close[i] < close[i - 5 : i].min() * 1.02:
            prev_10_k = df.iloc[i - 10 : i]["kdj_k"].values
            if len(prev_10_k) > 0:
                if (
                    df.iloc[i]["kdj_k"] > prev_10_k.min() * 1.1
                    or df.iloc[i]["kdj_j"] > df.iloc[i]["kdj_k"]
                ):
                    div_types.append("kdj_bottom")

        # CCI 背离
        if close[i] > close[i - 5 : i].max() * 0.98:
            prev_10_cci = df.iloc[i - 10 : i]["cci"].values
            if len(prev_10_cci) > 0:
                if df.iloc[i]["cci"] < prev_10_cci.max() * 0.8:
                    div_types.append("cci_top")
        if close[i] < close[i - 5 : i].min() * 1.02:
            prev_10_cci = df.iloc[i - 10 : i]["cci"].values
            if len(prev_10_cci) > 0:
                if df.iloc[i]["cci"] > prev_10_cci.min() * 1.2:
                    div_types.append("cci_bottom")

        if div_types:
            date_divergences[trade_date] = {
                "types": div_types,
                "trend": trend_pos["trend"],
                "position": trend_pos["position"],
            }

    return date_divergences


def main():
    import argparse

    parser = argparse.ArgumentParser(description="背离组合测试")
    parser.add_argument("--all", "-a", action="store_true", help="测试所有股票")
    parser.add_argument("--sample", "-n", type=int, default=500, help="采样股票数")
    parser.add_argument("--min-samples", "-m", type=int, default=30, help="最少样本数")
    parser.add_argument("--output", "-o", help="输出文件")
    args = parser.parse_args()

    daily_dir = STOCK_DATA_ROOT / "daily"
    files = list(daily_dir.glob("daily_*.csv"))

    if args.all and len(files) > args.sample:
        files = random.sample(files, args.sample)

    print(f"测试 {len(files)} 只股票...")
    print(f"最少样本数: {args.min_samples}")

    # 单指标
    single_indicators = ["rsi", "macd", "kdj", "cci"]
    # 双指标组合
    double_combos = list(combinations(single_indicators, 2))
    # 三指标组合
    triple_combos = list(combinations(single_indicators, 3))

    # 顶背离和底背离分开统计
    all_results = {
        "top": {},  # 顶背离
        "bottom": {},  # 底背离
    }

    # 初始化结果
    for ind in single_indicators:
        all_results["top"][ind] = []
        all_results["bottom"][ind] = []

    for combo in double_combos:
        combo_name = "+".join(combo)
        all_results["top"][combo_name] = []
        all_results["bottom"][combo_name] = []

    for combo in triple_combos:
        combo_name = "+".join(combo)
        all_results["top"][combo_name] = []
        all_results["bottom"][combo_name] = []

    # 同时出现才算组合
    for idx, f in enumerate(files):
        ts_code = f.name.replace("daily_", "").replace(".csv", "")
        if idx % 100 == 0:
            print(f"  进度: {idx}/{len(files)}")

        df = load_daily_data(ts_code)
        if df.empty:
            continue

        date_divs = detect_all_divergences(df)

        for trade_date, data in date_divs.items():
            future = get_future_returns(df, trade_date, [5])
            ret = future.get("+5d")
            if ret is None:
                continue

            top_types = [t.replace("_top", "") for t in data["types"] if "_top" in t]
            bottom_types = [
                t.replace("_bottom", "") for t in data["types"] if "_bottom" in t
            ]

            # 单指标
            for ind in single_indicators:
                if ind in top_types:
                    all_results["top"][ind].append(ret)
                if ind in bottom_types:
                    all_results["bottom"][ind].append(ret)

            # 双指标组合
            for combo in double_combos:
                combo_name = "+".join(combo)
                # 所有指标都出现才算
                if all(ind in top_types for ind in combo):
                    all_results["top"][combo_name].append(ret)
                if all(ind in bottom_types for ind in combo):
                    all_results["bottom"][combo_name].append(ret)

            # 三指标组合
            for combo in triple_combos:
                combo_name = "+".join(combo)
                if all(ind in top_types for ind in combo):
                    all_results["top"][combo_name].append(ret)
                if all(ind in bottom_types for ind in combo):
                    all_results["bottom"][combo_name].append(ret)

    # 统计结果
    print("\n" + "=" * 90)
    print("背离组合测试结果 (+5天)")
    print("=" * 90)

    summary = []

    # 顶背离
    print("\n【顶背离（看跌）】")
    print(f"{'组合':<20} {'样本':<8} {'命中率':<12} {'均收益':<10}")
    print("-" * 55)

    top_results = []
    for name, returns in all_results["top"].items():
        if len(returns) >= args.min_samples:
            hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
            avg_ret = sum(returns) / len(returns)
            top_results.append(
                {
                    "name": name,
                    "count": len(returns),
                    "hit_rate": hit_rate,
                    "avg_return": avg_ret,
                }
            )

    top_results.sort(key=lambda x: x["hit_rate"], reverse=True)
    for r in top_results:
        flag = "✓✓" if r["hit_rate"] > 58 else ("✓" if r["hit_rate"] > 55 else "")
        print(
            f"{r['name']:<20} {r['count']:<8} {r['hit_rate']:>5.1f}%     {r['avg_return']:>+6.2f}% {flag}"
        )

    # 底背离
    print("\n【底背离（看涨）】")
    print(f"{'组合':<20} {'样本':<8} {'命中率':<12} {'均收益':<10}")
    print("-" * 55)

    bottom_results = []
    for name, returns in all_results["bottom"].items():
        if len(returns) >= args.min_samples:
            hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
            avg_ret = sum(returns) / len(returns)
            bottom_results.append(
                {
                    "name": name,
                    "count": len(returns),
                    "hit_rate": hit_rate,
                    "avg_return": avg_ret,
                }
            )

    bottom_results.sort(key=lambda x: x["hit_rate"], reverse=True)
    for r in bottom_results:
        flag = "✓✓" if r["hit_rate"] > 58 else ("✓" if r["hit_rate"] > 55 else "")
        print(
            f"{r['name']:<20} {r['count']:<8} {r['hit_rate']:>5.1f}%     {r['avg_return']:>+6.2f}% {flag}"
        )

    # 最优组合
    print("\n" + "=" * 90)
    print("最优组合 TOP 5")
    print("=" * 90)

    all_sorted = []
    for r in top_results:
        all_sorted.append(
            {
                "type": "顶背离",
                "name": r["name"],
                "hit_rate": r["hit_rate"],
                "count": r["count"],
                "avg_return": r["avg_return"],
            }
        )
    for r in bottom_results:
        all_sorted.append(
            {
                "type": "底背离",
                "name": r["name"],
                "hit_rate": r["hit_rate"],
                "count": r["count"],
                "avg_return": r["avg_return"],
            }
        )

    all_sorted.sort(key=lambda x: x["hit_rate"], reverse=True)

    print(
        f"\n{'排名':<4} {'类型':<8} {'组合':<20} {'命中率':<10} {'样本':<8} {'均收益':<10}"
    )
    print("-" * 65)
    for i, r in enumerate(all_sorted[:10], 1):
        flag = "★" if i <= 3 else ""
        print(
            f"{i:<4} {r['type']:<8} {r['name']:<20} {r['hit_rate']:>5.1f}%   {r['count']:<8} {r['avg_return']:>+6.2f}% {flag}"
        )

    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "config": {"stocks_tested": len(files), "min_samples": args.min_samples},
            "top_divergence": top_results,
            "bottom_divergence": bottom_results,
            "summary": all_sorted[:10],
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
