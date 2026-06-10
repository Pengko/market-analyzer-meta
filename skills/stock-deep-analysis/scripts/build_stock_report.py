#!/usr/bin/env python3
"""
薄编排层 — 把常用分析脚本的结果汇总成可直接复用的报告骨架。

已提取函数从 time_util / financing_analyzer / capital_context 导入，
保留 re-export 兼容层供 parallel/agents.py 等外部消费者使用。

示例：
  python3 build_stock_report.py --symbol 002639 --trade-date 2026-04-08
  python3 build_stock_report.py --symbol 002639.SZ --trade-date 20260408 --format json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from data.parquet_io import load_latest_report

from common import STOCK_DATA_ROOT, normalize_symbol, normalize_trade_date
from data.data_access import (
    load_daily_basic_row as load_daily_basic_row_impl,
    load_daily_row as load_daily_row_impl,
    latest_open_trade_date_on_or_before as latest_open_trade_date_on_or_before_impl,
    load_browser_margin_signal as load_browser_margin_signal_impl,
    load_trade_calendar_index as load_trade_calendar_index_impl,
    next_trade_dates_compact as next_trade_dates_compact_impl,
    resolve_trade_date_by_calendar as resolve_trade_date_by_calendar_impl,
)
from data.portfolio_loader import get_position as get_position_impl
from signals.core.check_data_freshness import build_report as build_freshness_report

from time_util import (
    scenario_from_now as _scenario_from_now,
    normalize_trade_date_for_session as _normalize_trade_date_for_session,
    resolve_checkpoint as _resolve_checkpoint,
    parse_date_candidates as _parse_date_candidates,
    normalize_trade_date_text as _normalize_trade_date_text,
    next_trade_date_compact as _next_trade_date_compact,
)
from financing_analyzer import (
    safe_float as _safe_float,
    analyze_financing_context as _analyze_financing_context,
    build_fundamental as _build_fundamental,
    resolve_symbol as _resolve_symbol,
)
from capital_context import (
    is_event_theme as _is_event_theme,
    summarize_capital_freshness as _summarize_capital_freshness,
    build_mixed_trade_date_context as _build_mixed_trade_date_context,
    degrade_prediction_bundle as _degrade_prediction_bundle,
)

from decision.decision_engine import (
    analyze_context_propagation as analyze_context_propagation_impl,
    build_final_decision as build_final_decision_impl,
    build_peer_linkage as build_peer_linkage_impl,
    build_validation_tracking as build_validation_tracking_impl,
    persist_pending_validation as persist_pending_validation_impl,
)
from signals.core.analyze_auction_intent import analyze_auction_intent
from analysis.market_analyzer import analyze_market_context as analyze_market_context_impl
from runtime.news_runtime import (
    auto_resolve_news_json_path as auto_resolve_news_json_path_impl,
    enrich_news_pipeline_meta as enrich_news_pipeline_meta_impl,
    enrich_news_sentiment as enrich_news_sentiment_impl,
    load_manual_news as load_manual_news_impl,
)
from render.report_renderer import render_status_text, render_action_bias_text, render_acquisition_method_text
from signals.core.score_next_day_bias import load_narrative_context
from analysis.sector_analyzer import (
    analyze_sector_context as analyze_sector_context_impl,
    build_leader_prediction as build_leader_prediction_impl,
    discover_mobile_subthemes_if_needed as discover_mobile_subthemes_if_needed_impl,
    discover_mobile_theme_leaders_if_needed as discover_mobile_theme_leaders_if_needed_impl,
    load_stock_name as load_stock_name_impl,
    match_mobile_subthemes as match_mobile_subthemes_impl,
)
from analysis.stock_trend_analyzer import (
    analyze_chip_structure as analyze_chip_structure_impl,
    analyze_t_plus_two_bias as analyze_t_plus_two_bias_impl,
    analyze_trend_structure as analyze_trend_structure_impl,
    analyze_volatility_context as analyze_volatility_context_impl,
    safe_next_day as safe_next_day_impl,
)
from runtime.runtime_fetch import (
    auto_fetch_minute_data as auto_fetch_minute_data_impl,
    auto_fetch_minute_via_browser as auto_fetch_minute_via_browser_impl,
    fetch_china_network_time as fetch_china_network_time_impl,
    parse_network_datetime_text as parse_network_datetime_text_impl,
    resolve_now_china as resolve_now_china_impl,
    safe_intraday as safe_intraday_impl,
)
from runtime.runtime_quality import (
    extract_json_object as extract_json_object_impl,
    format_intraday_failure_reason as format_intraday_failure_reason_impl,
    minute_rows_in_window as minute_rows_in_window_impl,
    persist_browser_minute_payload as persist_browser_minute_payload_impl,
    resolve_minute_path as resolve_minute_path_impl,
    simplify_browser_fetch_error as simplify_browser_fetch_error_impl,
    validate_intraday_rows as validate_intraday_rows_impl,
)
from zoneinfo import ZoneInfo

from data.config_loader import cfg


# ── 常量 ──────────────────────────────────────────────
INDEX_DATA_ROOT = cfg.paths("index_data_root")
REFERENCES_ROOT = cfg.paths("references_dir")
TRADE_CAL_DIR_CANDIDATES = [
    cfg.paths("trade_cal_dir"),
    STOCK_DATA_ROOT / "trade_cal",
]


# ═══════════════════════════════════════════════════════
# Re-export 兼容层 — 转发到源模块
# ═══════════════════════════════════════════════════════

# --- time_util ---
scenario_from_now = _scenario_from_now
parse_date_candidates = _parse_date_candidates

def normalize_trade_date_for_session(
    now: datetime, trade_date_text: str, checkpoint_arg: str
) -> tuple[str, dict[str, Any]]:
    return _normalize_trade_date_for_session(
        now, trade_date_text, checkpoint_arg,
        latest_open_trade_date_on_or_before_fn=latest_open_trade_date_on_or_before_impl,
    )

def resolve_checkpoint(now: datetime, trade_date_text: str, checkpoint_arg: str) -> str:
    return _resolve_checkpoint(now, trade_date_text, checkpoint_arg)

def next_trade_dates_compact(trade_date_text: str, count: int = 1) -> list[str]:
    return next_trade_dates_compact_impl(trade_date_text, count=count)

def resolve_trade_date_by_calendar(trade_date_text: str) -> tuple[str, dict[str, Any]]:
    return resolve_trade_date_by_calendar_impl(trade_date_text)

def latest_open_trade_date_on_or_before(date_text: str) -> str | None:
    return latest_open_trade_date_on_or_before_impl(date_text)

# --- financing_analyzer ---
safe_float = _safe_float
analyze_financing_context = _analyze_financing_context
_build_fundamental_fn = _build_fundamental
resolve_symbol = _resolve_symbol

# --- capital_context ---
is_event_theme = _is_event_theme
summarize_capital_freshness = _summarize_capital_freshness

def build_mixed_trade_date_context(
    trade_date_text: str,
    now: datetime,
    freshness: dict[str, Any],
    kline_sync: dict[str, Any] | None,
    factor_sync: dict[str, Any] | None,
) -> dict[str, Any]:
    return _build_mixed_trade_date_context(
        trade_date_text, now, freshness, kline_sync, factor_sync,
        latest_open_trade_date_on_or_before_fn=latest_open_trade_date_on_or_before_impl,
    )

def _degrade_prediction_bundle_fn(mixed_context: dict[str, Any], payload: dict[str, Any]) -> None:
    _degrade_prediction_bundle(mixed_context, payload)

# --- decision.decision_engine ---
build_peer_linkage = build_peer_linkage_impl
build_final_decision = build_final_decision_impl
analyze_context_propagation = analyze_context_propagation_impl
build_validation_tracking = build_validation_tracking_impl
persist_pending_validation = persist_pending_validation_impl

# --- analysis.sector_analyzer ---
match_mobile_subthemes = match_mobile_subthemes_impl
build_leader_prediction = build_leader_prediction_impl
discover_mobile_subthemes_if_needed = discover_mobile_subthemes_if_needed_impl
discover_mobile_theme_leaders_if_needed = discover_mobile_theme_leaders_if_needed_impl
load_stock_name = load_stock_name_impl
analyze_sector_context = analyze_sector_context_impl

# --- analysis.stock_trend_analyzer ---
analyze_chip_structure = analyze_chip_structure_impl
analyze_t_plus_two_bias = analyze_t_plus_two_bias_impl
analyze_trend_structure = analyze_trend_structure_impl
analyze_volatility_context = analyze_volatility_context_impl
safe_next_day = safe_next_day_impl

# --- analysis.market_analyzer ---
analyze_market_context = analyze_market_context_impl

# --- runtime.runtime_fetch ---
resolve_now_china = resolve_now_china_impl
safe_intraday = safe_intraday_impl

# --- data.data_access (re-export for parallel/agents.py) ---
load_daily_basic_row_impl = load_daily_basic_row_impl
load_browser_margin_signal = load_browser_margin_signal_impl


# ═══════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成股票深度分析报告骨架")
    parser.add_argument("--symbol", required=True, help="如 002639 或 002639.SZ")
    parser.add_argument(
        "--trade-date", required=True, help="格式 YYYY-MM-DD 或 YYYYMMDD"
    )
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--news-json", help="手工消息输入 JSON 文件路径")
    parser.add_argument(
        "--checkpoint",
        choices=("auto", "pre_open", "open", "noon", "afternoon", "close", "next_close"),
        default="auto",
        help="本次分析所处检查点，默认 auto（按当前时段自动判断）",
    )
    return parser.parse_args()


# ═══════════════════════════════════════════════════════
# Phase 2 — 并行 Agent 调度
# ═══════════════════════════════════════════════════════

def _phase2_parallel(
    ctx: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Phase 2: 并行执行 8 个分析 Agent。"""
    import functools
    import importlib
    parallel_mod = importlib.import_module("parallel")

    parallel_mod.clear_tmp(ctx.get("trade_date_compact"))
    ParallelAgent = parallel_mod.ParallelAgent
    run_parallel = parallel_mod.run_parallel
    run_kline_sync_agent = parallel_mod.run_kline_sync_agent
    run_news_agent = parallel_mod.run_news_agent
    run_intraday_agent = parallel_mod.run_intraday_agent
    run_sector_agent = parallel_mod.run_sector_agent
    run_stock_dims_agent = parallel_mod.run_stock_dims_agent
    run_dragon_tiger_agent = parallel_mod.run_dragon_tiger_agent
    run_intraday_linkage_agent = parallel_mod.run_intraday_linkage_agent
    run_fundamental_agent = parallel_mod.run_fundamental_agent

    full_symbol = ctx["full_symbol"]
    pure_symbol = ctx["pure_symbol"]
    trade_date_text = ctx["trade_date_text"]
    trade_date_compact = ctx["trade_date_compact"]
    now = ctx["now"]
    resolved_checkpoint = ctx["checkpoint"]
    news_reference_date = ctx["news_reference_date"]

    try:
        from data.data_provider import get_stock_concepts
        concepts = get_stock_concepts(full_symbol) or []
        top_theme = concepts[0] if concepts else None
    except Exception:
        top_theme = None

    agents = [
        ParallelAgent(
            name="kline_sync",
            func=functools.partial(
                run_kline_sync_agent,
                full_symbol=full_symbol,
                trade_date_text=trade_date_text,
                now=now,
            ),
            timeout=120.0,
            default_result={
                "status": "timeout",
                "kline_sync": {"status": "timeout"},
                "factor_sync": {"status": "timeout"},
            },
        ),
        ParallelAgent(
            name="news",
            func=functools.partial(
                run_news_agent,
                full_symbol=full_symbol,
                trade_date_text=trade_date_text,
                news_json_path=ctx.get("news_json_path"),
                news_reference_date=news_reference_date,
            ),
            timeout=120.0,
            default_result={
                "status": "timeout",
                "narrative_context": {},
                "manual_news_raw": {},
            },
        ),
        ParallelAgent(
            name="intraday",
            func=functools.partial(
                run_intraday_agent,
                pure_symbol=pure_symbol,
                trade_date_text=trade_date_text,
                now=now,
                resolved_checkpoint=resolved_checkpoint,
            ),
            timeout=60.0,
            default_result={
                "status": "timeout",
                "intraday": {"status": "timeout"},
            },
        ),
        ParallelAgent(
            name="sector",
            func=functools.partial(
                run_sector_agent,
                full_symbol=full_symbol,
                trade_date_text=trade_date_text,
            ),
            timeout=30.0,
            default_result={
                "status": "timeout",
                "market_context": {},
                "sector_context": {},
            },
        ),
        ParallelAgent(
            name="stock_dims",
            func=functools.partial(
                run_stock_dims_agent,
                full_symbol=full_symbol,
                trade_date_text=trade_date_text,
                trade_date_compact=trade_date_compact,
            ),
            timeout=30.0,
            default_result={
                "status": "timeout",
                "financing_context": {},
                "auction_intent": {},
                "trend_structure": {},
                "chip_structure": {},
                "volatility_context": {},
                "fundamental": {},
            },
        ),
        ParallelAgent(
            name="dragon_tiger",
            func=functools.partial(
                run_dragon_tiger_agent,
                full_symbol=full_symbol,
                trade_date_text=trade_date_text,
                trade_date_compact=trade_date_compact,
            ),
            timeout=15.0,
            default_result={
                "status": "timeout",
                "signal": None,
                "overall_score": None,
            },
        ),
        ParallelAgent(
            name="intraday_linkage",
            func=functools.partial(
                run_intraday_linkage_agent,
                pure_symbol=pure_symbol,
                trade_date_text=trade_date_text,
                top_theme=top_theme,
            ),
            timeout=120.0,
            default_result={
                "status": "timeout",
                "linkage_label": "超时",
            },
        ),
        ParallelAgent(
            name="fundamental_deep",
            func=functools.partial(
                run_fundamental_agent,
                pure_symbol=pure_symbol,
                full_symbol=full_symbol,
                trade_date_text=trade_date_text,
            ),
            timeout=120.0,
            default_result={
                "status": "timeout",
                "financial_health": "超时",
            },
        ),
    ]

    return run_parallel(agents, trade_date_compact, max_workers=8)


# ═══════════════════════════════════════════════════════
# 历史对比
# ═══════════════════════════════════════════════════════

def build_history_comparison(payload: dict) -> dict[str, Any]:
    """
    生成历史对比数据
    
    对比当前分析与同股票最新的盘后报告，展示关键指标变化
    """
    from data.config_loader import cfg
    
    symbol = payload.get("symbol")
    trade_date = payload.get("trade_date")
    pending_dir = Path.home() / "quant-data" / "市场分析" / "reports" / "个股分析报告"
    
    if not symbol or not trade_date:
        return {"status": "missing_info"}
    
    latest = load_latest_report(pending_dir, symbol, trade_date)
    
    if not latest:
        return {"status": "no_history"}
    
    # 对比关键指标
    comparison = {
        "status": "available",
        "previous_date": latest.get("trade_date"),
        "previous_checkpoint": latest.get("checkpoint"),
        "changes": {},
    }
    
    # 价格变化
    current_price = payload.get("current_price")
    previous_price = latest.get("current_price")
    if current_price is not None and previous_price is not None:
        price_change = current_price - previous_price
        price_change_pct = (price_change / previous_price) * 100
        comparison["changes"]["price"] = {
            "current": current_price,
            "previous": previous_price,
            "change": round(price_change, 2),
            "change_pct": round(price_change_pct, 2),
        }
    
    # 决策变化
    current_decision = payload.get("final_decision", {}).get("decision")
    previous_decision = latest.get("decision")
    if current_decision is not None and previous_decision is not None:
        comparison["changes"]["decision"] = {
            "current": current_decision,
            "previous": previous_decision,
            "changed": current_decision != previous_decision,
        }
    
    # 筹码变化
    current_chip = payload.get("chip_structure") or {}
    previous_winner_rate = latest.get("winner_rate")
    current_winner_rate = current_chip.get("winner_rate")
    if current_winner_rate is not None and previous_winner_rate is not None:
        comparison["changes"]["winner_rate"] = {
            "current": current_winner_rate,
            "previous": previous_winner_rate,
            "change": round(current_winner_rate - previous_winner_rate, 4),
        }
    
    # 波动率变化
    current_vol = payload.get("volatility_context") or {}
    previous_atr = latest.get("atr14")
    current_atr = current_vol.get("atr14")
    if current_atr is not None and previous_atr is not None:
        comparison["changes"]["atr14"] = {
            "current": current_atr,
            "previous": previous_atr,
            "change": round(current_atr - previous_atr, 2),
        }
    
    return comparison


# ═══════════════════════════════════════════════════════
# 核心编排
# ═══════════════════════════════════════════════════════

def build_payload(
    symbol: str,
    trade_date: str,
    news_json_path: str | None = None,
    checkpoint: str = "auto",
) -> dict:
    symbol = resolve_symbol(symbol)

    pure_symbol, full_symbol = normalize_symbol(symbol)
    _requested_trade_date_compact, requested_trade_date_text = normalize_trade_date(
        trade_date
    )

    now, time_source = resolve_now_china()
    (
        normalized_requested_trade_date_text,
        session_trade_date_resolution,
    ) = normalize_trade_date_for_session(now, requested_trade_date_text, checkpoint)
    resolved_checkpoint = resolve_checkpoint(
        now, normalized_requested_trade_date_text, checkpoint
    )

    news_reference_date = now.strftime("%Y-%m-%d")
    trade_date_text, trade_cal_meta = resolve_trade_date_by_calendar(
        normalized_requested_trade_date_text
    )
    trade_date_compact = trade_date_text.replace("-", "")

    ctx = dict(
        full_symbol=full_symbol,
        pure_symbol=pure_symbol,
        trade_date_text=trade_date_text,
        trade_date_compact=trade_date_compact,
        now=now,
        checkpoint=resolved_checkpoint,
        news_reference_date=news_reference_date,
        news_json_path=news_json_path,
    )
    parallel_results = _phase2_parallel(ctx)

    kline_sync = (parallel_results.get("kline_sync") or {}).get("kline_sync", {})
    factor_sync = (parallel_results.get("kline_sync") or {}).get("factor_sync", {})

    news_agent = parallel_results.get("news", {})
    resolved_news_json_path = news_agent.get("resolved_news_json_path")
    narrative_context = news_agent.get("narrative_context", {})
    manual_news_raw = news_agent.get("manual_news_raw", {})

    intraday = (parallel_results.get("intraday") or {}).get("intraday", {})

    market_context = (parallel_results.get("sector") or {}).get(
        "market_context", analyze_market_context(full_symbol, trade_date_text)
    )
    sector_context = (parallel_results.get("sector") or {}).get(
        "sector_context", analyze_sector_context(full_symbol, trade_date_text)
    )

    dims = parallel_results.get("stock_dims", {})
    financing_context = dims.get("financing_context", analyze_financing_context(full_symbol, trade_date_text))
    auction_intent = dims.get("auction_intent", analyze_auction_intent(full_symbol, trade_date_text))
    trend_structure = dims.get("trend_structure", analyze_trend_structure(full_symbol, trade_date_text))
    chip_structure = dims.get("chip_structure", analyze_chip_structure(full_symbol, trade_date_text))
    volatility_context = dims.get("volatility_context", analyze_volatility_context(full_symbol, trade_date_text))
    fundamental = dims.get("fundamental", _build_fundamental_fn(full_symbol, trade_date_compact))

    dragon_tiger_result = parallel_results.get("dragon_tiger", {})
    intraday_linkage = parallel_results.get("intraday_linkage", {})
    fundamental_deep = parallel_results.get("fundamental_deep", {})

    freshness = build_freshness_report(full_symbol, pure_symbol, trade_date_text)

    next_day = safe_next_day(
        full_symbol, trade_date_compact, narrative_context=narrative_context
    )
    capital_freshness = summarize_capital_freshness(next_day)

    news_sentiment = enrich_news_sentiment_impl(
        manual_news_raw if manual_news_raw else load_manual_news_impl(resolved_news_json_path, news_reference_date),
        sector_context,
    )

    stock_name = load_stock_name(full_symbol) or full_symbol

    payload: dict[str, Any] = {
        "symbol": full_symbol,
        "stock_name": stock_name,
        "pure_symbol": pure_symbol,
        "trade_date": trade_date_text,
        "requested_trade_date": requested_trade_date_text,
        "session_trade_date_resolution": session_trade_date_resolution,
        "news_reference_date": news_reference_date,
        "news_json_path": resolved_news_json_path,
        "news_pipeline_meta": news_agent.get("news_pipeline_meta", {}),
        "trade_calendar_resolution": trade_cal_meta,
        "kline_sync": kline_sync,
        "factor_sync": factor_sync,
        "analysis_time": now.isoformat(timespec="seconds"),
        "time_source": time_source,
        "current_session": scenario_from_now(now),
        "freshness": freshness,
        "intraday_strength": intraday,
        "next_day_bias": next_day,
        "capital_freshness": capital_freshness,
        "market_context": market_context,
        "sector_context": sector_context,
        "financing_context": financing_context,
        "auction_intent": auction_intent,
        "trend_structure": trend_structure,
        "chip_structure": chip_structure,
        "volatility_context": volatility_context,
        "news_sentiment": news_sentiment,
        "narrative_context": narrative_context,
        "fundamental": fundamental,
    }
    payload["checkpoint"] = resolved_checkpoint
    payload["mixed_trade_date_context"] = build_mixed_trade_date_context(
        trade_date_text,
        now,
        freshness,
        kline_sync,
        factor_sync,
    )
    payload["dimension_results"] = {
        "market_context": market_context,
        "sector_context": sector_context,
        "news_sentiment": payload["news_sentiment"],
        "peer_linkage": build_peer_linkage(full_symbol, trade_date_text),
        "stock_structure": next_day,
        "intraday_structure": intraday,
        "auction_intent": auction_intent,
        "capital_chip_tech": capital_freshness,
        "financing_context": financing_context,
        "trend_structure": trend_structure,
        "chip_structure": chip_structure,
        "volatility_context": volatility_context,
        "dragon_tiger": dragon_tiger_result,
        "intraday_linkage": intraday_linkage,
        "fundamental_deep": fundamental_deep,
    }
    payload["context_propagation"] = analyze_context_propagation(payload)
    payload["t_plus_two_bias"] = analyze_t_plus_two_bias(payload)
    payload["final_decision"] = build_final_decision(payload)
    if (
        payload["mixed_trade_date_context"].get("status")
        == "mixed_trade_date_context"
    ):
        _degrade_prediction_bundle_fn(payload["mixed_trade_date_context"], payload)
    payload["validation_tracking"] = build_validation_tracking(payload, now)
    payload["validation_record_path"] = persist_pending_validation(
        payload, payload["checkpoint"]
    )
    daily_row = load_daily_row_impl(full_symbol, trade_date_compact)
    if daily_row is None:
        daily_row = {"close": None}
    payload["current_price"] = safe_float(daily_row.get("close")) if daily_row else None
    payload["portfolio"] = get_position_impl(full_symbol)
    
    # 历史对比
    payload["history_comparison"] = build_history_comparison(payload)

    return payload


def render_markdown(payload: dict) -> str:
    from render import report_renderer
    return report_renderer.render_markdown(payload)


def main() -> int:
    args = parse_args()
    payload = build_payload(
        args.symbol, args.trade_date, args.news_json, args.checkpoint
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
