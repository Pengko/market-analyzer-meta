#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量价背离检测脚本
检测价格与成交量的背离信号，用于判断趋势反转概率

背离类型：
- 顶背离：价格创新高，但成交量/能量未跟随 → 看跌信号
- 底背离：价格创新低，但成交量/能量未跟随 → 看涨信号

支持维度：
- 日线级别背离（短线 swing）
- 分钟级别背离（盘中日内信号）
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import argparse
import json
import pandas as pd
import numpy as np

# 数据路径
from data.config_loader import cfg

STOCK_DATA_ROOT = cfg.paths('stock_data_root')


def load_daily_data(ts_code: str, days: int = 60) -> pd.DataFrame:
    """加载日线数据"""
    daily_file = STOCK_DATA_ROOT / f"daily/daily_{ts_code}.csv"
    if not daily_file.exists():
        return pd.DataFrame()

    df = pd.read_csv(daily_file)
    df = df.sort_values("trade_date")

    # 取最近 N 天
    if len(df) > days:
        df = df.tail(days)

    return df


def load_minute_data(ts_code: str, trade_date: str = None, period: int = 5) -> pd.DataFrame:
    """加载分钟数据"""
    if trade_date is None:
        trade_date = datetime.now().strftime("%Y-%m-%d")

    pure = ts_code.replace(".SH", "").replace(".SZ", "")
    y, m, d = trade_date.split("-")

    # 新结构A：分钟数据/YYYY/MM/DD/{pure}_{period}m.csv
    new_base = STOCK_DATA_ROOT / "分钟数据" / y / m / d
    new_patterns = [
        new_base / f"{pure}_{period}m.csv",
        new_base / f"{pure}_1m.csv",
    ]

    # 新结构B：分钟数据/YYYY/MM/DD/{symbol}/1m.csv
    partitioned_patterns = [
        new_base / ts_code / f"{period}m.csv",
        new_base / ts_code / "1m.csv",
        new_base / pure / f"{period}m.csv",
        new_base / pure / "1m.csv",
    ]

    # 旧结构 fallback
    old_base = STOCK_DATA_ROOT / "分钟数据" / pure
    old_patterns = [
        old_base / trade_date / f"minute_kline_{period}m.csv",
        old_base / trade_date / "minute_kline.csv",
        old_base / f"{trade_date}/minute_kline_{period}m.csv",
        old_base / f"{trade_date}/minute_kline.csv",
    ]

    for pattern in new_patterns + partitioned_patterns + old_patterns:
        if pattern.exists():
            return pd.read_csv(pattern)

    return pd.DataFrame()


def calculate_ma(series: pd.Series, window: int) -> pd.Series:
    """计算移动平均"""
    return series.rolling(window=window, min_periods=1).mean()


def calculate_volume_ma(volumes: pd.Series, window: int = 5) -> pd.Series:
    """计算成交量移动平均"""
    return volumes.rolling(window=window, min_periods=1).mean()


def detect_daily_divergence(df: pd.DataFrame) -> dict:
    """检测日线级别背离

    使用 MACD 类方法：
    1. 价格用收盘价，指标用成交量
    2. 计算 DIF = 快线 - 慢线
    3. 检测价格与 DIF 的背离
    """
    if len(df) < 20:
        return {"has_divergence": False, "signal": None, "details": {}}

    df = df.copy()
    df = df.sort_values("trade_date").tail(60)

    # 计算指标
    close = df["close"].values
    volume = df["vol"].values

    # 成交量快慢线
    vol_fast = calculate_volume_ma(pd.Series(volume), 5)
    vol_slow = calculate_volume_ma(pd.Series(volume), 10)
    vol_dif = vol_fast - vol_slow

    # 价格快慢线
    price_fast = calculate_ma(pd.Series(close), 5)
    price_slow = calculate_ma(pd.Series(close), 10)
    price_dif = price_fast - price_slow

    # 检测最近 N 根 K 线
    results = {
        "has_divergence": False,
        "signal": None,
        "signal_strength": None,
        "details": {},
    }

    signals = []

    for i in range(max(5, len(df) - 10), len(df)):
        if i < 2:
            continue

        # 取最近 3 根 K 线分析
        recent_dif = vol_dif[i - 2 : i + 1].values
        recent_price = close[i - 2 : i + 1]

        # ===== 顶背离检测 =====
        # 价格创新高但 DIF 走低
        if (
            close[i] > close[i - 3 : i].max() * 0.98  # 价格接近/创新高
            and close[i] > price_fast.iloc[i]  # 在均线上
            and vol_dif.iloc[i] < vol_dif.iloc[i - 1]  # 成交量 DIF 下降
            and vol_dif.iloc[i] < 0
        ):  # 成交量萎缩
            # 计算背离强度
            price_chg = (close[i] - close[i - 3]) / close[i - 3] * 100
            vol_chg = (vol_dif.iloc[i] - vol_dif.iloc[i - 3]) / (
                abs(vol_dif.iloc[i - 3]) + 0.001
            )

            signals.append(
                {
                    "type": "top_divergence",  # 顶背离
                    "index": int(df.iloc[i]["trade_date"]),
                    "price": float(close[i]),
                    "price_change": round(price_chg, 2),
                    "volume_dif": float(vol_dif.iloc[i]),
                    "volume_change": round(vol_chg, 2),
                    "strength": "strong" if abs(vol_chg) > 0.5 else "normal",
                }
            )

        # ===== 底背离检测 =====
        # 价格创新低但 DIF 走高
        if (
            close[i] < close[i - 3 : i].min() * 1.02  # 价格接近/创新低
            and close[i] < price_fast.iloc[i]  # 在均线下方
            and vol_dif.iloc[i] > vol_dif.iloc[i - 1]  # 成交量 DIF 上升
            and vol_dif.iloc[i] > 0
        ):  # 成交量放大
            price_chg = (close[i] - close[i - 3]) / close[i - 3] * 100
            vol_chg = (vol_dif.iloc[i] - vol_dif.iloc[i - 3]) / (
                abs(vol_dif.iloc[i - 3]) + 0.001
            )

            signals.append(
                {
                    "type": "bottom_divergence",  # 底背离
                    "index": int(df.iloc[i]["trade_date"]),
                    "price": float(close[i]),
                    "price_change": round(price_chg, 2),
                    "volume_dif": float(vol_dif.iloc[i]),
                    "volume_change": round(vol_chg, 2),
                    "strength": "strong" if abs(vol_chg) > 0.5 else "normal",
                }
            )

    if signals:
        results["has_divergence"] = True
        results["details"] = {"signals": signals}

        # 综合判断信号
        latest = signals[-1]
        results["signal"] = latest["type"]
        results["signal_strength"] = latest["strength"]

        # 连续背离增强信号
        same_type = [s for s in signals if s["type"] == latest["type"]]
        if len(same_type) >= 2:
            results["signal_strength"] = "strong"
            results["cumulative"] = len(same_type)

    return results


def detect_intraday_divergence(df: pd.DataFrame, window: int = 20) -> dict:
    """检测分钟级别背离

    用于盘中实时信号检测
    """
    if len(df) < window * 2:
        return {"has_divergence": False, "signal": None, "details": {}}

    results = {
        "has_divergence": False,
        "signal": None,
        "signal_strength": None,
        "details": {},
    }

    # 尝试找到价格和成交量列
    price_col = None
    vol_col = None

    for col in ["close", "收盘", "price"]:
        if col in df.columns:
            price_col = col
            break

    for col in ["vol", "成交量", "volume", "amount"]:
        if col in df.columns:
            vol_col = col
            break

    if price_col is None or vol_col is None:
        return results

    # 计算滚动统计
    df = df.copy()
    df["price_ma"] = df[price_col].rolling(window=window, min_periods=1).mean()
    df["vol_ma"] = df[vol_col].rolling(window=window, min_periods=1).mean()

    # 计算相对位置
    df["price_pos"] = (df[price_col] - df["price_ma"]) / (df["price_ma"] + 0.001)
    df["vol_pos"] = (df[vol_col] - df["vol_ma"]) / (df["vol_ma"] + 0.001)

    signals = []

    for i in range(window, len(df)):
        # 顶背离：价格创新高，成交量未跟随
        recent_high = df[price_col].iloc[max(0, i - window) : i].max()
        if df[price_col].iloc[i] >= recent_high * 0.99:
            vol_ratio = df[vol_col].iloc[i] / (df["vol_ma"].iloc[i] + 0.001)

            if vol_ratio < 0.7:  # 成交量萎缩到均量 70% 以下
                signals.append(
                    {
                        "type": "top_divergence",
                        "time": df.iloc[i]["time"] if "time" in df.columns else str(i),
                        "price": float(df[price_col].iloc[i]),
                        "vol_ratio": round(vol_ratio, 2),
                        "strength": "strong" if vol_ratio < 0.5 else "normal",
                    }
                )

        # 底背离：价格创新低，成交量未跟随
        recent_low = df[price_col].iloc[max(0, i - window) : i].min()
        if df[price_col].iloc[i] <= recent_low * 1.01:
            vol_ratio = df[vol_col].iloc[i] / (df["vol_ma"].iloc[i] + 0.001)

            if vol_ratio > 1.3:  # 成交量放大到均量 130% 以上
                signals.append(
                    {
                        "type": "bottom_divergence",
                        "time": df.iloc[i]["time"] if "time" in df.columns else str(i),
                        "price": float(df[price_col].iloc[i]),
                        "vol_ratio": round(vol_ratio, 2),
                        "strength": "strong" if vol_ratio > 2.0 else "normal",
                    }
                )

    if signals:
        results["has_divergence"] = True
        results["details"] = {"signals": signals}

        latest = signals[-1]
        results["signal"] = latest["type"]
        results["signal_strength"] = latest["strength"]

    return results


def detect_price_momentum_divergence(df: pd.DataFrame, lookback: int = 20) -> dict:
    """基于动量指标的背离检测（更可靠）

    使用 RSI 类指标与价格对比
    """
    if len(df) < lookback * 2:
        return {"has_divergence": False, "signal": None}

    df = df.copy()
    close = df["close"].values
    volume = df["vol"].values

    # 计算 RSI(14)
    delta = pd.Series(close).diff()
    rsi_period = cfg.indicator('rsi', 'period', default=14)
    gain = delta.where(delta > 0, 0).rolling(window=rsi_period, min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=rsi_period, min_periods=1).mean()
    rs = gain / (loss + 0.001)
    rsi = 100 - (100 / (1 + rs))

    # 计算成交量动量
    vol_window = cfg.indicator('volume', 'slow_window', default=10)
    vol_ma = pd.Series(volume).rolling(window=vol_window, min_periods=1).mean()
    vol_momentum = volume / (vol_ma + 0.001)

    results = {
        "has_divergence": False,
        "signal": None,
        "signal_strength": None,
        "details": {},
    }

    signals = []

    for i in range(lookback, len(df)):
        # 顶背离：价格新高 + RSI 未能新高或开始下降
        if close[i] > close[i - 5 : i].max() * 0.98:
            price_high_idx = np.argmax(close[i - 10 : i])
            rsi_high_idx = np.argmax(rsi[i - 10 : i])

            # 价格创新高但 RSI 未创新高
            if rsi.iloc[i] < rsi.iloc[i - 10 + price_high_idx] - 5:
                signals.append(
                    {
                        "type": "top_divergence",
                        "index": int(df.iloc[i]["trade_date"]),
                        "price": float(close[i]),
                        "rsi": round(float(rsi.iloc[i]), 2),
                        "rsi_compare": round(
                            float(rsi.iloc[i - 10 + price_high_idx] - rsi.iloc[i]), 2
                        ),
                        "strength": "strong" if rsi.iloc[i] < 40 else "normal",
                    }
                )

        # 底背离：价格新低 + RSI 未能新低或开始上升
        if close[i] < close[i - 5 : i].min() * 1.02:
            price_low_idx = np.argmin(close[i - 10 : i])
            rsi_low_idx = np.argmin(rsi[i - 10 : i])

            # 价格创新低但 RSI 未创新低
            if rsi.iloc[i] > rsi.iloc[i - 10 + price_low_idx] + 5:
                signals.append(
                    {
                        "type": "bottom_divergence",
                        "index": int(df.iloc[i]["trade_date"]),
                        "price": float(close[i]),
                        "rsi": round(float(rsi.iloc[i]), 2),
                        "rsi_compare": round(
                            float(rsi.iloc[i] - rsi.iloc[i - 10 + price_low_idx]), 2
                        ),
                        "strength": "strong" if rsi.iloc[i] > 60 else "normal",
                    }
                )

    if signals:
        results["has_divergence"] = True
        latest = signals[-1]
        results["signal"] = latest["type"]
        results["signal_strength"] = latest["strength"]
        results["details"] = {"signals": signals}

    return results


def analyze_divergence(
    ts_code: str, trade_date: str = None, level: str = "daily"
) -> dict:
    """综合背离分析

    Args:
        ts_code: 股票代码，如 "000001.SZ"
        trade_date: 交易日期 YYYYMMDD
        level: 分析级别 "daily" / "intraday" / "all"

    Returns:
        dict: 背离分析结果
    """
    result = {
        "ts_code": ts_code,
        "trade_date": trade_date,
        "timestamp": datetime.now().isoformat(),
        "level": level,
        "volume_price_divergence": None,
        "momentum_divergence": None,
        "summary": None,
    }

    # 日线背离分析
    if level in ["daily", "all"]:
        daily_df = load_daily_data(ts_code)
        if not daily_df.empty:
            vol_price_result = detect_daily_divergence(daily_df)
            momentum_result = detect_price_momentum_divergence(daily_df)
            result["volume_price_divergence"] = vol_price_result
            result["momentum_divergence"] = momentum_result

    # 分钟背离分析
    if level in ["intraday", "all"]:
        minute_df = load_minute_data(ts_code, trade_date)
        if not minute_df.empty:
            intraday_result = detect_intraday_divergence(minute_df)
            result["intraday_divergence"] = intraday_result

    # 生成摘要
    signals = []
    if result.get("volume_price_divergence", {}).get("has_divergence"):
        signals.append(("量价背离", result["volume_price_divergence"]))
    if result.get("momentum_divergence", {}).get("has_divergence"):
        signals.append(("动量背离", result["momentum_divergence"]))
    if result.get("intraday_divergence", {}).get("has_divergence"):
        signals.append(("分钟背离", result["intraday_divergence"]))

    if signals:
        summary_parts = []
        for name, data in signals:
            signal = data.get("signal", "unknown")
            strength = data.get("signal_strength", "unknown")
            summary_parts.append(f"{name}:{signal}({strength})")
        result["summary"] = " | ".join(summary_parts)
    else:
        result["summary"] = "无背离信号"

    return result


def print_result(result: dict):
    """打印结果"""
    print(f"\n{'=' * 60}")
    print(f"背离分析: {result['ts_code']}")
    print(f"{'=' * 60}")

    print(f"\n📊 分析级别: {result['level']}")
    print(f"📅 摘要: {result['summary']}")

    # 量价背离
    vp = result.get("volume_price_divergence")
    if vp:
        print(f"\n【量价背离】")
        if vp.get("has_divergence"):
            sig = vp.get("signal", "unknown")
            strength = vp.get("signal_strength", "unknown")
            print(f"  ⚠️ 信号: {sig}")
            print(f"  💪 强度: {strength}")

            details = vp.get("details", {}).get("signals", [])
            if details:
                print(f"  📝 最近信号:")
                for d in details[-3:]:
                    print(
                        f"    - {d['type']}: 日期={d.get('index', 'N/A')}, 价格={d.get('price')}"
                    )
        else:
            print(f"  ✅ 无背离")

    # 动量背离
    mm = result.get("momentum_divergence")
    if mm:
        print(f"\n【动量背离】")
        if mm.get("has_divergence"):
            sig = mm.get("signal", "unknown")
            strength = mm.get("signal_strength", "unknown")
            print(f"  ⚠️ 信号: {sig}")
            print(f"  💪 强度: {strength}")

            details = mm.get("details", {}).get("signals", [])
            if details:
                print(f"  📝 最近信号:")
                for d in details[-3:]:
                    print(
                        f"    - {d['type']}: RSI={d.get('rsi')}, 价格={d.get('price')}"
                    )
        else:
            print(f"  ✅ 无背离")

    # 分钟背离
    intra = result.get("intraday_divergence")
    if intra:
        print(f"\n【分钟背离】")
        if intra.get("has_divergence"):
            sig = intra.get("signal", "unknown")
            print(f"  ⚠️ 信号: {sig}")

            details = intra.get("details", {}).get("signals", [])
            if details:
                print(f"  📝 最近信号:")
                for d in details[-3:]:
                    print(
                        f"    - {d['type']}: 时间={d.get('time')}, 价格={d.get('price')}"
                    )
        else:
            print(f"  ✅ 无背离")

    print()


def main():
    parser = argparse.ArgumentParser(description="量价背离检测")
    parser.add_argument("ts_code", help="股票代码，如 000001.SZ")
    parser.add_argument("--date", "-d", help="交易日期 YYYYMMDD")
    parser.add_argument(
        "--level",
        "-l",
        choices=["daily", "intraday", "all"],
        default="daily",
        help="分析级别",
    )
    parser.add_argument("--json", "-j", action="store_true", help="输出 JSON 格式")
    parser.add_argument("--save", "-s", help="保存结果到文件")

    args = parser.parse_args()

    result = analyze_divergence(args.ts_code, args.date, args.level)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_result(result)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"结果已保存到: {args.save}")


if __name__ == "__main__":
    main()
