"""
主力资金新鲜度、混合时点上下文、事件题材判断等辅助模块。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from data.config_loader import cfg
from time_util import normalize_trade_date_text


EVENT_THEMES: set[str] = set(cfg.decision("event_themes", default=[
    "回购增持再贷款",
    "并购重组",
    "股权转让(并购重组)",
    "融资融券",
    "回购",
    "增持",
]))


def is_event_theme(name: str) -> bool:
    text = (name or "").strip()
    if not text:
        return False
    if text in EVENT_THEMES:
        return True
    return any(
        keyword in text for keyword in ("回购", "增持", "并购", "重组", "融资融券")
    )


def summarize_capital_freshness(next_day: dict) -> dict[str, Any]:
    if next_day.get("status") != "available":
        return {
            "status": "unavailable",
            "label": "当前个股本地数据缺失",
            "summary": next_day.get("reason")
            or "隔夜脚本未生成，无法提取主力资金新鲜度",
            "signals": [],
        }

    result = next_day["result"]
    features = result.get("features", {})
    leaderboard = features.get("leaderboard_context", {}) or {}
    signals = result.get("signals", [])

    positive_hits = [
        signal
        for signal in signals
        if any(
            keyword in signal
            for keyword in (
                "新增主导资金介入",
                "新资金介入",
                "新资金关注",
                "量价",
                "协同较强",
            )
        )
    ]
    negative_hits = [
        signal
        for signal in signals
        if any(
            keyword in signal
            for keyword in ("派发", "兑现", "高位换手分歧", "净卖", "抛压")
        )
    ]

    if positive_hits and not negative_hits:
        label = "偏新资金介入"
    elif positive_hits and negative_hits:
        label = "新老资金换手"
    elif negative_hits:
        label = "偏派发分歧"
    else:
        label = "中性待确认"

    summary_parts: list[str] = []
    if features.get("is_bullish_candle"):
        summary_parts.append("T日为阳线")
    amount_ratio_prev1 = features.get("amount_ratio_vs_prev1")
    turnover_ratio_prev1 = features.get("turnover_ratio_vs_prev1")
    if amount_ratio_prev1 is not None:
        summary_parts.append(f"成交额比前一日 {amount_ratio_prev1:.2f}")
    if turnover_ratio_prev1 is not None:
        summary_parts.append(f"换手比前一日 {turnover_ratio_prev1:.2f}")
    if leaderboard.get("is_listed"):
        summary_parts.append(
            f"龙虎榜净买占比 {leaderboard.get('top_list_net_rate') or 0:.2f}%"
        )
    if not summary_parts:
        summary_parts.append("当前量价与龙虎榜信号不足")

    return {
        "status": "available",
        "label": label,
        "summary": "；".join(summary_parts),
        "signals": positive_hits[:2] + negative_hits[:2],
        "leaderboard_context": leaderboard,
    }


def build_mixed_trade_date_context(
    trade_date_text: str,
    now: datetime,
    freshness: dict[str, Any],
    kline_sync: dict[str, Any] | None,
    factor_sync: dict[str, Any] | None,
    latest_open_trade_date_on_or_before_fn=None,
) -> dict[str, Any]:
    if latest_open_trade_date_on_or_before_fn is None:
        raise RuntimeError(
            "latest_open_trade_date_on_or_before_fn must be provided"
        )
    latest_open_trade_date = latest_open_trade_date_on_or_before_fn(
        now.strftime("%Y-%m-%d")
    )
    is_latest_trade_date = bool(
        latest_open_trade_date and trade_date_text == latest_open_trade_date
    )
    items = freshness.get("items") or {}
    core_dates = {
        "daily": normalize_trade_date_text(
            (items.get("daily") or {}).get("latest_trade_date")
        ),
        "stk_factor_pro": normalize_trade_date_text(
            (factor_sync or {}).get("latest_trade_date")
            or (items.get("stk_factor_pro") or {}).get("latest_trade_date")
        ),
        "moneyflow": normalize_trade_date_text(
            (items.get("moneyflow") or {}).get("latest_trade_date")
        ),
        "cyq_perf": normalize_trade_date_text(
            (items.get("cyq_perf") or {}).get("latest_trade_date")
        ),
        "cyq_chips": normalize_trade_date_text(
            (items.get("cyq_chips") or {}).get("latest_trade_date")
        ),
    }
    core_statuses = {
        name: str((items.get(name) or {}).get("status") or "") for name in core_dates
    }
    if not is_latest_trade_date:
        return {
            "status": "aligned_or_not_latest",
            "is_latest_trade_date": False,
            "target_trade_date": trade_date_text,
            "latest_open_trade_date": latest_open_trade_date,
            "core_dates": core_dates,
            "core_statuses": core_statuses,
            "blocking_items": [],
            "summary": "目标日不是当前最新交易日，不触发混合时点拦截。",
        }

    hard_blocking_fields = {"daily", "stk_factor_pro"}
    blocking_items: list[str] = []
    warning_items: list[str] = []
    for name, latest_date in core_dates.items():
        if latest_date == trade_date_text:
            continue
        if name in hard_blocking_fields:
            blocking_items.append(name)
        else:
            warning_items.append(name)
    if (
        str((kline_sync or {}).get("status") or "") == "browser_fetch_failed"
        and "daily" not in blocking_items
    ):
        blocking_items.append("daily")
    blocking_items = sorted(set(blocking_items))
    warning_items = sorted(set(warning_items))
    if not blocking_items:
        warning_suffix = ""
        if warning_items:
            detail = "；".join(
                f"{name}={core_dates.get(name) or core_statuses.get(name) or '缺失'}"
                for name in warning_items
            )
            warning_suffix = f"；辅助维度仍非当天（{detail}），相关结论降权使用。"
        return {
            "status": "aligned",
            "is_latest_trade_date": True,
            "target_trade_date": trade_date_text,
            "latest_open_trade_date": latest_open_trade_date,
            "core_dates": core_dates,
            "core_statuses": core_statuses,
            "blocking_items": [],
            "warning_items": warning_items,
            "summary": "最新交易日硬核心维度已对齐到当天，可继续完整推演。" + warning_suffix,
        }

    detail = "；".join(
        f"{name}={core_dates.get(name) or core_statuses.get(name) or '缺失'}"
        for name in blocking_items
    )
    return {
        "status": "mixed_trade_date_context",
        "is_latest_trade_date": True,
        "target_trade_date": trade_date_text,
        "latest_open_trade_date": latest_open_trade_date,
        "core_dates": core_dates,
        "core_statuses": core_statuses,
        "blocking_items": blocking_items,
        "warning_items": warning_items,
        "summary": f"当前是最新交易日，但核心维度未全部同步到当天（{detail}），仅允许结构复盘，禁止完整 T+1/T+2/建仓推演。",
    }


def degrade_prediction_bundle(mixed_context: dict[str, Any], payload: dict[str, Any]) -> None:
    summary = mixed_context.get("summary") or "当前最新交易日存在混合时点上下文，已降级。"
    payload["next_day_bias"] = {
        "status": "mixed_trade_date_context",
        "reason": summary,
        "result": None,
    }
    payload["capital_freshness"] = {
        "status": "mixed_trade_date_context",
        "label": "混合时点已降级",
        "summary": summary,
        "signals": [],
    }
    payload["t_plus_two_bias"] = {
        "status": "mixed_trade_date_context",
        "label": "混合时点已降级",
        "score": None,
        "view": summary,
        "signals": [],
    }
    payload["final_decision"] = {
        "status": "mixed_trade_date_context",
        "data_completeness": None,
        "signal_score": None,
        "bullish_dimensions": [],
        "bearish_dimensions": [],
        "conflicts": mixed_context.get("blocking_items") or [],
        "decision": "仅保留结构复盘",
        "reason": summary,
        "preconditions": ["等待 daily / stk_factor_pro 等硬核心维度同步到当天后再做完整推演"],
        "invalidations": ["若继续使用混合日期数据直接推演，则结论无效"],
        "key_levels": {"observe": None, "confirm": None, "invalid": None},
        "news_supporting_sources": [],
    }
