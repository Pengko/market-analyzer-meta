#!/usr/bin/env python3
"""
基于分钟线评估上午结构，并给出“上午推下午”的强度标签。

示例：
  python3 score_intraday_strength.py --symbol 002639 --trade-date 2026-04-08
  python3 score_intraday_strength.py --path ${TMPDIR:-/tmp}/minute_002639.csv --format json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from common import STOCK_DATA_ROOT, normalize_trade_date


DEFAULT_ROOT = STOCK_DATA_ROOT / "分钟数据"


def candidate_paths(symbol: str, trade_date_text: str) -> list[Path]:
    y, m, d = trade_date_text.split("-")
    # 新结构A：分钟数据/YYYY/MM/DD/{symbol}_{granularity}.csv
    new_base = DEFAULT_ROOT / y / m / d
    new_paths = [
        new_base / f"{symbol}_1m.csv",
        new_base / f"{symbol}_5m.csv",
        new_base / f"{symbol}_15m.csv",
        new_base / f"{symbol}_30m.csv",
        new_base / f"{symbol}_60m.csv",
    ]
    # 新结构B：分钟数据/YYYY/MM/DD/{symbol}/1m.csv 或 1min.csv
    partitioned_paths = [
        new_base / symbol / "1m.csv",
        new_base / symbol / "1min.csv",
        new_base / symbol / "5m.csv",
        new_base / symbol / "15m.csv",
        new_base / symbol / "30m.csv",
        new_base / symbol / "60m.csv",
    ]
    # 旧结构 fallback
    old_base = DEFAULT_ROOT / symbol / trade_date_text
    old_paths = [
        old_base / "minute_kline.csv",
        old_base / "minute_kline_5m.csv",
        old_base / "minute_kline_15m.csv",
        old_base / "minute_kline_30m.csv",
        old_base / "minute_kline_60m.csv",
    ]
    return new_paths + partitioned_paths + old_paths


@dataclass
class MinuteRow:
    dt: datetime
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    avg: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评估上午结构强度")
    parser.add_argument("--symbol", help="6位股票代码，如 002639")
    parser.add_argument("--trade-date", help="交易日期，格式 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--path", help="分钟线 CSV 路径，优先级高于 symbol + trade-date")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def resolve_path(args: argparse.Namespace) -> Path:
    if args.path:
        return Path(args.path)
    if not args.symbol or not args.trade_date:
        raise SystemExit("either --path or both --symbol and --trade-date are required")
    _, trade_date_text = normalize_trade_date(args.trade_date)
    for path in candidate_paths(args.symbol, trade_date_text):
        if path.exists():
            return path
    return candidate_paths(args.symbol, trade_date_text)[0]


def load_rows(path: Path) -> list[MinuteRow]:
    rows: list[MinuteRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            rows.append(
                MinuteRow(
                    dt=datetime.strptime(raw["datetime"], "%Y-%m-%d %H:%M"),
                    open=float(raw["open"]),
                    close=float(raw["close"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    volume=float(raw["volume"]),
                    amount=float(raw["amount"]),
                    avg=float(raw["avg"]),
                )
            )
    if not rows:
        raise SystemExit(f"no minute rows found in {path}")
    return rows


def select(rows: list[MinuteRow], start: str, end: str) -> list[MinuteRow]:
    start_t = datetime.strptime(start, "%H:%M").time()
    end_t = datetime.strptime(end, "%H:%M").time()
    return [row for row in rows if start_t <= row.dt.time() <= end_t]


def pct_change(a: float, b: float) -> float:
    if a == 0:
        return 0.0
    return (b - a) / a * 100


def amount_yi(rows: list[MinuteRow]) -> float:
    return round(sum(row.amount for row in rows) / 100000000, 2)


def classify(score: int) -> tuple[str, str]:
    if score >= 7:
        return "强修复", "午后若市场和板块不拖累，更容易升级为趋势性拉升"
    if score >= 2:
        return "修复", "午后优先看延续修复，其次防冲高回落"
    if score >= -1:
        return "偏弱修复", "午后有修复尝试，但持续性依赖放量确认"
    return "弱结构", "午后更像反抽或震荡，不能默认会走强"


def analyze(rows: list[MinuteRow], path: Path) -> dict:
    morning = select(rows, "09:30", "11:30")
    open_burst = select(rows, "09:30", "09:35")
    first_push = select(rows, "09:48", "09:56")
    pre_noon = select(rows, "11:25", "11:30")
    pm_first = select(rows, "13:01", "13:30")
    pm_mid = select(rows, "13:30", "14:00")
    pm_late = select(rows, "14:00", "14:30")
    pm_all = select(rows, "13:01", "15:00")
    if not morning or not open_burst or not pre_noon:
        raise SystemExit("missing required morning windows in minute data")

    prev_close = None
    if len(rows) >= 2:
        prev_close = rows[0].close - rows[0].open

    morning_open = morning[0].open
    morning_close = morning[-1].close
    morning_high = max(row.high for row in morning)
    morning_low = min(row.low for row in morning)
    morning_avg = morning[-1].avg

    score = 0
    signals: list[str] = []

    open_repair_pct = pct_change(open_burst[0].open, open_burst[-1].close)
    if open_repair_pct >= 0:
        score += 1
        signals.append(f"开盘前 6 分钟未失控，收口修复 {open_repair_pct:.2f}%")
    else:
        score -= 1
        signals.append(f"开盘前 6 分钟承压，收口回落 {open_repair_pct:.2f}%")

    burst_drawdown_pct = pct_change(open_burst[0].open, min(row.low for row in open_burst))
    if burst_drawdown_pct <= -2.5:
        score -= 1
        signals.append(f"开盘初段下探较深 {burst_drawdown_pct:.2f}%")

    if first_push:
        first_push_high = max(row.high for row in first_push)
        first_push_last = first_push[-1].close
        retreat_from_push = pct_change(first_push_high, first_push_last)
        if first_push_high > morning_open and retreat_from_push >= -0.8:
            score += 2
            signals.append(f"09:48-09:56 首次强冲后承接稳定，回撤 {retreat_from_push:.2f}%")
        elif first_push_high > morning_open:
            score += 1
            signals.append(f"09:48-09:56 出现试盘上冲，但承接一般，回撤 {retreat_from_push:.2f}%")
        else:
            score -= 1
            signals.append("09:48-09:56 没有形成有效上冲")

    morning_close_pct = pct_change(morning_open, morning_close)
    if morning_close >= morning_avg:
        score += 1
        signals.append(f"上午收盘站上均价线，收口相对稳，收盘偏离均价 {pct_change(morning_avg, morning_close):.2f}%")
    else:
        score -= 1
        signals.append(f"上午收盘仍在均价线下，修复质量一般，收盘偏离均价 {pct_change(morning_avg, morning_close):.2f}%")

    rebound_from_low = pct_change(morning_low, morning_close)
    if rebound_from_low >= 2.5:
        score += 1
        signals.append(f"上午最低点到收盘修复明显，反弹 {rebound_from_low:.2f}%")

    pre_noon_pct = pct_change(pre_noon[0].open, pre_noon[-1].close)
    pre_noon_amount = amount_yi(pre_noon)
    if pre_noon_pct > 0 and pre_noon_amount >= 0.15:
        score += 1
        signals.append(f"11:25-11:30 午前收口偏强，涨幅 {pre_noon_pct:.2f}%，成交额 {pre_noon_amount:.2f} 亿元")
    elif pre_noon_pct > 0:
        signals.append(f"11:25-11:30 有修复但量能一般，涨幅 {pre_noon_pct:.2f}%，成交额 {pre_noon_amount:.2f} 亿元")
    else:
        score -= 1
        signals.append(f"11:25-11:30 午前未强化，涨幅 {pre_noon_pct:.2f}%")

    if pm_all:
        pm_close = pm_all[-1].close
        pm_high = max(row.high for row in pm_all)
        pm_open = pm_all[0].open
        day_high = max(row.high for row in rows)
        close_to_day_high_pct = pct_change(day_high, pm_close)

        broke_morning_high = pm_high > morning_high
        if broke_morning_high:
            score += 1
            signals.append(f"午后有效突破上午高点，突破后最高到 {pm_high:.2f}")

        if pm_first:
            pm_first_close = pm_first[-1].close
            pm_first_pct = pct_change(pm_first[0].open, pm_first_close)
            first_break_hold = min(row.low for row in pm_first) >= morning_high * 0.992
            if broke_morning_high and first_break_hold:
                score += 1
                signals.append("13:01-13:30 突破上午高点后没有明显跌回，午后确认有效")
            elif broke_morning_high:
                signals.append("13:01-13:30 虽突破上午高点，但回踩仍偏大")
            if pm_first_close < morning_close and pm_first_pct <= -1.0:
                score -= 2
                signals.append(f"13:01-13:30 持续弱于上午收盘且走弱，区间变化 {pm_first_pct:.2f}%")
            elif pm_first_close < morning_close:
                score -= 1
                signals.append("13:01-13:30 未能站回上午收盘，午后偏弱")

        pm_mid_close = pm_mid[-1].close if pm_mid else pm_close
        if broke_morning_high and pm_mid and pm_mid_close >= morning_high:
            score += 1
            signals.append("13:30-14:00 仍能站在上午高点之上，趋势延续性较好")

        if close_to_day_high_pct >= -1.0:
            score += 2
            signals.append(f"收盘接近全天高点，收盘距日高 {close_to_day_high_pct:.2f}%")
        elif close_to_day_high_pct >= -2.0:
            score += 1
            signals.append(f"收盘仍处高位区，收盘距日高 {close_to_day_high_pct:.2f}%")
        else:
            signals.append(f"收盘离全天高点较远，收盘距日高 {close_to_day_high_pct:.2f}%")

        if pm_late:
            pm_late_open = pm_late[0].open
            pm_late_close = pm_late[-1].close
            late_pct = pct_change(pm_late_open, pm_late_close)
            if late_pct >= -0.3:
                score += 1
                signals.append(f"14:00-14:30 未出现明显回落，区间变化 {late_pct:.2f}%")
            else:
                score -= 1
                signals.append(f"14:00-14:30 已出现明显回落，区间变化 {late_pct:.2f}%")

        pm_total_pct = pct_change(pm_open, pm_close)
        pm_all_low = min(row.low for row in pm_all)
        if (
            pm_first
            and pm_first[-1].close >= morning_close
            and pm_total_pct > -0.6
            and pm_all_low >= morning_close * 0.99
        ):
            score += 1
            signals.append("午后没有有效跌破上午收盘，整体更像弱震荡而非持续转弱")

        if pm_total_pct >= 3:
            score += 1
            signals.append(f"下午整体走强明显，午后涨幅 {pm_total_pct:.2f}%")
        elif pm_total_pct <= -1.0:
            score -= 2
            signals.append(f"下午整体走弱明显，午后涨幅 {pm_total_pct:.2f}%")
        elif pm_total_pct < 0:
            score -= 1
            signals.append(f"下午整体偏弱，午后涨幅 {pm_total_pct:.2f}%")

        if pm_close < morning_close and close_to_day_high_pct < -3.0:
            score -= 2
            signals.append("全天收盘弱于上午收盘，且明显远离日高，不应按强修复处理")

    if pm_all:
        pm_close = pm_all[-1].close
        close_to_day_high_pct = pct_change(max(row.high for row in rows), pm_close)
        if pm_close < morning_close and close_to_day_high_pct < -3.0 and score >= 2:
            score = 1
            signals.append("触发弱收盘约束，标签上限压到偏弱修复")

    label, afternoon_view = classify(score)
    freshness = {
        "minute_file": {
            "path": str(path),
            "status": "available",
            "rows": len(rows),
            "first_dt": rows[0].dt.isoformat(timespec="minutes"),
            "last_dt": rows[-1].dt.isoformat(timespec="minutes"),
        }
    }

    return {
        "label": label,
        "score": score,
        "afternoon_view": afternoon_view,
        "signals": signals,
        "snapshot": {
            "morning_open": morning_open,
            "morning_close": morning_close,
            "morning_high": morning_high,
            "morning_low": morning_low,
            "morning_amount_yi": amount_yi(morning),
            "morning_close_pct": round(morning_close_pct, 2),
            "first_push_window": "09:48-09:56" if first_push else None,
            "pre_noon_amount_yi": pre_noon_amount,
            "pm_first_amount_yi": amount_yi(pm_first) if pm_first else None,
            "pm_close": pm_all[-1].close if pm_all else None,
            "pm_high": max(row.high for row in pm_all) if pm_all else None,
            "broke_morning_high": (max(row.high for row in pm_all) > morning_high) if pm_all else None,
        },
        "freshness": freshness,
    }


def print_text(result: dict) -> None:
    print(f"label: {result['label']}")
    print(f"score: {result['score']}")
    print(f"afternoon_view: {result['afternoon_view']}")
    print("signals:")
    for item in result["signals"]:
        print(f"- {item}")
    print("snapshot:")
    for key, value in result["snapshot"].items():
        print(f"- {key}: {value}")
    print("freshness:")
    for key, value in result["freshness"]["minute_file"].items():
        print(f"- {key}: {value}")


def main() -> int:
    args = parse_args()
    path = resolve_path(args)
    rows = load_rows(path)
    result = analyze(rows, path)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
