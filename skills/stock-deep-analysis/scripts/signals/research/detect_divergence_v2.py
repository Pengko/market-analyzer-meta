#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版量价背离检测器 v2

结合趋势位置的背离检测

核心发现（2026-04-10 测试 500 只股票）：
1. 单纯背离信号命中率约 50%，接近随机
2. 结合趋势位置可显著提升命中率：
   - 量价顶背离 + 上升趋势中继 → 61.1% 命中率 (+5.2%)
   - 量价底背离 + 震荡市反弹高点 → 61.3% 命中率 (+10.4%)
   - RSI顶背离 + 上升趋势中继 → 57.6% 命中率 (+4.0%)
   - RSI底背离 + 震荡市反弹高点 → 56.3% 命中率 (+11.4%)

3. 关键洞察：
   - A股趋势信号 > 均值回归信号
   - 上升趋势中的顶背离往往是中继，不是反转
   - 下降趋势中的底背离效果不佳
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import argparse
import json
import pandas as pd
import numpy as np

from data.config_loader import cfg

STOCK_DATA_ROOT = cfg.paths('stock_data_root')


def load_daily_data(ts_code: str, days: int = 120) -> pd.DataFrame:
    daily_file = STOCK_DATA_ROOT / f"daily/daily_{ts_code}.csv"
    if not daily_file.exists():
        return pd.DataFrame()
    df = pd.read_csv(daily_file)
    df = df.sort_values("trade_date")
    if len(df) > days:
        df = df.tail(days)
    return df.reset_index(drop=True)


def detect_trend_position(df: pd.DataFrame, i: int, lookback: int = 20) -> dict:
    """检测趋势位置

    Returns:
        trend: 上升/下降/震荡
        position: 回调低点/反弹高点/上升中继/下降中继
    """
    if i < lookback:
        return {"trend": "unknown", "position": "unknown"}

    close = df["close"].values
    ma5 = pd.Series(close).rolling(window=cfg.indicator("moving_average", "short", default=5), min_periods=1).mean().values
    ma20 = pd.Series(close).rolling(window=cfg.indicator("moving_average", "long", default=20), min_periods=1).mean().values
    ma60 = (
        pd.Series(close).rolling(window=cfg.indicator("moving_average", "very_long", default=60), min_periods=1).mean().values
        if len(close) >= 60
        else ma20
    )

    current_price = close[i]
    current_ma5 = ma5[i]
    current_ma20 = ma20[i]
    current_ma60 = ma60[i]

    if current_ma5 > current_ma20 > current_ma60:
        trend = "上升"
    elif current_ma5 < current_ma20 < current_ma60:
        trend = "下降"
    else:
        trend = "震荡"

    recent_high = max(close[max(0, i - lookback) : i + 1])
    recent_low = min(close[max(0, i - lookback) : i + 1])
    range_size = recent_high - recent_low

    if range_size < 0.001:
        position = "中间"
    else:
        price_pos = (current_price - recent_low) / range_size

        if price_pos < 0.25:
            position = "回调低点"
        elif price_pos > 0.75:
            position = "反弹高点"
        elif current_ma5 > current_ma20:
            position = "上升中继"
        else:
            position = "下降中继"

    return {"trend": trend, "position": position}


def calculate_confidence_score(
    trend_pos: dict, div_type: str, rsi: float = 50
) -> float:
    """根据趋势位置计算置信度评分 (0-1)

    基于测试结果调整评分：
    - 顶背离在上升趋势中继效果最好
    - 底背离在震荡市反弹高点效果最好
    """
    trend = trend_pos["trend"]
    position = trend_pos["position"]

    score = 0.5

    if "bottom" in div_type:
        if trend == "震荡" and position == "反弹高点":
            score += 0.25
        if trend == "下降" and position == "回调低点":
            score += 0.15
        if rsi < 35:
            score += 0.1
        elif rsi > 60:
            score -= 0.15

    if "top" in div_type:
        if trend == "上升" and position == "上升中继":
            score += 0.25
        if trend == "下降" and position == "反弹高点":
            score += 0.15
        if rsi > 65:
            score += 0.1
        elif rsi < 40:
            score -= 0.15

    return max(0, min(1, score))


def detect_divergences(df: pd.DataFrame, min_confidence: float = 0.0) -> list:
    """检测背离信号并计算置信度"""
    if len(df) < 30:
        return []

    df = df.copy()
    df = df.sort_values("trade_date").tail(120).reset_index(drop=True)

    close = df["close"].values
    volume = df["vol"].values

    vol_fast = pd.Series(volume).rolling(window=cfg.indicator("volume", "fast_window", default=5), min_periods=1).mean()
    vol_slow = pd.Series(volume).rolling(window=cfg.indicator("volume", "slow_window", default=10), min_periods=1).mean()
    vol_dif = vol_fast - vol_slow
    price_fast = pd.Series(close).rolling(window=cfg.indicator("moving_average", "short", default=5), min_periods=1).mean()

    delta = pd.Series(close).diff()
    gain = delta.where(delta > 0, 0).rolling(window=cfg.indicator("rsi", "period", default=14), min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=cfg.indicator("rsi", "period", default=14), min_periods=1).mean()
    rs = gain / (loss + 0.001)
    rsi = 100 - (100 / (1 + rs))

    signals = []

    for i in range(20, len(df)):
        trade_date = int(df.iloc[i]["trade_date"])
        trend_pos = detect_trend_position(df, i)
        rsi_val = rsi.iloc[i]

        # 量价顶背离
        if (
            close[i] > close[i - 3 : i].max() * 0.98
            and close[i] > price_fast.iloc[i]
            and vol_dif.iloc[i] < vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] < 0
        ):
            conf = calculate_confidence_score(trend_pos, "volume_top_div", rsi_val)
            if conf >= min_confidence:
                signals.append(
                    {
                        "type": "volume_top_div",
                        "date": trade_date,
                        "price": float(close[i]),
                        "confidence": conf,
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
            conf = calculate_confidence_score(trend_pos, "volume_bottom_div", rsi_val)
            if conf >= min_confidence:
                signals.append(
                    {
                        "type": "volume_bottom_div",
                        "date": trade_date,
                        "price": float(close[i]),
                        "confidence": conf,
                        "trend": trend_pos["trend"],
                        "position": trend_pos["position"],
                    }
                )

        # RSI顶背离
        if close[i] > close[i - 5 : i].max() * 0.98:
            price_high_idx = np.argmax(close[i - 10 : i])
            if rsi.iloc[i] < rsi.iloc[i - 10 + price_high_idx] - 5:
                conf = calculate_confidence_score(trend_pos, "rsi_top_div", rsi_val)
                if conf >= min_confidence:
                    signals.append(
                        {
                            "type": "rsi_top_div",
                            "date": trade_date,
                            "price": float(close[i]),
                            "rsi": round(float(rsi_val), 2),
                            "confidence": conf,
                            "trend": trend_pos["trend"],
                            "position": trend_pos["position"],
                        }
                    )

        # RSI底背离
        if close[i] < close[i - 5 : i].min() * 1.02:
            price_low_idx = np.argmin(close[i - 5 : i])
            if rsi.iloc[i] > rsi.iloc[i - 5 + price_low_idx] + 5:
                conf = calculate_confidence_score(trend_pos, "rsi_bottom_div", rsi_val)
                if conf >= min_confidence:
                    signals.append(
                        {
                            "type": "rsi_bottom_div",
                            "date": trade_date,
                            "price": float(close[i]),
                            "rsi": round(float(rsi_val), 2),
                            "confidence": conf,
                            "trend": trend_pos["trend"],
                            "position": trend_pos["position"],
                        }
                    )

    return signals


def analyze(ts_code: str, min_confidence: float = 0.0) -> dict:
    """分析单只股票的背离信号"""
    df = load_daily_data(ts_code)
    if df.empty:
        return {"error": "数据不存在"}

    signals = detect_divergences(df, min_confidence)

    return {
        "ts_code": ts_code,
        "total_signals": len(signals),
        "signals": signals[-5:] if len(signals) > 5 else signals,
    }


def print_result(result: dict):
    print(f"\n{'=' * 60}")
    print(f"背离分析: {result['ts_code']}")
    print(f"{'=' * 60}")
    print(f"信号总数: {result['total_signals']}")

    if result["signals"]:
        print("\n最近信号:")
        for sig in result["signals"]:
            type_name = {
                "volume_top_div": "量价顶背离",
                "volume_bottom_div": "量价底背离",
                "rsi_top_div": "RSI顶背离",
                "rsi_bottom_div": "RSI底背离",
            }.get(sig["type"], sig["type"])

            conf_bar = "█" * int(sig["confidence"] * 10) + "░" * (
                10 - int(sig["confidence"] * 10)
            )
            print(
                f"  {sig['date']} | {type_name} | {sig['trend']}_{sig['position']} | {conf_bar} {sig['confidence']:.0%}"
            )


def main():
    parser = argparse.ArgumentParser(description="增强版量价背离检测 (v2)")
    parser.add_argument("ts_code", help="股票代码，如 000001.SZ")
    parser.add_argument(
        "--min-confidence", "-c", type=float, default=0.0, help="最小置信度 (0-1)"
    )
    parser.add_argument("--json", "-j", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--save", "-s", help="保存结果到文件")

    args = parser.parse_args()

    result = analyze(args.ts_code, args.min_confidence)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_result(result)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {args.save}")


if __name__ == "__main__":
    main()
