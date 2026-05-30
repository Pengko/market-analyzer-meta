#!/usr/bin/env python3
"""
统一执行隔夜倾向多层测试，并支持双层回放：

1. standard: 标准案例回放
2. selected: 精选池回测
3. full: 全量历史回测
4. leaderboard: 历史龙虎榜 T-1 -> T -> T+1 三段推演
5. all: 同时跑以上各层

示例：
  python3 run_next_day_bias_suite.py --layer standard
  python3 run_next_day_bias_suite.py --layer selected
  python3 run_next_day_bias_suite.py --layer full
  python3 run_next_day_bias_suite.py --layer leaderboard
  python3 run_next_day_bias_suite.py --layer all --format json
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from common import STOCK_DATA_ROOT, normalize_symbol as normalize_symbol_common, normalize_trade_date
from score_next_day_bias import analyze, build_features, evaluate_leaderboard_context


BASE_DIR = STOCK_DATA_ROOT
DAILY_DIR = BASE_DIR / "daily"


STANDARD_CASES = [
    {
        "name": "美利云高开假强失败",
        "symbol": "000815.SZ",
        "checkpoints": [
            {"trade_date": "20260408", "note": "强势次日预判前夜"},
            {"trade_date": "20260409", "note": "高开冲高失败当日收盘回放"},
        ],
    },
    {
        "name": "国晟科技黄金坑与二波启动",
        "symbol": "603778.SH",
        "checkpoints": [
            {"trade_date": "20251014", "note": "黄金坑右侧确认起点"},
            {"trade_date": "20251022", "note": "坑后主升确认"},
            {"trade_date": "20260303", "note": "止跌震荡后二波启动"},
        ],
    },
    {
        "name": "平潭发展消息驱动趋势",
        "symbol": "000592.SZ",
        "checkpoints": [
            {"trade_date": "20250820", "note": "回调结束与右侧确认前夜"},
            {"trade_date": "20250821", "note": "巨量冲板回落但未转弱"},
            {"trade_date": "20251017", "note": "二波趋势再启动"},
        ],
    },
    {
        "name": "航天发展商业航天趋势",
        "symbol": "000547.SZ",
        "checkpoints": [
            {"trade_date": "20251023", "note": "均线黏合后方向选择前夜"},
            {"trade_date": "20251028", "note": "缺口强势未补与放量洗筹确认"},
            {"trade_date": "20251114", "note": "二波涨停主升启动"},
        ],
    },
]


SELECTED_BACKTEST_GROUPS = [
    {
        "name": "精选池回测：2026-04-07 -> 2026-04-08",
        "eval_date": "20260407",
        "pred_date": "20260408",
        "symbols": [
            "002639.SZ",
            "000815.SZ",
            "603778.SH",
            "002008.SZ",
            "002342.SZ",
            "002471.SZ",
            "605162.SH",
            "000890.SZ",
            "002263.SZ",
            "002309.SZ",
        ],
        "source": "test-2026-04-07-to-2026-04-08-next-day-bias.md",
    }
]


FULL_BACKTEST_GROUPS = [
    {
        "name": "全量回测：2026-04-07 -> 2026-04-08",
        "eval_date": "20260407",
        "pred_date": "20260408",
        "symbols": [
            "002639.SZ",
            "000815.SZ",
            "603778.SH",
            "002008.SZ",
            "002342.SZ",
            "002471.SZ",
            "605162.SH",
            "000890.SZ",
            "002263.SZ",
            "002309.SZ",
        ],
        "source": "test-2026-04-07-to-2026-04-08-next-day-bias.md",
    },
    {
        "name": "全量回测：2026-04-03 -> 2026-04-07",
        "eval_date": "20260403",
        "pred_date": "20260407",
        "symbols": [
            "000720.SZ",
            "002222.SZ",
            "002831.SZ",
            "300006.SZ",
            "300608.SZ",
            "600056.SH",
            "000008.SZ",
            "000037.SZ",
            "000993.SZ",
            "600207.SH",
        ],
        "source": "test-2026-04-09-next-day-bias-20260403-to-20260407.md",
    },
    {
        "name": "全量回测：2026-04-02 -> 2026-04-03",
        "eval_date": "20260402",
        "pred_date": "20260403",
        "symbols": [
            "000155.SZ",
            "000586.SZ",
            "000720.SZ",
            "000788.SZ",
            "000950.SZ",
            "002038.SZ",
            "002263.SZ",
            "000048.SZ",
            "002102.SZ",
            "002357.SZ",
        ],
        "source": "test-2026-04-09-next-day-bias-20260402-to-20260403.md",
    },
]


LEADERBOARD_FORWARD_CASES = [
    {
        "name": "新能泰山龙虎榜主导接力",
        "symbol": "000720.SZ",
        "leaderboard_trade_date": "20260402",
        "category": "龙虎榜主导接力",
        "source": "test-2026-04-09-xinneng-taishan-new-leaderboard-takeover-case.md",
    },
    {
        "name": "北大医药高位博弈",
        "symbol": "000788.SZ",
        "leaderboard_trade_date": "20260402",
        "category": "高位博弈",
        "source": "test-2026-04-09-beida-pharma-high-position-game-case.md",
    },
    {
        "name": "重药控股强净买但题材转弱",
        "symbol": "000950.SZ",
        "leaderboard_trade_date": "20260403",
        "category": "强净买但题材转弱",
        "source": "test-2026-04-09-zhongyao-holdings-strong-buy-but-weak-followthrough-case.md",
    },
    {
        "name": "思特奇20cm高位低换手",
        "symbol": "300608.SZ",
        "leaderboard_trade_date": "20260403",
        "category": "20cm高位低换手",
        "source": "test-2026-04-09-sitech-20cm-low-turnover-case.md",
    },
    {
        "name": "特发信息强锁仓接力代理",
        "symbol": "000070.SZ",
        "leaderboard_trade_date": "20260211",
        "category": "强锁仓接力代理",
        "source": "test-2026-04-09-strong-lockup-proxy-round-1.md",
    },
    {
        "name": "韩建河山极端情绪延续",
        "symbol": "603616.SH",
        "leaderboard_trade_date": "20260205",
        "category": "极端情绪延续",
        "source": "test-2026-04-10-extreme-emotion-continuation-bucket-round-1.md",
    },
    {
        "name": "京投发展极端情绪延续",
        "symbol": "600683.SH",
        "leaderboard_trade_date": "20260203",
        "category": "极端情绪延续",
        "source": "test-2026-04-10-extreme-emotion-continuation-bucket-round-1.md",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一执行隔夜倾向三层测试")
    parser.add_argument("--layer", choices=("standard", "selected", "full", "leaderboard", "all"), default="all")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def normalize_symbol(value: str) -> str:
    _, full_symbol = normalize_symbol_common(value)
    return full_symbol


def normalize_date(value: str) -> str:
    compact, _ = normalize_trade_date(value)
    return compact


def load_daily_rows(symbol: str) -> list[dict[str, Any]]:
    path = DAILY_DIR / f"daily_{symbol}.csv"
    if not path.exists():
        raise SystemExit(f"missing daily history: {path}")

    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row_symbol = (row.get("ts_code") or row.get("\ufeffts_code") or "").lstrip("\ufeff")
            if row_symbol != symbol:
                continue
            row_date = str(row.get("trade_date", "")).split(".")[0].strip()
            if not row_date:
                continue
            rows.append(
                {
                    "trade_date": row_date,
                    "close": float(row["close"]),
                }
            )
    rows.sort(key=lambda item: item["trade_date"])
    deduped: list[dict[str, Any]] = []
    seen_dates: set[str] = set()
    for row in reversed(rows):
        if row["trade_date"] in seen_dates:
            continue
        seen_dates.add(row["trade_date"])
        deduped.append(row)
    return list(reversed(deduped))


def next_trade_row(symbol: str, trade_date: str) -> dict[str, Any] | None:
    for row in load_daily_rows(symbol):
        if row["trade_date"] > trade_date:
            return row
    return None


def previous_trade_row(symbol: str, trade_date: str) -> dict[str, Any] | None:
    previous = None
    for row in load_daily_rows(symbol):
        if row["trade_date"] >= trade_date:
            return previous
        previous = row
    return previous


def second_next_trade_row(symbol: str, trade_date: str) -> dict[str, Any] | None:
    seen_first = False
    for row in load_daily_rows(symbol):
        if row["trade_date"] > trade_date:
            if not seen_first:
                seen_first = True
                continue
            return row
    return None


def classify_actual_by_pct(pct: float) -> str:
    if pct >= 7:
        return "次日强延续"
    if pct >= 2:
        return "次日偏强"
    if pct > -2:
        return "次日分歧"
    if pct > -7:
        return "次日偏弱"
    return "次日高位兑现"


def coarse_bucket(label: str) -> str:
    if label in ("次日强延续", "次日偏强"):
        return "偏多"
    if label in ("次日偏弱", "次日高位兑现"):
        return "偏空"
    return "中性"


def classify_preheat_actual(next_pct: float, is_listed: bool) -> str:
    if is_listed and next_pct >= 7:
        return "强预热兑现"
    if is_listed or next_pct >= 2:
        return "弱预热兑现"
    return "无预热兑现"


def preheat_coarse_bucket(label: str) -> str:
    if label in ("强预热", "弱预热", "强预热兑现", "弱预热兑现"):
        return "有预热"
    return "无预热"


def leaderboard_day_snapshot(symbol: str, trade_date: str) -> dict[str, Any]:
    context = evaluate_leaderboard_context(symbol, trade_date)
    return {
        "is_listed": context.get("is_listed", False),
        "reason": context.get("reason"),
        "top_list_net_rate": context.get("top_list_net_rate"),
        "top_list_amount_rate": context.get("top_list_amount_rate"),
        "active_buy_seat_count": context.get("active_buy_seat_count"),
        "buy_seat_count": context.get("buy_seat_count"),
        "hm_buyers": context.get("hm_buyers", [])[:2],
        "hm_sellers": context.get("hm_sellers", [])[:2],
    }


def evaluate_symbol(symbol: str, trade_date: str) -> dict[str, Any]:
    symbol = normalize_symbol(symbol)
    trade_date = normalize_date(trade_date)
    features, freshness = build_features(symbol, trade_date)
    result = analyze(features, freshness)
    next_row = next_trade_row(symbol, trade_date)

    actual: dict[str, Any] | None = None
    if next_row is not None:
        current_close = features.current_close
        next_close = float(next_row["close"])
        next_pct = round((next_close - current_close) / current_close * 100, 2) if current_close else 0.0
        actual_label = classify_actual_by_pct(next_pct)
        actual = {
            "pred_trade_date": next_row["trade_date"],
            "next_close": next_close,
            "next_pct": next_pct,
            "label": actual_label,
            "coarse_bucket": coarse_bucket(actual_label),
        }

    return {
        "symbol": symbol,
        "trade_date": trade_date,
        "result": result,
        "actual": actual,
    }


def evaluate_symbol_with_t2(symbol: str, trade_date: str) -> dict[str, Any]:
    base_eval = evaluate_symbol(symbol, trade_date)
    first_actual = base_eval["actual"]

    t2_prediction: dict[str, Any] | None = None
    if first_actual is not None:
        t1_eval = evaluate_symbol(symbol, first_actual["pred_trade_date"])
        second_actual = t1_eval["actual"]
        t2_prediction = {
            "eval_trade_date": first_actual["pred_trade_date"],
            "predicted_label": t1_eval["result"]["label"],
            "score": t1_eval["result"]["score"],
            "next_day_view": t1_eval["result"]["next_day_view"],
            "actual": second_actual,
        }

    return {
        "symbol": base_eval["symbol"],
        "trade_date": base_eval["trade_date"],
        "t1_result": base_eval["result"],
        "t1_actual": first_actual,
        "t2_prediction": t2_prediction,
    }


def run_leaderboard_forward_cases() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    pre_exact_hits = 0
    pre_coarse_hits = 0
    follow_exact_hits = 0
    follow_coarse_hits = 0

    for case in LEADERBOARD_FORWARD_CASES:
        symbol = normalize_symbol(case["symbol"])
        t_date = normalize_date(case["leaderboard_trade_date"])
        prev_row = previous_trade_row(symbol, t_date)
        if prev_row is None:
            continue

        pre_eval = evaluate_symbol(symbol, prev_row["trade_date"])
        t_eval = evaluate_symbol(symbol, t_date)
        t_actual = pre_eval["actual"]
        t_plus_1_actual = t_eval["actual"]
        if t_actual is None or t_plus_1_actual is None:
            continue

        preheat_prediction = pre_eval["result"]["preheat"]
        t_leaderboard = leaderboard_day_snapshot(symbol, t_date)
        pre_predicted_label = preheat_prediction["label"]
        pre_actual_label = classify_preheat_actual(t_actual["next_pct"], t_leaderboard["is_listed"])
        pre_exact_hit = {
            "强预热": "强预热兑现",
            "弱预热": "弱预热兑现",
            "无预热": "无预热兑现",
        }[pre_predicted_label] == pre_actual_label
        pre_coarse_hit = preheat_coarse_bucket(pre_predicted_label) == preheat_coarse_bucket(pre_actual_label)
        if pre_exact_hit:
            pre_exact_hits += 1
        if pre_coarse_hit:
            pre_coarse_hits += 1

        follow_predicted_label = t_eval["result"]["label"]
        follow_actual_label = t_plus_1_actual["label"]
        follow_exact_hit = follow_predicted_label == follow_actual_label
        follow_coarse_hit = coarse_bucket(follow_predicted_label) == t_plus_1_actual["coarse_bucket"]
        if follow_exact_hit:
            follow_exact_hits += 1
        if follow_coarse_hit:
            follow_coarse_hits += 1

        items.append(
            {
                "name": case["name"],
                "category": case["category"],
                "symbol": symbol,
                "source": case["source"],
                "t_minus_1_date": prev_row["trade_date"],
                "t_date": t_date,
                "t_plus_1_date": t_plus_1_actual["pred_trade_date"],
                "t_minus_1_prediction": {
                    "label": pre_predicted_label,
                    "score": preheat_prediction["score"],
                    "summary": preheat_prediction["summary"],
                    "top_signals": preheat_prediction["signals"][:3],
                },
                "t_actual": {
                    "label": pre_actual_label,
                    "next_pct": t_actual["next_pct"],
                    "coarse_bucket": preheat_coarse_bucket(pre_actual_label),
                    "leaderboard": t_leaderboard,
                },
                "t_minus_1_exact_hit": pre_exact_hit,
                "t_minus_1_coarse_hit": pre_coarse_hit,
                "t_prediction": {
                    "label": follow_predicted_label,
                    "score": t_eval["result"]["score"],
                    "sample_type": t_eval["result"]["features"]["sample_profile"]["sample_type"],
                    "top_signals": t_eval["result"]["signals"][:3],
                },
                "t_plus_1_actual": {
                    "label": follow_actual_label,
                    "next_pct": t_plus_1_actual["next_pct"],
                    "coarse_bucket": t_plus_1_actual["coarse_bucket"],
                },
                "t_exact_hit": follow_exact_hit,
                "t_coarse_hit": follow_coarse_hit,
            }
        )

    sample_count = len(items)
    return {
        "layer": "leaderboard",
        "sample_count": sample_count,
        "t_minus_1_exact_hits": pre_exact_hits,
        "t_minus_1_coarse_hits": pre_coarse_hits,
        "t_minus_1_exact_hit_rate": round(pre_exact_hits / sample_count * 100, 1) if sample_count else 0.0,
        "t_minus_1_coarse_hit_rate": round(pre_coarse_hits / sample_count * 100, 1) if sample_count else 0.0,
        "t_exact_hits": follow_exact_hits,
        "t_coarse_hits": follow_coarse_hits,
        "t_exact_hit_rate": round(follow_exact_hits / sample_count * 100, 1) if sample_count else 0.0,
        "t_coarse_hit_rate": round(follow_coarse_hits / sample_count * 100, 1) if sample_count else 0.0,
        "items": items,
    }


def run_standard_cases() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for case in STANDARD_CASES:
        checkpoints: list[dict[str, Any]] = []
        for checkpoint in case["checkpoints"]:
            evaluated = evaluate_symbol_with_t2(case["symbol"], checkpoint["trade_date"])
            checkpoints.append(
                {
                    "trade_date": checkpoint["trade_date"],
                    "note": checkpoint["note"],
                    "label": evaluated["t1_result"]["label"],
                    "score": evaluated["t1_result"]["score"],
                    "next_day_view": evaluated["t1_result"]["next_day_view"],
                    "top_signals": evaluated["t1_result"]["signals"][:3],
                    "actual": evaluated["t1_actual"],
                    "t2_prediction": evaluated["t2_prediction"],
                }
            )
        cases.append(
            {
                "name": case["name"],
                "symbol": case["symbol"],
                "checkpoints": checkpoints,
            }
        )
    return {
        "layer": "standard",
        "case_count": len(cases),
        "cases": cases,
    }


def run_backtest_groups(layer_name: str, groups: list[dict[str, Any]]) -> dict[str, Any]:
    summary_groups: list[dict[str, Any]] = []
    t1_exact_total = 0
    t1_coarse_total = 0
    t1_sample_total = 0
    t2_exact_total = 0
    t2_coarse_total = 0
    t2_sample_total = 0

    for group in groups:
        items: list[dict[str, Any]] = []
        t1_exact_hits = 0
        t1_coarse_hits = 0
        t2_exact_hits = 0
        t2_coarse_hits = 0

        for symbol in group["symbols"]:
            evaluated = evaluate_symbol_with_t2(symbol, group["eval_date"])
            t1_actual = evaluated["t1_actual"]
            if t1_actual is None:
                continue

            t1_predicted_label = evaluated["t1_result"]["label"]
            t1_actual_label = t1_actual["label"]
            t1_exact_hit = t1_predicted_label == t1_actual_label
            t1_coarse_hit = coarse_bucket(t1_predicted_label) == t1_actual["coarse_bucket"]

            if t1_exact_hit:
                t1_exact_hits += 1
            if t1_coarse_hit:
                t1_coarse_hits += 1

            t2_prediction = evaluated["t2_prediction"]
            t2_actual = t2_prediction["actual"] if t2_prediction else None
            t2_predicted_label = t2_prediction["predicted_label"] if t2_prediction else None
            t2_exact_hit = False
            t2_coarse_hit = False
            if t2_prediction and t2_actual:
                t2_exact_hit = t2_predicted_label == t2_actual["label"]
                t2_coarse_hit = coarse_bucket(t2_predicted_label) == t2_actual["coarse_bucket"]
                if t2_exact_hit:
                    t2_exact_hits += 1
                if t2_coarse_hit:
                    t2_coarse_hits += 1

            items.append(
                {
                    "symbol": evaluated["symbol"],
                    "eval_date": group["eval_date"],
                    "t1_pred_date": t1_actual["pred_trade_date"],
                    "t1_predicted_label": t1_predicted_label,
                    "t1_score": evaluated["t1_result"]["score"],
                    "t1_actual_label": t1_actual_label,
                    "t1_actual_next_pct": t1_actual["next_pct"],
                    "t1_exact_hit": t1_exact_hit,
                    "t1_coarse_hit": t1_coarse_hit,
                    "t2_eval_date": t2_prediction["eval_trade_date"] if t2_prediction else None,
                    "t2_pred_date": t2_actual["pred_trade_date"] if t2_actual else None,
                    "t2_predicted_label": t2_predicted_label,
                    "t2_score": t2_prediction["score"] if t2_prediction else None,
                    "t2_actual_label": t2_actual["label"] if t2_actual else None,
                    "t2_actual_next_pct": t2_actual["next_pct"] if t2_actual else None,
                    "t2_exact_hit": t2_exact_hit if t2_prediction and t2_actual else None,
                    "t2_coarse_hit": t2_coarse_hit if t2_prediction and t2_actual else None,
                }
            )

        t1_sample_count = len(items)
        t2_sample_count = sum(1 for item in items if item["t2_predicted_label"] is not None and item["t2_actual_label"] is not None)
        t1_sample_total += t1_sample_count
        t2_sample_total += t2_sample_count
        t1_exact_total += t1_exact_hits
        t1_coarse_total += t1_coarse_hits
        t2_exact_total += t2_exact_hits
        t2_coarse_total += t2_coarse_hits

        summary_groups.append(
            {
                "name": group["name"],
                "source": group["source"],
                "t1_sample_count": t1_sample_count,
                "t1_exact_hits": t1_exact_hits,
                "t1_coarse_hits": t1_coarse_hits,
                "t1_exact_hit_rate": round(t1_exact_hits / t1_sample_count * 100, 1) if t1_sample_count else 0.0,
                "t1_coarse_hit_rate": round(t1_coarse_hits / t1_sample_count * 100, 1) if t1_sample_count else 0.0,
                "t2_sample_count": t2_sample_count,
                "t2_exact_hits": t2_exact_hits,
                "t2_coarse_hits": t2_coarse_hits,
                "t2_exact_hit_rate": round(t2_exact_hits / t2_sample_count * 100, 1) if t2_sample_count else 0.0,
                "t2_coarse_hit_rate": round(t2_coarse_hits / t2_sample_count * 100, 1) if t2_sample_count else 0.0,
                "items": items,
            }
        )

    return {
        "layer": layer_name,
        "group_count": len(summary_groups),
        "t1_sample_count": t1_sample_total,
        "t1_exact_hits": t1_exact_total,
        "t1_coarse_hits": t1_coarse_total,
        "t1_exact_hit_rate": round(t1_exact_total / t1_sample_total * 100, 1) if t1_sample_total else 0.0,
        "t1_coarse_hit_rate": round(t1_coarse_total / t1_sample_total * 100, 1) if t1_sample_total else 0.0,
        "t2_sample_count": t2_sample_total,
        "t2_exact_hits": t2_exact_total,
        "t2_coarse_hits": t2_coarse_total,
        "t2_exact_hit_rate": round(t2_exact_total / t2_sample_total * 100, 1) if t2_sample_total else 0.0,
        "t2_coarse_hit_rate": round(t2_coarse_total / t2_sample_total * 100, 1) if t2_sample_total else 0.0,
        "groups": summary_groups,
    }


def render_text(report: dict[str, Any]) -> str:
    sections: list[str] = []

    standard = report.get("standard")
    if standard:
        lines = [
            "## 标准案例回放",
            f"- 案例数：`{standard['case_count']}`",
        ]
        for case in standard["cases"]:
            lines.append(f"- {case['name']} `{case['symbol']}`")
            for checkpoint in case["checkpoints"]:
                lines.append(
                    f"  - {checkpoint['trade_date']}：`{checkpoint['label']}` / 分数 `{checkpoint['score']}` / {checkpoint['note']}"
                )
                if checkpoint.get("t2_prediction") and checkpoint["t2_prediction"].get("actual"):
                    lines.append(
                        f"    - T+2 回放：`{checkpoint['t2_prediction']['predicted_label']}` / 分数 `{checkpoint['t2_prediction']['score']}` / 基于 `{checkpoint['t2_prediction']['eval_trade_date']}` 预测"
                    )
        sections.append("\n".join(lines))

    leaderboard = report.get("leaderboard")
    if leaderboard:
        lines = [
            "## 龙虎榜三段推演",
            f"- 样本数：`{leaderboard['sample_count']}`",
            f"- T-1 -> T 5档精确：`{leaderboard['t_minus_1_exact_hits']} / {leaderboard['sample_count']} = {leaderboard['t_minus_1_exact_hit_rate']}%`",
            f"- T-1 -> T 粗方向：`{leaderboard['t_minus_1_coarse_hits']} / {leaderboard['sample_count']} = {leaderboard['t_minus_1_coarse_hit_rate']}%`",
            f"- T -> T+1 5档精确：`{leaderboard['t_exact_hits']} / {leaderboard['sample_count']} = {leaderboard['t_exact_hit_rate']}%`",
            f"- T -> T+1 粗方向：`{leaderboard['t_coarse_hits']} / {leaderboard['sample_count']} = {leaderboard['t_coarse_hit_rate']}%`",
        ]
        for item in leaderboard["items"]:
            lines.append(
                f"- {item['name']} `{item['symbol']}`：T-1 预测 `{item['t_minus_1_prediction']['label']}` -> T 实际 `{item['t_actual']['label']}`；T 当日预测 `{item['t_prediction']['label']}` -> T+1 实际 `{item['t_plus_1_actual']['label']}`"
            )
        sections.append("\n".join(lines))

    for key, title in (("selected", "精选池回测"), ("full", "全量历史回测")):
        layer = report.get(key)
        if not layer:
            continue
        lines = [
            f"## {title}",
            f"- 组数：`{layer['group_count']}`",
            f"- T+1 样本数：`{layer['t1_sample_count']}`",
            f"- T+1 5档精确：`{layer['t1_exact_hits']} / {layer['t1_sample_count']} = {layer['t1_exact_hit_rate']}%`",
            f"- T+1 粗方向：`{layer['t1_coarse_hits']} / {layer['t1_sample_count']} = {layer['t1_coarse_hit_rate']}%`",
            f"- T+2 样本数：`{layer['t2_sample_count']}`",
            f"- T+2 5档精确：`{layer['t2_exact_hits']} / {layer['t2_sample_count']} = {layer['t2_exact_hit_rate']}%`",
            f"- T+2 粗方向：`{layer['t2_coarse_hits']} / {layer['t2_sample_count']} = {layer['t2_coarse_hit_rate']}%`",
        ]
        for group in layer["groups"]:
            lines.append(
                f"- {group['name']}：T+1 精确 `{group['t1_exact_hits']} / {group['t1_sample_count']} = {group['t1_exact_hit_rate']}%`，T+1 粗方向 `{group['t1_coarse_hits']} / {group['t1_sample_count']} = {group['t1_coarse_hit_rate']}%`，T+2 精确 `{group['t2_exact_hits']} / {group['t2_sample_count']} = {group['t2_exact_hit_rate']}%`，T+2 粗方向 `{group['t2_coarse_hits']} / {group['t2_sample_count']} = {group['t2_coarse_hit_rate']}%`"
            )
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def main() -> None:
    args = parse_args()
    report: dict[str, Any] = {}

    if args.layer in ("standard", "all"):
        report["standard"] = run_standard_cases()
    if args.layer in ("leaderboard", "all"):
        report["leaderboard"] = run_leaderboard_forward_cases()
    if args.layer in ("selected", "all"):
        report["selected"] = run_backtest_groups("selected", SELECTED_BACKTEST_GROUPS)
    if args.layer in ("full", "all"):
        report["full"] = run_backtest_groups("full", FULL_BACKTEST_GROUPS)

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))


if __name__ == "__main__":
    main()
