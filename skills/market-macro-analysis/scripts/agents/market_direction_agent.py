#!/usr/bin/env python3
"""
Agent-市场方向：判断市场多空状态、是否回踩到位、支撑/压力位。

数据源：本地 index_daily parquet + Tushare API 补全
分析：MA/RSI/MACD/成交量 → 多空信号
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ── 路径设置 ──
SCRIPT_DIR = Path(__file__).resolve().parent
META_ROOT = SCRIPT_DIR.parents[2]  # market-analyzer-meta/
SDA_SCRIPTS = META_ROOT / "skills" / "stock-deep-analysis" / "scripts"
sys.path.insert(0, str(SDA_SCRIPTS))

import pyarrow.parquet as pq


# ═══════════════════════════════════════════════════════
# 数据获取
# ═══════════════════════════════════════════════════════

INDEX_DATA_ROOT = Path.home() / "quant-data" / "tushare" / "指数数据" / "index_daily"

INDEX_MAP = {
    "上证指数": "000001.SH",
    "深证成指": "399001.SZ",
    "创业板指": "399006.SZ",
}


def load_index_history(ts_code: str, days: int = 60) -> list[dict]:
    """加载指数历史日线。"""
    path = INDEX_DATA_ROOT / f"{ts_code}.parquet"
    if not path.exists():
        return []
    try:
        df = pq.read_table(path).to_pandas()
        df = df.sort_values("trade_date")
        rows = df.tail(days).to_dict("records")
        return [
            {
                "trade_date": str(r.get("trade_date", "")),
                "open": float(r.get("open", 0) or 0),
                "close": float(r.get("close", 0) or 0),
                "high": float(r.get("high", 0) or 0),
                "low": float(r.get("low", 0) or 0),
                "vol": float(r.get("vol", 0) or 0),
                "amount": float(r.get("amount", 0) or 0),
                "pct_chg": float(r.get("pct_chg", 0) or 0),
            }
            for r in rows
        ]
    except Exception:
        return []


# ═══════════════════════════════════════════════════════
# 技术指标计算
# ═══════════════════════════════════════════════════════

def calc_ma(closes: list[float], period: int) -> float | None:
    """计算移动平均线。"""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """计算 RSI。"""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, float] | None:
    """计算 MACD。"""
    if len(closes) < slow + signal:
        return None

    def ema(data: list[float], period: int) -> list[float]:
        result = [data[0]]
        multiplier = 2 / (period + 1)
        for i in range(1, len(data)):
            result.append((data[i] - result[-1]) * multiplier + result[-1])
        return result

    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = ema(dif, signal)
    macd_hist = [(d - e) * 2 for d, e in zip(dif, dea)]

    return {
        "dif": round(dif[-1], 4),
        "dea": round(dea[-1], 4),
        "macd": round(macd_hist[-1], 4),
    }


def calc_volume_signal(volumes: list[float], period: int = 5) -> str:
    """判断成交量信号（放量/缩量）。"""
    if len(volumes) < period + 1:
        return "数据不足"
    avg_vol = sum(volumes[-period - 1:-1]) / period
    current_vol = volumes[-1]
    if avg_vol == 0:
        return "数据异常"
    ratio = current_vol / avg_vol
    if ratio > 1.5:
        return "放量"
    elif ratio > 1.1:
        return "温和放量"
    elif ratio > 0.9:
        return "平量"
    elif ratio > 0.7:
        return "缩量"
    else:
        return "明显缩量"


# ═══════════════════════════════════════════════════════
# 单指数分析
# ═══════════════════════════════════════════════════════

def analyze_single_index(name: str, ts_code: str, trade_date_compact: str) -> dict[str, Any]:
    """分析单个指数。"""
    history = load_index_history(ts_code, days=60)
    if not history:
        return {"status": "missing", "name": name, "reason": "本地数据缺失"}

    # 最新一行
    latest = None
    for r in reversed(history):
        if r["trade_date"] <= trade_date_compact:
            latest = r
            break
    if not latest:
        latest = history[-1]

    closes = [r["close"] for r in history if r["close"] > 0]
    volumes = [r["vol"] for r in history if r["vol"] > 0]
    current_close = latest["close"]

    # 均线
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)

    # 偏离度
    def deviation(current, ma):
        if ma is None or ma == 0:
            return None
        return round((current - ma) / ma * 100, 2)

    # RSI
    rsi = calc_rsi(closes, 14)
    rsi_signal = "中性"
    if rsi is not None:
        if rsi > 70:
            rsi_signal = "超买"
        elif rsi > 60:
            rsi_signal = "偏强"
        elif rsi > 40:
            rsi_signal = "中性"
        elif rsi > 30:
            rsi_signal = "偏弱"
        else:
            rsi_signal = "超卖"

    # MACD
    macd = calc_macd(closes)
    macd_signal = "中性"
    if macd:
        if macd["dif"] > macd["dea"]:
            macd_signal = "多头"
        elif macd["dif"] < macd["dea"]:
            macd_signal = "空头"

    # 成交量
    vol_signal = calc_volume_signal(volumes)

    # MA 关系
    ma_relation = "数据不足"
    if ma5 and ma10 and ma20:
        if current_close > ma5 > ma10 > ma20:
            ma_relation = "多头排列（价>MA5>MA10>MA20）"
        elif current_close < ma5 < ma10 < ma20:
            ma_relation = "空头排列（价<MA5<MA10<MA20）"
        elif current_close > ma20:
            ma_relation = f"在MA20上方，偏离MA20 {deviation(current_close, ma20):+.1f}%"
        else:
            ma_relation = f"在MA20下方，偏离MA20 {deviation(current_close, ma20):+.1f}%"

    # 支撑/压力位
    support_levels = []
    resistance_levels = []
    if ma20 and current_close > ma20:
        support_levels.append(round(ma20, 2))
    if ma10 and current_close > ma10:
        support_levels.append(round(ma10, 2))
    if ma5 and current_close > ma5:
        support_levels.append(round(ma5, 2))
    if ma20 and current_close < ma20:
        resistance_levels.append(round(ma20, 2))
    if ma10 and current_close < ma10:
        resistance_levels.append(round(ma10, 2))
    if ma5 and current_close < ma5:
        resistance_levels.append(round(ma5, 2))
    if ma60:
        if current_close > ma60:
            support_levels.append(round(ma60, 2))
        else:
            resistance_levels.append(round(ma60, 2))

    # 多空判断
    bullish_signals = 0
    bearish_signals = 0
    if rsi_signal in ("超买", "偏强"):
        bullish_signals += 1
    elif rsi_signal in ("超卖", "偏弱"):
        bearish_signals += 1
    if macd_signal == "多头":
        bullish_signals += 1
    elif macd_signal == "空头":
        bearish_signals += 1
    if ma5 and ma10 and ma20 and current_close > ma5 > ma10 > ma20:
        bullish_signals += 2
    elif ma5 and ma10 and ma20 and current_close < ma5 < ma10 < ma20:
        bearish_signals += 2
    if vol_signal in ("放量", "温和放量") and latest.get("pct_chg", 0) > 0:
        bullish_signals += 1
    elif vol_signal in ("放量", "温和放量") and latest.get("pct_chg", 0) < 0:
        bearish_signals += 1

    direction = "中性"
    if bullish_signals > bearish_signals + 1:
        direction = "偏多"
    elif bearish_signals > bullish_signals + 1:
        direction = "偏空"
    elif bullish_signals > bearish_signals:
        direction = "中性偏多"
    elif bearish_signals > bullish_signals:
        direction = "中性偏弱"

    # 回踩状态
    pullback_status = "未回踩"
    if ma20 and current_close < ma20 * 1.02 and current_close > ma20 * 0.98:
        pullback_status = "接近MA20支撑"
    elif ma60 and current_close < ma60 * 1.02 and current_close > ma60 * 0.98:
        pullback_status = "接近MA60支撑"
    elif ma20 and current_close < ma20:
        pullback_status = "已跌破MA20"
    elif ma60 and current_close < ma60:
        pullback_status = "已跌破MA60"

    return {
        "status": "available",
        "name": name,
        "ts_code": ts_code,
        "trade_date": latest["trade_date"],
        "close": current_close,
        "pct_chg": latest.get("pct_chg", 0),
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "deviation_ma5": deviation(current_close, ma5),
        "deviation_ma20": deviation(current_close, ma20),
        "deviation_ma60": deviation(current_close, ma60),
        "ma_relation": ma_relation,
        "rsi": round(rsi, 2) if rsi is not None else None,
        "rsi_signal": rsi_signal,
        "macd": macd,
        "macd_signal": macd_signal,
        "vol_signal": vol_signal,
        "support_levels": sorted(set(support_levels), reverse=True)[:3],
        "resistance_levels": sorted(set(resistance_levels))[:3],
        "direction": direction,
        "pullback_status": pullback_status,
        "bullish_signals": bullish_signals,
        "bearish_signals": bearish_signals,
    }


# ═══════════════════════════════════════════════════════
# 汇总分析
# ═══════════════════════════════════════════════════════

def analyze_market_direction(trade_date_compact: str) -> dict[str, Any]:
    """市场方向分析主函数。"""
    indices = {}
    for name, ts_code in INDEX_MAP.items():
        indices[name] = analyze_single_index(name, ts_code, trade_date_compact)

    # 汇总判断
    directions = [idx.get("direction", "中性") for idx in indices.values() if idx.get("status") == "available"]
    bullish_count = sum(1 for d in directions if "偏多" in d)
    bearish_count = sum(1 for d in directions if "偏弱" in d or "空" in d)

    if bullish_count >= 2:
        overall_direction = "偏多"
    elif bearish_count >= 2:
        overall_direction = "偏空"
    elif bullish_count > bearish_count:
        overall_direction = "中性偏多"
    elif bearish_count > bullish_count:
        overall_direction = "中性偏弱"
    else:
        overall_direction = "中性"

    # 回踩状态汇总
    pullback_states = [idx.get("pullback_status", "") for idx in indices.values() if idx.get("status") == "available"]
    if any("跌破" in s for s in pullback_states):
        overall_pullback = "已跌破关键均线"
    elif any("接近" in s for s in pullback_states):
        overall_pullback = "接近支撑位"
    else:
        overall_pullback = "未回踩"

    # 关键价位（取三大指数的支撑/压力中位数）
    all_supports = []
    all_resistances = []
    for idx in indices.values():
        if idx.get("status") == "available":
            all_supports.extend(idx.get("support_levels", []))
            all_resistances.extend(idx.get("resistance_levels", []))

    return {
        "status": "available",
        "trade_date": trade_date_compact,
        "indices": indices,
        "overall": {
            "direction": overall_direction,
            "pullback_status": overall_pullback,
            "key_levels": {
                "support": sorted(set(all_supports), reverse=True)[:3],
                "resistance": sorted(set(all_resistances))[:3],
            },
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
        },
    }


# ═══════════════════════════════════════════════════════
# 测试
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    import json
    date = sys.argv[1] if len(sys.argv) > 1 else "20260529"
    result = analyze_market_direction(date)
    print(json.dumps(result, ensure_ascii=False, indent=2))
