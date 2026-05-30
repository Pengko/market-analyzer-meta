#!/usr/bin/env python3
"""
基于 T 日收盘前可见数据，评估 T+1 的隔夜强弱倾向。

示例：
  python3 score_next_day_bias.py --symbol 002639 --trade-date 2026-04-07
  python3 score_next_day_bias.py --symbol 002639.SZ --trade-date 20260407 --format json
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean

from common import STOCK_DATA_ROOT, normalize_symbol as normalize_symbol_common, normalize_trade_date
from data.config_loader import cfg
from data.data_access import (
    load_dc_concepts_local,
    load_dc_concept_constituents_local,
    _read_stock_parquet,
    _read_year_parquet,
    _read_single_parquet,
)
from news_context import load_news_payload, narrative_context_from_news, normalize_news_sentiment


BASE_DIR = STOCK_DATA_ROOT


def _moneyflow_daily_candidates(trade_date: str) -> list[Path]:
    candidates: list[Path] = []
    for key, name in (
        ("moneyflow_individual_dc", "dc"),
        ("moneyflow_individual_ths", "ths"),
    ):
        try:
            base = cfg.paths(key)
        except Exception:
            continue
        candidates.append(base / f"moneyflow_{name}_{trade_date}.csv")
    # Legacy fallback in case config keys are unavailable.
    if not candidates:
        candidates = [
            BASE_DIR / "moneyflow_data" / "individual" / "dc" / f"moneyflow_dc_{trade_date}.csv",
            BASE_DIR / "moneyflow_data" / "individual" / "ths" / f"moneyflow_ths_{trade_date}.csv",
        ]
    return candidates


@dataclass
class DayRow:
    ts_code: str
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    pre_close: float
    pct_chg: float
    amount: float


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


@dataclass
class FeatureSet:
    symbol: str
    trade_date: str
    prev_dates: list[str]
    prev_pcts: list[float]
    prev5_dates: list[str]
    stock_name: str | None
    area: str | None
    industry: str | None
    current_open: float
    current_high: float
    current_low: float
    t_pct: float
    sum3_pct: float
    close_pos: float
    current_close: float
    current_amount: float
    prev_amounts: list[float]
    prev_turnover_rates: list[float | None]
    prev5_amounts: list[float]
    prev5_turnover_rates: list[float | None]
    amount_ratio: float | None
    amount_ratio_prev1: float | None
    amount_ratio_prev3_avg: float | None
    turnover_ratio_prev1: float | None
    turnover_ratio_prev3_avg: float | None
    is_bullish_candle: bool
    net_amount: float | None
    moneyflow_source: str | None
    moneyflow_net_amount_rate: float | None
    moneyflow_net_d5_amount: float | None
    moneyflow_size_abs_total: float | None
    moneyflow_large_net_amount: float | None
    moneyflow_small_net_amount: float | None
    moneyflow_large_pressure_ratio: float | None
    moneyflow_small_pressure_ratio: float | None
    turnover_rate: float | None
    volume_ratio: float | None
    rsi6: float | None
    ma5: float | None
    ma10: float | None
    ma20: float | None
    ma30: float | None
    area_theme_name: str | None
    area_theme_strength: float | None
    area_theme_hot: float | None
    market_score: int
    sector_score: int
    initiative_score: int
    sector_signals: list[str]
    sector_context: dict
    leaderboard_context: dict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="评估次日隔夜强弱倾向")
    parser.add_argument("--symbol", required=True, help="股票代码，支持 002639 或 002639.SZ")
    parser.add_argument("--trade-date", required=True, help="交易日期，支持 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--news-json", help="手工消息输入 JSON 文件路径")
    return parser.parse_args()


def normalize_date(value: str) -> str:
    compact, _ = normalize_trade_date(value)
    return compact


def normalize_symbol(value: str) -> str:
    _, full_symbol = normalize_symbol_common(value)
    return full_symbol


def to_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip().lstrip("\ufeff")
    if raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def pct_change(base: float, current: float) -> float:
    if base == 0:
        return 0.0
    return (current - base) / base * 100

def load_narrative_context(news_json_path: str | None, trade_date_text: str | None = None) -> dict:
    raw = load_news_payload(news_json_path)
    news = normalize_news_sentiment(raw, trade_date_text)
    return narrative_context_from_news(news)


def safe_close_pos(day: DayRow) -> float:
    width = day.high - day.low
    if width <= 0:
        return 0.5
    return round((day.close - day.low) / width, 4)


def row_to_day(row: dict) -> DayRow:
    return DayRow(
        ts_code=(row.get("ts_code") or row.get("\ufeffts_code") or "").lstrip("\ufeff"),
        trade_date=str(row["trade_date"]).split(".")[0],
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        pre_close=float(row["pre_close"]),
        pct_chg=float(row["pct_chg"]),
        amount=float(row["amount"]),
    )


def load_daily_history(symbol: str, trade_date: str, count: int = 3) -> list[DayRow]:
    raw_rows = _read_stock_parquet("daily", symbol)
    rows: list[DayRow] = []
    for raw in raw_rows:
        raw_symbol = raw.get("ts_code", "")
        if raw_symbol != symbol:
            continue
        row_date = str(raw.get("trade_date", "")).split(".")[0]
        if row_date <= trade_date:
            rows.append(row_to_day(raw))
    rows.sort(key=lambda item: item.trade_date)
    deduped: list[DayRow] = []
    seen_dates: set[str] = set()
    seen_signatures: set[tuple[float, float, float, float, float, float]] = set()
    for row in reversed(rows):
        signature = (
            round(row.open, 4),
            round(row.high, 4),
            round(row.low, 4),
            round(row.close, 4),
            round(row.pct_chg, 4),
            round(row.amount, 4),
        )
        if row.trade_date in seen_dates or signature in seen_signatures:
            continue
        seen_dates.add(row.trade_date)
        seen_signatures.add(signature)
        deduped.append(row)
        if len(deduped) == count:
            break
    if len(deduped) < count:
        raise SystemExit(f"not enough daily history for {symbol} before {trade_date}")
    return list(reversed(deduped))


def load_daily_series(symbol: str, trade_date: str) -> list[DayRow]:
    raw_rows = _read_stock_parquet("daily", symbol)
    rows: list[DayRow] = []
    for raw in raw_rows:
        raw_symbol = raw.get("ts_code", "")
        if raw_symbol != symbol:
            continue
        row_date = str(raw.get("trade_date", "")).split(".")[0]
        if row_date and row_date <= trade_date:
            rows.append(row_to_day(raw))
    rows.sort(key=lambda item: item.trade_date)
    deduped: list[DayRow] = []
    seen_dates: set[str] = set()
    for row in reversed(rows):
        if row.trade_date in seen_dates:
            continue
        seen_dates.add(row.trade_date)
        deduped.append(row)
    return list(reversed(deduped))


def latest_row_on_or_before(rows: list[dict], trade_date: str) -> dict | None:
    latest: dict | None = None
    latest_date = ""
    for row in rows:
        row_date = str(row.get("trade_date", "")).split(".")[0].strip()
        if not row_date or row_date > trade_date:
            continue
        if row_date >= latest_date:
            latest_date = row_date
            latest = row
    return latest


def freshness_status(rows: list[dict], trade_date: str) -> tuple[str, str | None]:
    row = latest_row_on_or_before(rows, trade_date)
    if row is None:
        return "missing", None
    row_date = str(row.get("trade_date", "")).split(".")[0].strip()
    if row_date == trade_date:
        return "available", row_date
    return "stale", row_date


def minute_freshness_status(symbol: str, trade_date: str) -> tuple[str, str | None]:
    path = minute_path(symbol, trade_date)
    if not path.exists():
        return "missing", None
    return "available", trade_date


def load_moneyflow_snapshot(symbol: str, trade_date: str) -> dict:
    default = {
        "source": None,
        "net_amount": None,
        "net_amount_rate": None,
        "net_d5_amount": None,
        "size_abs_total": None,
        "large_net_amount": None,
        "small_net_amount": None,
        "large_pressure_ratio": None,
        "small_pressure_ratio": None,
    }
    rows = _read_stock_parquet("moneyflow_data/individual/tushare", symbol)
    for row in rows:
        if row.get("ts_code") == symbol and row.get("trade_date") == trade_date:
            elg_net = to_float(row.get("buy_elg_amount"))
            lg_net = to_float(row.get("buy_lg_amount"))
            md_net = to_float(row.get("buy_md_amount"))
            sm_net = to_float(row.get("buy_sm_amount"))
            large_net_amount = None
            if elg_net is not None or lg_net is not None:
                large_net_amount = (elg_net or 0.0) + (lg_net or 0.0)
            small_net_amount = None
            if md_net is not None or sm_net is not None:
                small_net_amount = (md_net or 0.0) + (sm_net or 0.0)
            size_abs_total = None
            if any(value is not None for value in (elg_net, lg_net, md_net, sm_net)):
                size_abs_total = (
                    abs(elg_net or 0.0)
                    + abs(lg_net or 0.0)
                    + abs(md_net or 0.0)
                    + abs(sm_net or 0.0)
                )
            large_pressure_ratio = None
            small_pressure_ratio = None
            if size_abs_total not in (None, 0):
                if large_net_amount is not None:
                    large_pressure_ratio = round(abs(large_net_amount) / size_abs_total, 4)
                if small_net_amount is not None:
                    small_pressure_ratio = round(abs(small_net_amount) / size_abs_total, 4)
            return {
                "source": "tushare_parquet",
                "net_amount": to_float(row.get("net_mf_amount")),
                "net_amount_rate": to_float(row.get("net_mf_amount_rate")),
                "net_d5_amount": to_float(row.get("net_d5_amount")),
                "size_abs_total": size_abs_total,
                "large_net_amount": large_net_amount,
                "small_net_amount": small_net_amount,
                "large_pressure_ratio": large_pressure_ratio,
                "small_pressure_ratio": small_pressure_ratio,
            }
    return default


def load_factor_row(symbol: str, trade_date: str) -> dict | None:
    rows = _read_stock_parquet("stk_factor_pro", symbol)
    return latest_row_on_or_before(rows, trade_date)


def load_factor_history(symbol: str, trade_date: str, count: int = 3) -> list[dict]:
    rows = _read_stock_parquet("stk_factor_pro", symbol)
    filtered: list[dict] = []
    for row in rows:
        row_date = str(row.get("trade_date", "")).split(".")[0].strip()
        if row_date and row_date <= trade_date:
            filtered.append(row)
    filtered.sort(key=lambda item: str(item.get("trade_date", "")).split(".")[0].strip())
    if not filtered:
        return []
    return filtered[-count:]





def load_top_list_row(symbol: str, trade_date: str) -> dict | None:
    rows = _read_year_parquet("top_list", trade_date[:4])
    best_row: dict | None = None
    best_amount = None
    for row in rows:
        if row.get("ts_code") != symbol or row.get("trade_date") != trade_date:
            continue
        amount = to_float(row.get("amount")) or 0.0
        if best_amount is None or amount > best_amount:
            best_amount = amount
            best_row = row
    return best_row


def load_top_inst_rows(symbol: str, trade_date: str) -> list[dict]:
    rows = _read_year_parquet("top_inst", trade_date[:4])
    return [row for row in rows if row.get("ts_code") == symbol and row.get("trade_date") == trade_date]


def load_hm_detail_rows(symbol: str, trade_date: str) -> list[dict]:
    rows = _read_single_parquet("hm_detail", "hm_detail.parquet")
    return [row for row in rows if row.get("ts_code") == symbol and row.get("trade_date") == trade_date]


def evaluate_leaderboard_context(symbol: str, trade_date: str) -> dict:
    top_list_row = load_top_list_row(symbol, trade_date)
    top_inst_rows = load_top_inst_rows(symbol, trade_date)
    hm_rows = load_hm_detail_rows(symbol, trade_date)

    buy_rows = [row for row in top_inst_rows if row.get("side") == "0"]
    sell_rows = [row for row in top_inst_rows if row.get("side") == "1"]
    total_buy = sum(to_float(row.get("buy")) or 0.0 for row in buy_rows)
    total_sell = sum(to_float(row.get("sell")) or 0.0 for row in sell_rows)
    total_net_buy = sum(to_float(row.get("net_buy")) or 0.0 for row in top_inst_rows)
    active_buy_rows = [row for row in buy_rows if (to_float(row.get("net_buy")) or 0.0) > 0]

    hm_net_buy = sum(to_float(row.get("net_amount")) or 0.0 for row in hm_rows)
    hm_buyers = [
        {
            "name": row.get("hm_name"),
            "net_amount": to_float(row.get("net_amount")) or 0.0,
            "org": row.get("hm_orgs"),
        }
        for row in hm_rows
        if (to_float(row.get("net_amount")) or 0.0) > 0
    ]
    hm_sellers = [
        {
            "name": row.get("hm_name"),
            "net_amount": to_float(row.get("net_amount")) or 0.0,
            "org": row.get("hm_orgs"),
        }
        for row in hm_rows
        if (to_float(row.get("net_amount")) or 0.0) < 0
    ]

    return {
        "is_listed": top_list_row is not None,
        "reason": top_list_row.get("reason") if top_list_row else None,
        "top_list_turnover_rate": to_float(top_list_row.get("turnover_rate")) if top_list_row else None,
        "top_list_amount_rate": to_float(top_list_row.get("amount_rate")) if top_list_row else None,
        "top_list_net_rate": to_float(top_list_row.get("net_rate")) if top_list_row else None,
        "top_list_net_amount": to_float(top_list_row.get("net_amount")) if top_list_row else None,
        "buy_seat_count": len(buy_rows),
        "sell_seat_count": len(sell_rows),
        "active_buy_seat_count": len(active_buy_rows),
        "total_buy": total_buy,
        "total_sell": total_sell,
        "total_net_buy": total_net_buy,
        "hm_net_buy": hm_net_buy,
        "hm_buyers": hm_buyers[:5],
        "hm_sellers": hm_sellers[:5],
    }


def minute_path(symbol: str, trade_date: str) -> Path:
    pure_symbol = symbol.split(".")[0]
    trade_date_text = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    y, m, d = trade_date_text.split("-")

    # 新结构A：分钟数据/YYYY/MM/DD/{symbol}_{granularity}.csv
    new_base = BASE_DIR / "分钟数据" / y / m / d
    new_names = [
        f"{pure_symbol}_1m.csv",
        f"{pure_symbol}_5m.csv",
        f"{pure_symbol}_15m.csv",
        f"{pure_symbol}_30m.csv",
        f"{pure_symbol}_60m.csv",
    ]
    for name in new_names:
        path = new_base / name
        if path.exists():
            return path

    # 新结构B：分钟数据/YYYY/MM/DD/{symbol}/1m.csv
    partitioned_base = new_base / symbol
    for name in ("1m.csv", "5m.csv", "15m.csv", "30m.csv", "60m.csv"):
        path = partitioned_base / name
        if path.exists():
            return path

    partitioned_pure_base = new_base / pure_symbol
    for name in ("1m.csv", "5m.csv", "15m.csv", "30m.csv", "60m.csv"):
        path = partitioned_pure_base / name
        if path.exists():
            return path

    # 旧结构 fallback
    old_base = BASE_DIR / "分钟数据" / pure_symbol / trade_date_text
    old_names = [
        "minute_kline.csv",
        "minute_kline_5m.csv",
        "minute_kline_15m.csv",
        "minute_kline_30m.csv",
        "minute_kline_60m.csv",
    ]
    for name in old_names:
        path = old_base / name
        if path.exists():
            return path

    return new_base / f"{pure_symbol}_1m.csv"


def load_minute_rows(symbol: str, trade_date: str) -> list[MinuteRow]:
    trade_date_text = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    path = minute_path(symbol, trade_date)
    if not path.exists():
        return []
    rows: list[MinuteRow] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for raw in csv.DictReader(f):
            dt_text = str(raw.get("datetime") or "").strip()
            if dt_text:
                current_dt = datetime.strptime(dt_text, "%Y-%m-%d %H:%M")
            else:
                raw_time = str(raw.get("time") or "").strip()
                if len(raw_time) == 4 and raw_time.isdigit():
                    hhmm = f"{raw_time[:2]}:{raw_time[2:]}"
                elif len(raw_time) == 5 and raw_time[2] == ":":
                    hhmm = raw_time
                else:
                    continue
                current_dt = datetime.strptime(f"{trade_date_text} {hhmm}", "%Y-%m-%d %H:%M")
            rows.append(
                MinuteRow(
                    dt=current_dt,
                    open=float(raw["open"]),
                    close=float(raw["close"]),
                    high=float(raw["high"]),
                    low=float(raw["low"]),
                    volume=float(raw["volume"]),
                    amount=float(raw["amount"]),
                    avg=float(raw["avg"]),
                )
            )
    return rows


def select_minutes(rows: list[MinuteRow], start: str, end: str) -> list[MinuteRow]:
    start_t = datetime.strptime(start, "%H:%M").time()
    end_t = datetime.strptime(end, "%H:%M").time()
    return [row for row in rows if start_t <= row.dt.time() <= end_t]


def intraday_context(symbol: str, trade_date: str) -> dict | None:
    rows = load_minute_rows(symbol, trade_date)
    if not rows:
        return None

    day_high_row = max(rows, key=lambda row: row.high)
    day_low_row = min(rows, key=lambda row: row.low)
    close_row = rows[-1]
    last30 = select_minutes(rows, "14:30", "15:00")
    pm_all = select_minutes(rows, "13:00", "15:00")
    after_high = [row for row in rows if row.dt >= day_high_row.dt]

    late_open = last30[0].open if last30 else close_row.open
    late_close = last30[-1].close if last30 else close_row.close
    late_avg = last30[-1].avg if last30 else close_row.avg
    after_high_low = min(row.low for row in after_high) if after_high else close_row.low
    pm_low = min(row.low for row in pm_all) if pm_all else close_row.low
    pm_open = pm_all[0].open if pm_all else rows[0].open

    return {
        "day_high_time": day_high_row.dt.strftime("%H:%M"),
        "day_high": day_high_row.high,
        "day_low": day_low_row.low,
        "close": close_row.close,
        "close_vs_day_high_pct": round(pct_change(day_high_row.high, close_row.close), 2),
        "close_vs_avg_pct": round(pct_change(close_row.avg, close_row.close), 2),
        "late_session_pct": round(pct_change(late_open, late_close), 2),
        "late_close_vs_avg_pct": round(pct_change(late_avg, late_close), 2),
        "drawdown_after_high_pct": round(pct_change(day_high_row.high, after_high_low), 2),
        "pm_rebound_pct": round(pct_change(pm_low, close_row.close), 2),
        "pm_total_pct": round(pct_change(pm_open, close_row.close), 2),
    }


def load_stock_basic(symbol: str) -> dict | None:
    path = BASE_DIR / "stock_basic" / "stock_basic_all.csv"
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            row_symbol = (row.get("ts_code") or row.get("\ufeffts_code") or "").lstrip("\ufeff")
            if row_symbol == symbol:
                return row
    return None


def area_theme_keywords(area: str | None) -> list[str]:
    mapping = {
        "深圳": ["深圳特区", "深圳国企改革"],
        "广东": ["粤港澳", "广东", "深圳特区", "深圳国企改革"],
        "上海": ["上海自贸"],
        "海南": ["海南自贸"],
        "新疆": ["新疆核心区"],
        "河北": ["雄安新区"],
        "西藏": ["西藏"],
        "江苏": ["长三角"],
        "浙江": ["长三角"],
        "重庆": ["成渝"],
        "四川": ["成渝"],
    }
    if area is None:
        return []
    return mapping.get(area, [area])


def load_area_theme_snapshot(area: str | None, trade_date: str) -> tuple[str | None, float | None, float | None]:
    if area is None:
        return None, None, None
    path = BASE_DIR / "dc_concept" / f"_题材列表_{trade_date}.csv"
    if not path.exists():
        return None, None, None

    keywords = area_theme_keywords(area)
    best_name = None
    best_strength = None
    best_hot = None
    best_score = None
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            name = row.get("name", "")
            if not any(keyword in name for keyword in keywords):
                continue
            strength = to_float(row.get("strength")) or 0.0
            hot = to_float(row.get("hot")) or 0.0
            score = strength + hot / 10
            if best_score is None or score > best_score:
                best_score = score
                best_name = name
                best_strength = strength
                best_hot = hot
    return best_name, best_strength, best_hot


def load_dc_concepts(trade_date: str) -> list[dict]:
    rows = load_dc_concepts_local(trade_date)
    if not rows:
        return []
    rows: list[dict] = []
    for row in load_dc_concepts_local(trade_date):
        rows.append(
            {
                "theme_code": row.get("theme_code"),
                "name": row.get("name"),
                "pct_change": to_float(row.get("pct_change")) or 0.0,
                "hot": to_float(row.get("hot")) or 0.0,
                "strength": to_float(row.get("strength")) or 0.0,
                "z_t_num": to_float(row.get("z_t_num")) or 0.0,
                "lead_stock_code": row.get("lead_stock_code"),
                "lead_stock": row.get("lead_stock"),
                "lead_stock_pct_change": to_float(row.get("lead_stock_pct_change")) or 0.0,
            }
        )
    return rows


def latest_trade_date_for_cons(rows: list[dict], trade_date: str) -> str | None:
    dates = [str(row.get("trade_date", "")).split(".")[0] for row in rows if str(row.get("trade_date", "")).split(".")[0] <= trade_date]
    return max(dates) if dates else None


def load_dc_constituents(stock_name: str | None, trade_date: str) -> list[dict]:
    return load_dc_concept_constituents_local(stock_name, trade_date)


def load_kpl_constituents(symbol: str, trade_date: str) -> list[dict]:
    base = STOCK_DATA_ROOT / "theme_data" / "kpl_concept_cons"
    matches: list[dict] = []
    for path in base.glob("*.csv"):
        if path.name == "README.md":
            continue
        with path.open("r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        target_date = latest_trade_date_for_cons(rows, trade_date)
        if target_date is None:
            continue
        for row in rows:
            code = row.get("con_code")
            row_date = str(row.get("trade_date", "")).split(".")[0]
            if code == symbol and row_date == target_date:
                matches.append(
                    {
                        "concept_name": row.get("name"),
                        "hot_num": to_float(row.get("hot_num")) or 0.0,
                        "trade_date": target_date,
                    }
                )
    return matches


def evaluate_concept_context(symbol: str, stock_name: str | None, trade_date: str, t_pct: float, close_pos: float) -> tuple[int, int, int, list[str], dict]:
    dc_concepts = load_dc_concepts(trade_date)
    dc_by_code = {row["theme_code"]: row for row in dc_concepts if row.get("theme_code")}
    dc_cons = load_dc_constituents(stock_name, trade_date)
    kpl_cons = load_kpl_constituents(symbol, trade_date)

    market_score = 0
    sector_score = 0
    initiative_score = 0
    signals: list[str] = []

    if dc_concepts:
        top_strengths = sorted((row["strength"] for row in dc_concepts), reverse=True)[:20]
        top_pcts = sorted((row["pct_change"] for row in dc_concepts), reverse=True)[:20]
        avg_top_strength = mean(top_strengths) if top_strengths else 0.0
        avg_top_pct = mean(top_pcts) if top_pcts else 0.0
        if avg_top_pct >= 4 and avg_top_strength >= 1500:
            market_score += 1
            signals.append(f"题材环境偏热，前排题材平均涨幅 {avg_top_pct:.2f}%")
        elif avg_top_pct <= 1 and avg_top_strength < 900:
            market_score -= 1
            signals.append(f"题材环境偏弱，前排题材平均涨幅 {avg_top_pct:.2f}%")

    matched_dc: list[dict] = []
    for row in dc_cons:
        theme = dc_by_code.get(row.get("theme_code"))
        if theme:
            matched_dc.append(theme)
    matched_dc.sort(key=lambda item: (item["strength"], item["hot"], item["pct_change"]), reverse=True)
    strongest_dc = matched_dc[0] if matched_dc else None

    if strongest_dc:
        if strongest_dc["strength"] >= 1500 or strongest_dc["pct_change"] >= 4:
            sector_score += 2
            signals.append(f"东财题材强势，{strongest_dc['name']} 强度 {strongest_dc['strength']:.0f}")
        elif strongest_dc["strength"] >= 900 or strongest_dc["pct_change"] >= 2:
            sector_score += 1
            signals.append(f"东财题材偏强，{strongest_dc['name']} 涨幅 {strongest_dc['pct_change']:.2f}%")
        if strongest_dc["lead_stock_code"] == symbol and t_pct >= strongest_dc["pct_change"] - 0.5:
            initiative_score += 2
            signals.append(f"个股接近东财题材前排，匹配 {strongest_dc['name']} 龙头属性")
        elif t_pct >= strongest_dc["pct_change"] + 1:
            initiative_score += 1
            signals.append(f"个股强于所属东财题材 {strongest_dc['name']}")
        elif t_pct < 0 and strongest_dc["pct_change"] >= 2:
            initiative_score -= 1
            signals.append(f"所属东财题材 {strongest_dc['name']} 活跃，但个股当日掉队")

    strongest_kpl = None
    if kpl_cons:
        strongest_kpl = sorted(kpl_cons, key=lambda item: item["hot_num"], reverse=True)[0]
        if strongest_kpl["hot_num"] >= 15000:
            sector_score += 1
            signals.append(f"开盘啦题材关注度较高，{strongest_kpl['concept_name']} 热度 {strongest_kpl['hot_num']:.0f}")
        if t_pct >= 3.5 and close_pos >= 0.6 and strongest_kpl["hot_num"] >= 15000:
            initiative_score += 1
            signals.append(f"个股在开盘啦热门题材 {strongest_kpl['concept_name']} 中表现不弱")
        elif t_pct < 0 and strongest_kpl["hot_num"] >= 15000:
            initiative_score -= 1
            signals.append(f"个股身处开盘啦热门题材 {strongest_kpl['concept_name']}，但当日偏弱")

    sector_context = {
        "dc_theme_count": len(matched_dc),
        "strongest_dc_theme": strongest_dc["name"] if strongest_dc else None,
        "strongest_dc_strength": strongest_dc["strength"] if strongest_dc else None,
        "strongest_dc_pct_change": strongest_dc["pct_change"] if strongest_dc else None,
        "strongest_kpl_theme": strongest_kpl["concept_name"] if strongest_kpl else None,
        "strongest_kpl_hot_num": strongest_kpl["hot_num"] if strongest_kpl else None,
    }
    return market_score, sector_score, initiative_score, signals, sector_context


def build_features(symbol: str, trade_date: str) -> tuple[FeatureSet, dict]:
    d2, d1, d0 = load_daily_history(symbol, trade_date, count=3)
    d5, d4, d3, _, _ = load_daily_history(symbol, trade_date, count=5)
    daily_series = load_daily_series(symbol, trade_date)
    recent_closes = [row.close for row in daily_series]
    computed_ma5 = round(sum(recent_closes[-5:]) / 5, 4) if len(recent_closes) >= 5 else None
    computed_ma10 = round(sum(recent_closes[-10:]) / 10, 4) if len(recent_closes) >= 10 else None
    computed_ma20 = round(sum(recent_closes[-20:]) / 20, 4) if len(recent_closes) >= 20 else None
    computed_ma30 = round(sum(recent_closes[-30:]) / 30, 4) if len(recent_closes) >= 30 else None
    prev_dates = [d2.trade_date, d1.trade_date]
    prev5_dates = [d5.trade_date, d4.trade_date, d3.trade_date, d2.trade_date, d1.trade_date]
    factor = load_factor_row(symbol, trade_date) or {}
    factor_history = load_factor_history(symbol, trade_date, count=6)
    turnover_by_date = {
        str(row.get("trade_date", "")).split(".")[0].strip(): to_float(row.get("turnover_rate"))
        for row in factor_history
    }
    stock_basic = load_stock_basic(symbol) or {}
    area = stock_basic.get("area")
    area_theme_name, area_theme_strength, area_theme_hot = load_area_theme_snapshot(area, trade_date)
    market_score, sector_score, initiative_score, sector_signals, sector_context = evaluate_concept_context(
        symbol=symbol,
        stock_name=stock_basic.get("name"),
        trade_date=trade_date,
        t_pct=d0.pct_chg,
        close_pos=safe_close_pos(d0),
    )
    amount_ratio = None
    if d2.amount > 0:
        amount_ratio = round(d0.amount / d2.amount, 2)
    amount_ratio_prev1 = round(d0.amount / d1.amount, 2) if d1.amount > 0 else None
    prev3_amounts = [d2.amount, d1.amount]
    prev3_avg_amount = sum(prev3_amounts) / len(prev3_amounts) if prev3_amounts else None
    amount_ratio_prev3_avg = round(d0.amount / prev3_avg_amount, 2) if prev3_avg_amount and prev3_avg_amount > 0 else None
    d1_turnover = turnover_by_date.get(d1.trade_date)
    valid_prev_turnovers = [value for value in [turnover_by_date.get(d2.trade_date), d1_turnover] if value is not None]
    prev3_avg_turnover = sum(valid_prev_turnovers) / len(valid_prev_turnovers) if valid_prev_turnovers else None
    current_turnover = to_float(factor.get("turnover_rate"))
    turnover_ratio_prev1 = round(current_turnover / d1_turnover, 2) if current_turnover is not None and d1_turnover not in (None, 0) else None
    turnover_ratio_prev3_avg = round(current_turnover / prev3_avg_turnover, 2) if current_turnover is not None and prev3_avg_turnover not in (None, 0) else None
    leaderboard_context = evaluate_leaderboard_context(symbol, trade_date)

    moneyflow = load_moneyflow_snapshot(symbol, trade_date)

    features = FeatureSet(
        symbol=symbol,
        trade_date=trade_date,
        prev_dates=prev_dates,
        prev_pcts=[d2.pct_chg, d1.pct_chg],
        prev5_dates=prev5_dates,
        stock_name=stock_basic.get("name"),
        area=area,
        industry=stock_basic.get("industry"),
        current_open=d0.open,
        current_high=d0.high,
        current_low=d0.low,
        t_pct=d0.pct_chg,
        sum3_pct=round(d2.pct_chg + d1.pct_chg + d0.pct_chg, 2),
        close_pos=safe_close_pos(d0),
        current_close=d0.close,
        current_amount=d0.amount,
        prev_amounts=[d2.amount, d1.amount],
        prev_turnover_rates=[turnover_by_date.get(d2.trade_date), turnover_by_date.get(d1.trade_date)],
        prev5_amounts=[d5.amount, d4.amount, d3.amount, d2.amount, d1.amount],
        prev5_turnover_rates=[
            turnover_by_date.get(d5.trade_date),
            turnover_by_date.get(d4.trade_date),
            turnover_by_date.get(d3.trade_date),
            turnover_by_date.get(d2.trade_date),
            turnover_by_date.get(d1.trade_date),
        ],
        amount_ratio=amount_ratio,
        amount_ratio_prev1=amount_ratio_prev1,
        amount_ratio_prev3_avg=amount_ratio_prev3_avg,
        turnover_ratio_prev1=turnover_ratio_prev1,
        turnover_ratio_prev3_avg=turnover_ratio_prev3_avg,
        is_bullish_candle=d0.close > d0.open,
        net_amount=moneyflow.get("net_amount"),
        moneyflow_source=moneyflow.get("source"),
        moneyflow_net_amount_rate=moneyflow.get("net_amount_rate"),
        moneyflow_net_d5_amount=moneyflow.get("net_d5_amount"),
        moneyflow_size_abs_total=moneyflow.get("size_abs_total"),
        moneyflow_large_net_amount=moneyflow.get("large_net_amount"),
        moneyflow_small_net_amount=moneyflow.get("small_net_amount"),
        moneyflow_large_pressure_ratio=moneyflow.get("large_pressure_ratio"),
        moneyflow_small_pressure_ratio=moneyflow.get("small_pressure_ratio"),
        turnover_rate=current_turnover,
        volume_ratio=to_float(factor.get("volume_ratio")),
        rsi6=to_float(factor.get("rsi_bfq_6")),
        ma5=computed_ma5 if computed_ma5 is not None else to_float(factor.get("ma_bfq_5")),
        ma10=computed_ma10 if computed_ma10 is not None else to_float(factor.get("ma_bfq_10")),
        ma20=computed_ma20 if computed_ma20 is not None else to_float(factor.get("ma_bfq_20")),
        ma30=computed_ma30 if computed_ma30 is not None else to_float(factor.get("ma_bfq_30")),
        area_theme_name=area_theme_name,
        area_theme_strength=area_theme_strength,
        area_theme_hot=area_theme_hot,
        market_score=market_score,
        sector_score=sector_score,
        initiative_score=initiative_score,
        sector_signals=sector_signals,
        sector_context=sector_context,
        leaderboard_context=leaderboard_context,
    )

    freshness = {
        "daily": {"status": "available", "trade_date": trade_date},
        "stock_basic": {
            "status": "available" if stock_basic else "missing",
            "trade_date": None,
        },
        "moneyflow": {
            "status": "available" if features.net_amount is not None else "missing",
            "trade_date": trade_date if features.net_amount is not None else None,
        },
        "stk_factor_pro": {
            "status": "available" if factor else "missing",
            "trade_date": str(factor.get("trade_date")) if factor else None,
        },
    }

    for name, subdir in (
        ("cyq_perf", "cyq_perf"),
        ("cyq_chips", "cyq_chips"),
        ("stk_auction_o", "stk_auction_o"),
        ("stk_auction_c", "stk_auction_c"),
    ):
        rows = _read_stock_parquet(subdir, symbol)
        status, row_date = freshness_status(rows, trade_date)
        freshness[name] = {"status": status, "trade_date": row_date}
    minute_status, minute_row_date = minute_freshness_status(symbol, trade_date)
    freshness["minute"] = {"status": minute_status, "trade_date": minute_row_date}

    return features, freshness


def classify_sample_profile(features: FeatureSet) -> dict:
    leaderboard = features.leaderboard_context
    listed_reason = leaderboard.get("reason") or ""
    top_list_net_rate = leaderboard.get("top_list_net_rate") or 0.0
    top_list_amount_rate = leaderboard.get("top_list_amount_rate") or 0.0
    turnover = features.turnover_rate or 0.0
    close_pos = features.close_pos
    sum3 = features.sum3_pct
    t_pct = features.t_pct
    rsi6 = features.rsi6 or 0.0
    volume_ratio = features.volume_ratio or 0.0
    sector_plus = features.sector_score + features.initiative_score

    strong_lockup_proxy = (
        leaderboard.get("is_listed")
        and leaderboard.get("buy_seat_count", 0) == 0
        and top_list_net_rate >= 35
        and top_list_amount_rate >= 60
        and turnover <= 3
        and close_pos >= 0.5
    )
    extreme_emotion_continuation = (
        strong_lockup_proxy
        and t_pct >= 9.5
        and sum3 >= 12
        and rsi6 >= 90
        and volume_ratio <= 0.4
    )
    high_position_game = (
        leaderboard.get("is_listed")
        and "连续三个交易日" in listed_reason
        and sum3 >= 15
        and close_pos >= 0.9
        and top_list_net_rate < 8
    )
    leaderboard_takeover = (
        leaderboard.get("is_listed")
        and top_list_net_rate >= 12
        and leaderboard.get("active_buy_seat_count", 0) >= 3
        and close_pos >= 0.9
        and t_pct >= 9.5
        and 5 <= turnover <= 25
        and sector_plus >= 0
    )
    low_turnover_20cm = (
        t_pct >= 15
        and sum3 >= 18
        and close_pos >= 0.95
        and turnover < 8
        and leaderboard.get("is_listed")
        and top_list_net_rate >= 10
    )

    flags: list[str] = []
    sample_type = "normal_momentum"
    label = "普通量价接力"
    summary = "以量价、题材和分时承接信号为主"

    if leaderboard_takeover:
        flags.append("leaderboard_takeover")
    if high_position_game:
        flags.append("high_position_game")
    if low_turnover_20cm:
        flags.append("low_turnover_20cm")
    if strong_lockup_proxy:
        flags.append("strong_lockup_proxy")
    if extreme_emotion_continuation:
        flags.append("extreme_emotion_continuation")

    if extreme_emotion_continuation:
        sample_type = "extreme_emotion_continuation"
        label = "极端情绪延续"
        summary = "龙虎榜极强且低换手，但已明显过热，只适合单独观察"
    elif high_position_game:
        sample_type = "high_position_game"
        label = "高位博弈"
        summary = "连续上榜后的高位换手博弈，不宜和新主导接力混看"
    elif low_turnover_20cm:
        sample_type = "low_turnover_20cm"
        label = "20cm高位低换手"
        summary = "更像锁仓博弈或筹码惜售，不等于高质量接力"
    elif strong_lockup_proxy:
        sample_type = "strong_lockup_proxy"
        label = "强锁仓接力代理"
        summary = "席位明细缺失，但龙虎榜净买/成交占比极强"
    elif leaderboard_takeover:
        sample_type = "leaderboard_takeover"
        label = "龙虎榜主导接力"
        summary = "多席位协同净买，偏新增主导资金主导"

    return {
        "sample_type": sample_type,
        "label": label,
        "summary": summary,
        "flags": flags,
    }


def classify(score: int) -> tuple[str, str]:
    if score >= 4:
        return "次日强延续", "隔夜结构偏强，若竞价不走坏，更容易继续加速"
    if score >= 2:
        return "次日偏强", "次日更偏向上修复或震荡走强，但未到无脑追强"
    if score >= 0:
        return "次日分歧", "更像高低切换或强弱博弈，方向不宜单押"
    if score >= -2:
        return "次日偏弱", "次日优先防冲高承压或弱修复失败"
    return "次日高位兑现", "更像高位获利了结或强转弱，不宜按强延续处理"


def classify_preheat(score: int) -> tuple[str, str]:
    if score >= 4:
        return "强预热", "已出现较强的量价和资金预热，次日有被点火或走强的可能"
    if score >= 2:
        return "弱预热", "已有一定预热迹象，但更像观察名单而非强确认"
    return "无预热", "暂时看不到明确的前夜点火征兆"


def analyze_preheat(features: FeatureSet, narrative_context: dict | None = None) -> dict:
    score = 0
    signals: list[str] = []

    t_pct = features.t_pct
    sum3 = features.sum3_pct
    close_pos = features.close_pos
    amount_ratio_prev1 = features.amount_ratio_prev1 or 0.0
    amount_ratio_prev3_avg = features.amount_ratio_prev3_avg or 0.0
    turnover = features.turnover_rate or 0.0
    turnover_ratio_prev1 = features.turnover_ratio_prev1 or 0.0
    volume_ratio = features.volume_ratio or 0.0
    net_amount = features.net_amount or 0.0
    moneyflow_large_net_amount = features.moneyflow_large_net_amount
    moneyflow_large_pressure_ratio = features.moneyflow_large_pressure_ratio
    moneyflow_small_net_amount = features.moneyflow_small_net_amount
    moneyflow_small_pressure_ratio = features.moneyflow_small_pressure_ratio
    rsi6 = features.rsi6 or 0.0
    sector_plus = features.sector_score + features.initiative_score
    leaderboard = features.leaderboard_context
    prev2_pct, prev1_pct = features.prev_pcts
    current_low = features.current_low
    current_high = features.current_high
    ma5 = features.ma5
    ma10 = features.ma10

    rebound_preheat = False
    lockup_preheat = False
    continuation_preheat = False
    ma_pullback_preheat = False

    if (
        t_pct <= -9.5
        and close_pos <= 0.12
        and turnover >= 12
        and volume_ratio >= 1.2
        and prev1_pct >= 9.5
    ):
        rebound_preheat = True
        score += 4
        signals.append("T日属于涨停后高换手跌停回杀，次日容易进入反包博弈观察区")

    if (
        net_amount > 0
        and (moneyflow_large_net_amount or 0) > 0
        and moneyflow_large_pressure_ratio is not None
        and moneyflow_large_pressure_ratio >= 0.55
    ):
        score += 1
        signals.append(f"T日资金净额结构偏大单主导（压力占比 {moneyflow_large_pressure_ratio * 100:.1f}%），预热质量加分")
    elif (
        net_amount < 0
        and (moneyflow_large_net_amount or 0) < 0
        and moneyflow_large_pressure_ratio is not None
        and moneyflow_large_pressure_ratio >= 0.55
    ):
        score -= 1
        signals.append(f"T日净流出且大单净流出主导（压力占比 {moneyflow_large_pressure_ratio * 100:.1f}%），预热质量扣分")
    if (
        net_amount > 0
        and (moneyflow_large_net_amount or 0) <= 0
        and (moneyflow_small_net_amount or 0) > 0
        and moneyflow_small_pressure_ratio is not None
        and moneyflow_small_pressure_ratio >= 0.6
        and t_pct >= 8
    ):
        score -= 1
        signals.append("T日虽净流入但偏中小单驱动，次日追高容错需降低")

    if not leaderboard.get("is_listed"):
        if features.is_bullish_candle and close_pos >= 0.68 and amount_ratio_prev1 >= 1.5:
            score += 2
            signals.append(f"T日阳线且较前一日明显放量，成交额比 {amount_ratio_prev1:.2f}")
        elif close_pos >= 0.6 and amount_ratio_prev3_avg >= 1.8:
            score += 1
            signals.append(f"T日量能已高于近两日均值，成交额比 {amount_ratio_prev3_avg:.2f}")

        if turnover >= 5 and (turnover_ratio_prev1 >= 1.2 or volume_ratio >= 1.0):
            score += 1
            signals.append(f"T日换手开始抬升到 {turnover:.2f}% ，关注度有提升迹象")

        if 1.5 <= t_pct <= 6 and close_pos >= 0.6 and sum3 <= 8:
            score += 1
            signals.append("T日温和走强且未明显过热，更像资金预热而非情绪透支")

        if sector_plus >= 1 and t_pct >= 2:
            score += 1
            signals.append("题材和个股主动性开始同步改善，具备次日继续发酵基础")

        if net_amount > 0:
            score += 1
            signals.append(f"T日存在主动净流入 {net_amount:.2f} 万")

        touched_ma5 = ma5 is not None and current_low <= ma5 * 1.01
        touched_ma10 = ma10 is not None and current_low <= ma10 * 1.01
        recovered_above_ma = (
            (ma5 is not None and close_pos >= 0.3 and features.current_close >= ma5 * 0.99)
            or (ma10 is not None and features.current_close >= ma10 * 0.99)
        )
        if (
            -4 <= t_pct <= 3
            and turnover >= 4
            and volume_ratio >= 1.0
            and recovered_above_ma
            and (touched_ma5 or touched_ma10)
            and current_high > features.current_close
        ):
            ma_pullback_preheat = True
            score += 3
            signals.append("T日更像回踩均线后的洗筹而非趋势破坏，次日存在重新转强基础")
            if sum3 >= -2 and prev1_pct >= 0:
                score += 1
                signals.append("前一日并未明显转弱，回踩更接近主升中的正常换手")

        if (
            t_pct >= 9.5
            and close_pos >= 0.45
            and amount_ratio_prev1 <= 0.35
            and turnover <= 4
            and volume_ratio <= 0.95
        ):
            lockup_preheat = True
            score += 3
            signals.append("T日缩量封板且换手极低，更像筹码锁仓后的继续点火前夜")
            if sum3 >= 10 and close_pos >= 0.95:
                score += 1
                signals.append("个股已进入缩量连板状态，次日继续被点火的概率提升")
            if sum3 >= 18 and rsi6 >= 85:
                score += 2
                signals.append("短线虽已过热，但极端锁仓样本里过热本身就是继续强化的信号")

        if (
            0 <= t_pct <= 3
            and sum3 >= 8
            and close_pos >= 0.3
            and turnover >= 2.5
            and volume_ratio >= 1.0
            and amount_ratio_prev1 >= 0.8
            and prev1_pct >= 1.5
        ):
            continuation_preheat = True
            score += 3
            signals.append("T日高位横住未转弱，量能未塌，属于题材扩散前夜的先行卡位形态")
            if rsi6 >= 80:
                score += 1
                signals.append("短线热度仍在高位，说明市场注意力尚未撤离")

    if (close_pos < 0.4 and not continuation_preheat) or (t_pct < -2 and not rebound_preheat):
        penalty = 1 if ma_pullback_preheat else 2
        score -= penalty
        if penalty == 1:
            signals.append("T日收口偏弱，但更像均线回踩洗筹，弱收口扣分降级到 1 分")
        else:
            signals.append("T日收口偏弱或当日转弱，前夜预热质量不高")

    if narrative_context and narrative_context.get("status") == "available":
        narrative_bonus = 0
        if narrative_context.get("hard_catalyst"):
            narrative_bonus += 1
            signals.append("消息面存在硬逻辑支撑，不只是技术形态博弈")
        if narrative_context.get("catalyst_fresh"):
            narrative_bonus += 1
            signals.append("催化属于新消息或新阶段强化，次日延续基础更强")
        if narrative_context.get("core_stock"):
            narrative_bonus += 1
            signals.append("个股处于消息链或题材链前排，更容易承接主线资金")
        if narrative_context.get("theme_active") or narrative_context.get("main_line"):
            narrative_bonus += 1
            signals.append("题材仍在发酵，预热信号更容易被市场继续放大")
        score += narrative_bonus

    if sum3 >= 15 and rsi6 >= 78:
        penalty = 1 if lockup_preheat or continuation_preheat or ma_pullback_preheat else 2
        score -= penalty
        if penalty == 1:
            signals.append(f"短线已偏热，但属于高位延续型样本，过热扣分降级到 {penalty} 分")
        else:
            signals.append(f"短线已偏热，三日累计 {sum3:.2f}% 且 RSI6 {rsi6:.2f}")

    if t_pct >= 9.5 and turnover < 3 and volume_ratio < 0.9:
        penalty = 1 if lockup_preheat else 2
        score -= penalty
        if penalty == 1:
            signals.append("T日虽是缩量封板，但更接近锁仓接力代理，保留部分预热分")
        else:
            signals.append("T日更像独立缩量封板，难当作前夜可复制的预热信号")

    label, summary = classify_preheat(score)
    return {
        "score": score,
        "label": label,
        "summary": summary,
        "signals": signals,
    }


def strong_continuation_gate(features: FeatureSet, score: int) -> tuple[bool, str | None]:
    t_pct = features.t_pct
    sum3 = features.sum3_pct
    close_pos = features.close_pos
    turnover = features.turnover_rate or 0.0
    volume_ratio = features.volume_ratio or 0.0
    net_amount = features.net_amount or 0.0
    rsi6 = features.rsi6 or 0.0
    sector_plus = features.sector_score + features.initiative_score
    strongest_dc_pct = features.sector_context.get("strongest_dc_pct_change")

    if score < 4:
        return False, None

    if (
        t_pct >= 9.5
        and close_pos >= 0.95
        and turnover >= 14
        and sum3 < 12
        and volume_ratio >= 0.9
        and rsi6 < 80
        and net_amount > 0
    ):
        return True, "涨停或接近涨停且封板质量、量价结构都达到强延续门槛"

    if (
        sum3 <= -8
        and close_pos >= 0.55
        and 10 <= turnover <= 22
        and (
            (t_pct >= 3 and net_amount > 0 and volume_ratio >= 0.9)
            or (0 <= t_pct <= 1.2 and rsi6 <= 50 and close_pos >= 0.55)
        )
    ):
        return True, "低位蓄势后出现强承接或补涨前收口，达到强延续门槛"

    if (
        sector_plus >= 2
        and t_pct >= 4.5
        and close_pos >= 0.7
        and net_amount > 0
        and volume_ratio >= 1.0
        and (strongest_dc_pct is None or strongest_dc_pct >= 0)
    ):
        return True, "题材与个股主动性同步强化，达到强延续门槛"

    return False, "虽有偏强信号，但缺少强延续确认结构，标签上限压到次日偏强"


def bullish_bias_gate(features: FeatureSet, score: int, label: str) -> tuple[str, str | None]:
    if label != "次日偏强":
        return label, None

    t_pct = features.t_pct
    close_pos = features.close_pos
    turnover = features.turnover_rate or 0.0
    volume_ratio = features.volume_ratio or 0.0
    net_amount = features.net_amount or 0.0
    rsi6 = features.rsi6 or 0.0
    sector_plus = features.sector_score + features.initiative_score
    sum3 = features.sum3_pct
    amount_ratio = features.amount_ratio or 0.0
    strongest_dc_pct = features.sector_context.get("strongest_dc_pct_change")
    leaderboard = features.leaderboard_context
    sample_profile = classify_sample_profile(features)
    strong_lockup_proxy = (
        leaderboard.get("is_listed")
        and leaderboard.get("buy_seat_count", 0) == 0
        and (leaderboard.get("top_list_net_rate") or 0) >= 35
        and (leaderboard.get("top_list_amount_rate") or 0) >= 60
        and turnover <= 3
        and close_pos >= 0.5
    )

    if sample_profile["sample_type"] == "high_position_game" and turnover >= 8:
        return "次日分歧", "分型识别为高位博弈，次日上限压到分歧，不再按普通偏强处理"

    if (
        t_pct >= 9.5
        and close_pos >= 0.95
        and turnover >= 14
        and (volume_ratio < 0.85 or rsi6 >= 80)
        and sector_plus <= 0
    ):
        return "次日分歧", "高位强势但量比偏弱或过热，且缺少题材主动性，标签上限压到次日分歧"

    if score <= 2 and net_amount <= 0 and volume_ratio < 1.0 and rsi6 >= 78:
        return "次日分歧", "偏强信号不足且量价未继续改善，更像次日分歧而非偏强"

    if (
        t_pct >= 9.5
        and sum3 >= 15
        and rsi6 >= 78
        and sector_plus <= 0
    ):
        return "次日分歧", "高位涨停后短线已偏热且缺少题材共振，次日更像分歧而非继续走强"

    if (
        t_pct >= 9.5
        and sector_plus <= 0
        and amount_ratio < 0.8
        and volume_ratio < 0.9
        and not strong_lockup_proxy
    ):
        return "次日偏弱", "独立封板但量能未继续跟上，次日更容易承压转弱"

    if (
        3 <= t_pct <= 6
        and turnover >= 18
        and net_amount < 0
        and close_pos < 0.75
        and rsi6 >= 78
    ):
        return "次日分歧", "高位放量上冲但资金未同步改善，更像次日分歧"

    if (
        strongest_dc_pct is not None
        and strongest_dc_pct < 0
        and t_pct >= 9.5
        and turnover < 1.5
    ):
        return "次日偏弱", "个股虽强但所属题材当日走弱且换手过低，次日延续性存疑"

    if (
        strongest_dc_pct is not None
        and strongest_dc_pct < 0
        and t_pct >= 15
        and turnover < 5
        and sum3 >= 20
    ):
        return "次日高位兑现", "20cm 脉冲过猛且缺少题材同步强化，次日更像高位兑现"

    return label, None


def analyze(features: FeatureSet, freshness: dict, narrative_context: dict | None = None) -> dict:
    score = 0
    signals: list[str] = []
    sample_profile = classify_sample_profile(features)
    narrative_context = narrative_context or narrative_context_from_news({})
    preheat = analyze_preheat(features, narrative_context=narrative_context)

    t_pct = features.t_pct
    sum3 = features.sum3_pct
    close_pos = features.close_pos
    close_price = features.current_close
    amount_ratio = features.amount_ratio
    amount_ratio_prev1 = features.amount_ratio_prev1
    amount_ratio_prev3_avg = features.amount_ratio_prev3_avg
    net_amount = features.net_amount
    turnover = features.turnover_rate
    turnover_ratio_prev1 = features.turnover_ratio_prev1
    turnover_ratio_prev3_avg = features.turnover_ratio_prev3_avg
    volume_ratio = features.volume_ratio
    rsi6 = features.rsi6
    moneyflow_large_net_amount = features.moneyflow_large_net_amount
    moneyflow_small_net_amount = features.moneyflow_small_net_amount
    moneyflow_large_pressure_ratio = features.moneyflow_large_pressure_ratio
    moneyflow_small_pressure_ratio = features.moneyflow_small_pressure_ratio
    moneyflow_net_d5_amount = features.moneyflow_net_d5_amount
    ma5 = features.ma5
    ma10 = features.ma10
    ma20 = features.ma20
    ma30 = features.ma30
    current_low = features.current_low
    prev5_amounts = features.prev5_amounts
    prev5_turnovers = features.prev5_turnover_rates
    minute = intraday_context(features.symbol, features.trade_date)
    area = features.area
    area_theme_name = features.area_theme_name
    area_theme_strength = features.area_theme_strength
    area_theme_hot = features.area_theme_hot
    sector_plus = features.sector_score + features.initiative_score
    leaderboard = features.leaderboard_context
    score += features.market_score + features.sector_score + features.initiative_score
    signals.extend(features.sector_signals)
    if sample_profile["sample_type"] != "normal_momentum":
        signals.append(f"样本分型：{sample_profile['label']}，{sample_profile['summary']}")

    if narrative_context.get("status") == "available":
        narrative_score = 0
        if narrative_context.get("hard_catalyst"):
            narrative_score += 2
        if narrative_context.get("catalyst_fresh"):
            narrative_score += 1
        if narrative_context.get("core_stock"):
            narrative_score += 2
        if narrative_context.get("theme_active") or narrative_context.get("main_line"):
            narrative_score += 1
        score += narrative_score
        if narrative_score > 0:
            signals.append(f"消息/题材硬逻辑加权 +{narrative_score} 分")
            signals.extend(narrative_context.get("signals", [])[:3])

    valid_prev5_turnovers = [value for value in prev5_turnovers if value is not None]
    low_turnover_base = len(valid_prev5_turnovers) >= 4 and sum(valid_prev5_turnovers) / len(valid_prev5_turnovers) < 5
    shrinking_amount_base = (
        len(prev5_amounts) == 5
        and prev5_amounts[-1] < max(prev5_amounts[:-1])
        and sum(prev5_amounts[-3:]) / 3 < sum(prev5_amounts[:2]) / 2
    )
    prev5_avg_amount = sum(prev5_amounts) / len(prev5_amounts) if prev5_amounts else 0.0
    fresh_attention_setup = (
        low_turnover_base
        and shrinking_amount_base
        and prev5_avg_amount > 0
        and (features.prev_amounts[-1] if features.prev_amounts else 0) < prev5_avg_amount
        and ((amount_ratio is not None and amount_ratio >= 1.2) or features.current_amount >= prev5_avg_amount * 1.5)
        and turnover is not None
        and turnover >= 5
    )
    if fresh_attention_setup:
        if turnover >= 10:
            score += 3
            signals.append("前五日整体缩量低换手，T日放量并把换手抬到 10% 以上，说明有明显新资金关注")
        else:
            score += 2
            signals.append("前五日整体缩量低换手，T日放量且换手回到 5% 以上，说明开始有新资金关注")

    if (
        features.is_bullish_candle
        and close_pos >= 0.7
        and (amount_ratio_prev1 or 0) >= 1.6
        and (turnover_ratio_prev1 or 0) >= 1.4
    ):
        score += 2
        signals.append(
            f"T日阳线且相对前一日量能/换手明显突增，成交额比 {amount_ratio_prev1:.2f}，换手比 {turnover_ratio_prev1:.2f}"
        )
    elif (
        features.is_bullish_candle
        and close_pos >= 0.65
        and (amount_ratio_prev3_avg or 0) >= 1.8
        and (turnover_ratio_prev3_avg or 0) >= 1.5
    ):
        score += 2
        signals.append(
            f"T日阳线且明显高于近两日平均量换，成交额比 {amount_ratio_prev3_avg:.2f}，换手比 {turnover_ratio_prev3_avg:.2f}"
        )
    elif (
        close_pos < 0.6
        and (amount_ratio_prev1 or 0) >= 1.3
        and (turnover_ratio_prev1 or 0) >= 1.3
    ):
        score -= 2
        signals.append(
            f"T日量换放大但收口一般，成交额比 {amount_ratio_prev1:.2f}，换手比 {turnover_ratio_prev1:.2f}，更像高位换手分歧"
        )

    if (
        (net_amount or 0) > 0
        and (moneyflow_large_net_amount or 0) > 0
        and moneyflow_large_pressure_ratio is not None
        and moneyflow_large_pressure_ratio >= 0.55
    ):
        score += 1
        signals.append(f"T日个股资金净额结构偏大单主导（压力占比 {moneyflow_large_pressure_ratio * 100:.1f}%），对延续有确认意义")
    elif (
        (net_amount or 0) < 0
        and (moneyflow_large_net_amount or 0) < 0
        and moneyflow_large_pressure_ratio is not None
        and moneyflow_large_pressure_ratio >= 0.55
    ):
        score -= 1
        signals.append(f"T日净流出且大单净流出压力主导（占比 {moneyflow_large_pressure_ratio * 100:.1f}%），隔夜更易分歧")
    elif (
        (net_amount or 0) > 0
        and (moneyflow_large_net_amount or 0) <= 0
        and (moneyflow_small_net_amount or 0) > 0
        and moneyflow_small_pressure_ratio is not None
        and moneyflow_small_pressure_ratio >= 0.6
    ):
        score -= 1
        signals.append(f"T日净流入以中小单为主（压力占比 {moneyflow_small_pressure_ratio * 100:.1f}%），延续质量需观察")
    if moneyflow_net_d5_amount is not None and moneyflow_net_d5_amount > 0 and (net_amount or 0) > 0:
        score += 1
        signals.append("近五日累计资金面为净流入，且 T 日仍维持净流入")
    elif moneyflow_net_d5_amount is not None and moneyflow_net_d5_amount < 0 and (net_amount or 0) <= 0:
        score -= 1
        signals.append("近五日累计资金面偏弱，且 T 日未出现资金修复")

    if (
        leaderboard.get("is_listed")
        and (leaderboard.get("top_list_net_rate") or 0) >= 8
        and leaderboard.get("active_buy_seat_count", 0) >= 3
    ):
        score += 2
        signals.append(
            f"T日龙虎榜净买占比 {leaderboard['top_list_net_rate']:.2f}% 且买方席位协同较强，说明有新增主导资金介入"
        )
    elif (
        leaderboard.get("is_listed")
        and (leaderboard.get("top_list_net_rate") or 0) <= -5
    ):
        score -= 2
        signals.append(
            f"T日龙虎榜净卖占比较高 {leaderboard['top_list_net_rate']:.2f}% ，更像派发或高位兑现"
        )

    if (
        leaderboard.get("is_listed")
        and leaderboard.get("buy_seat_count", 0) == 0
        and (leaderboard.get("top_list_net_rate") or 0) >= 35
        and (leaderboard.get("top_list_amount_rate") or 0) >= 60
        and (turnover or 0) <= 3
        and t_pct >= 9.5
        and close_pos >= 0.5
    ):
        score += 3
        signals.append(
            f"T日虽缺少席位明细，但龙虎榜净买占比 {(leaderboard.get('top_list_net_rate') or 0):.2f}% 且成交占比 {(leaderboard.get('top_list_amount_rate') or 0):.2f}% ，更像强锁仓接力"
        )

    if (
        leaderboard.get("is_listed")
        and (leaderboard.get("top_list_net_rate") or 0) >= 8
        and leaderboard.get("hm_buyers")
    ):
        top_hm = leaderboard["hm_buyers"][0]
        if top_hm["net_amount"] >= 20000000:
            score += 1
            signals.append(
                f"T日知名活跃席位 {top_hm['name']} 净买 {top_hm['net_amount']:.0f}，对新资金介入有确认意义"
            )
    if leaderboard.get("hm_sellers"):
        top_hm_sell = leaderboard["hm_sellers"][0]
        if abs(top_hm_sell["net_amount"]) >= 10000000:
            score -= 1
            signals.append(
                f"T日活跃席位 {top_hm_sell['name']} 明显净卖 {abs(top_hm_sell['net_amount']):.0f}，派发信号需提高权重"
            )

    listed_reason = leaderboard.get("reason") or ""
    if (
        leaderboard.get("is_listed")
        and "连续三个交易日" in listed_reason
        and sum3 >= 15
        and close_pos >= 0.9
        and (leaderboard.get("top_list_net_rate") or 0) < 6
    ):
        score -= 2
        signals.append(
            f"T日属于连续三日累计上榜且龙虎榜净买占比仅 {(leaderboard.get('top_list_net_rate') or 0):.2f}% ，更像高位博弈而非新主导资金接力"
        )

    if (
        t_pct >= 15
        and sum3 >= 18
        and close_pos >= 0.95
        and (turnover or 0) < 8
        and leaderboard.get("is_listed")
        and (leaderboard.get("top_list_net_rate") or 0) >= 10
    ):
        score -= 2
        signals.append(
            f"T日虽有龙虎榜净买，但 20cm 级别高位低换手仅 {(turnover or 0):.2f}% ，更像锁仓博弈而非高质量接力"
        )

    if minute:
        close_vs_day_high_pct = minute["close_vs_day_high_pct"]
        close_vs_avg_pct = minute["close_vs_avg_pct"]
        late_session_pct = minute["late_session_pct"]
        late_close_vs_avg_pct = minute["late_close_vs_avg_pct"]
        drawdown_after_high_pct = minute["drawdown_after_high_pct"]
        pm_rebound_pct = minute["pm_rebound_pct"]
        pm_total_pct = minute["pm_total_pct"]

        if (
            (amount_ratio or 0) >= 3
            and close_pos >= 0.8
            and close_vs_day_high_pct >= -1.2
            and late_close_vs_avg_pct >= 0
            and pm_rebound_pct >= 1.0
        ):
            score += 2
            signals.append("放量 3 倍以上且分时承接稳定，尾盘仍有回流，大资金参与质量较好")
        elif (
            (amount_ratio or 0) >= 3
            and drawdown_after_high_pct <= -3.0
            and close_vs_avg_pct <= -0.5
            and late_session_pct < 0
        ):
            score -= 2
            signals.append("放量 3 倍以上但冲高回落明显，尾盘承接偏弱，抛压仍然较大")

        if close_vs_day_high_pct <= -3.0 and close_vs_avg_pct <= -0.5 and pm_total_pct <= -1.0:
            score -= 2
            signals.append("分时从日高回落较深且收在均价下，全天更像兑现结构")
        elif close_vs_day_high_pct >= -1.0 and late_close_vs_avg_pct >= 0.2 and pm_rebound_pct >= 1.2:
            score += 1
            signals.append("尾盘收在均价上方且回流明显，分时承接质量偏强")

    if ma5 is not None and ma10 is not None and close_price < ma5 and close_price >= ma10:
        signals.append("收盘跌回 MA5 下方，但仍守住 MA10，后续更适合按分歧观察而非直接转空")

    if ma5 is not None and ma10 is not None:
        touched_ma5 = current_low <= ma5 * 1.01
        touched_ma10 = current_low <= ma10 * 1.01
        if (
            -4 <= t_pct <= 3
            and turnover is not None
            and turnover >= 4
            and (volume_ratio or 0) >= 1.0
            and (touched_ma5 or touched_ma10)
            and close_price >= min(ma5, ma10) * 0.99
        ):
            score += 2
            signals.append("T日最低点接近 MA5/MA10 后收回，结构更像回踩均线洗筹而非破位")

    if ma20 is not None and ma30 is not None and close_price >= ma20 >= ma30 and close_pos >= 0.55:
        signals.append("收盘仍站在 MA20、MA30 之上，中段支撑结构尚在")

    if sum3 <= 2 and t_pct >= 0.5 and (net_amount or 0) > 0:
        score += 2
        signals.append(f"近三日未明显过热，T日转强且资金净流入 {net_amount:.2f} 万")
    if sum3 < -5 and close_pos >= 0.5:
        score += 2
        signals.append(f"近三日充分回撤后，T日收在区间上半部，三日累计 {sum3:.2f}%")
    if t_pct < 0 and close_pos >= 0.55 and (turnover or 0) >= 15 and (volume_ratio or 0) >= 1:
        score += 2
        signals.append("T日虽未收红，但承接不差且换手活跃，偏向次日修复")
    if t_pct >= 0 and close_pos >= 0.55 and (net_amount or 0) > 0:
        score += 1
        signals.append("T日收在相对高位且有主动资金承接")
    if 3 <= t_pct <= 5 and close_pos >= 0.7 and (turnover or 0) >= 15 and (volume_ratio or 0) >= 1.1:
        score += 1
        signals.append("T日温和走强且量价配合不差，次日仍有上修复基础")
    if amount_ratio is not None and amount_ratio >= 1.0 and (net_amount or 0) > 0 and rsi6 is not None and 45 <= rsi6 <= 60:
        score += 1
        signals.append(f"T日放量但未过热，量能比 {amount_ratio:.2f}，RSI6 {rsi6:.2f}")
    if 0 <= t_pct <= 1 and close_pos >= 0.55 and sum3 <= 0 and 10 <= (turnover or 0) <= 15:
        score += 2
        signals.append("T日低位窄幅整理但承接尚可，更像次日补涨前的收口")
    if 0 <= t_pct <= 1 and close_pos < 0.5 and (net_amount or 0) >= 10000 and (volume_ratio or 0) >= 1 and rsi6 is not None and 45 <= rsi6 <= 60:
        score += 2
        signals.append("T日表面不强，但资金与量比提前改善，次日有升级可能")
    if turnover is not None and 10 <= turnover <= 22:
        score += 1
        signals.append(f"换手处于可持续区间 {turnover:.2f}%")
    if rsi6 is not None and 45 <= rsi6 <= 75:
        score += 1
        signals.append(f"RSI6 未过热 {rsi6:.2f}")
    if t_pct >= 9.5 and close_pos >= 0.95 and (turnover or 0) >= 14 and sum3 < 15:
        score += 3
        signals.append("T日涨停或接近涨停但封板质量不差，仍有隔夜延续条件")
    elif t_pct >= 9.5 and (net_amount or 0) > 0:
        score += 1
        signals.append("T日强势封板且资金未明显流出")

    if t_pct >= 9.5 and close_pos >= 0.95 and sector_plus <= 0:
        if (turnover or 0) < 3 and (amount_ratio or 0) >= 8:
            score -= 4
            signals.append("T日虽强封板，但更像独立脉冲且换手过低，次日接力基础偏弱")
        elif (turnover or 0) < 8:
            score -= 2
            signals.append("T日封板但换手不足且缺少题材主动性，次日更容易转分歧")
        if sum3 >= 18 and (turnover or 0) < 8:
            score -= 1
            signals.append(f"近三日累计涨幅已偏大且换手不够，三日累计 {sum3:.2f}%")

    if t_pct >= 9.5 and (net_amount or 0) < 0 and (amount_ratio or 1.0) < 0.7 and (turnover or 0) < 13:
        score -= 4
        signals.append("T日高位封板但量缩且净流出，次日更像兑现而非接力")
    elif t_pct >= 9.5 and (volume_ratio or 1.0) < 0.7 and (turnover or 0) < 13:
        score -= 4
        signals.append("T日涨停但缩量过于明显，次日更容易高位兑现")
    elif t_pct >= 9.5 and (net_amount or 0) < 0:
        score -= 1
        signals.append("T日虽强，但主力净流出，隔夜不宜按无脑强延续处理")

    if sum3 >= 12 and (turnover or 0) >= 25:
        score -= 3
        signals.append(f"近三日涨幅与换手都偏高，三日累计 {sum3:.2f}%，换手 {turnover:.2f}%")
    elif sum3 >= 12 and close_pos < 0.75 and (turnover or 0) >= 18:
        score -= 2
        signals.append("近三日已较热，T日收盘位置一般，次日更容易分歧")
    if 3 <= t_pct <= 6 and close_pos < 0.75 and (turnover or 0) >= 25 and (volume_ratio or 1.0) <= 1.0:
        score -= 3
        signals.append("T日高换手但收口不强，次日更容易走成强分歧或转弱")

    if rsi6 is not None and rsi6 >= 80 and (net_amount or 0) <= 0:
        score -= 2
        signals.append(f"RSI6 过热且资金未同步改善 {rsi6:.2f}")
    elif rsi6 is not None and rsi6 >= 78 and sum3 > 8:
        score -= 1
        signals.append(f"短线指标已偏热，RSI6 {rsi6:.2f}")

    if close_pos < 0.5:
        score -= 1
        signals.append(f"T日收盘偏离日高较远，收盘位置 {close_pos:.2f}")
    if amount_ratio is not None and volume_ratio is not None and amount_ratio < 0.75 and volume_ratio < 0.9:
        score -= 1
        signals.append(f"T日量能未明显放大，量能比 {amount_ratio:.2f}，量比 {volume_ratio:.2f}")
    if 3 <= t_pct <= 6 and sum3 > 12 and close_pos < 0.7:
        score -= 1
        signals.append("T日虽收涨，但更像高位震荡后的弱收口")

    if t_pct <= -9.5 and close_pos <= 0.05 and (net_amount or 0) < 0:
        if sum3 > -8:
            score -= 3
            signals.append("T日跌停但并非充分回撤后的出清，次日更容易继续弱化")
        elif amount_ratio is not None and amount_ratio < 0.6 and (volume_ratio or 0) <= 1.0:
            score -= 2
            signals.append("T日跌停且量能未明显改善，次日更偏向延续弱势")

    if t_pct <= -9.5 and sum3 <= -20 and (turnover or 0) >= 25 and (volume_ratio or 0) >= 1.5:
        score += 4
        signals.append("T日更像极端超跌后的恐慌出清，次日应防分歧修复而非机械续跌")

    if area_theme_name and area_theme_strength is not None:
        if area_theme_strength >= 1000 and (area_theme_hot or 0) >= 450 and t_pct >= 0 and close_pos >= 0.55:
            score += 1
            signals.append(f"地区题材共振存在，{area_theme_name} 强度 {area_theme_strength:.0f}，对次日有弱加成")
        elif area_theme_strength >= 1000 and t_pct < 0 and close_pos < 0.5:
            score -= 1
            signals.append(f"地区题材虽活跃，但个股未跟上 {area_theme_name}，说明个股主动性偏弱")

    label, next_day_view = classify(score)
    strong_ok, strong_gate_note = strong_continuation_gate(features, score)
    if label == "次日强延续" and not strong_ok:
        label, next_day_view = "次日偏强", "次日更偏向上修复或震荡走强，但未到强延续门槛"
        if strong_gate_note:
            signals.append(strong_gate_note)
    elif label == "次日强延续" and strong_gate_note:
        signals.append(strong_gate_note)
    original_label = label
    label, bullish_gate_note = bullish_bias_gate(features, score, label)
    if label != original_label:
        _, next_day_view = classify({
            "次日强延续": 4,
            "次日偏强": 2,
            "次日分歧": 0,
            "次日偏弱": -1,
            "次日高位兑现": -3,
        }[label])
    if bullish_gate_note:
        signals.append(bullish_gate_note)
    return {
        "symbol": features.symbol,
        "trade_date": features.trade_date,
        "score": score,
        "label": label,
        "next_day_view": next_day_view,
        "preheat": preheat,
        "signals": signals,
        "features": {
            "stock_name": features.stock_name,
            "area": features.area,
            "industry": features.industry,
            "sample_profile": sample_profile,
            "prev_dates": features.prev_dates,
            "t_pct": round(features.t_pct, 2),
            "sum3_pct": round(features.sum3_pct, 2),
            "close_pos": round(features.close_pos, 4),
            "amount_ratio_vs_prev2": features.amount_ratio,
            "amount_ratio_vs_prev1": features.amount_ratio_prev1,
            "amount_ratio_vs_prev3_avg": features.amount_ratio_prev3_avg,
            "net_amount": features.net_amount,
            "moneyflow_source": features.moneyflow_source,
            "moneyflow_net_amount_rate": features.moneyflow_net_amount_rate,
            "moneyflow_net_d5_amount": features.moneyflow_net_d5_amount,
            "moneyflow_size_abs_total": features.moneyflow_size_abs_total,
            "moneyflow_large_net_amount": features.moneyflow_large_net_amount,
            "moneyflow_small_net_amount": features.moneyflow_small_net_amount,
            "moneyflow_large_pressure_ratio": features.moneyflow_large_pressure_ratio,
            "moneyflow_small_pressure_ratio": features.moneyflow_small_pressure_ratio,
            "turnover_rate": features.turnover_rate,
            "turnover_ratio_vs_prev1": features.turnover_ratio_prev1,
            "turnover_ratio_vs_prev3_avg": features.turnover_ratio_prev3_avg,
            "volume_ratio": features.volume_ratio,
            "rsi6": features.rsi6,
            "is_bullish_candle": features.is_bullish_candle,
            "area_theme_name": features.area_theme_name,
            "area_theme_strength": features.area_theme_strength,
            "area_theme_hot": features.area_theme_hot,
            "market_score": features.market_score,
            "sector_score": features.sector_score,
            "initiative_score": features.initiative_score,
            "sector_context": features.sector_context,
            "leaderboard_context": features.leaderboard_context,
            "narrative_context": narrative_context,
        },
        "freshness": freshness,
    }


def render_text(result: dict) -> str:
    lines = [
        f"股票: {result['symbol']}",
        f"交易日: {result['trade_date']}",
        f"隔夜评分: {result['score']}",
        f"标签: {result['label']}",
        f"解读: {result['next_day_view']}",
        "",
        "核心特征:",
    ]
    features = result["features"]
    for key in (
        "stock_name",
        "area",
        "industry",
        "sample_profile",
        "prev_dates",
        "t_pct",
        "sum3_pct",
        "close_pos",
        "amount_ratio_vs_prev2",
        "net_amount",
        "turnover_rate",
        "volume_ratio",
        "rsi6",
        "area_theme_name",
        "area_theme_strength",
        "area_theme_hot",
        "market_score",
        "sector_score",
        "initiative_score",
        "sector_context",
    ):
        lines.append(f"- {key}: {features[key]}")
    lines.append("")
    lines.append("信号:")
    for item in result["signals"]:
        lines.append(f"- {item}")
    lines.append("")
    lines.append("数据新鲜度:")
    for key, value in result["freshness"].items():
        lines.append(f"- {key}: {value['status']} ({value['trade_date']})")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    symbol = normalize_symbol(args.symbol)
    trade_date = normalize_date(args.trade_date)
    features, freshness = build_features(symbol, trade_date)
    trade_date_text = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    narrative_context = load_narrative_context(args.news_json, trade_date_text=trade_date_text)
    result = analyze(features, freshness, narrative_context=narrative_context)
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(render_text(result))


if __name__ == "__main__":
    main()
