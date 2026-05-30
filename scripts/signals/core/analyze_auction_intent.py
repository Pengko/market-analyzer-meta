#!/usr/bin/env python3
"""
基于本地 stk_auction_o / stk_auction_c / daily 数据，输出集合竞价汇总意图判断。

示例：
  python3 analyze_auction_intent.py --symbol 002806.SZ --trade-date 2026-04-10
  python3 analyze_auction_intent.py --symbol 000823 --trade-date 20260410 --format text
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from common import STOCK_DATA_ROOT, normalize_symbol, normalize_trade_date
from common import STOCK_DATA_ROOT, normalize_symbol, normalize_trade_date
from data.data_access import load_daily_row as _load_daily_row

def safe_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def load_timeseries_rows(path: Path, trade_date_compact: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            d = str(row.get("trade_date") or "").strip()
            if len(d) != 8 or not d.isdigit() or d > trade_date_compact:
                continue
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("trade_date") or ""))
    return rows


def load_daily_row(full_symbol: str, trade_date_compact: str) -> dict[str, Any] | None:
    return _load_daily_row(full_symbol, trade_date_compact)


def load_auction_row(full_symbol: str, trade_date_text: str, auction_type: str) -> dict[str, Any] | None:
    td = trade_date_text.replace("-", "")
    dir_name = "stk_auction_o" if auction_type == "open" else "stk_auction_c"
    base_dir = STOCK_DATA_ROOT / dir_name
    
    # 尝试直接路径（无年份子目录）
    path = base_dir / f"{dir_name}_{full_symbol}.csv"
    if not path.exists():
        # 尝试年份子目录结构（如 2025/stk_auction_o_000001.SZ.csv）
        year = td[:4]
        path = base_dir / year / f"{dir_name}_{full_symbol}.csv"
    
    rows = load_timeseries_rows(path, td)
    if not rows:
        return None
    for row in reversed(rows):
        if str(row.get("trade_date") or "").strip() == td:
            return row
    return None


def analyze_auction_intent(full_symbol: str, trade_date_text: str) -> dict[str, Any]:
    td = trade_date_text.replace("-", "")
    daily_row = load_daily_row(full_symbol, td)
    open_row = load_auction_row(full_symbol, trade_date_text, "open")
    close_row = load_auction_row(full_symbol, trade_date_text, "close")

    if not daily_row:
        return {"status": "manual_pending", "summary": "日线缺失，无法判定竞价意图"}
    if not open_row and not close_row:
        return {"status": "manual_pending", "summary": "stk_auction_o/stk_auction_c 缺失，无法判定竞价意图"}

    prev_close = safe_float(daily_row.get("pre_close"))
    day_amount = safe_float(daily_row.get("amount"))
    day_close = safe_float(daily_row.get("close"))
    if prev_close in (None, 0) or day_amount in (None, 0):
        return {"status": "manual_pending", "summary": "日线昨收或成交额缺失，无法判定竞价意图"}
    day_amount_yuan = day_amount * 1000.0

    open_cfg = {
        "strong_gap": 0.6,
        "mild_gap": 0.1,
        "strong_amount_ratio": 0.003,
        "weak_amount_ratio": 0.0005,
        "vwap_diff": 0.08,
        "span_pct": 0.5,
    }
    close_cfg = {
        "strong_gap": 1.0,
        "mild_gap": 0.2,
        "strong_amount_ratio": 0.02,
        "weak_amount_ratio": 0.003,
        "vwap_diff": 0.12,
        "span_pct": 0.8,
        "tail_close_diff": 0.15,
    }

    def analyze_side(row: dict[str, Any] | None, label: str) -> dict[str, Any] | None:
        if not row:
            return None
        close_px = safe_float(row.get("close"))
        open_px = safe_float(row.get("open"))
        high_px = safe_float(row.get("high"))
        low_px = safe_float(row.get("low"))
        amount = safe_float(row.get("amount")) or 0.0
        vwap = safe_float(row.get("vwap"))
        if close_px is None:
            return None

        cfg = open_cfg if label == "开盘" else close_cfg
        gap_pct = (close_px - prev_close) / prev_close * 100.0
        amount_ratio = amount / day_amount_yuan if day_amount_yuan else 0.0
        price_span_pct = ((high_px - low_px) / prev_close * 100.0) if high_px is not None and low_px is not None else 0.0
        vwap_diff_pct = ((close_px - vwap) / prev_close * 100.0) if vwap not in (None, 0) else 0.0

        score = 0
        signals: list[str] = []

        if gap_pct >= cfg["strong_gap"]:
            score += 2
            signals.append(f"{label}撮合价较昨收高 {gap_pct:.2f}%")
        elif gap_pct >= cfg["mild_gap"]:
            score += 1
            signals.append(f"{label}小幅高开/抬价 {gap_pct:.2f}%")
        elif gap_pct <= -cfg["strong_gap"]:
            score -= 2
            signals.append(f"{label}撮合价较昨收低 {gap_pct:.2f}%")
        elif gap_pct <= -cfg["mild_gap"]:
            score -= 1
            signals.append(f"{label}小幅压价 {gap_pct:.2f}%")
        else:
            signals.append(f"{label}价格接近昨收 {gap_pct:.2f}%")

        if amount_ratio >= cfg["strong_amount_ratio"]:
            score += 1
            signals.append(f"{label}成交额占全天约 {amount_ratio:.2%}")
        elif amount_ratio <= cfg["weak_amount_ratio"]:
            score -= 1
            signals.append(f"{label}成交额占全天仅 {amount_ratio:.2%}")

        if vwap not in (None, 0):
            if vwap_diff_pct >= cfg["vwap_diff"]:
                score += 1
                signals.append(f"{label}收于竞价均价上方 {vwap_diff_pct:.2f}%")
            elif vwap_diff_pct <= -cfg["vwap_diff"]:
                score -= 1
                signals.append(f"{label}收于竞价均价下方 {abs(vwap_diff_pct):.2f}%")

        if price_span_pct >= cfg["span_pct"]:
            score -= 1
            signals.append(f"{label}价差偏大 {price_span_pct:.2f}%")

        if label == "开盘":
            if open_px is not None and close_px >= open_px:
                score += 1
                signals.append("开盘竞价末端抬升")
            elif open_px is not None and close_px < open_px:
                score -= 1
                signals.append("开盘竞价末端回落")
        else:
            if day_close not in (None, 0):
                tail_vs_day_close = (close_px - day_close) / day_close * 100.0
                if tail_vs_day_close >= close_cfg["tail_close_diff"]:
                    score += 1
                    signals.append(f"尾盘竞价抬高收盘 {tail_vs_day_close:.2f}%")
                elif tail_vs_day_close <= -close_cfg["tail_close_diff"]:
                    score -= 1
                    signals.append(f"尾盘竞价压低收盘 {abs(tail_vs_day_close):.2f}%")

        if score >= 3:
            intent = "抢筹"
        elif score >= 1:
            intent = "偏积极"
        elif score <= -3:
            intent = "兑现"
        elif score <= -1:
            intent = "偏谨慎"
        else:
            intent = "平衡"

        return {
            "label": label,
            "intent": intent,
            "score": score,
            "gap_pct": round(gap_pct, 4),
            "amount_ratio": round(amount_ratio, 6),
            "price_span_pct": round(price_span_pct, 4),
            "signals": signals,
        }

    open_result = analyze_side(open_row, "开盘")
    close_result = analyze_side(close_row, "收盘")
    available_results = [item for item in (open_result, close_result) if item]
    if not available_results:
        return {"status": "manual_pending", "summary": "竞价数据不完整，无法形成有效意图判断"}

    total_score = sum(int(item.get("score") or 0) for item in available_results)
    if total_score >= 4:
        overall = "偏主动抢筹"
    elif total_score >= 1:
        overall = "偏积极试盘"
    elif total_score <= -4:
        overall = "偏兑现离场"
    elif total_score <= -1:
        overall = "偏谨慎观望"
    else:
        overall = "多空平衡"

    summary_parts = []
    if open_result:
        summary_parts.append(f"开盘{open_result['intent']}({open_result['score']:+d})")
    if close_result:
        summary_parts.append(f"收盘{close_result['intent']}({close_result['score']:+d})")
    summary_parts.append(f"综合判断 {overall}")

    return {
        "status": "available",
        "summary": "；".join(summary_parts),
        "overall_intent": overall,
        "score": total_score,
        "open": open_result,
        "close": close_result,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分析集合竞价汇总意图")
    parser.add_argument("--symbol", required=True, help="如 002806 或 002806.SZ")
    parser.add_argument("--trade-date", required=True, help="格式 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    return parser.parse_args()


def render_text(result: dict[str, Any], full_symbol: str, trade_date_text: str) -> str:
    lines = [
        f"symbol: {full_symbol}",
        f"trade_date: {trade_date_text}",
        f"status: {result.get('status')}",
        f"summary: {result.get('summary')}",
        f"overall_intent: {result.get('overall_intent')}",
        f"score: {result.get('score')}",
    ]
    for key in ("open", "close"):
        item = result.get(key) or {}
        if not item:
            continue
        lines.append(f"{key}_intent: {item.get('intent')} ({item.get('score')})")
        for signal in item.get("signals") or []:
            lines.append(f"- {signal}")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    _, trade_date_text = normalize_trade_date(args.trade_date)
    _, full_symbol = normalize_symbol(args.symbol)
    result = analyze_auction_intent(full_symbol, trade_date_text)
    if args.format == "text":
        print(render_text(result, full_symbol, trade_date_text))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
