#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
背离检测 - 趋势位置结合测试

测试核心假设：
1. 底背离 + 处于上升趋势的回调低点 → 命中率应该更高
2. 顶背离 + 处于下降趋势的反弹高点 → 命中率应该更高
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


def detect_trend_position(df: pd.DataFrame, i: int, lookback: int = 20) -> dict:
    """检测趋势位置

    Returns:
        trend: 上升/下降/震荡
        position: 回调低点/回调高点/中间/突破/破位
    """
    if i < lookback:
        return {"trend": "unknown", "position": "unknown"}

    close = df["close"].values
    ma5 = pd.Series(close).rolling(window=5, min_periods=1).mean().values
    ma20 = pd.Series(close).rolling(window=20, min_periods=1).mean().values
    ma60 = (
        pd.Series(close).rolling(window=60, min_periods=1).mean().values
        if len(close) >= 60
        else ma20
    )

    current_price = close[i]
    current_ma5 = ma5[i]
    current_ma20 = ma20[i]
    current_ma60 = ma60[i]

    # 判断趋势
    if current_ma5 > current_ma20 > current_ma60:
        trend = "上升"
    elif current_ma5 < current_ma20 < current_ma60:
        trend = "下降"
    else:
        trend = "震荡"

    # 判断位置 - 用最近 lookback 天的高低点
    recent_high = max(close[max(0, i - lookback) : i + 1])
    recent_low = min(close[max(0, i - lookback) : i + 1])
    range_size = recent_high - recent_low

    if range_size < 0.001:
        position = "中间"
    else:
        price_pos = (current_price - recent_low) / range_size  # 0=低点, 1=高点

        if price_pos < 0.25:
            position = "回调低点"
        elif price_pos > 0.75:
            position = "反弹高点"
        elif current_ma5 > current_ma20:
            position = "上升中继"
        else:
            position = "下降中继"

    return {"trend": trend, "position": position}


def detect_divergences_with_position(df: pd.DataFrame) -> list:
    """检测背离并记录趋势位置"""
    if len(df) < 30:
        return []

    df = df.copy()
    df = df.sort_values("trade_date").tail(120).reset_index(drop=True)

    close = df["close"].values
    volume = df["vol"].values

    vol_fast = pd.Series(volume).rolling(window=5, min_periods=1).mean()
    vol_slow = pd.Series(volume).rolling(window=10, min_periods=1).mean()
    vol_dif = vol_fast - vol_slow
    price_fast = pd.Series(close).rolling(window=5, min_periods=1).mean()

    delta = pd.Series(close).diff()
    gain = delta.where(delta > 0, 0).rolling(window=14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=1).mean()
    rs = gain / (loss + 0.001)
    rsi = 100 - (100 / (1 + rs))

    signals = []

    for i in range(20, len(df)):
        trade_date = int(df.iloc[i]["trade_date"])
        trend_pos = detect_trend_position(df, i)

        # 量价顶背离
        if (
            close[i] > close[i - 3 : i].max() * 0.98
            and close[i] > price_fast.iloc[i]
            and vol_dif.iloc[i] < vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] < 0
        ):
            signals.append(
                {
                    "type": "volume_top_div",
                    "date": trade_date,
                    "trend": trend_pos["trend"],
                    "position": trend_pos["position"],
                }
            )

        # 量价底背离
        if (
            close[i] < close[i - 3 : i].min() * 1.02
            and close[i] < price_fast.iloc[i]
            and vol_dif.iloc[i] > vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] > 0
        ):
            signals.append(
                {
                    "type": "volume_bottom_div",
                    "date": trade_date,
                    "trend": trend_pos["trend"],
                    "position": trend_pos["position"],
                }
            )

        # RSI顶背离
        if close[i] > close[i - 5 : i].max() * 0.98:
            price_high_idx = np.argmax(close[i - 10 : i])
            if rsi.iloc[i] < rsi.iloc[i - 10 + price_high_idx] - 5:
                signals.append(
                    {
                        "type": "rsi_top_div",
                        "date": trade_date,
                        "trend": trend_pos["trend"],
                        "position": trend_pos["position"],
                    }
                )

        # RSI底背离
        if close[i] < close[i - 5 : i].min() * 1.02:
            price_low_idx = np.argmin(close[i - 5 : i])
            if rsi.iloc[i] > rsi.iloc[i - 5 + price_low_idx] + 5:
                signals.append(
                    {
                        "type": "rsi_bottom_div",
                        "date": trade_date,
                        "trend": trend_pos["trend"],
                        "position": trend_pos["position"],
                    }
                )

    return signals


def main():
    import argparse

    parser = argparse.ArgumentParser(description="背离 + 趋势位置分析")
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
        "volume_top_div": "量价顶背离",
        "volume_bottom_div": "量价底背离",
        "rsi_top_div": "RSI顶背离",
        "rsi_bottom_div": "RSI底背离",
    }

    all_signals = {t: [] for t in type_names.keys()}

    for idx, f in enumerate(files):
        ts_code = f.name.replace("daily_", "").replace(".csv", "")
        if idx % 100 == 0:
            print(f"  进度: {idx}/{len(files)}")

        df = load_daily_data(ts_code)
        if df.empty:
            continue

        signals = detect_divergences_with_position(df)

        for sig in signals:
            future = get_future_returns(df, sig["date"], [1, 3, 5])
            sig_data = {
                "trend": sig["trend"],
                "position": sig["position"],
                "returns": {p: future.get(p) for p in ["+1d", "+3d", "+5d"]},
            }
            all_signals[sig["type"]].append(sig_data)

    print("\n" + "=" * 90)
    print("背离 + 趋势位置分析")
    print("=" * 90)

    results = {}

    for div_type, type_name in type_names.items():
        is_top = "top" in div_type
        signals = all_signals[div_type]

        if not signals:
            continue

        # 按趋势+位置分组
        groups = {}
        for sig in signals:
            key = f"{sig['trend']}_{sig['position']}"
            if key not in groups:
                groups[key] = {p: [] for p in ["+1d", "+3d", "+5d"]}
            for p in ["+1d", "+3d", "+5d"]:
                if sig["returns"].get(p) is not None:
                    groups[key][p].append(sig["returns"][p])

        results[div_type] = {}

        print(f"\n【{type_name}】 (共 {len(signals)} 个信号)")

        # 全部平均
        all_returns = {p: [] for p in ["+1d", "+3d", "+5d"]}
        for sig in signals:
            for p in ["+1d", "+3d", "+5d"]:
                if sig["returns"].get(p) is not None:
                    all_returns[p].append(sig["returns"][p])

        print(f"\n{'场景':<20} {'周期':<6} {'样本':<6} {'命中率':<10} {'均收益':<10}")
        print("-" * 60)

        # 全部
        for period in ["+1d", "+3d", "+5d"]:
            returns = all_returns[period]
            if returns:
                if is_top:
                    hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
                else:
                    hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
                avg_return = sum(returns) / len(returns)
                print(
                    f"{'【全部】':<20} {period:<6} {len(returns):<6} {hit_rate:>6.1f}%   {avg_return:>+6.2f}%"
                )

        # 按趋势位置分组
        for group_name in sorted(groups.keys()):
            if group_name == "unknown_unknown":
                continue
            group_returns = groups[group_name]
            has_data = any(group_returns[p] for p in ["+1d", "+3d", "+5d"])
            if not has_data:
                continue

            print(f"\n{group_name}:")
            for period in ["+1d", "+3d", "+5d"]:
                returns = group_returns[period]
                if returns:
                    if is_top:
                        hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
                    else:
                        hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
                    avg_return = sum(returns) / len(returns)
                    flag = (
                        "✓✓"
                        if (hit_rate > 55 if is_top else hit_rate > 55)
                        else ("✓" if hit_rate > 50 else "")
                    )
                    print(
                        f"  {period:<6} {len(returns):<6} {hit_rate:>6.1f}%   {avg_return:>+6.2f}% {flag}"
                    )

        results[div_type] = {
            "total": len(signals),
            "groups": {},
        }
        for group_name, group_returns in groups.items():
            results[div_type]["groups"][group_name] = {}
            for period in ["+1d", "+3d", "+5d"]:
                returns = group_returns[period]
                if returns:
                    if is_top:
                        hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
                    else:
                        hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
                    results[div_type]["groups"][group_name][period] = {
                        "count": len(returns),
                        "hit_rate": round(hit_rate, 1),
                        "avg_return": round(sum(returns) / len(returns), 2),
                    }

    # 总结最优场景
    print("\n" + "=" * 90)
    print("\n最优场景总结")
    print("-" * 70)

    summary = []

    for div_type, type_name in type_names.items():
        is_top = "top" in div_type
        signals = all_signals[div_type]
        if not signals:
            continue

        # 计算全部命中率
        all_returns = {p: [] for p in ["+1d", "+3d", "+5d"]}
        for sig in signals:
            for p in ["+1d", "+3d", "+5d"]:
                if sig["returns"].get(p) is not None:
                    all_returns[p].append(sig["returns"][p])

        if is_top:
            baseline = (
                sum(1 for r in all_returns["+5d"] if r < 0)
                / len(all_returns["+5d"])
                * 100
                if all_returns["+5d"]
                else 50
            )
        else:
            baseline = (
                sum(1 for r in all_returns["+5d"] if r > 0)
                / len(all_returns["+5d"])
                * 100
                if all_returns["+5d"]
                else 50
            )

        # 按趋势位置分组
        groups = {}
        for sig in signals:
            key = f"{sig['trend']}_{sig['position']}"
            if key not in groups:
                groups[key] = {p: [] for p in ["+1d", "+3d", "+5d"]}
            for p in ["+1d", "+3d", "+5d"]:
                if sig["returns"].get(p) is not None:
                    groups[key][p].append(sig["returns"][p])

        # 找最优场景
        best_scenario = None
        best_improvement = 0

        for group_name, group_returns in groups.items():
            if group_name == "unknown_unknown":
                continue
            returns = group_returns["+5d"]
            if len(returns) < 30:  # 样本太少不统计
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
            summary.append(
                {
                    "type": type_name,
                    "best_scenario": scenario_name,
                    "hit_rate": hit_rate,
                    "count": count,
                    "improvement": round(best_improvement, 1),
                }
            )

    for s in summary:
        direction = "跌" if "top" in s["type"] else "涨"
        flag = "✓✓" if s["improvement"] > 5 else ("✓" if s["improvement"] > 0 else "")
        print(
            f"{s['type']:<14} 最佳场景: {s['best_scenario']:<25} 命中率:{s['hit_rate']:>5.1f}%({s['count']:>4}样) 提升:{s['improvement']:>+5.1f}% {flag}"
        )

    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "config": {"stocks_tested": len(files)},
            "results": results,
            "summary": summary,
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
