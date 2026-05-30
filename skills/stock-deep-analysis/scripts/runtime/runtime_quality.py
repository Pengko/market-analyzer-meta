#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from common import MINUTE_DATA_ROOT

MINUTE_ROOT = MINUTE_DATA_ROOT
from data.config_loader import cfg

HERMES_FETCH_ARTIFACT_ROOT = cfg.paths('temp_dir') / 'hermes-browser-fetch'


def resolve_minute_path(symbol: str, trade_date_text: str) -> Path:
    y, m, d = trade_date_text.split("-")
    # 新结构：分钟数据/YYYY/MM/DD/{symbol}/{granularity}.csv
    new_base = MINUTE_ROOT / y / m / d / symbol
    for suffix in ("1m", "5m", "15m", "30m", "60m"):
        path = new_base / f"{suffix}.csv"
        if path.exists():
            return path
    # 旧结构 fallback（迁移完成后可移除）
    old_flat = MINUTE_ROOT / y / m / d
    for suffix in ("1m", "5m", "15m", "30m", "60m"):
        path = old_flat / f"{symbol}_{suffix}.csv"
        if path.exists():
            return path
    old_base = MINUTE_ROOT / symbol / trade_date_text
    for name in (
        "minute_kline.csv",
        "minute_kline_5m.csv",
        "minute_kline_15m.csv",
        "minute_kline_30m.csv",
        "minute_kline_60m.csv",
    ):
        path = old_base / name
        if path.exists():
            return path
    return new_base / "1m.csv"


def format_intraday_failure_reason(
    path: Path, fetch_meta: dict[str, Any] | None
) -> str:
    base = "分钟线文件存在但无法完成解析" if path.exists() else "本地分钟线暂未落地"
    if not fetch_meta:
        return base
    mode = str(fetch_meta.get("mode") or "").strip()
    if fetch_meta.get("status") == "fetched":
        return f"{base}，已尝试自动补抓但结果仍不可用"
    reason = str(fetch_meta.get("reason") or "").strip()
    if mode and reason:
        return f"{base}，自动补抓失败（{mode}）：{reason}"
    if mode:
        return f"{base}，自动补抓失败（{mode}）"
    if reason:
        return f"{base}，自动补抓失败：{reason}"
    return f"{base}，自动补抓失败"


def minute_rows_in_window(rows: list[Any], start: str, end: str) -> list[Any]:
    start_t = datetime.strptime(start, "%H:%M").time()
    end_t = datetime.strptime(end, "%H:%M").time()
    return [row for row in rows if start_t <= row.dt.time() <= end_t]


def scenario_from_now(now: datetime) -> str:
    current = now.time()
    if current < datetime.strptime("09:15", "%H:%M").time():
        return "盘前"
    if current <= datetime.strptime("11:30", "%H:%M").time():
        return "上午盘中"
    if current < datetime.strptime("13:00", "%H:%M").time():
        return "午间休盘"
    if current <= datetime.strptime("15:00", "%H:%M").time():
        return "下午盘中"
    return "盘后"


def checkpoint_to_session(checkpoint: str | None) -> str | None:
    mapping = {
        "pre_open": "盘前",
        "open": "上午盘中",
        "noon": "午间休盘",
        "afternoon": "下午盘中",
        "close": "盘后",
        "next_close": "盘后",
    }
    if not checkpoint:
        return None
    return mapping.get(str(checkpoint).strip())


def expected_intraday_session(
    now: datetime, trade_date_text: str, checkpoint: str | None = None
) -> str:
    forced = checkpoint_to_session(checkpoint)
    if forced:
        return forced
    trade_day = datetime.strptime(trade_date_text, "%Y-%m-%d").date()
    if now.date() > trade_day:
        return "盘后"
    if now.date() < trade_day:
        return "盘前"
    return scenario_from_now(now)


def validate_intraday_rows(
    rows: list[Any], trade_date_text: str, now: datetime, checkpoint: str | None = None
) -> dict[str, Any]:
    if not rows:
        return {"status": "unavailable", "summary": "分钟线为空，无法参与分时判断"}

    session = expected_intraday_session(now, trade_date_text, checkpoint=checkpoint)
    first_dt = rows[0].dt
    last_dt = rows[-1].dt
    row_count = len(rows)
    has_open = bool(minute_rows_in_window(rows, "09:30", "09:35"))
    has_first_push = bool(minute_rows_in_window(rows, "09:48", "09:56"))
    has_pre_noon = bool(minute_rows_in_window(rows, "11:25", "11:30"))
    has_pm_open = bool(minute_rows_in_window(rows, "13:01", "13:30"))
    has_pm_tail = bool(minute_rows_in_window(rows, "14:30", "15:00"))

    if first_dt.time() > datetime.strptime("09:35", "%H:%M").time():
        return {
            "status": "unavailable",
            "summary": f"分钟线起始时间偏晚（首条 {first_dt.strftime('%H:%M')}），开盘关键窗口缺失",
            "session": session,
        }

    if session == "盘前":
        return {
            "status": "partial_available",
            "summary": "当前仍在盘前，分钟线尚未形成，不参与分时评分",
            "session": session,
        }

    if session == "上午盘中":
        if not has_open:
            return {
                "status": "unavailable",
                "summary": "上午盘中分钟线未覆盖开盘关键窗口，暂不参与分时判断",
                "session": session,
            }
        return {
            "status": "partial_available",
            "summary": f"当前上午盘中，分钟线已覆盖至 {last_dt.strftime('%H:%M')}，可观察盘中过程，午间强度评分需待上午收盘后生成",
            "session": session,
            "row_count": row_count,
        }

    if session == "午间休盘":
        if has_open and has_pre_noon:
            return {
                "status": "available",
                "summary": f"上午分钟线已完整覆盖至 {last_dt.strftime('%H:%M')}，可生成午间强度评分",
                "session": session,
                "row_count": row_count,
            }
        return {
            "status": "partial_available",
            "summary": f"午间分钟线仅覆盖至 {last_dt.strftime('%H:%M')}，上午关键窗口仍不完整",
            "session": session,
        }

    if session == "下午盘中":
        if has_open and has_pre_noon and has_pm_open and has_pm_tail and row_count >= 200:
            return {
                "status": "available",
                "summary": f"当前下午盘中，分钟线已完整覆盖至 {last_dt.strftime('%H:%M')}，可按实时全天结构参与分时评分",
                "session": session,
                "row_count": row_count,
            }
        return {
            "status": "unavailable",
            "summary": f"当前下午盘中，分钟线仅覆盖至 {last_dt.strftime('%H:%M')}，尾盘关键窗口仍不完整",
            "session": session,
        }

    if has_open and has_pre_noon and has_pm_tail and row_count >= 30:
        return {
            "status": "available",
            "summary": f"盘后分钟线已覆盖至 {last_dt.strftime('%H:%M')}，可按全天结构参与分时评分",
            "session": session,
            "row_count": row_count,
        }

    if has_open and has_pre_noon:
        return {
            "status": "partial_available",
            "summary": f"盘后分钟线仅覆盖至 {last_dt.strftime('%H:%M')}，上午结构完整但尾盘覆盖不足",
            "session": session,
            "row_count": row_count,
        }

    return {
        "status": "unavailable",
        "summary": f"分钟线覆盖到 {last_dt.strftime('%H:%M')}，但关键窗口不足，暂不参与分时评分",
        "session": session,
    }


def extract_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    candidates = [raw]
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip()
            if part.startswith("json"):
                candidates.append(part[4:].strip())
            elif part.startswith("{") or part.startswith("["):
                candidates.append(part)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return None
    return None


def _normalize_bar_datetime(bar: dict[str, Any], trade_date_text: str) -> str | None:
    for key in ("datetime", "dt"):
        value = str(bar.get(key) or "").strip()
        if value:
            return value[:16]
    time_text = str(bar.get("time") or "").strip()
    if time_text:
        if len(time_text) == 5:
            return f"{trade_date_text} {time_text}"
        if len(time_text) >= 16:
            return time_text[:16].replace("T", " ")
    return None


def _safe_bar_number(bar: dict[str, Any], *keys: str) -> float:
    for key in keys:
        value = bar.get(key)
        if value in (None, ""):
            continue
        try:
            return float(str(value).strip())
        except ValueError:
            continue
    return 0.0


def persist_browser_minute_payload(
    raw_payload: dict[str, Any], symbol: str, trade_date_text: str
) -> Path | None:
    payload = raw_payload
    if "stdout" in payload:
        nested = extract_json_object(str(payload.get("stdout") or ""))
        if nested:
            payload = nested
    bars = payload.get("bars")
    if not isinstance(bars, list) or not bars:
        return None

    y, m, d = trade_date_text.split("-")
    target = MINUTE_ROOT / y / m / d / symbol / "1m.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "datetime",
                "open",
                "close",
                "high",
                "low",
                "volume",
                "amount",
                "avg",
            ],
        )
        writer.writeheader()
        for item in bars:
            if not isinstance(item, dict):
                continue
            dt_text = _normalize_bar_datetime(item, trade_date_text)
            if not dt_text:
                continue
            open_ = _safe_bar_number(item, "open")
            close = _safe_bar_number(item, "close", "last", "price")
            high = _safe_bar_number(item, "high", "max")
            low = _safe_bar_number(item, "low", "min")
            volume = _safe_bar_number(item, "volume", "vol")
            amount = _safe_bar_number(item, "amount", "turnover")
            avg = _safe_bar_number(item, "avg")
            if not avg:
                avg = round((high + low + close) / 3, 4) if any((high, low, close)) else 0.0
            writer.writerow(
                {
                    "datetime": dt_text,
                    "open": open_,
                    "close": close,
                    "high": high,
                    "low": low,
                    "volume": volume,
                    "amount": amount,
                    "avg": avg,
                }
            )
    return target if target.exists() else None


def minute_fetch_artifact_path(symbol: str, trade_date_text: str) -> Path:
    compact = trade_date_text.replace("-", "")
    safe_symbol = symbol.replace(".", "_")
    return HERMES_FETCH_ARTIFACT_ROOT / "minute" / f"{safe_symbol}_{compact}.json"


def load_partial_minute_snapshot(symbol: str, trade_date_text: str) -> dict[str, Any] | None:
    path = minute_fetch_artifact_path(symbol, trade_date_text)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None

    nested = extract_json_object(str(result.get("stdout") or ""))
    candidates = [nested, result]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("partial") is True and isinstance(candidate.get("day_stats"), dict):
            return {
                "artifact_path": str(path),
                "provider": candidate.get("provider") or candidate.get("source") or "unknown",
                "reason": candidate.get("reason") or "minute_partial_snapshot_only",
                "summary": candidate.get("summary") or "",
                "day_stats": candidate.get("day_stats") or {},
                "attempts": candidate.get("attempts") or [],
            }
    return None


def simplify_browser_fetch_error(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return "浏览器执行器返回失败"
    lowered = raw.lower()
    if "hermes_timeout_after_" in lowered or "local_timeout_after_" in lowered:
        return "浏览器执行器超时"
    if "permission denied" in lowered and "docker" in lowered:
        return "浏览器执行器缺少 Docker 访问权限"
    if "docker.sock" in lowered:
        return "浏览器执行器无法连接 Docker 服务"
    if "timeout" in lowered:
        return "浏览器执行器超时"
    if "remote end closed connection without response" in lowered:
        return "东方财富分钟接口主动断开连接"
    if "empty reply from server" in lowered:
        return "东方财富分钟接口返回空响应"
    if "socket hang up" in lowered:
        return "东方财富分钟接口连接被重置"
    if "nodename nor servname provided" in lowered:
        return "分钟接口网络解析失败"
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    for line in lines:
        if "permission denied" in line.lower():
            return line
    for line in lines:
        if "warning" in line.lower() or "obsolete" in line.lower():
            continue
        return line
    return lines[0] if lines else "浏览器执行器返回失败"
