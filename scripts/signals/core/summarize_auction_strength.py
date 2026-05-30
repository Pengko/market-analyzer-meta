#!/usr/bin/env python3
"""
读取本地 `stk_auction_o / stk_auction_c` 数据，输出竞价强弱结论摘要。

示例：
  python3 summarize_auction_strength.py --type open --symbol 002639 --trade-date 2026-04-08
  python3 summarize_auction_strength.py --type close --symbol 002639 --trade-date 2026-04-08
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from common import STOCK_DATA_ROOT, normalize_symbol, normalize_trade_date

TYPE_DIR = {"open": "stk_auction_o", "close": "stk_auction_c"}
TYPE_LABEL = {"open": "开盘集合竞价", "close": "尾盘集合竞价"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成竞价强弱结论摘要")
    parser.add_argument("--type", choices=("open", "close"), required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--trade-date", required=True)
    return parser.parse_args()


def safe_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def resolve_source_path(auction_type: str, full_symbol: str, trade_date_compact: str) -> Path:
    base_dir = STOCK_DATA_ROOT / TYPE_DIR[auction_type]
    filename = f"{TYPE_DIR[auction_type]}_{full_symbol}.csv"
    direct = base_dir / filename
    if direct.exists():
        return direct
    return base_dir / trade_date_compact[:4] / filename


def load_auction_row(path: Path, trade_date_compact: str) -> dict | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        if str(row.get("trade_date") or "").strip() == trade_date_compact:
            return row
    return None


def classify(row: dict, auction_type: str) -> dict:
    open_price = safe_float(row.get("open"))
    close_price = safe_float(row.get("close"))
    high_price = safe_float(row.get("high"))
    low_price = safe_float(row.get("low"))
    avg_price = safe_float(row.get("vwap"))
    amount = safe_float(row.get("amount")) or 0.0
    volume = safe_float(row.get("vol")) or 0.0
    price_span = (high_price - low_price) if high_price is not None and low_price is not None else 0.0

    signals = []
    score = 0

    if open_price is not None and close_price is not None:
        edge_pct = (close_price - open_price) / open_price * 100 if open_price else 0.0
        if edge_pct >= 0.5:
            signals.append(f"竞价末端抬升 {edge_pct:.2f}%")
            score += 1
        elif edge_pct <= -0.5:
            signals.append(f"竞价末端回落 {abs(edge_pct):.2f}%")
            score -= 1
        else:
            signals.append(f"竞价首尾变化温和 {edge_pct:.2f}%")

    if avg_price and close_price:
        vwap_diff_pct = (close_price - avg_price) / avg_price * 100 if avg_price else 0.0
        if vwap_diff_pct > 0.3:
            signals.append(f"现价高于均价 {vwap_diff_pct:.2f}%，偏抢筹")
            score += 1
        elif vwap_diff_pct < -0.3:
            signals.append(f"现价低于均价 {abs(vwap_diff_pct):.2f}%，偏压制")
            score -= 1
        else:
            signals.append("现价与均价接近，竞价相对平稳")

    if amount >= 100_000_000:
        signals.append(f"竞价成交额较大，约 {amount / 100000000:.2f} 亿元")
        score += 1
    elif amount >= 30_000_000:
        signals.append(f"竞价成交额中等，约 {amount / 100000000:.2f} 亿元")
    else:
        signals.append(f"竞价成交额偏小，约 {amount / 100000000:.2f} 亿元")
        score -= 1

    if price_span >= 0.4:
        signals.append(f"竞价期间波动较大，价差 {price_span:.2f}")
    elif price_span > 0:
        signals.append(f"竞价期间波动温和，价差 {price_span:.2f}")

    if volume > 0:
        signals.append(f"竞价成交量约 {volume:.0f}")

    if auction_type == "close" and open_price is not None and close_price is not None and close_price >= open_price:
        score += 1
        signals.append("尾盘竞价末端承接偏强")

    if score >= 3:
        strength = "强"
        intent = "偏抢筹"
    elif score >= 1:
        strength = "中强"
        intent = "偏积极"
    elif score <= -3:
        strength = "弱"
        intent = "偏兑现"
    elif score <= -1:
        strength = "中弱"
        intent = "偏谨慎"
    else:
        strength = "中性"
        intent = "多空平衡"

    return {
        "strength": strength,
        "intent": intent,
        "signals": signals,
        "score": score,
        "amount": amount,
    }


def main() -> int:
    args = parse_args()
    _, full_symbol = normalize_symbol(args.symbol)
    trade_date_compact, trade_date_text = normalize_trade_date(args.trade_date)
    source_path = resolve_source_path(args.type, full_symbol, trade_date_compact)
    row = load_auction_row(source_path, trade_date_compact)
    if not row:
        raise SystemExit(f"missing {TYPE_DIR[args.type]} data for {full_symbol} on {trade_date_compact}: {source_path}")

    analysis = classify(row, args.type)

    print(f"type: {TYPE_LABEL[args.type]}")
    print(f"symbol: {full_symbol}")
    print(f"trade_date: {trade_date_text}")
    print(f"strength: {analysis['strength']}")
    print(f"intent: {analysis['intent']}")
    print(f"score: {analysis['score']}")
    print("signals:")
    for item in analysis["signals"]:
        print(f"- {item}")
    print(f"source_file: {source_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
