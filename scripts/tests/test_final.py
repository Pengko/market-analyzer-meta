#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import pandas as pd
import numpy as np
import json
from pathlib import Path

_DEFAULT_TUSHARE_ROOT = Path.home() / "quant-data" / "tushare"
STOCK_DATA_ROOT = Path(
    os.environ.get("STOCK_DATA_ROOT")
    or (_DEFAULT_TUSHARE_ROOT / "股票数据")
)


def load_daily_data(ts_code, days=150):
    daily_file = STOCK_DATA_ROOT / f"daily/daily_{ts_code}.csv"
    if not daily_file.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(daily_file)
    except:
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()
    df = df.sort_values("trade_date")
    if len(df) > days:
        df = df.tail(days)
    return df.reset_index(drop=True)


def calculate_indicators(df):
    df = df.copy()
    close = df["close"].values
    high = df["high"].values if "high" in df.columns else close
    low = df["low"].values if "low" in df.columns else close
    volume = df["vol"].values

    df["ma20"] = pd.Series(close).rolling(window=20, min_periods=1).mean()
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
    df["kdj_d"] = df["kdj_k"].ewm(alpha=1 / 3, adjust=False).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]

    tr1 = np.abs(high - np.roll(close, 1))
    tr2 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr1, tr2)
    atr = pd.Series(tr).rolling(window=14, min_periods=1).mean()
    df["atr_pct"] = atr / close * 100

    df["vol_ma5"] = pd.Series(volume).rolling(window=5, min_periods=1).mean()
    df["vol_ratio"] = volume / (df["vol_ma5"] + 1)

    df["bb_mid"] = pd.Series(close).rolling(window=20, min_periods=1).mean()
    bb_std = pd.Series(close).rolling(window=20, min_periods=1).std()
    df["bb_lower"] = df["bb_mid"] - 2 * bb_std

    df["high20"] = pd.Series(close).rolling(window=20, min_periods=1).max()
    df["low20"] = pd.Series(close).rolling(window=20, min_periods=1).min()
    df["high10"] = pd.Series(close).rolling(window=10, min_periods=1).max()
    df["low10"] = pd.Series(close).rolling(window=10, min_periods=1).min()
    df["high5"] = pd.Series(close).rolling(window=5, min_periods=1).max()
    df["low5"] = pd.Series(close).rolling(window=5, min_periods=1).min()
    df["price_near_low"] = (close - df["low20"]) / (df["high20"] - df["low20"] + 0.001)

    return df


def check_conditions(df, i):
    if i < 20 or i >= len(df):
        return {}
    close_p = df.iloc[i]["close"]
    open_p = df.iloc[i]["open"]
    high = df.iloc[i]["high"]
    low = df.iloc[i]["low"]
    conditions = {}
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
    atr_pct = df.iloc[i]["atr_pct"]
    if atr_pct < 1.0:
        conditions["极低波动1.0%"] = True
    if atr_pct < 1.2:
        conditions["极低波动1.2%"] = True
    if atr_pct < 1.5:
        conditions["极低波动1.5%"] = True
    if not pd.isna(df.iloc[i]["vol_ratio"]) and df.iloc[i]["vol_ratio"] > 1.5:
        conditions["成交量放大1.5倍"] = True
    if df.iloc[i]["price_near_low"] < 0.2:
        conditions["接近20日低点"] = True
    if not pd.isna(df.iloc[i]["rsi"]):
        if df.iloc[i]["rsi"] < 30:
            conditions["RSI超卖"] = True
        if df.iloc[i]["rsi"] < 20:
            conditions["RSI极度超卖"] = True
    if not pd.isna(df.iloc[i]["kdj_j"]) and df.iloc[i]["kdj_j"] < 0:
        conditions["KDJ超卖"] = True
    return conditions


daily_dir = STOCK_DATA_ROOT / "daily"
files = list(daily_dir.glob("daily_*.csv"))
print(f"测试 {len(files)} 只股票...")

combos = [
    # ========== 从少到多测试 ==========
    # 1. 单条件
    ["macd_bottom+kdj_bottom", "大阳线"],
    ["macd_bottom+kdj_bottom", "极低波动1.5%"],
    ["macd_bottom+kdj_bottom", "布林下轨"],
    # 2. 双条件
    ["macd_bottom+kdj_bottom", "大阳线", "极低波动1.5%"],
    ["macd_bottom+kdj_bottom", "大阳线", "布林下轨"],
    ["macd_bottom+kdj_bottom", "极低波动1.5%", "布林下轨"],
    ["macd_bottom+kdj_bottom", "阳线", "极低波动1.5%"],
    ["macd_bottom+kdj_bottom", "阳线", "布林下轨"],
    # 3. 三条件
    ["macd_bottom+kdj_bottom", "大阳线", "极低波动1.5%", "布林下轨"],
    ["macd_bottom+kdj_bottom", "阳线", "极低波动1.5%", "布林下轨"],
    ["macd_bottom+kdj_bottom", "大阳线", "新低20日", "极低波动1.5%"],
    ["macd_bottom+kdj_bottom", "大阳线", "MA20支撑", "极低波动1.5%"],
    # 4. 四条件
    ["macd_bottom+kdj_bottom", "大阳线", "新低20日", "极低波动1.5%", "布林下轨"],
    ["macd_bottom+kdj_bottom", "大阳线", "MA20支撑", "极低波动1.5%", "布林下轨"],
    ["macd_bottom+kdj_bottom", "阳线", "新低20日", "极低波动1.5%", "布林下轨"],
    # 5. 五条件
    [
        "macd_bottom+kdj_bottom",
        "大阳线",
        "新低20日",
        "MA20支撑",
        "极低波动1.5%",
        "布林下轨",
    ],
    [
        "macd_bottom+kdj_bottom",
        "阳线",
        "新低20日",
        "MA20支撑",
        "极低波动1.5%",
        "布林下轨",
    ],
    # 6. 六条件（最多）
    [
        "macd_bottom+kdj_bottom",
        "大阳线",
        "新低20日",
        "MA20支撑",
        "极低波动1.5%",
        "布林下轨",
        "接近20日低点",
    ],
    [
        "macd_bottom+kdj_bottom",
        "阳线",
        "新低20日",
        "MA20支撑",
        "极低波动1.5%",
        "布林下轨",
        "接近20日低点",
    ],
]

results = {",".join(combo): {"count": 0, "hits": 0, "returns": []} for combo in combos}

for idx, f in enumerate(files):
    if idx % 500 == 0:
        print(f"  进度: {idx}/{len(files)}")
    ts_code = f.name.replace("daily_", "").replace(".csv", "")
    df = load_daily_data(ts_code)
    if df.empty or len(df) < 30:
        continue
    df = calculate_indicators(df)
    for i in range(20, len(df) - 5):
        close = df["close"].values
        close_p = close[i]
        divs = []
        if close_p < close[i - 5 : i].min() * 1.02:
            macd_low = df.iloc[i - 10 : i]["macd_dif"].min()
            if df.iloc[i]["macd_dif"] > macd_low:
                divs.append("macd_bottom")
            kdj_k = df["kdj_k"].values
            kdj_low = kdj_k[i - 10 : i].min()
            if kdj_k[i] > kdj_low * 1.1:
                divs.append("kdj_bottom")
        if not divs:
            continue
        conditions = check_conditions(df, i)
        for div in divs:
            conditions[div] = True
        if len(conditions) >= 2:
            for combo in combos:
                combo_div = combo[0].split("+")
                combo_rest = combo[1:]
                if all(d in divs for d in combo_div):
                    if all(c in conditions for c in combo_rest):
                        key = ",".join(combo)
                        future_price = df.iloc[i + 5]["close"]
                        ret = (future_price - close_p) / close_p * 100
                        results[key]["count"] += 1
                        results[key]["returns"].append(ret)
                        if ret > 0:
                            results[key]["hits"] += 1

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
print("\n=== 命中率测试 (+5天) ===")
for item in output:
    flag = "★★★" if item["hit_rate"] >= 70 else ("★★" if item["hit_rate"] >= 67 else "")
    print(
        f"{item['combo']}: {item['count']}样本 {item['hit_rate']}% {item['avg_return']}% {flag}"
    )

with open("/tmp/divergence_70_test.json", "w") as f:
    json.dump(output, f, ensure_ascii=False, indent=2)
