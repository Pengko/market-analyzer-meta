#!/usr/bin/env python3
"""
背离+K线形态组合命中率测试
测试不同形态组合的信号命中率
"""

import sys
import os
from pathlib import Path
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

_DEFAULT_TUSHARE_ROOT = Path.home() / "quant-data" / "tushare"
STOCK_DATA_ROOT = Path(
    os.environ.get("STOCK_DATA_ROOT")
    or (_DEFAULT_TUSHARE_ROOT / "股票数据")
)
MINUTE_DATA_ROOT = Path(
    os.environ.get("MINUTE_DATA_ROOT")
    or (STOCK_DATA_ROOT / "分钟数据")
)


def build_kline_cache():
    """构建K线形态缓存"""
    cache = {}  # {code: {date: shape}}

    for year_dir in MINUTE_DATA_ROOT.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            for day_dir in month_dir.iterdir():
                if not day_dir.is_dir() or not day_dir.name.isdigit():
                    continue
                date = f"{year_dir.name}-{month_dir.name}-{day_dir.name}"

                for symbol_dir in day_dir.iterdir():
                    if not symbol_dir.is_dir():
                        continue
                    code = symbol_dir.name.split(".")[0]
                    csv_candidates = [
                        symbol_dir / "1m.csv",
                        day_dir / f"{code}_1m.csv",
                    ]
                    csv_path = next((p for p in csv_candidates if p.exists()), None)
                    if csv_path is None:
                        continue

                    try:
                        df = pd.read_csv(csv_path)
                        if df.empty:
                            continue

                        first = df.iloc[0]
                        last = df.iloc[-1]

                        o = float(first['open'])
                        c = float(last['close'])
                        h = df['high'].astype(float).max()
                        l = df['low'].astype(float).min()

                        body = c - o
                        range_price = h - l

                        if range_price == 0:
                            continue

                        if body > 0:
                            upper = h - c
                            lower = o - l
                        elif body < 0:
                            upper = h - o
                            lower = c - l
                        else:
                            upper = h - c
                            lower = c - l

                        body_pct = abs(body) / range_price * 100
                        upper_pct = upper / range_price * 100
                        lower_pct = lower / range_price * 100

                        shapes = []
                        if body_pct <= 15:
                            shapes.append("十字星")
                        elif body > 0:
                            shapes.append("阳线")
                        else:
                            shapes.append("阴线")

                        if body_pct >= 70:
                            shapes.append("大实体")
                        if upper_pct < 10:
                            shapes.append("光头")
                        if lower_pct < 10:
                            shapes.append("光脚")
                        if upper_pct > 35 and lower_pct < 25:
                            shapes.append("上影长+下影短")
                        elif lower_pct > 35 and upper_pct < 25:
                            shapes.append("下影长+上影短")
                        elif upper_pct > 35:
                            shapes.append("上影长")
                        elif lower_pct > 35:
                            shapes.append("下影长")

                        if code not in cache:
                            cache[code] = {}
                        cache[code][date] = "_".join(shapes)

                    except Exception:
                        continue
    
    print(f"构建K线缓存: {len(cache)} 只股票, {sum(len(v) for v in cache.values())} 条数据")
    return cache


def load_daily_data(ts_code: str, days: int = 120) -> pd.DataFrame:
    daily_file = STOCK_DATA_ROOT / "daily" / f"{datetime.now().year}" / f"daily_{ts_code}.csv"
    if not daily_file.exists():
        year_files = sorted((STOCK_DATA_ROOT / "daily").glob(f"*/daily_{ts_code}.csv"))
        daily_file = year_files[-1] if year_files else STOCK_DATA_ROOT / "daily" / f"daily_{ts_code}.csv"
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
    
    delta = pd.Series(close).diff()
    gain = delta.where(delta > 0, 0).rolling(window=14, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14, min_periods=1).mean()
    rs = gain / (loss + 0.001)
    df["rsi"] = 100 - (100 / (1 + rs))
    
    ema12 = pd.Series(close).ewm(span=12, adjust=False).mean()
    ema26 = pd.Series(close).ewm(span=26, adjust=False).mean()
    df["macd_dif"] = ema12 - ema26
    df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = 2 * (df["macd_dif"] - df["macd_dea"])
    
    low_min = pd.Series(close).rolling(window=9, min_periods=1).min()
    high_max = pd.Series(close).rolling(window=9, min_periods=1).max()
    rsv = (close - low_min) / (high_max - low_min + 0.001) * 100
    df["kdj_k"] = rsv.ewm(com=2, adjust=False).mean()
    df["kdj_d"] = df["kdj_k"].ewm(com=2, adjust=False).mean()
    df["kdj_j"] = 3 * df["kdj_k"] - 2 * df["kdj_d"]
    
    return df


def detect_divergence(df: pd.DataFrame, i: int) -> list:
    signals = []
    close = df["close"].values
    trade_dates = df["trade_date"].values
    
    if i >= 5 and i < len(df) - 5:
        price_low_5 = close[i-5:i].min()
        if close[i] < price_low_5 * 1.02:
            macd_low = df.iloc[i-5:i]["macd_dif"].min()
            if df.iloc[i]["macd_dif"] > macd_low:
                signals.append(("macd_bottom", str(trade_dates[i])))
    
    if i >= 10:
        price_low_5 = close[i-5:i].min()
        if close[i] < price_low_5 * 1.02:
            kdj_low = df.iloc[i-10:i]["kdj_k"].min()
            if df.iloc[i]["kdj_k"] > kdj_low * 1.1:
                signals.append(("kdj_bottom", str(trade_dates[i])))
    
    return signals


def test_combo(stocks: list, kline_cache: dict, output_file: str = None):
    results = {}
    total_matched = 0
    
    for idx, ts_code in enumerate(stocks):
        if idx % 100 == 0:
            print(f"进度: {idx}/{len(stocks)}")
        
        df = load_daily_data(ts_code)
        if df.empty or len(df) < 30:
            continue
        
        df = calculate_indicators(df)
        
        # 获取该股票有K线数据的日期
        code = ts_code.replace('.SZ', '').replace('.SH', '')
        kline_dates = kline_cache.get(code, {})
        
        for i in range(20, len(df) - 5):
            trade_date = str(df.iloc[i]["trade_date"])
            # 转换日期格式
            trade_date_fmt = f"20{trade_date[:2]}-{trade_date[2:4]}-{trade_date[4:6]}"
            
            # 检查是否有K线形态
            kline = kline_dates.get(trade_date_fmt)
            
            divs = detect_divergence(df, i)
            
            if divs and kline:
                for div_type, div_date in divs:
                    key = f"{div_type}+{kline}"
                    if key not in results:
                        results[key] = {"count": 0, "hits": 0, "returns": []}
                    
                    results[key]["count"] += 1
                    total_matched += 1
                    
                    if i + 5 < len(df):
                        future_close = df.iloc[i + 5]["close"]
                        current_close = df.iloc[i]["close"]
                        ret = (future_close - current_close) / current_close * 100
                        results[key]["returns"].append(ret)
                        
                        if "bottom" in div_type and ret > 0:
                            results[key]["hits"] += 1
    
    output = []
    for key, data in results.items():
        if data["count"] >= 20:
            hit_rate = data["hits"] / data["count"] * 100
            avg_ret = np.mean(data["returns"]) if data["returns"] else 0
            output.append({
                "combo": key,
                "count": data["count"],
                "hit_rate": round(hit_rate, 2),
                "avg_return": round(avg_ret, 2)
            })
    
    output.sort(key=lambda x: x["hit_rate"], reverse=True)
    
    print(f"\n总匹配信号: {total_matched}")
    print("\n" + "="*70)
    print("背离+K线形态组合命中率测试结果")
    print("="*70)
    print(f"{'组合':<50} {'样本':>6} {'命中率':>8} {'平均收益':>10}")
    print("-"*70)
    
    for item in output[:40]:
        print(f"{item['combo']:<50} {item['count']:>6} {item['hit_rate']:>7.1f}% {item['avg_return']:>+9.2f}%")
    
    if output_file:
        with open(output_file, 'w') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存到: {output_file}")
    
    return output


def get_stock_list():
    daily_dir = STOCK_DATA_ROOT / "daily"
    stocks = []
    for f in daily_dir.glob("daily_*.csv"):
        ts_code = f.stem.replace("daily_", "")
        stocks.append(ts_code)
    return sorted(stocks)[:500]


if __name__ == '__main__':
    print("构建K线缓存...")
    kline_cache = build_kline_cache()
    
    stocks = get_stock_list()
    print(f"\n测试 {len(stocks)} 只股票...")
    
    output_file = "/tmp/divergence_kline_combo.json"
    test_combo(stocks, kline_cache, output_file)
