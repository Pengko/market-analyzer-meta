"""
分钟级联动分析引擎

输入: 个股/大盘/板块 三个分钟级时间序列
输出: 结构化联动指标 + LLM 定性上下文
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any


def align_series(
    stock_rows: list[dict],
    bench_rows: list[dict],
) -> tuple[list[dict], list[dict]]:
    """以基准序列时间轴对齐两个序列"""
    bench_map = {}
    for r in bench_rows:
        dt_str = r["dt"] if isinstance(r["dt"], str) else r["dt"].strftime("%Y-%m-%d %H:%M")
        bench_map[dt_str] = r
    aligned_stock = []
    aligned_bench = []
    for r in stock_rows:
        dt_str = r["dt"] if isinstance(r["dt"], str) else r["dt"].strftime("%Y-%m-%d %H:%M")
        if dt_str in bench_map:
            aligned_stock.append(r)
            aligned_bench.append(bench_map[dt_str])
    return aligned_stock, aligned_bench


def compute_relative_strength(
    stock_rows: list[dict], bench_rows: list[dict]
) -> dict:
    if not stock_rows or not bench_rows:
        return {"final_rs": 0, "trend": "数据不足", "key_points": {}}

    s_aligned, b_aligned = align_series(stock_rows, bench_rows)
    if len(s_aligned) < 5:
        return {"final_rs": 0, "trend": "数据不足", "key_points": {}}

    s_open = s_aligned[0].get("open", s_aligned[0].get("close", 0)) or 0.001
    b_open = b_aligned[0].get("open", b_aligned[0].get("close", 0)) or 0.001
    if s_open == 0 or b_open == 0:
        return {"final_rs": 0, "trend": "数据不足", "key_points": {}}

    snapshots = ["10:00", "10:30", "11:30", "14:00", "15:00"]
    key_points: dict[str, float] = {}
    rs_values: list[dict] = []

    for sr, br in zip(s_aligned, b_aligned):
        s_ret = (sr["close"] - s_open) / s_open * 100
        b_ret = (br["close"] - b_open) / b_open * 100
        raw_dt = sr["dt"]
        if isinstance(raw_dt, str) and " " in raw_dt:
            r_dt = raw_dt.split()[1][:5]
        elif isinstance(raw_dt, str):
            r_dt = raw_dt
        else:
            r_dt = raw_dt.strftime("%H:%M")
        rs_values.append({"dt": r_dt, "rs": round(s_ret - b_ret, 2)})

    for snap in snapshots:
        snap_h, snap_m = snap.split(":")
        for item in rs_values:
            parts = item["dt"].split(":")
            ih, im = int(parts[0]), int(parts[1])
            if ih == int(snap_h) and abs(im - int(snap_m)) <= 1:
                key_points[snap] = item["rs"]
                break

    final_rs = rs_values[-1]["rs"] if rs_values else 0
    mid = len(rs_values) // 2
    first_half = [x["rs"] for x in rs_values[:mid]] if rs_values else []
    second_half = [x["rs"] for x in rs_values[mid:]] if rs_values else []
    first_avg = statistics.mean(first_half) if first_half else 0
    second_avg = statistics.mean(second_half) if second_half else 0

    if final_rs > 2:
        trend = "持续走强" if first_avg > 0 else "先弱后强"
    elif final_rs < -2:
        trend = "持续走弱" if first_avg < 0 else "先强后弱"
    else:
        trend = "窄幅波动"

    return {"final_rs": round(final_rs, 2), "trend": trend, "key_points": key_points}


def detect_time_conduction(
    market_rows: list[dict],
    stock_rows: list[dict],
    threshold_pct: float = 0.5,
) -> dict:
    """检测大盘极值点到个股的传导"""
    if not market_rows or not stock_rows:
        return {"follow_ratio": 0, "avg_delay_min": 0, "label": "数据不足"}

    m_aligned, s_aligned = align_series(market_rows, stock_rows)
    if len(m_aligned) < 10:
        return {"follow_ratio": 0, "avg_delay_min": 0, "label": "数据不足"}

    m_prices = [r["close"] for r in m_aligned]
    s_prices = [r["close"] for r in s_aligned]

    extremes = []
    for i in range(5, len(m_aligned) - 5):
        window = m_prices[i - 5:i + 5]
        window_high = max(window)
        window_low = min(window)
        pct_chg = (m_prices[i] - m_prices[i - 5]) / m_prices[i - 5] * 100
        if m_prices[i] == window_high and pct_chg > threshold_pct:
            extremes.append({"idx": i, "dir": "up", "pct": pct_chg})
        elif m_prices[i] == window_low and abs(pct_chg) > threshold_pct:
            extremes.append({"idx": i, "dir": "down", "pct": abs(pct_chg)})

    follow_count = 0
    delays: list[int] = []
    for e in extremes:
        for j in range(e["idx"] + 1, min(e["idx"] + 10, len(s_aligned))):
            s_change = (s_prices[j] - s_prices[e["idx"]]) / s_prices[e["idx"]] * 100
            if (e["dir"] == "up" and s_change > 0) or (e["dir"] == "down" and s_change < 0):
                follow_count += 1
                delays.append(j - e["idx"])
                break

    follow_ratio = follow_count / len(extremes) if extremes else 0
    avg_delay = round(statistics.mean(delays)) if delays else 0
    label = "及时跟随" if follow_ratio > 0.7 else ("部分跟随" if follow_ratio > 0.3 else "不跟随")
    return {"follow_ratio": round(follow_ratio, 2), "avg_delay_min": avg_delay, "label": label}


def sliding_correlation(
    series_a: list[float], series_b: list[float], window: int = 15
) -> dict:
    n = min(len(series_a), len(series_b))
    if n < window:
        return {"avg_r": 0, "breakdown_ratio": 0, "label": "样本不足"}

    def _pearson(x: list[float], y: list[float]) -> float:
        m = len(x)
        if m < 3:
            return 0
        sx, sy = sum(x), sum(y)
        sxx = sum(v * v for v in x)
        syy = sum(v * v for v in y)
        sxy = sum(x[i] * y[i] for i in range(m))
        num = m * sxy - sx * sy
        den = ((m * sxx - sx * sx) * (m * syy - sy * sy)) ** 0.5
        return num / den if den != 0 else 0

    rs: list[float] = []
    breakdown = 0
    for i in range(n - window + 1):
        r = _pearson(series_a[i:i + window], series_b[i:i + window])
        rs.append(r)
        if r < 0.3:
            breakdown += 1

    avg_r = statistics.mean(rs) if rs else 0
    breakdown_ratio = breakdown / len(rs) if rs else 0
    label = "紧密" if avg_r > 0.6 else ("中等" if avg_r > 0.3 else "松散")
    return {"avg_r": round(avg_r, 3), "breakdown_ratio": round(breakdown_ratio, 3), "label": label}


def detect_divergence(
    stock_rows: list[dict],
    bench_rows: list[dict],
    threshold: float = 2.0,
) -> dict:
    """检测个股与大盘/板块的方向背离"""
    s_aligned, b_aligned = align_series(stock_rows, bench_rows)
    if len(s_aligned) < 5:
        return {"count": 0, "max_pct": 0, "periods": []}

    s_open = s_aligned[0].get("open", s_aligned[0].get("close", 0)) or 0.001
    b_open = b_aligned[0].get("open", b_aligned[0].get("close", 0)) or 0.001
    if s_open == 0 or b_open == 0:
        return {"count": 0, "max_pct": 0, "periods": []}

    periods: list[dict] = []
    in_divergence = False
    div_start: str | None = None
    max_div = 0
    count = 0

    for i in range(len(s_aligned)):
        s_ret = (s_aligned[i]["close"] - s_open) / s_open * 100
        b_ret = (b_aligned[i]["close"] - b_open) / b_open * 100
        diff = abs(s_ret - b_ret)
        if s_ret * b_ret < 0 and diff > threshold:
            if not in_divergence:
                in_divergence = True
                raw = s_aligned[i].get("dt", "")
                div_start = raw if isinstance(raw, str) else raw.strftime("%H:%M")
            count += 1
            max_div = max(max_div, diff)
        else:
            if in_divergence:
                raw = s_aligned[i - 1].get("dt", "")
                end_ts = raw if isinstance(raw, str) else raw.strftime("%H:%M")
                periods.append({
                    "start": div_start,
                    "end": end_ts,
                    "direction": "个股逆势" if s_ret > 0 else "个股逆跌",
                })
                in_divergence = False

    return {"count": count, "max_pct": round(max_div, 2), "periods": periods[:5]}


def score_linkage(
    stock_rows: list[dict],
    market_rows: list[dict],
    sector_rows: list[dict] | None = None,
) -> dict:
    """综合联动分析入口"""
    result: dict[str, Any] = {}

    rs_market = compute_relative_strength(stock_rows, market_rows)
    result["vs_market"] = rs_market

    conduction = detect_time_conduction(market_rows, stock_rows)
    result["time_conduction"] = conduction

    s_prices = [r["close"] for r in stock_rows]
    m_prices = [r["close"] for r in market_rows]
    corr_market = sliding_correlation(s_prices, m_prices)
    result["correlation_market"] = corr_market

    div = detect_divergence(stock_rows, market_rows)
    result["divergence"] = div

    if sector_rows:
        rs_sector = compute_relative_strength(stock_rows, sector_rows)
        result["vs_sector"] = rs_sector
        sec_prices = [r["close"] for r in sector_rows]
        corr_sector = sliding_correlation(s_prices, sec_prices)
        result["correlation_sector"] = corr_sector
        div_sec = detect_divergence(stock_rows, sector_rows)
        result["divergence_sector"] = div_sec

    return result
