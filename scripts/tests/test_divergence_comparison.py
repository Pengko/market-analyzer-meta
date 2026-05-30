#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
背离检测 - 基础版 vs 增强版 对比测试

同时运行两个版本，对比命中率差异
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
    """计算所有指标"""
    df = df.copy()
    close = df["close"].values
    volume = df["vol"].values
    high = df["high"].values if "high" in df.columns else close
    low = df["low"].values if "low" in df.columns else close

    df["ma5"] = pd.Series(close).rolling(window=5, min_periods=1).mean()
    df["ma10"] = pd.Series(close).rolling(window=10, min_periods=1).mean()
    df["ma20"] = pd.Series(close).rolling(window=20, min_periods=1).mean()

    df["ma排列"] = 0
    df.loc[df["ma5"] > df["ma10"], "ma排列"] = 1
    df.loc[df["ma5"] < df["ma10"], "ma排列"] = -1

    delta = pd.Series(close).diff()
    gain = delta.where(delta > 0, 0).rolling(window=14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=1).mean()
    rs = gain / (loss + 0.001)
    df["rsi"] = 100 - (100 / (1 + rs))

    tr1 = high - low
    tr2 = np.abs(high - np.roll(close, 1))
    tr3 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    tr[0] = tr1[0]
    df["atr"] = pd.Series(tr).rolling(window=14, min_periods=1).mean()
    df["volatility_ratio"] = df["atr"] / (close + 0.001) * 100

    df["vol_dif"] = (
        pd.Series(volume).rolling(window=5, min_periods=1).mean()
        - pd.Series(volume).rolling(window=10, min_periods=1).mean()
    )
    df["vol_ma5"] = pd.Series(volume).rolling(window=5, min_periods=1).mean()

    df["high20"] = pd.Series(close).rolling(window=20, min_periods=1).max()
    df["low20"] = pd.Series(close).rolling(window=20, min_periods=1).min()
    df["price_near_high"] = (df["high20"] - close) / (
        df["high20"] - df["low20"] + 0.001
    )
    df["price_near_low"] = (close - df["low20"]) / (df["high20"] - df["low20"] + 0.001)

    plus_dm = np.zeros(len(close))
    minus_dm = np.zeros(len(close))
    for i in range(1, len(close)):
        high_diff = high[i] - high[i - 1] if i < len(high) else 0
        low_diff = low[i - 1] - low[i] if i < len(low) else 0
        if high_diff > low_diff and high_diff > 0:
            plus_dm[i] = high_diff
        if low_diff > high_diff and low_diff > 0:
            minus_dm[i] = low_diff

    plus_di = (
        pd.Series(plus_dm).rolling(window=14, min_periods=1).mean()
        / (df["atr"] + 0.001)
        * 100
    )
    minus_di = (
        pd.Series(minus_dm).rolling(window=14, min_periods=1).mean()
        / (df["atr"] + 0.001)
        * 100
    )
    dx = np.abs(plus_di - minus_di) / (plus_di + minus_di + 0.001) * 100
    df["adx"] = dx.rolling(window=14, min_periods=1).mean()

    return df


def detect_divergences(
    df: pd.DataFrame, enhanced: bool = False, min_filters: int = 3
) -> list:
    """检测背离信号

    Args:
        df: 日线数据
        enhanced: 是否使用增强版检测
        min_filters: 增强版最少通过的过滤条件数
    """
    if len(df) < 30:
        return []

    df = df.copy()
    df = df.sort_values("trade_date").tail(120).reset_index(drop=True)
    df = calculate_indicators(df)

    close = df["close"].values
    volume = df["vol"].values

    vol_fast = pd.Series(volume).rolling(window=5, min_periods=1).mean()
    vol_slow = pd.Series(volume).rolling(window=10, min_periods=1).mean()
    vol_dif = vol_fast - vol_slow
    price_fast = pd.Series(close).rolling(window=5, min_periods=1).mean()
    rsi = df["rsi"]

    signals = []

    for i in range(15, len(df)):
        trade_date = int(df.iloc[i]["trade_date"])

        filters_passed = 0
        filter_reasons = []

        # ===== 量价顶背离 =====
        vol_top_base = (
            close[i] > close[i - 3 : i].max() * 0.98
            and close[i] > price_fast.iloc[i]
            and vol_dif.iloc[i] < vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] < 0
        )

        if vol_top_base:
            if enhanced:
                adx = df.iloc[i]["adx"] if pd.notna(df.iloc[i]["adx"]) else 30
                rsi_val = rsi.iloc[i]
                vol_ratio = df.iloc[i]["volatility_ratio"]
                near_high = df.iloc[i]["price_near_high"]
                vol_dif_val = df.iloc[i]["vol_dif"]
                vol_ma5_val = df.iloc[i]["vol_ma5"]

                # ADX 过滤
                if adx <= 35:
                    if adx <= 25:
                        filters_passed += 1
                    # RSI 过滤
                    if rsi_val > 50:
                        filters_passed += 1
                    # 波动率过滤
                    if vol_ratio <= 3.5:
                        filters_passed += 1
                    # 价格位置
                    if near_high < 0.25:
                        filters_passed += 1
                    # 量能协调
                    if vol_dif_val < -vol_ma5_val * 0.3:
                        filters_passed += 1

                if filters_passed < min_filters:
                    continue

            signals.append(
                {"type": "volume_top_div", "date": trade_date, "enhanced": enhanced}
            )

        # ===== 量价底背离 =====
        vol_bottom_base = (
            close[i] < close[i - 3 : i].min() * 1.02
            and close[i] < price_fast.iloc[i]
            and vol_dif.iloc[i] > vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] > 0
        )

        if vol_bottom_base:
            if enhanced:
                adx = df.iloc[i]["adx"] if pd.notna(df.iloc[i]["adx"]) else 30
                rsi_val = rsi.iloc[i]
                vol_ratio = df.iloc[i]["volatility_ratio"]
                near_low = df.iloc[i]["price_near_low"]
                vol_dif_val = df.iloc[i]["vol_dif"]
                vol_ma5_val = df.iloc[i]["vol_ma5"]

                if adx <= 35:
                    if adx <= 25:
                        filters_passed += 1
                    if rsi_val < 50:
                        filters_passed += 1
                    if vol_ratio <= 3.5:
                        filters_passed += 1
                    if near_low < 0.25:
                        filters_passed += 1
                    if vol_dif_val > vol_ma5_val * 0.3:
                        filters_passed += 1

                if filters_passed < min_filters:
                    continue

            signals.append(
                {"type": "volume_bottom_div", "date": trade_date, "enhanced": enhanced}
            )

        # ===== RSI 顶背离 =====
        rsi_top_base = False
        if close[i] > close[i - 5 : i].max() * 0.98:
            price_high_idx = np.argmax(close[i - 10 : i])
            if rsi.iloc[i] < rsi.iloc[i - 10 + price_high_idx] - 5:
                rsi_top_base = True

        if rsi_top_base:
            if enhanced:
                adx = df.iloc[i]["adx"] if pd.notna(df.iloc[i]["adx"]) else 30
                rsi_val = rsi.iloc[i]
                vol_ratio = df.iloc[i]["volatility_ratio"]
                near_high = df.iloc[i]["price_near_high"]

                if adx <= 35:
                    if adx <= 25:
                        filters_passed += 1
                    if rsi_val > 50:
                        filters_passed += 1
                    if vol_ratio <= 3.5:
                        filters_passed += 1
                    if near_high < 0.25:
                        filters_passed += 1

                if filters_passed < min_filters:
                    continue

            signals.append(
                {"type": "rsi_top_div", "date": trade_date, "enhanced": enhanced}
            )

        # ===== RSI 底背离 =====
        rsi_bottom_base = False
        if close[i] < close[i - 5 : i].min() * 1.02:
            price_low_idx = np.argmin(close[i - 5 : i])
            if rsi.iloc[i] > rsi.iloc[i - 5 + price_low_idx] + 5:
                rsi_bottom_base = True

        if rsi_bottom_base:
            if enhanced:
                adx = df.iloc[i]["adx"] if pd.notna(df.iloc[i]["adx"]) else 30
                rsi_val = rsi.iloc[i]
                vol_ratio = df.iloc[i]["volatility_ratio"]
                near_low = df.iloc[i]["price_near_low"]

                if adx <= 35:
                    if adx <= 25:
                        filters_passed += 1
                    if rsi_val < 50:
                        filters_passed += 1
                    if vol_ratio <= 3.5:
                        filters_passed += 1
                    if near_low < 0.25:
                        filters_passed += 1

                if filters_passed < min_filters:
                    continue

            signals.append(
                {"type": "rsi_bottom_div", "date": trade_date, "enhanced": enhanced}
            )

    return signals


def main():
    import argparse

    parser = argparse.ArgumentParser(description="基础版 vs 增强版背离对比测试")
    parser.add_argument("--all", "-a", action="store_true", help="测试所有股票")
    parser.add_argument("--sample", "-n", type=int, default=100, help="采样股票数")
    parser.add_argument(
        "--min-filters", "-f", type=int, default=3, help="增强版最少通过过滤条件"
    )
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

    basic_results = {
        t: {p: [] for p in ["+1d", "+3d", "+5d"]} for t in type_names.keys()
    }
    enhanced_results = {
        t: {p: [] for p in ["+1d", "+3d", "+5d"]} for t in type_names.keys()
    }

    total_basic = 0
    total_enhanced = 0

    for idx, f in enumerate(files):
        ts_code = f.name.replace("daily_", "").replace(".csv", "")
        if idx % 50 == 0:
            print(f"  进度: {idx}/{len(files)}")

        df = load_daily_data(ts_code)
        if df.empty:
            continue

        basic_signals = detect_divergences(df, enhanced=False)
        enhanced_signals = detect_divergences(
            df, enhanced=True, min_filters=args.min_filters
        )

        total_basic += len(basic_signals)
        total_enhanced += len(enhanced_signals)

        for sig in basic_signals:
            future = get_future_returns(df, sig["date"], [1, 3, 5])
            for p in ["+1d", "+3d", "+5d"]:
                if future.get(p) is not None:
                    basic_results[sig["type"]][p].append(future[p])

        for sig in enhanced_signals:
            future = get_future_returns(df, sig["date"], [1, 3, 5])
            for p in ["+1d", "+3d", "+5d"]:
                if future.get(p) is not None:
                    enhanced_results[sig["type"]][p].append(future[p])

    print(f"\n信号数量: 基础版={total_basic}, 增强版={total_enhanced}")

    print("\n" + "=" * 85)
    print("背离检测对比测试结果")
    print("=" * 85)
    print(f"增强版过滤条件: min_filters={args.min_filters}")

    comparison = []

    for div_type, type_name in type_names.items():
        is_top = "top" in div_type
        direction = "跌" if is_top else "涨"

        print(f"\n【{type_name}】")
        print(f"{'周期':<6} {'基础版':<20} {'增强版':<20} {'变化':<12} {'提升':<8}")
        print("-" * 70)

        for period in ["+1d", "+3d", "+5d"]:
            b_returns = basic_results[div_type][period]
            e_returns = enhanced_results[div_type][period]

            if b_returns:
                if is_top:
                    b_hit_rate = (
                        sum(1 for r in b_returns if r < 0) / len(b_returns) * 100
                    )
                else:
                    b_hit_rate = (
                        sum(1 for r in b_returns if r > 0) / len(b_returns) * 100
                    )
                b_avg = sum(b_returns) / len(b_returns)
                b_str = f"{b_hit_rate:.1f}% ({len(b_returns)}样) 均{b_avg:.1f}%"
            else:
                b_hit_rate = 0
                b_str = "N/A"

            if e_returns:
                if is_top:
                    e_hit_rate = (
                        sum(1 for r in e_returns if r < 0) / len(e_returns) * 100
                    )
                else:
                    e_hit_rate = (
                        sum(1 for r in e_returns if r > 0) / len(e_returns) * 100
                    )
                e_avg = sum(e_returns) / len(e_returns)
                e_str = f"{e_hit_rate:.1f}% ({len(e_returns)}样) 均{e_avg:.1f}%"
            else:
                e_hit_rate = 0
                e_str = "N/A"

            change = f"{len(b_returns)}→{len(e_returns)}"
            improvement = e_hit_rate - b_hit_rate
            flag = (
                "✓✓"
                if improvement > 5
                else ("✓" if improvement > 0 else ("✗" if improvement < -5 else ""))
            )

            print(
                f"{period:<6} {b_str:<20} {e_str:<20} {change:<12} {improvement:>+5.1f}% {flag}"
            )

            comparison.append(
                {
                    "type": div_type,
                    "period": period,
                    "basic_hit_rate": round(b_hit_rate, 1),
                    "enhanced_hit_rate": round(e_hit_rate, 1),
                    "basic_count": len(b_returns),
                    "enhanced_count": len(e_returns),
                    "improvement": round(improvement, 1),
                }
            )

    print("\n" + "=" * 85)
    valid_improvements = [
        c["improvement"]
        for c in comparison
        if c["basic_count"] > 0 and c["enhanced_count"] > 0
    ]
    if valid_improvements:
        avg_improvement = sum(valid_improvements) / len(valid_improvements)
        print(
            f"平均命中率提升: {avg_improvement:+.1f}% (基于 {len(valid_improvements)} 个有效对比)"
        )

    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "config": {"min_filters": args.min_filters, "stocks_tested": len(files)},
            "signal_counts": {"basic": total_basic, "enhanced": total_enhanced},
            "comparison": comparison,
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
