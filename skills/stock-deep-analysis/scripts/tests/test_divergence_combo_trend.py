#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
背离组合 + 趋势位置 测试

测试单指标、双指标组合、三指标组合在不同趋势位置下的命中率
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


def get_future_returns(df: pd.DataFrame, signal_date: int) -> float:
    df = df.reset_index(drop=True)
    idx_list = df[df["trade_date"] == signal_date].index.tolist()
    if len(idx_list) == 0:
        return None
    idx = idx_list[0]
    if idx + 5 >= len(df):
        return None
    signal_price = df.iloc[idx]["close"]
    future_price = df.iloc[idx + 5]["close"]
    return round((future_price - signal_price) / signal_price * 100, 2)


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    close = df["close"].values
    high = df["high"].values if "high" in df.columns else close
    low = df["low"].values if "low" in df.columns else close

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

    low_n = pd.Series(low).rolling(window=9, min_periods=1).min()
    high_n = pd.Series(high).rolling(window=9, min_periods=1).max()
    rsv = (close - low_n) / (high_n - low_n + 0.001) * 100
    df["kdj_k"] = rsv.ewm(alpha=1 / 3, adjust=False).mean()
    df["kdj_j"] = (
        3 * df["kdj_k"] - 2 * df["kdj_k"].ewm(alpha=1 / 3, adjust=False).mean()
    )

    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma_tp = tp.rolling(window=14, min_periods=1).mean()
    mad = tp.rolling(window=14, min_periods=1).apply(
        lambda x: np.abs(x - x.mean()).mean()
    )
    df["cci"] = (tp - sma_tp) / (0.015 * mad + 0.001)

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
    """返回所有背离信号的列表"""
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
            signals.append(
                {
                    "date": trade_date,
                    "types": div_types,
                    "trend": trend_pos["trend"],
                    "position": trend_pos["position"],
                }
            )

    return signals


def main():
    import argparse

    parser = argparse.ArgumentParser(description="背离组合 + 趋势位置测试")
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

    # 指标列表
    indicators = ["rsi", "macd", "kdj", "cci"]
    combos = indicators.copy()
    combos.extend(["+".join(c) for c in combinations(indicators, 2)])
    combos.extend(["+".join(c) for c in combinations(indicators, 3)])

    # 结果结构: {div_type}_{trend}_{position}_{combo} -> [returns]
    results = {}

    for idx, f in enumerate(files):
        ts_code = f.name.replace("daily_", "").replace(".csv", "")
        if idx % 100 == 0:
            print(f"  进度: {idx}/{len(files)}")

        df = load_daily_data(ts_code)
        if df.empty:
            continue

        signals = detect_all_divergences(df)

        for sig in signals:
            ret = get_future_returns(df, sig["date"])
            if ret is None:
                continue

            top_types = [t.replace("_top", "") for t in sig["types"] if "_top" in t]
            bottom_types = [
                t.replace("_bottom", "") for t in sig["types"] if "_bottom" in t
            ]

            trend_pos = f"{sig['trend']}_{sig['position']}"

            # 单指标 + 趋势位置
            for ind in indicators:
                if ind in top_types:
                    key = f"top_{trend_pos}_{ind}"
                    if key not in results:
                        results[key] = []
                    results[key].append(ret)

                if ind in bottom_types:
                    key = f"bottom_{trend_pos}_{ind}"
                    if key not in results:
                        results[key] = []
                    results[key].append(ret)

            # 组合 + 趋势位置
            for combo in ["+".join(c) for c in combinations(indicators, 2)]:
                if all(ind in top_types for ind in combo.split("+")):
                    key = f"top_{trend_pos}_{combo}"
                    if key not in results:
                        results[key] = []
                    results[key].append(ret)

                if all(ind in bottom_types for ind in combo.split("+")):
                    key = f"bottom_{trend_pos}_{combo}"
                    if key not in results:
                        results[key] = []
                    results[key].append(ret)

    # 分析结果
    print("\n" + "=" * 90)
    print("背离组合 + 趋势位置测试结果 (+5天)")
    print("=" * 90)

    def calc_hit_rate(returns, is_top):
        if is_top:
            return sum(1 for r in returns if r < 0) / len(returns) * 100
        else:
            return sum(1 for r in returns if r > 0) / len(returns) * 100

    # 按趋势位置分组展示
    trend_positions = [
        "上升_上升中继",
        "震荡_反弹高点",
        "震荡_回调低点",
        "下降_下降中继",
        "上升_反弹高点",
    ]

    all_top_results = []
    all_bottom_results = []

    for tp in trend_positions:
        top_key = f"top_{tp}_"
        bottom_key = f"bottom_{tp}_"

        top_matches = {
            k.replace(top_key, ""): v
            for k, v in results.items()
            if k.startswith(top_key) and len(v) >= args.min_samples
        }
        bottom_matches = {
            k.replace(bottom_key, ""): v
            for k, v in results.items()
            if k.startswith(bottom_key) and len(v) >= args.min_samples
        }

        if not top_matches and not bottom_matches:
            continue

        print(f"\n{'=' * 60}")
        print(f"【{tp}】")
        print(f"{'=' * 60}")

        if top_matches:
            print("\n顶背离（看跌）:")
            print(f"{'组合':<15} {'样本':<6} {'命中率':<10} {'均收益':<10}")
            for combo, returns in sorted(
                top_matches.items(),
                key=lambda x: calc_hit_rate(x[1], True),
                reverse=True,
            )[:8]:
                hr = calc_hit_rate(returns, True)
                avg = sum(returns) / len(returns)
                flag = "✓✓" if hr > 60 else ("✓" if hr > 55 else "")
                print(
                    f"{combo:<15} {len(returns):<6} {hr:>5.1f}%   {avg:>+6.2f}% {flag}"
                )
                all_top_results.append(
                    {
                        "trend_pos": tp,
                        "combo": combo,
                        "count": len(returns),
                        "hit_rate": hr,
                        "avg_return": avg,
                    }
                )

        if bottom_matches:
            print("\n底背离（看涨）:")
            print(f"{'组合':<15} {'样本':<6} {'命中率':<10} {'均收益':<10}")
            for combo, returns in sorted(
                bottom_matches.items(),
                key=lambda x: calc_hit_rate(x[1], False),
                reverse=True,
            )[:8]:
                hr = calc_hit_rate(returns, False)
                avg = sum(returns) / len(returns)
                flag = "✓✓" if hr > 60 else ("✓" if hr > 55 else "")
                print(
                    f"{combo:<15} {len(returns):<6} {hr:>5.1f}%   {avg:>+6.2f}% {flag}"
                )
                all_bottom_results.append(
                    {
                        "trend_pos": tp,
                        "combo": combo,
                        "count": len(returns),
                        "hit_rate": hr,
                        "avg_return": avg,
                    }
                )

    # 全局最优
    print("\n" + "=" * 90)
    print("全局最优 TOP 10")
    print("=" * 90)

    all_results = []
    for r in all_top_results:
        all_results.append({"type": "顶背离", **r})
    for r in all_bottom_results:
        all_results.append({"type": "底背离", **r})

    all_results.sort(key=lambda x: x["hit_rate"], reverse=True)

    print(
        f"\n{'排名':<4} {'类型':<8} {'场景':<18} {'组合':<12} {'命中率':<10} {'样本':<6}"
    )
    print("-" * 70)
    for i, r in enumerate(all_results[:10], 1):
        flag = "★" if i <= 3 else ""
        print(
            f"{i:<4} {r['type']:<8} {r['trend_pos']:<18} {r['combo']:<12} {r['hit_rate']:>5.1f}%   {r['count']:<6} {flag}"
        )

    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "config": {"stocks_tested": len(files), "min_samples": args.min_samples},
            "all_results": all_results[:20],
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
