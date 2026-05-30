from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any, Iterable

_TIME_0915 = time(9, 15)
_TIME_1130 = time(11, 30)
_TIME_1300 = time(13, 0)
_TIME_1500 = time(15, 0)


def scenario_from_now(now: datetime) -> str:
    current = now.time()
    if current < _TIME_0915:
        return "盘前"
    if current <= _TIME_1130:
        return "上午盘中"
    if current < _TIME_1300:
        return "午间休盘"
    if current <= _TIME_1500:
        return "下午盘中"
    return "盘后"


def normalize_trade_date_for_session(
    now: datetime,
    trade_date_text: str,
    checkpoint_arg: str,
    latest_open_trade_date_on_or_before_fn: Any = None,
) -> tuple[str, dict[str, Any]]:
    session = scenario_from_now(now)
    should_use_close_logic = checkpoint_arg == "pre_open" or (
        checkpoint_arg == "auto" and session == "盘前"
    )
    if not should_use_close_logic:
        return trade_date_text, {
            "adjusted": False,
            "reason": "session_kept_requested_trade_date",
        }
    now_text = now.strftime("%Y-%m-%d")
    if trade_date_text != now_text:
        return trade_date_text, {
            "adjusted": False,
            "reason": "requested_trade_date_not_today",
        }
    previous_reference = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    _fn = latest_open_trade_date_on_or_before_fn
    if _fn is None:
        raise RuntimeError(
            "latest_open_trade_date_on_or_before_fn must be provided "
            "when checkpoint adjustment is needed"
        )
    previous_open_trade_date = _fn(previous_reference)
    if not previous_open_trade_date:
        return trade_date_text, {
            "adjusted": False,
            "reason": "previous_open_trade_date_missing",
        }
    return previous_open_trade_date, {
        "adjusted": True,
        "reason": "pre_open_use_previous_close_logic",
        "requested_trade_date": trade_date_text,
        "resolved_trade_date": previous_open_trade_date,
    }


def resolve_checkpoint(now: datetime, trade_date_text: str, checkpoint_arg: str) -> str:
    if checkpoint_arg == "pre_open":
        return "close"
    if checkpoint_arg != "auto":
        return checkpoint_arg
    session = scenario_from_now(now)
    if session == "盘前":
        return "close"
    trade_date_obj = datetime.strptime(trade_date_text, "%Y-%m-%d").date()
    now_date = now.date()
    if now_date > trade_date_obj:
        return "next_close"
    mapping = {
        "上午盘中": "open",
        "午间休盘": "noon",
        "下午盘中": "afternoon",
        "盘后": "close",
    }
    return mapping.get(session, "close")


def parse_date_candidates(values: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if not value:
            continue
        candidate = value[:10]
        try:
            normalized = datetime.strptime(candidate, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            continue
        result.append(normalized)
    return result


def normalize_trade_date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def next_trade_date_compact(
    trade_date_text: str, next_trade_dates_fn: Any = None
) -> str:
    if next_trade_dates_fn is None:
        raise RuntimeError(
            "next_trade_dates_fn must be provided to resolve next trade date"
        )
    dates = next_trade_dates_fn(trade_date_text, count=1)
    return dates[0] if dates else (
        datetime.strptime(trade_date_text, "%Y-%m-%d").date() + timedelta(days=1)
    ).strftime("%Y%m%d")
