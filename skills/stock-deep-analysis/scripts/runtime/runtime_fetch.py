#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import request
from zoneinfo import ZoneInfo

from common import normalize_symbol
from runtime.runtime_quality import (
    expected_intraday_session,
    format_intraday_failure_reason,
    resolve_minute_path,
    validate_intraday_rows,
)
from signals.core.score_intraday_strength import analyze as analyze_intraday
from signals.core.score_intraday_strength import load_rows as load_minute_rows

from data.config_loader import cfg

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
HERMES_BROWSER_FETCH_SCRIPT = SCRIPT_ROOT / "fetchers" / "hermes_browser_fetch.py"
TMP_DIR = cfg.paths("temp_dir")


def _trim_rows_for_checkpoint(rows: list[Any], checkpoint: str | None) -> list[Any]:
    checkpoint_text = str(checkpoint or "").strip()
    if checkpoint_text in {"", "auto"}:
        return rows
    if checkpoint_text != "noon":
        return rows
    cutoff = datetime.strptime("11:30", "%H:%M").time()
    trimmed = [row for row in rows if row.dt.time() <= cutoff]
    return trimmed or rows


def parse_network_datetime_text(raw: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00"), text.replace(" ", "T")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo:
                return dt.astimezone(ZoneInfo("Asia/Shanghai"))
            return dt.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        except ValueError:
            continue
    return None


def fetch_china_network_time() -> datetime | None:
    endpoints = [
        ("https://worldtimeapi.org/api/timezone/Asia/Shanghai", "datetime"),
        ("https://timeapi.io/api/Time/current/zone?timeZone=Asia/Shanghai", "dateTime"),
    ]
    _net_timeout = cfg.network("timeout_seconds", default=3.0)
    for url, key in endpoints:
        try:
            with request.urlopen(url, timeout=_net_timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="ignore"))

            value = payload.get(key) if isinstance(payload, dict) else None
            dt = parse_network_datetime_text(str(value or ""))
            if dt:
                return dt
        except Exception:
            continue
    return None


def resolve_now_china() -> tuple[datetime, str]:
    network_now = fetch_china_network_time()
    if network_now:
        return network_now, "网络时间（Asia/Shanghai）"
    return datetime.now(ZoneInfo("Asia/Shanghai")), "本地时间（Asia/Shanghai）"


def auto_fetch_minute_via_browser(
    symbol: str, trade_date_text: str
) -> dict[str, Any] | None:
    if not HERMES_BROWSER_FETCH_SCRIPT.exists():
        return {
            "status": "fetch_failed",
            "mode": "浏览器分钟补抓",
            "reason": "浏览器任务脚本不存在",
        }
    compact = trade_date_text.replace("-", "")
    _pure_symbol, full_symbol = normalize_symbol(symbol)
    log_path = TMP_DIR / f"stock_minute_fetch_{symbol}_{compact}.log"
    session_id = f"stock-agent:{symbol}:{compact}"
    _browser_timeout = cfg.network("browser", "timeout_ms", default=30000) // 1000
    command = [
        "python3",
        str(HERMES_BROWSER_FETCH_SCRIPT),
        "--task-kind",
        "minute",
        "--executor",
        "auto",
        "--symbol",
        full_symbol,
        "--trade-date",
        compact,
        "--agent",
        "stock-agent",
        "--session-id",
        session_id,
        "--timeout",
        str(_browser_timeout),
    ]

    try:
        running = subprocess.run(
            ["pgrep", "-af", session_id],
            capture_output=True,
            text=True,
            timeout=cfg.network('pgrep_timeout', default=5),
            check=False,
        )
    except Exception:
        running = None

    if running and running.returncode == 0 and (running.stdout or "").strip():
        return {
            "status": "pending_running",
            "mode": "浏览器分钟补抓",
            "reason": "minute_browser_fetch_still_running",
            "log_path": str(log_path),
            "session_id": session_id,
        }

    try:
        log_handle = log_path.open("a", encoding="utf-8")
        subprocess.Popen(
            command,
            stdout=log_handle,
            stderr=log_handle,
            text=True,
            start_new_session=True,
        )
    except Exception as exc:
        return {
            "status": "fetch_failed",
            "mode": "浏览器分钟补抓",
            "reason": f"{exc.__class__.__name__}：浏览器执行器不可用",
            "log_path": str(log_path),
            "session_id": session_id,
        }

    return {
        "status": "pending_started",
        "mode": "浏览器分钟补抓",
        "reason": "minute_browser_fetch_started_in_background",
        "log_path": str(log_path),
        "session_id": session_id,
        "artifact_path": str(TMP_DIR / f"hermes-browser-fetch/minute/{symbol.replace('.', '_')}_{compact}.json"),

    }


def auto_fetch_minute_via_infoway(
    symbol: str, trade_date_text: str
) -> dict[str, Any] | None:
    """
    [RESERVED] 通过 Infoway REST API 获取历史分钟数据
    
    当前状态: 未激活。Infoway 主要用于盘中实时监控（WebSocket推送），
    历史分钟线主源为腾讯API。此函数保留以便未来开发实时盯盘功能时使用。
    """
    import os
    import sys
    
    api_key = os.getenv("INFOWAY_API_KEY")
    if not api_key:
        return {
            "status": "fetch_failed",
            "mode": "Infoway分钟补抓",
            "reason": "INFOWAY_API_KEY 未设置",
        }
    
    _pure_symbol, full_symbol = normalize_symbol(symbol)
    
    # 尝试使用 REST API 获取
    try:
        sys.path.insert(0, str(SCRIPT_ROOT / "fetchers"))
        from infoway_rest_client import get_kline_today
        from infoway_minute_writer import persist_infoway_bars
        
        bars = get_kline_today(full_symbol)
        
        if bars and len(bars) > 0:
            # 持久化数据
            persist_infoway_bars(full_symbol, bars, trade_date_text)
            
            return {
                "status": "success",
                "mode": "Infoway REST",
                "bars_count": len(bars),
            }
        else:
            return {
                "status": "fetch_failed",
                "mode": "Infoway分钟补抓",
                "reason": "未返回数据",
            }
        
    except Exception as e:
        return {
            "status": "fetch_failed",
            "mode": "Infoway分钟补抓",
            "reason": f"REST API 调用失败: {e}",
        }


def _fetch_minute_via_unified_entry(
    symbol: str, trade_date_text: str
) -> dict[str, Any] | None:
    """通过 fetch_minute_data.py 统一入口获取分钟线（内含腾讯 API fallback）。"""
    fetcher_script = SCRIPT_ROOT / "fetchers" / "fetch_minute_data.py"
    if not fetcher_script.exists():
        return None
    compact = trade_date_text.replace("-", "")
    cmd = [
        sys.executable,
        str(fetcher_script),
        "--symbol", symbol,
        "--trade-date", compact,
        "--timeout", "20",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        raw = proc.stdout.strip()
        if not raw and proc.returncode != 0:
            return {
                "status": "fetch_failed",
                "mode": "unified_fetcher",
                "reason": (proc.stderr or "").strip()[:300],
            }
        payload = json.loads(raw)
        if payload.get("status") == "success":
            return {
                "status": "success",
                "mode": payload.get("source", "unified_fetcher"),
                "count": payload.get("count"),
                "filename": payload.get("filename"),
            }
        return {
            "status": "fetch_failed",
            "mode": payload.get("source", "unified_fetcher"),
            "reason": payload.get("message", "unified_fetcher_failed"),
        }
    except subprocess.TimeoutExpired:
        return {"status": "fetch_failed", "mode": "unified_fetcher", "reason": "timeout"}
    except Exception as exc:
        return {"status": "fetch_failed", "mode": "unified_fetcher", "reason": str(exc)[:200]}


def auto_fetch_minute_data(
    symbol: str, trade_date_text: str, now: datetime
) -> dict[str, Any] | None:
    existing = resolve_minute_path(symbol, trade_date_text)
    session = expected_intraday_session(now, trade_date_text, checkpoint=None)
    force_refresh = now.date().strftime("%Y-%m-%d") == trade_date_text and session != "盘后"
    if existing.exists() and not force_refresh:
        return None
    
    # 1) 浏览器补抓（Hermes 执行层）
    browser_meta = auto_fetch_minute_via_browser(symbol, trade_date_text)
    browser_ok = browser_meta and browser_meta.get("status") == "success"

    if browser_ok:
        result = browser_meta
    else:
        # 2) 浏览器失败 → 统一入口（Eastmoney Node + 腾讯 API fallback）
        unified_meta = _fetch_minute_via_unified_entry(symbol, trade_date_text)
        unified_ok = unified_meta and unified_meta.get("status") == "success"
        if unified_ok:
            result = unified_meta
        else:
            # 合并两个失败原因供排查
            result = unified_meta or browser_meta
            if result and browser_meta and browser_meta.get("reason"):
                result["browser_reason"] = browser_meta.get("reason")
    
    if result and force_refresh:
        result["force_refresh"] = True
        result["reason"] = "current_trade_day_realtime_refresh"
    
    return result


def safe_intraday(
    symbol: str,
    trade_date_text: str,
    now: datetime | None = None,
    checkpoint: str | None = None,
) -> dict[str, Any]:
    resolved_now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    path = resolve_minute_path(symbol, trade_date_text)
    fetch_meta = None
    if not path.exists():
        fetch_meta = auto_fetch_minute_data(symbol, trade_date_text, resolved_now)
        path = resolve_minute_path(symbol, trade_date_text)
    try:
        rows = load_minute_rows(path)
        quality = validate_intraday_rows(
            rows, trade_date_text, resolved_now, checkpoint=checkpoint
        )
        if quality.get("status") == "partial_available":
            payload = {
                "status": "partial_available",
                "reason": quality.get("summary") or "当前时段分钟线尚未完整，暂不生成分时评分",
                "quality": quality,
            }
            if fetch_meta:
                payload["fetch_meta"] = fetch_meta
            return payload
        if quality.get("status") != "available":
            payload = {
                "status": "unavailable",
                "reason": quality.get("summary") or "分钟线关键窗口不足，暂不参与分时评分",
                "quality": quality,
            }
            if fetch_meta:
                payload["fetch_meta"] = fetch_meta
            return payload
        analysis_rows = _trim_rows_for_checkpoint(rows, checkpoint)
        payload = {
            "status": "available",
            "result": analyze_intraday(analysis_rows, path),
            "quality": quality,
        }
        if fetch_meta:
            payload["fetch_meta"] = fetch_meta
        return payload
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        return {
            "status": "unavailable",
            "reason": format_intraday_failure_reason(path, fetch_meta),
            "raw_reason": str(exc),
            "fetch_meta": fetch_meta,
        }


# ── 大盘/板块分钟采集 ────────────────────────────────────

_TENCENT_MINUTE_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
    "?_var=min_data_{code}&code={code}&day={date}"
)


def fetch_index_minutes(index_code: str, trade_date_text: str) -> list[dict] | None:
    """通过腾讯分钟K线 API 获取指数分钟级数据"""
    url = _TENCENT_MINUTE_URL.format(code=index_code, date=trade_date_text)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
    except Exception:
        return None
    var_name = f"min_data_{index_code}"
    if f"{var_name}=" not in text:
        return None
    try:
        json_str = text.split("=", 1)[1].rstrip(";")
        data = json.loads(json_str)
    except Exception:
        return None
    if "data" not in data or index_code not in data["data"]:
        return None
    raw = data["data"][index_code].get("data", {}).get("data", [])
    if not raw:
        return None
    parsed = []
    for line in raw:
        parts = line.split()
        if len(parts) >= 4:
            trade_time = f"{trade_date_text} {parts[0]}"
            parsed.append({
                "dt": trade_time,
                "price": float(parts[1]),
                "volume": int(parts[2]),
                "amount": float(parts[3]),
                "open": None,
                "close": float(parts[1]),
            })
    return parsed if parsed else None


def fetch_sector_minutes(sector_code: str, trade_date_text: str) -> list[dict] | None:
    """通过腾讯分钟K线 API 获取板块指数分钟级数据"""
    return fetch_index_minutes(sector_code, trade_date_text)


def fetch_index_and_sector_minutes(
    index_codes: list[str], sector_code: str | None, trade_date_text: str
) -> dict:
    """同时获取大盘和板块分钟数据"""
    result: dict[str, Any] = {"indexes": {}, "sector": None}
    for code in index_codes:
        data = fetch_index_minutes(code, trade_date_text)
        if data:
            result["indexes"][code] = data
    if sector_code:
        result["sector"] = fetch_sector_minutes(sector_code, trade_date_text)
    return result


def resolve_sector_code(concept_name: str | None) -> str | None:
    """从配置映射表查概念名→板块指数代码"""
    if not concept_name:
        return None
    cfg_path = SCRIPT_ROOT / "config" / "sector_index_codes.json"
    if not cfg_path.exists():
        return None
    try:
        mapping = json.loads(cfg_path.read_text(encoding="utf-8"))
        # 先查 sectors 表
        code = mapping.get("sectors", {}).get(concept_name)
        if code:
            return code
        # 再查 indexes 表（概念名可能匹配大盘指数）
        return mapping.get("indexes", {}).get(concept_name)
    except Exception:
        return None
