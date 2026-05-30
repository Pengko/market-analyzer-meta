#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
增强版量价背离检测器

在基础背离检测上增加辅助验证条件：
1. 趋势位置过滤 - MA排列、ADX判断趋势强度
2. 波动率过滤 - ATR/标准差过滤极端波动
3. RSI区间过滤 - 极端值信号更可靠
4. 量能协调验证 - 量价同向增强信号
5. 价格位置验证 - 离关键位的距离

预期效果：牺牲部分信号数量，提升命中率
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


def load_daily_data(ts_code: str, days: int = 120) -> pd.DataFrame:
    """加载日线数据"""
    daily_file = STOCK_DATA_ROOT / f"daily/daily_{ts_code}.csv"
    if not daily_file.exists():
        return pd.DataFrame()

    df = pd.read_csv(daily_file)
    df = df.sort_values("trade_date")

    if len(df) > days:
        df = df.tail(days)

    return df.reset_index(drop=True)


def calculate_auxiliary_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算辅助指标"""
    df = df.copy()

    close = df["close"].values
    volume = df["vol"].values

    # 1. MA 排列 (趋势方向)
    ma_short = cfg.indicator("moving_average", "short", default=5)
    ma_medium = cfg.indicator("moving_average", "medium", default=10)
    ma_long = cfg.indicator("moving_average", "long", default=20)
    df["ma5"] = pd.Series(close).rolling(window=ma_short, min_periods=1).mean()
    df["ma10"] = pd.Series(close).rolling(window=ma_medium, min_periods=1).mean()
    df["ma20"] = pd.Series(close).rolling(window=ma_long, min_periods=1).mean()

    # MA 排列: 1=多头排列(上升), -1=空头排列(下降), 0=混乱
    df["ma排列"] = 0
    df.loc[df["ma5"] > df["ma10"], "ma排列"] = 1
    df.loc[df["ma5"] < df["ma10"], "ma排列"] = -1

    # 2. RSI(14)
    delta = pd.Series(close).diff()
    gain = delta.where(delta > 0, 0).rolling(window=cfg.indicator("rsi", "period", default=14), min_periods=1).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=cfg.indicator("rsi", "period", default=14), min_periods=1).mean()
    rs = gain / (loss + 0.001)
    df["rsi"] = 100 - (100 / (1 + rs))

    # 3. ATR (波动率)
    high = df["high"].values if "high" in df.columns else close
    low = df["low"].values if "low" in df.columns else close
    tr1 = high - low
    tr2 = np.abs(high - np.roll(close, 1))
    tr3 = np.abs(low - np.roll(close, 1))
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    tr[0] = tr1[0] if len(tr1) > 0 else 0
    df["atr"] = pd.Series(tr).rolling(window=cfg.indicator("atr", "period", default=14), min_periods=1).mean()

    # 相对波动率 (ATR / 价格 * 100)
    df["volatility_ratio"] = df["atr"] / (close + 0.001) * 100

    # 4. 成交量指标
    vol_fast = pd.Series(volume).rolling(window=cfg.indicator("volume", "fast_window", default=5), min_periods=1).mean()
    vol_slow = pd.Series(volume).rolling(window=cfg.indicator("volume", "slow_window", default=10), min_periods=1).mean()
    df["vol_dif"] = vol_fast - vol_slow
    df["vol_ma5"] = vol_fast

    # 5. 价格位置 (离最近20日高低的距离)
    df["high20"] = pd.Series(close).rolling(window=cfg.indicator("moving_average", "long", default=20), min_periods=1).max()
    df["low20"] = pd.Series(close).rolling(window=cfg.indicator("moving_average", "long", default=20), min_periods=1).min()
    df["price_near_high"] = (df["high20"] - close) / (
        df["high20"] - df["low20"] + 0.001
    )
    df["price_near_low"] = (close - df["low20"]) / (df["high20"] - df["low20"] + 0.001)

    # 6. ADX (趋势强度)
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
        pd.Series(plus_dm).rolling(window=cfg.indicator("adx", "period", default=14), min_periods=1).mean()
        / (df["atr"] + 0.001)
        * 100
    )
    minus_di = (
        pd.Series(minus_dm).rolling(window=cfg.indicator("adx", "period", default=14), min_periods=1).mean()
        / (df["atr"] + 0.001)
        * 100
    )

    dx = np.abs(plus_di - minus_di) / (plus_di + minus_di + 0.001) * 100
    df["adx"] = dx.rolling(window=cfg.indicator("adx", "period", default=14), min_periods=1).mean()

    # 7. 价格动量 (5日涨跌)
    df["momentum5"] = pd.Series(close).pct_change(5) * 100

    return df


def check_trend_filter(df: pd.DataFrame, i: int, div_type: str) -> dict:
    """检查趋势过滤条件

    Returns:
        pass_filter: 是否通过过滤
        reason: 原因说明
        bonus: 强度加成 (0-1)
    """
    result = {"pass": True, "reason": "", "bonus": 0.0}

    # 趋势强度 (ADX)
    adx = df.iloc[i]["adx"] if "adx" in df.columns else 30

    # 顶背离在强趋势中不太可靠
    if "top" in div_type:
        if adx > 35:
            result["pass"] = False
            result["reason"] = "强趋势中顶背离不可靠"
            return result
        elif adx > 25:
            result["bonus"] = 0.3

    # 底背离在强趋势中不太可靠
    if "bottom" in div_type:
        if adx > 35:
            result["pass"] = False
            result["reason"] = "强趋势中底背离不可靠"
            return result
        elif adx > 25:
            result["bonus"] = 0.3

    # MA 排列一致性
    ma排列 = df.iloc[i]["ma排列"] if "ma排列" in df.columns else 0

    # 顶背离应该对应短期回调
    if "top" in div_type and ma排列 == -1:
        result["bonus"] += 0.2

    # 底背离应该对应短期反弹
    if "bottom" in div_type and ma排列 == 1:
        result["bonus"] += 0.2

    return result


def check_rsi_filter(df: pd.DataFrame, i: int, div_type: str) -> dict:
    """检查 RSI 区间过滤

    RSI 极端值时背离更可靠
    """
    result = {"pass": True, "reason": "", "bonus": 0.0}

    rsi = df.iloc[i]["rsi"] if "rsi" in df.columns else 50

    if "bottom" in div_type:
        # 底背离在超卖区(RSI<35)更可靠
        if rsi > 50:
            result["pass"] = False
            result["reason"] = "底背离需RSI<50"
            return result
        elif rsi < 35:
            result["bonus"] = 0.3
        elif rsi < 45:
            result["bonus"] = 0.1

    if "top" in div_type:
        # 顶背离在超买区(RSI>65)更可靠
        if rsi < 50:
            result["pass"] = False
            result["reason"] = "顶背离需RSI>50"
            return result
        elif rsi > 65:
            result["bonus"] = 0.3
        elif rsi > 55:
            result["bonus"] = 0.1

    return result


def check_volatility_filter(df: pd.DataFrame, i: int) -> dict:
    """检查波动率过滤

    极端波动时信号不可靠
    """
    result = {"pass": True, "reason": "", "bonus": 0.0}

    vol_ratio = (
        df.iloc[i]["volatility_ratio"] if "volatility_ratio" in df.columns else 2.0
    )

    # 波动率过高时不可靠
    if vol_ratio > 5:
        result["pass"] = False
        result["reason"] = f"波动率过高({vol_ratio:.1f}%)"
        return result
    elif vol_ratio > 3.5:
        result["bonus"] = -0.1
    elif vol_ratio < 2:
        result["bonus"] = 0.2

    return result


def check_price_position_filter(df: pd.DataFrame, i: int, div_type: str) -> dict:
    """检查价格位置过滤

    价格接近极端位置时背离更可靠
    """
    result = {"pass": True, "reason": "", "bonus": 0.0}

    if "bottom" in div_type:
        near_low = (
            df.iloc[i]["price_near_low"] if "price_near_low" in df.columns else 0.3
        )
        # 价格离低点越近，底背离越可靠
        if near_low < 0.15:
            result["bonus"] = 0.3
        elif near_low < 0.25:
            result["bonus"] = 0.1
        else:
            result["bonus"] = -0.1

    if "top" in div_type:
        near_high = (
            df.iloc[i]["price_near_high"] if "price_near_high" in df.columns else 0.3
        )
        # 价格离高点越近，顶背离越可靠
        if near_high < 0.15:
            result["bonus"] = 0.3
        elif near_high < 0.25:
            result["bonus"] = 0.1
        else:
            result["bonus"] = -0.1

    return result


def check_volume_coordination(df: pd.DataFrame, i: int, div_type: str) -> dict:
    """检查量能协调性

    底背离时量能放大增强，顶背离时量能萎缩增强
    """
    result = {"pass": True, "reason": "", "bonus": 0.0}

    vol_dif = df.iloc[i]["vol_dif"] if "vol_dif" in df.columns else 0

    if "bottom" in div_type:
        # 底背离需要量能放大
        if vol_dif <= 0:
            result["bonus"] = -0.2
        elif vol_dif > df.iloc[i]["vol_ma5"] * 0.3:
            result["bonus"] = 0.2

    if "top" in div_type:
        # 顶背离需要量能萎缩
        if vol_dif >= 0:
            result["bonus"] = -0.2
        elif vol_dif < -df.iloc[i]["vol_ma5"] * 0.3:
            result["bonus"] = 0.2

    return result


def detect_enhanced_divergences(df: pd.DataFrame, min_filters_passed: int = 2) -> list:
    """增强版背离检测

    Args:
        df: 日线数据
        min_filters_passed: 最少需要通过的过滤条件数量

    Returns:
        信号列表，每条信号包含类型、日期、价格和强度评分
    """
    if len(df) < 30:
        return []

    df = df.copy()
    df = df.sort_values("trade_date").tail(120).reset_index(drop=True)

    # 计算辅助指标
    df = calculate_auxiliary_indicators(df)

    close = df["close"].values
    volume = df["vol"].values

    # 基础背离指标
    vol_fast = pd.Series(volume).rolling(window=cfg.indicator("volume", "fast_window", default=5), min_periods=1).mean()
    vol_slow = pd.Series(volume).rolling(window=cfg.indicator("volume", "slow_window", default=10), min_periods=1).mean()
    vol_dif = vol_fast - vol_slow

    price_fast = pd.Series(close).rolling(window=cfg.indicator("moving_average", "short", default=5), min_periods=1).mean()
    rsi = df["rsi"] if "rsi" in df.columns else pd.Series(50, index=df.index)

    signals = []

    for i in range(15, len(df)):
        trade_date = int(df.iloc[i]["trade_date"])
        filters_passed = 0
        total_bonus = 0.0
        filter_details = {}

        # ===== 量价顶背离基础检测 =====
        vol_top_base = (
            close[i] > close[i - 3 : i].max() * 0.98
            and close[i] > price_fast.iloc[i]
            and vol_dif.iloc[i] < vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] < 0
        )

        # ===== 量价底背离基础检测 =====
        vol_bottom_base = (
            close[i] < close[i - 3 : i].min() * 1.02
            and close[i] < price_fast.iloc[i]
            and vol_dif.iloc[i] > vol_dif.iloc[i - 1]
            and vol_dif.iloc[i] > 0
        )

        # ===== RSI 顶背离基础检测 =====
        rsi_top_base = False
        if close[i] > close[i - 5 : i].max() * 0.98:
            price_high_idx = np.argmax(close[i - 10 : i])
            rsi_high_idx = np.argmax(rsi[i - 10 : i])
            if rsi.iloc[i] < rsi.iloc[i - 10 + price_high_idx] - 5:
                rsi_top_base = True

        # ===== RSI 底背离基础检测 =====
        rsi_bottom_base = False
        if close[i] < close[i - 5 : i].min() * 1.02:
            price_low_idx = np.argmin(close[i - 5 : i])
            rsi_low_idx = np.argmin(rsi[i - 5 : i])
            if rsi.iloc[i] > rsi.iloc[i - 5 + price_low_idx] + 5:
                rsi_bottom_base = True

        # 处理量价顶背离
        if vol_top_base:
            div_type = "volume_top_div"

            # 趋势过滤
            trend_result = check_trend_filter(df, i, div_type)
            filter_details["trend"] = trend_result
            if trend_result["pass"]:
                filters_passed += 1
            total_bonus += trend_result["bonus"]

            # RSI过滤
            rsi_result = check_rsi_filter(df, i, div_type)
            filter_details["rsi"] = rsi_result
            if rsi_result["pass"]:
                filters_passed += 1
            total_bonus += rsi_result["bonus"]

            # 波动率过滤
            vol_result = check_volatility_filter(df, i)
            filter_details["volatility"] = vol_result
            if vol_result["pass"]:
                filters_passed += 1
            total_bonus += vol_result["bonus"]

            # 价格位置过滤
            pos_result = check_price_position_filter(df, i, div_type)
            filter_details["price_position"] = pos_result
            if pos_result["pass"]:
                filters_passed += 1
            total_bonus += pos_result["bonus"]

            # 量能协调
            coord_result = check_volume_coordination(df, i, div_type)
            filter_details["volume_coord"] = coord_result
            total_bonus += coord_result["bonus"]

            if filters_passed >= min_filters_passed:
                price_chg = (close[i] - close[i - 3]) / close[i - 3] * 100
                vol_chg = (vol_dif.iloc[i] - vol_dif.iloc[i - 3]) / (
                    abs(vol_dif.iloc[i - 3]) + 0.001
                )

                base_strength = 1.0 if abs(vol_chg) > 0.5 else 0.7
                final_strength = min(1.5, base_strength + total_bonus)

                signals.append(
                    {
                        "type": div_type,
                        "date": trade_date,
                        "price": float(close[i]),
                        "price_change": round(price_chg, 2),
                        "filters_passed": filters_passed,
                        "filters_detail": {
                            k: v["reason"]
                            if not v["pass"]
                            else f"pass(bonus:{v['bonus']:.1f})"
                            for k, v in filter_details.items()
                        },
                        "strength_score": round(final_strength, 2),
                        "strength": "strong" if final_strength > 1.1 else "normal",
                    }
                )

        # 处理量价底背离
        if vol_bottom_base:
            div_type = "volume_bottom_div"

            trend_result = check_trend_filter(df, i, div_type)
            filter_details["trend"] = trend_result
            if trend_result["pass"]:
                filters_passed += 1
            total_bonus += trend_result["bonus"]

            rsi_result = check_rsi_filter(df, i, div_type)
            filter_details["rsi"] = rsi_result
            if rsi_result["pass"]:
                filters_passed += 1
            total_bonus += rsi_result["bonus"]

            vol_result = check_volatility_filter(df, i)
            filter_details["volatility"] = vol_result
            if vol_result["pass"]:
                filters_passed += 1
            total_bonus += vol_result["bonus"]

            pos_result = check_price_position_filter(df, i, div_type)
            filter_details["price_position"] = pos_result
            if pos_result["pass"]:
                filters_passed += 1
            total_bonus += pos_result["bonus"]

            coord_result = check_volume_coordination(df, i, div_type)
            filter_details["volume_coord"] = coord_result
            total_bonus += coord_result["bonus"]

            if filters_passed >= min_filters_passed:
                price_chg = (close[i] - close[i - 3]) / close[i - 3] * 100
                vol_chg = (vol_dif.iloc[i] - vol_dif.iloc[i - 3]) / (
                    abs(vol_dif.iloc[i - 3]) + 0.001
                )

                base_strength = 1.0 if abs(vol_chg) > 0.5 else 0.7
                final_strength = min(1.5, base_strength + total_bonus)

                signals.append(
                    {
                        "type": div_type,
                        "date": trade_date,
                        "price": float(close[i]),
                        "price_change": round(price_chg, 2),
                        "filters_passed": filters_passed,
                        "filters_detail": {
                            k: v["reason"]
                            if not v["pass"]
                            else f"pass(bonus:{v['bonus']:.1f})"
                            for k, v in filter_details.items()
                        },
                        "strength_score": round(final_strength, 2),
                        "strength": "strong" if final_strength > 1.1 else "normal",
                    }
                )

        # 处理 RSI 顶背离
        if rsi_top_base:
            div_type = "rsi_top_div"

            trend_result = check_trend_filter(df, i, div_type)
            filter_details["trend"] = trend_result
            if trend_result["pass"]:
                filters_passed += 1
            total_bonus += trend_result["bonus"]

            rsi_result = check_rsi_filter(df, i, div_type)
            filter_details["rsi"] = rsi_result
            if rsi_result["pass"]:
                filters_passed += 1
            total_bonus += rsi_result["bonus"]

            vol_result = check_volatility_filter(df, i)
            filter_details["volatility"] = vol_result
            if vol_result["pass"]:
                filters_passed += 1
            total_bonus += vol_result["bonus"]

            pos_result = check_price_position_filter(df, i, div_type)
            filter_details["price_position"] = pos_result
            if pos_result["pass"]:
                filters_passed += 1
            total_bonus += pos_result["bonus"]

            if filters_passed >= min_filters_passed:
                rsi_val = rsi.iloc[i]

                base_strength = 1.0 if rsi_val < 40 else 0.7
                final_strength = min(1.5, base_strength + total_bonus)

                signals.append(
                    {
                        "type": div_type,
                        "date": trade_date,
                        "price": float(close[i]),
                        "rsi": round(float(rsi_val), 2),
                        "filters_passed": filters_passed,
                        "filters_detail": {
                            k: v["reason"]
                            if not v["pass"]
                            else f"pass(bonus:{v['bonus']:.1f})"
                            for k, v in filter_details.items()
                        },
                        "strength_score": round(final_strength, 2),
                        "strength": "strong" if final_strength > 1.1 else "normal",
                    }
                )

        # 处理 RSI 底背离
        if rsi_bottom_base:
            div_type = "rsi_bottom_div"

            trend_result = check_trend_filter(df, i, div_type)
            filter_details["trend"] = trend_result
            if trend_result["pass"]:
                filters_passed += 1
            total_bonus += trend_result["bonus"]

            rsi_result = check_rsi_filter(df, i, div_type)
            filter_details["rsi"] = rsi_result
            if rsi_result["pass"]:
                filters_passed += 1
            total_bonus += rsi_result["bonus"]

            vol_result = check_volatility_filter(df, i)
            filter_details["volatility"] = vol_result
            if vol_result["pass"]:
                filters_passed += 1
            total_bonus += vol_result["bonus"]

            pos_result = check_price_position_filter(df, i, div_type)
            filter_details["price_position"] = pos_result
            if pos_result["pass"]:
                filters_passed += 1
            total_bonus += pos_result["bonus"]

            if filters_passed >= min_filters_passed:
                rsi_val = rsi.iloc[i]

                base_strength = 1.0 if rsi_val > 60 else 0.7
                final_strength = min(1.5, base_strength + total_bonus)

                signals.append(
                    {
                        "type": div_type,
                        "date": trade_date,
                        "price": float(close[i]),
                        "rsi": round(float(rsi_val), 2),
                        "filters_passed": filters_passed,
                        "filters_detail": {
                            k: v["reason"]
                            if not v["pass"]
                            else f"pass(bonus:{v['bonus']:.1f})"
                            for k, v in filter_details.items()
                        },
                        "strength_score": round(final_strength, 2),
                        "strength": "strong" if final_strength > 1.1 else "normal",
                    }
                )

    return signals


def test_enhanced_divergence(
    ts_code: str, min_signals: int = 3, min_filters: int = 2
) -> dict:
    """测试增强版背离命中率"""
    df = load_daily_data(ts_code)
    if df.empty:
        return None

    signals = detect_enhanced_divergences(df, min_filters_passed=min_filters)
    if len(signals) < min_signals:
        return None

    result = {"ts_code": ts_code, "total_signals": len(signals), "by_type": {}}

    for div_type in [
        "volume_top_div",
        "volume_bottom_div",
        "rsi_top_div",
        "rsi_bottom_div",
    ]:
        type_signals = [s for s in signals if s["type"] == div_type]
        if not type_signals:
            continue

        type_result = {
            "count": len(type_signals),
            "returns": {f"+{d}d": [] for d in [1, 3, 5]},
        }

        for signal in type_signals:
            future = get_future_returns(df, signal["date"], [1, 3, 5])
            for d in [1, 3, 5]:
                ret_data = future.get(f"+{d}d")
                if ret_data:
                    type_result["returns"][f"+{d}d"].append(ret_data["return"])

        is_top = "top" in div_type
        hit_rates = {}
        for d in [1, 3, 5]:
            returns = type_result["returns"][f"+{d}d"]
            if returns:
                if is_top:
                    hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
                else:
                    hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100

                avg_return = sum(returns) / len(returns)
                hit_rates[f"+{d}d"] = {
                    "hit_rate": round(hit_rate, 1),
                    "avg_return": round(avg_return, 2),
                    "count": len(returns),
                }

        type_result["hit_rates"] = hit_rates
        result["by_type"][div_type] = type_result

    return result


def get_future_returns(df: pd.DataFrame, signal_date: int, days: list) -> dict:
    """获取信号后的未来收益"""
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
            returns[f"+{d}d"] = {
                "price": float(future_price),
                "return": round(pct_chg, 2),
            }
        else:
            returns[f"+{d}d"] = None

    return returns


def main():
    parser = argparse.ArgumentParser(description="增强版量价背离检测")
    parser.add_argument("--symbol", "-s", help="股票代码")
    parser.add_argument("--all", "-a", action="store_true", help="测试所有股票")
    parser.add_argument("--min-signals", "-m", type=int, default=3, help="最少信号数")
    parser.add_argument(
        "--min-filters", "-f", type=int, default=2, help="最少通过过滤条件数"
    )
    parser.add_argument("--output", "-o", help="输出结果到文件")

    args = parser.parse_args()

    results = []

    if args.all:
        daily_dir = STOCK_DATA_ROOT / "daily"
        for f in daily_dir.glob("daily_*.csv"):
            ts_code = f.name.replace("daily_", "").replace(".csv", "")
            print(f"测试 {ts_code}...", end=" ", flush=True)
            result = test_enhanced_divergence(
                ts_code, args.min_signals, args.min_filters
            )
            if result:
                results.append(result)
                print(f"✓ {result['total_signals']} 个信号")
            else:
                print("✗", flush=True)
    elif args.symbol:
        result = test_enhanced_divergence(
            args.symbol, args.min_signals, args.min_filters
        )
        if result:
            results.append(result)
        else:
            print(f"❌ {args.symbol} 数据不足或信号不足")
            return
    else:
        test_stocks = ["600110.SH", "000001.SZ", "002806.SZ", "000815.SZ"]
        for ts_code in test_stocks:
            print(f"测试 {ts_code}...", end=" ", flush=True)
            result = test_enhanced_divergence(
                ts_code, args.min_signals, args.min_filters
            )
            if result:
                results.append(result)
                print(f"✓ {result['total_signals']} 个信号")
            else:
                print("✗")

    # 汇总
    print("\n" + "=" * 70)
    print("增强版背离命中率测试汇总 (min_filters=" + str(args.min_filters) + ")")
    print("=" * 70)

    all_stats = {}
    total_signals = 0

    for result in results:
        total_signals += result["total_signals"]
        for div_type, data in result["by_type"].items():
            if div_type not in all_stats:
                all_stats[div_type] = {f"+{d}d": [] for d in [1, 3, 5]}

            for d in [1, 3, 5]:
                all_stats[div_type][f"+{d}d"].extend(data["returns"][f"+{d}d"])

    type_names = {
        "volume_top_div": "量价顶背离",
        "volume_bottom_div": "量价底背离",
        "rsi_top_div": "RSI顶背离",
        "rsi_bottom_div": "RSI底背离",
    }

    print(f"\n总计: {len(results)} 只股票, {total_signals} 个信号\n")

    for div_type, periods in all_stats.items():
        type_name = type_names.get(div_type, div_type)
        is_top = "top" in div_type
        direction = "跌" if is_top else "涨"

        print(f"【{type_name}】")
        for period, returns in periods.items():
            if returns:
                if is_top:
                    hit_rate = sum(1 for r in returns if r < 0) / len(returns) * 100
                else:
                    hit_rate = sum(1 for r in returns if r > 0) / len(returns) * 100

                avg_return = sum(returns) / len(returns)
                print(
                    f"  {period}: 命中率={hit_rate:.1f}% ({direction}), "
                    f"平均收益={avg_return:.2f}%, 样本={len(returns)}"
                )
        print()

    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "method": "enhanced",
            "min_filters": args.min_filters,
            "results": results,
        }

        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)

        print(f"✅ 结果已保存到: {args.output}")


if __name__ == "__main__":
    main()
