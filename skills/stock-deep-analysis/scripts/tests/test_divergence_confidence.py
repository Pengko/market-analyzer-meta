#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
背离检测 - 加权评分版

不消除信号，而是给每个信号计算一个置信度评分
测试高置信度信号的命中率是否显著提升
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


def calculate_confidence_score(df: pd.DataFrame, i: int, div_type: str) -> float:
    """计算信号置信度评分 (0-1)"""
    score = 0.5  # 基础分

    adx = (
        df.iloc[i]["adx"] if "adx" in df.columns and pd.notna(df.iloc[i]["adx"]) else 30
    )
    rsi = df.iloc[i]["rsi"] if "rsi" in df.columns else 50
    vol_ratio = (
        df.iloc[i]["volatility_ratio"] if "volatility_ratio" in df.columns else 2.0
    )
    near_high = (
        df.iloc[i]["price_near_high"] if "price_near_high" in df.columns else 0.5
    )
    near_low = df.iloc[i]["price_near_low"] if "price_near_low" in df.columns else 0.5
    vol_dif = df.iloc[i]["vol_dif"] if "vol_dif" in df.columns else 0
    vol_ma5 = df.iloc[i]["vol_ma5"] if "vol_ma5" in df.columns else 1
    ma排列 = df.iloc[i]["ma排列"] if "ma排列" in df.columns else 0

    if "bottom" in div_type:
        # 底背离加分项
        if rsi < 35:
            score += 0.15
        elif rsi < 45:
            score += 0.08
        elif rsi > 60:
            score -= 0.15  # RSI太高不靠谱

        if adx < 25:
            score += 0.1
        elif adx > 35:
            score -= 0.15

        if vol_ratio < 2:
            score += 0.1
        elif vol_ratio > 4:
            score -= 0.1

        if near_low < 0.15:
            score += 0.15
        elif near_low < 0.25:
            score += 0.08

        if vol_dif > vol_ma5 * 0.3:
            score += 0.1

        if ma排列 == 1:
            score += 0.05

    if "top" in div_type:
        # 顶背离加分项
        if rsi > 65:
            score += 0.15
        elif rsi > 55:
            score += 0.08
        elif rsi < 40:
            score -= 0.15

        if adx < 25:
            score += 0.1
        elif adx > 35:
            score -= 0.15

        if vol_ratio < 2:
            score += 0.1
        elif vol_ratio > 4:
            score -= 0.1

        if near_high < 0.15:
            score += 0.15
        elif near_high < 0.25:
            score += 0.08

        if vol_dif < -vol_ma5 * 0.3:
            score += 0.1

        if ma排列 == -1:
            score += 0.05

    return max(0, min(1, score))


def detect_divergences_with_confidence(df: pd.DataFrame) -> list:
    """检测背离信号并计算置信度"""
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

        # 量价顶背离
        if (
            close[i] > close[i - 3 : i].max() * 0.98
            and close[i] > price_fast.iloc[i]
            and vol_dif.iloc[i] < vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] < 0
        ):
            conf = calculate_confidence_score(df, i, "volume_top_div")
            signals.append(
                {"type": "volume_top_div", "date": trade_date, "confidence": conf}
            )

        # 量价底背离
        if (
            close[i] < close[i - 3 : i].min() * 1.02
            and close[i] < price_fast.iloc[i]
            and vol_dif.iloc[i] > vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] > 0
        ):
            conf = calculate_confidence_score(df, i, "volume_bottom_div")
            signals.append(
                {"type": "volume_bottom_div", "date": trade_date, "confidence": conf}
            )

        # RSI顶背离
        if close[i] > close[i - 5 : i].max() * 0.98:
            price_high_idx = np.argmax(close[i - 10 : i])
            if rsi.iloc[i] < rsi.iloc[i - 10 + price_high_idx] - 5:
                conf = calculate_confidence_score(df, i, "rsi_top_div")
                signals.append(
                    {"type": "rsi_top_div", "date": trade_date, "confidence": conf}
                )

        # RSI底背离
        if close[i] < close[i - 5 : i].min() * 1.02:
            price_low_idx = np.argmin(close[i - 5 : i])
            if rsi.iloc[i] > rsi.iloc[i - 5 + price_low_idx] + 5:
                conf = calculate_confidence_score(df, i, "rsi_bottom_div")
                signals.append(
                    {"type": "rsi_bottom_div", "date": trade_date, "confidence": conf}
                )

    return signals


def main():
    import argparse

    parser = argparse.ArgumentParser(description="背离置信度评分测试")
    parser.add_argument("--all", "-a", action="store_true", help="测试所有股票")
    parser.add_argument("--sample", "-n", type=int, default=500, help="采样股票数")
    parser.add_argument(
        "--thresholds",
        "-t",
        type=str,
        default="0.5,0.6,0.7",
        help="置信度阈值(逗号分隔)",
    )
    parser.add_argument("--output", "-o", help="输出文件")
    args = parser.parse_args()

    thresholds = [float(x) for x in args.thresholds.split(",")]

    daily_dir = STOCK_DATA_ROOT / "daily"
    files = list(daily_dir.glob("daily_*.csv"))

    if args.all and len(files) > args.sample:
        files = random.sample(files, args.sample)

    print(f"测试 {len(files)} 只股票...")
    print(f"置信度阈值: {thresholds}")

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

        signals = detect_divergences_with_confidence(df)

        for sig in signals:
            future = get_future_returns(df, sig["date"], [1, 3, 5])
            sig_data = {
                "confidence": sig["confidence"],
                "returns": {p: future.get(p) for p in ["+1d", "+3d", "+5d"]},
            }
            all_signals[sig["type"]].append(sig_data)

    print("\n" + "=" * 90)
    print("背离置信度分析")
    print("=" * 90)

    results = {}

    for div_type, type_name in type_names.items():
        is_top = "top" in div_type
        direction = "跌" if is_top else "涨"
        signals = all_signals[div_type]

        if not signals:
            continue

        print(f"\n【{type_name}】 (共 {len(signals)} 个信号)")

        # 按置信度分组统计
        bucket_results = {}

        for threshold in thresholds:
            bucket_results[threshold] = {p: [] for p in ["+1d", "+3d", "+5d"]}

        for sig in signals:
            for threshold in thresholds:
                if sig["confidence"] >= threshold:
                    for p in ["+1d", "+3d", "+5d"]:
                        if sig["returns"].get(p) is not None:
                            bucket_results[threshold][p].append(sig["returns"][p])

        print(f"{'阈值':<8} {'周期':<6} {'样本数':<8} {'命中率':<10} {'均收益':<10}")
        print("-" * 50)

        results[div_type] = {}

        for threshold in thresholds:
            bucket = bucket_results[threshold]
            results[div_type][threshold] = {}

            for period in ["+1d", "+3d", "+5d"]:
                returns = bucket[period]
                if returns:
                    if is_top:
                        hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
                    else:
                        hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100
                    avg_return = sum(returns) / len(returns)
                    results[div_type][threshold][period] = {
                        "count": len(returns),
                        "hit_rate": round(hit_rate, 1),
                        "avg_return": round(avg_return, 2),
                    }
                    print(
                        f"≥{threshold:<7} {period:<6} {len(returns):<8} {hit_rate:>6.1f}%   {avg_return:>+6.2f}%"
                    )

        # 找最优阈值
        best_threshold = max(
            thresholds,
            key=lambda t: (
                bucket_results.get(t, {}).get("+3d", [])
                and (
                    sum(
                        1
                        for r in bucket_results[t]["+3d"]
                        if (r < 0 if is_top else r > 0)
                    )
                    / len(bucket_results[t]["+3d"])
                    * 100
                    if bucket_results[t]["+3d"]
                    else 0
                )
            ),
        )
        print()

    # 对比不同阈值的效果
    print("=" * 90)
    print("\n阈值效果对比总结")
    print("-" * 70)

    summary = []
    for div_type, type_name in type_names.items():
        is_top = "top" in div_type
        signals = all_signals[div_type]
        if not signals:
            continue

        baseline_hr = {}
        high_conf_hr = {}

        # 全部信号
        all_returns = {p: [] for p in ["+1d", "+3d", "+5d"]}
        for sig in signals:
            for p in ["+1d", "+3d", "+5d"]:
                if sig["returns"].get(p) is not None:
                    all_returns[p].append(sig["returns"][p])

        # 高置信度信号 (>=0.7)
        high_returns = {p: [] for p in ["+1d", "+3d", "+5d"]}
        for sig in signals:
            if sig["confidence"] >= 0.7:
                for p in ["+1d", "+3d", "+5d"]:
                    if sig["returns"].get(p) is not None:
                        high_returns[p].append(sig["returns"][p])

        for period in ["+1d", "+3d", "+5d"]:
            if all_returns[period]:
                if is_top:
                    b_hr = (
                        sum(1 for r in all_returns[period] if r < 0)
                        / len(all_returns[period])
                        * 100
                    )
                else:
                    b_hr = (
                        sum(1 for r in all_returns[period] if r > 0)
                        / len(all_returns[period])
                        * 100
                    )

                if high_returns[period]:
                    if is_top:
                        h_hr = (
                            sum(1 for r in high_returns[period] if r < 0)
                            / len(high_returns[period])
                            * 100
                        )
                    else:
                        h_hr = (
                            sum(1 for r in high_returns[period] if r > 0)
                            / len(high_returns[period])
                            * 100
                        )
                else:
                    h_hr = 0

                summary.append(
                    {
                        "type": type_name,
                        "period": period,
                        "all_count": len(all_returns[period]),
                        "all_hit_rate": round(b_hr, 1),
                        "high_count": len(high_returns[period]),
                        "high_hit_rate": round(h_hr, 1),
                        "improvement": round(h_hr - b_hr, 1),
                    }
                )

    for s in summary:
        flag = "✓✓" if s["improvement"] > 3 else ("✓" if s["improvement"] > 0 else "✗")
        print(
            f"{s['type']:<12} {s['period']:<6} 全部:{s['all_hit_rate']:>5.1f}%({s['all_count']:>4}样) "
            f"高置信:{s['high_hit_rate']:>5.1f}%({s['high_count']:>4}样) 提升:{s['improvement']:>+5.1f}% {flag}"
        )

    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "config": {"stocks_tested": len(files), "thresholds": thresholds},
            "results": results,
            "summary": summary,
        }
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
