#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
背离+增强条件命中率测试 - 调试版本
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


def load_daily_data(ts_code: str, days: int = 150) -> pd.DataFrame:
    daily_file = STOCK_DATA_ROOT / f"daily/daily_{ts_code}.csv"
    if not daily_file.exists():
        return pd.DataFrame()
    df = pd.read_csv(daily_file)
    df = df.sort_values("trade_date")
    if len(df) > days:
        df = df.tail(days)
    return df.reset_index(drop=True)


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

    high14 = df["high"].rolling(window=14, min_periods=1).max()
    low14 = df["low"].rolling(window=14, min_periods=1).min()
    df["wr"] = (high14 - close) / (high14 - low14 + 0.001) * 100

    df["atr"] = (
        pd.Series(
            np.maximum(
                np.abs(high - np.roll(close, 1)), np.abs(np.roll(low, 1) - close)
            )
        )
        .rolling(window=14, min_periods=1)
        .mean()
    )

    df["atr_pct"] = df["atr"] / close * 100

    df["vol_ma5"] = pd.Series(volume).rolling(window=5, min_periods=1).mean()
    df["vol_ratio"] = volume / (df["vol_ma5"] + 1)

    df["bb_mid"] = pd.Series(close).rolling(window=20, min_periods=1).mean()
    bb_std = pd.Series(close).rolling(window=20, min_periods=1).std()
    df["bb_upper"] = df["bb_mid"] + 2 * bb_std
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std

    df["high20"] = pd.Series(close).rolling(window=20, min_periods=1).max()
    df["low20"] = pd.Series(close).rolling(window=20, min_periods=1).min()
    df["high10"] = pd.Series(close).rolling(window=10, min_periods=1).max()
    df["low10"] = pd.Series(close).rolling(window=10, min_periods=1).min()
    df["high5"] = pd.Series(close).rolling(window=5, min_periods=1).max()
    df["low5"] = pd.Series(close).rolling(window=5, min_periods=1).min()

    df["price_near_low"] = (close - df["low20"]) / (df["high20"] - df["low20"] + 0.001)
    df["price_near_high"] = (df["high20"] - close) / (
        df["high20"] - df["low20"] + 0.001
    )

    return df


def check_conditions(df: pd.DataFrame, i: int) -> dict:
    """检查各种增强条件"""
    if i < 20 or i >= len(df):
        return {}

    close = df.iloc[i]["close"]
    open_p = df.iloc[i]["open"]
    high = df.iloc[i]["high"]
    low = df.iloc[i]["low"]

    conditions = {}

    if close < df.iloc[i]["low10"] * 1.01:
        conditions["新低10日"] = True
    if close < df.iloc[i]["low5"] * 1.01:
        conditions["新低5日"] = True
    if close < df.iloc[i]["low20"] * 1.01:
        conditions["新低20日"] = True

    ma20 = df.iloc[i]["ma20"]
    if ma20 * 1.05 > close > ma20 * 0.95:
        conditions["MA20支撑"] = True

    bb_lower = df.iloc[i]["bb_lower"]
    if not pd.isna(bb_lower) and close < bb_lower * 1.02:
        conditions["布林下轨"] = True

    body = close - open_p
    range_price = high - low
    if range_price > 0:
        body_pct = abs(body) / range_price * 100
        if body > 0:
            conditions["阳线"] = True
            if body_pct >= 70:
                conditions["大阳线"] = True
            if (high - close) / range_price < 0.1:
                conditions["光头"] = True
            if (open_p - low) / range_price < 0.1:
                conditions["光脚"] = True
        else:
            conditions["阴线"] = True
            if body_pct <= 15:
                conditions["十字星"] = True

    atr_pct = df.iloc[i]["atr_pct"]
    if atr_pct < 1.0:
        conditions["极低波动1.0%"] = True
    if atr_pct < 1.2:
        conditions["极低波动1.2%"] = True
    if atr_pct < 1.5:
        conditions["极低波动1.5%"] = True
    if atr_pct < 2.0:
        conditions["低波动2.0%"] = True

    if not pd.isna(df.iloc[i]["vol_ratio"]):
        if df.iloc[i]["vol_ratio"] > 1.5:
            conditions["成交量放大1.5倍"] = True
        if df.iloc[i]["vol_ratio"] > 2.0:
            conditions["成交量放大2倍"] = True

    if i >= 3:
        consec_down = 0
        for j in range(1, 4):
            if df.iloc[i - j + 1]["close"] < df.iloc[i - j]["close"]:
                consec_down += 1
            else:
                break
        if consec_down >= 2:
            conditions["连跌2天"] = True
        if consec_down >= 3:
            conditions["连跌3天"] = True

    if df.iloc[i]["price_near_low"] < 0.2:
        conditions["接近20日低点"] = True

    if df.iloc[i]["rsi"] < 30:
        conditions["RSI超卖"] = True
    if df.iloc[i]["rsi"] < 20:
        conditions["RSI极度超卖"] = True

    if df.iloc[i]["kdj_j"] < 0:
        conditions["KDJ超卖"] = True

    return conditions


def detect_divergence(df: pd.DataFrame, i: int) -> list:
    """检测底背离"""
    if i < 20:
        return []

    close = df["close"].values

    divs = []

    if close[i] < close[i - 5 : i].min() * 1.02:
        macd_low = df.iloc[i - 10 : i]["macd_dif"].min()
        if df.iloc[i]["macd_dif"] > macd_low:
            divs.append("macd_bottom")

    if close[i] < close[i - 5 : i].min() * 1.02:
        kdj_low = df.iloc[i - 10 : i]["kdj_k"].min()
        if df.iloc[i]["kdj_k"] > kdj_low * 1.1:
            divs.append("kdj_bottom")

    if close[i] < close[i - 5 : i].min() * 1.02:
        rsi_low = df.iloc[i - 10 : i]["rsi"].min()
        if df.iloc[i]["rsi"] > rsi_low + 5:
            divs.append("rsi_bottom")

    return divs


def test_enhanced_combos():
    """测试增强条件组合"""
    daily_dir = STOCK_DATA_ROOT / "daily"
    files = list(daily_dir.glob("daily_*.csv"))

    print(f"测试 {len(files)} 只股票...")

    combos = [
        # 最严格组合: 三重底背离+大阳线+新低+MA20+ATR<1.5%+布林+接近20日低点+额外条件
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "MA20支撑", "极低波动1.5%", "布林下轨", "接近20日低点", "成交量放大1.5倍"],
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "MA20支撑", "极低波动1.5%", "布林下轨", "接近20日低点", "RSI超卖"],
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "MA20支撑", "极低波动1.5%", "布林下轨", "接近20日低点", "KDJ超卖"],
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "MA20支撑", "极低波动1.5%", "布林下轨", "接近20日低点", "成交量放大1.5倍", "RSI超卖"],
        
        # 去掉新低试试
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "MA20支撑", "极低波动1.5%", "布林下轨", "接近20日低点", "成交量放大1.5倍"],
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "MA20支撑", "极低波动1.5%", "布林下轨", "接近20日低点", "RSI超卖"],
        
        # 更严格ATR
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "极低波动1.0%", "布林下轨", "接近20日低点", "成交量放大1.5倍"],
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "极低波动0.8%", "布林下轨", "接近20日低点"],
        
        # 双重底背离+大阳线+更多条件
        ["macd_bottom+kdj_bottom", "大阳线", "新低20日", "MA20支撑", "极低波动1.5%", "布林下轨", "接近20日低点", "成交量放大1.5倍"],
        ["macd_bottom+kdj_bottom", "大阳线", "新低20日", "MA20支撑", "极低波动1.5%", "布林下轨", "接近20日低点", "RSI超卖"],
        
        # 尝试没有接近20日低点
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "MA20支撑", "极低波动1.5%", "布林下轨", "成交量放大1.5倍"],
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "MA20支撑", "极低波动1.5%", "布林下轨", "RSI超卖"],
        
        # 组合更多条件
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "MA20支撑", "极低波动1.5%", "布林下轨", "成交量放大1.5倍", "连跌2天"],
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "MA20支撑", "极低波动1.5%", "布林下轨", "RSI超卖", "连跌2天"],
        
        # 尝试没有MA20
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "新低20日", "极低波动1.5%", "布林下轨", "接近20日低点", "成交量放大1.5倍", "RSI超卖"],
        
        # 全部去掉新低
        ["macd_bottom+kdj_bottom+rsi_bottom", "大阳线", "MA20支撑", "极低波动1.5%", "布林下轨", "接近20日低点", "成交量放大1.5倍", "RSI超卖"],
    ]

    results = {
        ",".join(combo): {"count": 0, "hits": 0, "returns": []} for combo in combos
    }

    debug_count = 0

    for idx, f in enumerate(files):
        if idx % 100 == 0:
            print(f"  进度: {idx}/{len(files)}")

        ts_code = f.name.replace("daily_", "").replace(".csv", "")
        df = load_daily_data(ts_code)
        if df.empty or len(df) < 30:
            continue

        df = calculate_indicators(df)

        for i in range(20, len(df) - 5):
            close = df["close"].values

            divs = []
            if close[i] < close[i - 5 : i].min() * 1.02:
                macd_low = df.iloc[i - 10 : i]["macd_dif"].min()
                if df.iloc[i]["macd_dif"] > macd_low:
                    divs.append("macd_bottom")

                # Check for other divergences
                kdj_k = df["kdj_k"].values
                kdj_low = kdj_k[i - 10 : i].min()
                if kdj_k[i] > kdj_low * 1.1:
                    divs.append("kdj_bottom")

                rsi = df["rsi"].values
                rsi_low = rsi[i - 10 : i].min()
                if rsi[i] > rsi_low + 5:
                    divs.append("rsi_bottom")

            if not divs:
                continue

            if ts_code == "000001.SZ" and debug_count < 5:
                print(f"调试 {ts_code} i={i}: divs={divs}")

            conditions = {}
            close_p = close[i]
            open_p = df.iloc[i]["open"]
            high = df.iloc[i]["high"]
            low = df.iloc[i]["low"]

            if close_p < df.iloc[i]["low10"] * 1.02:
                conditions["新低10日"] = True
            if close_p < df.iloc[i]["low5"] * 1.02:
                conditions["新低5日"] = True
            if close_p < df.iloc[i]["low20"] * 1.02:
                conditions["新低20日"] = True

            ma20 = df.iloc[i]["ma20"]
            if not pd.isna(ma20) and ma20 * 1.05 > close_p > ma20 * 0.95:
                conditions["MA20支撑"] = True

            bb_lower = df.iloc[i]["bb_lower"]
            if not pd.isna(bb_lower) and close_p < bb_lower * 1.02:
                conditions["布林下轨"] = True

            body = close_p - open_p
            range_price = high - low
            if range_price > 0:
                body_pct = abs(body) / range_price * 100
                if body > 0:
                    conditions["阳线"] = True
                    if body_pct >= 70:
                        conditions["大阳线"] = True
                else:
                    conditions["阴线"] = True
                    if body_pct <= 15:
                        conditions["十字星"] = True

            atr_pct = df.iloc[i]["atr_pct"]
            if atr_pct < 1.0:
                conditions["极低波动1.0%"] = True
            if atr_pct < 1.2:
                conditions["极低波动1.2%"] = True
            if atr_pct < 1.5:
                conditions["极低波动1.5%"] = True
            if atr_pct < 2.0:
                conditions["低波动2.0%"] = True

            if df.iloc[i]["price_near_low"] < 0.2:
                conditions["接近20日低点"] = True

            for div in divs:
                conditions[div] = True

            if len(conditions) >= 2:
                if ts_code == "000001.SZ" and debug_count < 10:
                    print(
                        f"调试 {ts_code} i={i}: divs={divs}, 条件={list(conditions.keys())}"
                    )

                for combo in combos:
                    combo_div = combo[0].split("+")
                    combo_rest = combo[1:]

                    # 检查是否所有要求的背离都在
                    if all(d in divs for d in combo_div):
                        # 检查其他条件
                        if all(c in conditions for c in combo_rest):
                            key = ",".join(combo)

                            signal_price = close_p
                            if i + 5 < len(df):
                                future_price = df.iloc[i + 5]["close"]
                                ret = (future_price - signal_price) / signal_price * 100

                                results[key]["count"] += 1
                                results[key]["returns"].append(ret)

                                if ret > 0:
                                    results[key]["hits"] += 1

                            debug_count += 1

                            if ts_code == "000001.SZ" and debug_count < 20:
                                print(f"匹配: {key}")

    print(f"\n调试信息: 匹配数={debug_count}")

    output = []
    for key, data in results.items():
        if data["count"] >= 15:
            hit_rate = data["hits"] / data["count"] * 100
            avg_ret = np.mean(data["returns"]) if data["returns"] else 0
            output.append(
                {
                    "combo": key,
                    "count": data["count"],
                    "hit_rate": round(hit_rate, 1),
                    "avg_return": round(avg_ret, 2),
                }
            )

    output.sort(key=lambda x: x["hit_rate"], reverse=True)

    print("\n" + "=" * 80)
    print("增强条件组合命中率测试结果 (+5天)")
    print("=" * 80)
    print(f"{'组合':<60} {'样本':>6} {'命中率':>8} {'均收益':>10}")
    print("-" * 80)

    for item in output[:30]:
        flag = (
            "★★★"
            if item["hit_rate"] >= 80
            else (
                "★★"
                if item["hit_rate"] >= 75
                else ("★" if item["hit_rate"] >= 70 else "")
            )
        )
        print(
            f"{item['combo']:<60} {item['count']:>6} {item['hit_rate']:>7.1f}% {item['avg_return']:>+9.2f}% {flag}"
        )

    with open("/tmp/divergence_80_test.json", "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存到: /tmp/divergence_80_test.json")

    return output


if __name__ == "__main__":
    test_enhanced_combos()
