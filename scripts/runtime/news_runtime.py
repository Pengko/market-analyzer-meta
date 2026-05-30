#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

from common import STOCK_DATA_ROOT
from news_context import default_news_sentiment, normalize_news_sentiment
from analysis.sector_analyzer import load_stock_basic_index

from data.config_loader import cfg

NEWS_DATA_ROOT = cfg.paths("news_data_root")
NEWS_PIPELINE_ROOT = NEWS_DATA_ROOT / "raw" / "news_pipeline"
NEWS_BROWSER_ROOT = NEWS_DATA_ROOT / "raw" / "browser_news"
FETCH_BROWSER_NEWS_SCRIPT = Path(cfg.get("paths", "external", "news_browser") or str(Path.home() / ".openclaw" / "skills" / "custom" / "market-news-intelligence" / "scripts" / "fetch_browser_news.py"))
RUN_NEWS_PIPELINE_SCRIPT = Path(cfg.get("paths", "external", "news_pipeline") or str(Path.home() / ".openclaw" / "skills" / "custom" / "market-news-intelligence" / "scripts" / "run_news_pipeline.py"))
PREPARE_NEWS_CONTEXT_SCRIPT = Path(cfg.get("paths", "external", "news_prepare") or str(Path.home() / ".openclaw" / "skills" / "custom" / "market-news-intelligence" / "scripts" / "prepare_news_context.py"))


NEWS_PIPELINE_SOURCE_TEXT_MAP = {
    "existing_quant_data_news": "已有本地结构化结果",
    "hermes_sync_pipeline": "当天 Hermes 同步抓取",
    "local_sync_pipeline": "当天本地同步抓取",
    "local_fallback_pipeline": "Hermes 失败后回退本地抓取",
    "hermes_fallback_pipeline": "本地失败后回退 Hermes 抓取",
    "latest_valid_cached_news": "最近一次有效结构化结果回退",
}

NEWS_PIPELINE_REASON_TEXT_MAP = {
    "news_capture_returned_no_articles": "未抓到有效文章",
    "news_capture_prepared_but_not_structured": "抓到候选内容但未完成结构化",
    "news_capture_backend_failed": "抓取后端执行失败",
    "news_pipeline_unavailable": "当天消息链未产出有效结果",
}


def load_manual_news(
    news_json_path: str | None, trade_date_text: str | None = None
) -> dict[str, Any]:
    if not news_json_path:
        return default_news_sentiment()
    path = Path(news_json_path).expanduser()
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("news_sentiment"), dict):
        raw = raw["news_sentiment"]
    return normalize_news_sentiment(raw, trade_date_text)


def enrich_news_sentiment(
    news_sentiment: dict[str, Any], sector_context: dict[str, Any] | None = None
) -> dict[str, Any]:
    news = dict(news_sentiment or {})
    if str(news.get("status") or "") != "available":
        return news

    direction = str(news.get("direction") or "").strip()
    level = str(news.get("level") or "").strip()
    impact_role = str(news.get("impact_role") or "").strip()
    impact_on_price = str(news.get("impact_on_price") or "").strip()
    sector = sector_context or {}
    theme_role = str(sector.get("target_theme_role") or "").strip()
    top_theme = str(sector.get("top_theme") or "").strip()

    if not impact_role:
        if direction == "偏多" and level in {"国家级", "板块级"}:
            if theme_role in {"题材龙头", "题材前排"}:
                impact_role = "核心受益前排"
            elif top_theme:
                impact_role = "板块催化映射"
            else:
                impact_role = "题材映射"
        elif direction == "偏多" and level == "个股级":
            impact_role = "个股催化"
        elif direction == "偏空":
            impact_role = "风险扰动"

    if not impact_on_price:
        if direction == "偏多" and level in {"国家级", "板块级"}:
            impact_on_price = "提升板块活跃度，利于前排与辨识度个股获得溢价"
        elif direction == "偏多" and level == "个股级":
            impact_on_price = "更偏个股强化，持续性取决于量能与承接"
        elif direction == "偏空":
            impact_on_price = "压制风险偏好，容易引发分歧或兑现"

    if impact_role:
        news["impact_role"] = impact_role
    if impact_on_price:
        news["impact_on_price"] = impact_on_price
    return news


def _load_stock_name(symbol: str) -> str | None:
    basics = load_stock_basic_index()
    basic = basics.get(symbol) or {}
    return str(basic.get("name") or "").strip() or None


def _read_log_tail(fetch_log_path: Path) -> str:
    if not fetch_log_path.exists():
        return ""
    try:
        return fetch_log_path.read_text(encoding="utf-8", errors="ignore")[-1200:]
    except Exception:
        return ""


def _classify_prepared_news(
    prepared_payload: dict[str, Any] | None,
    raw_payload: dict[str, Any] | None,
    fetch_log_path: Path,
) -> tuple[str, str | None]:
    news = {}
    if isinstance(prepared_payload, dict) and isinstance(
        prepared_payload.get("news_sentiment"), dict
    ):
        news = prepared_payload.get("news_sentiment") or {}

    if news.get("status") == "available" and (
        news.get("summary") or news.get("main_sources")
    ):
        return "generated", None

    articles: list[Any] = []
    if isinstance(raw_payload, dict):
        maybe_articles = raw_payload.get("articles") or raw_payload.get("raw_items") or []
        if isinstance(maybe_articles, list):
            articles = maybe_articles

    log_tail = _read_log_tail(fetch_log_path)
    if any(
        keyword in log_tail
        for keyword in (
            "permission denied while trying to connect to the docker API",
            "Hermes capture failed",
            "TargetClosedError",
            "SIGABRT",
            "EPERM",
        )
    ):
        return "failed", "news_capture_backend_failed"
    if not articles:
        return "empty", "news_capture_returned_no_articles"
    return "pending", "news_capture_prepared_but_not_structured"


def _render_attempt_reason(meta: dict[str, Any]) -> str:
    reason = str((meta or {}).get("reason") or "").strip()
    if reason:
        return NEWS_PIPELINE_REASON_TEXT_MAP.get(reason, reason)
    stderr = str((meta or {}).get("stderr") or "").strip()
    if "TargetClosedError" in stderr:
        return "浏览器启动失败"
    if "permission denied while trying to connect to the docker API" in stderr:
        return "Docker 权限不足"
    if stderr:
        return stderr.splitlines()[-1][:80]
    return "原因未明"


def summarize_news_pipeline_source(meta: dict[str, Any]) -> str:
    source = str((meta or {}).get("source") or "").strip()
    if not source:
        return ""
    text = NEWS_PIPELINE_SOURCE_TEXT_MAP.get(source, source)
    requested = str((meta or {}).get("requested_executor") or "").strip()
    fallback_from = str((meta or {}).get("fallback_from") or "").strip()
    if source == "latest_valid_cached_news":
        path = str((meta or {}).get("path") or "").strip()
        if path:
            parts = path.rsplit("_", 1)
            if len(parts) == 2 and parts[1].endswith(".json"):
                suffix = parts[1][:-5]
                if len(suffix) == 10:
                    text += f"（参考日期 {suffix}）"
    elif fallback_from:
        text += f"（原计划 {fallback_from}）"
    elif requested and source not in {"existing_quant_data_news"}:
        text += f"（原计划 {requested}）"
    return text


def summarize_news_pipeline_attempts(meta: dict[str, Any]) -> str:
    first = (meta or {}).get("first_attempt") or {}
    second = (meta or {}).get("second_attempt") or {}
    parts: list[str] = []
    if first:
        parts.append(
            f"{str(first.get('executor') or '首次尝试')}：{_render_attempt_reason(first)}"
        )
    if second:
        parts.append(
            f"{str(second.get('executor') or '回退尝试')}：{_render_attempt_reason(second)}"
        )
    if parts:
        return "；".join(parts)
    reason = str((meta or {}).get("reason") or "").strip()
    if reason:
        return NEWS_PIPELINE_REASON_TEXT_MAP.get(reason, reason)
    return ""


def enrich_news_pipeline_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(meta or {})
    source_summary = summarize_news_pipeline_source(result)
    attempt_summary = summarize_news_pipeline_attempts(result)
    if source_summary:
        result["source_summary"] = source_summary
    if attempt_summary:
        result["attempt_summary"] = attempt_summary
    return result


def auto_resolve_news_json_path(
    full_symbol: str,
    trade_date_text: str,
    requested_news_json_path: str | None = None,
) -> tuple[str | None, dict[str, Any]]:
    if requested_news_json_path:
        path = str(Path(requested_news_json_path).expanduser())
        return path, {"mode": "manual", "status": "provided", "path": path}

    if not RUN_NEWS_PIPELINE_SCRIPT.exists() or not PREPARE_NEWS_CONTEXT_SCRIPT.exists():
        return None, {
            "mode": "auto",
            "status": "unavailable",
            "reason": "news_pipeline_scripts_missing",
        }

    stock_name = _load_stock_name(full_symbol)
    basics = load_stock_basic_index()
    basic = basics.get(full_symbol) or {}
    industry = str(basic.get("industry") or "").strip()

    def _news_path(root: Path, prefix: str, trade_date: str) -> Path:
        """按 年/月/日 层级构建新闻文件路径"""
        year, month, day = trade_date.split("-")
        return root / year / month / day / f"{prefix}_{full_symbol.split('.')[0]}_{trade_date}.json"

    canonical_raw_output_path = _news_path(NEWS_BROWSER_ROOT, "browser_news", trade_date_text)
    canonical_output_path = _news_path(NEWS_PIPELINE_ROOT, "news_pipeline", trade_date_text)
    fetch_log_path = (
        cfg.paths("temp_dir") / f"stock_news_pipeline_{full_symbol.replace('.', '_')}_{trade_date_text}.log"
    )

    run_tag = uuid.uuid4().hex[:8]
    session_id = f"news-agent:{full_symbol}:{trade_date_text}"
    message_id = f"{session_id}:{run_tag}"

    base_pipeline_cmd = [
        "python3",
        str(RUN_NEWS_PIPELINE_SCRIPT),
        "--symbol",
        full_symbol,
        "--trade-date",
        trade_date_text,
        "--preset",
        "eastmoney",
        "--preset",
        "10jqka_news",
        "--limit",
        "4",
        "--per-page-limit",
        "4",
        "--deep-open-limit",
        "0",
        "--session-id",
        session_id,
        "--message-id",
        message_id,
        "--output",
        str(canonical_output_path),
        "--raw-output",
        str(canonical_raw_output_path),
    ]
    if stock_name:
        base_pipeline_cmd.extend(["--stock-name", stock_name])
    if industry:
        base_pipeline_cmd.extend(["--sector-keyword", industry])

    def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return raw if isinstance(raw, dict) else None

    def _existing_canonical_news() -> tuple[str | None, dict[str, Any]] | None:
        prepared_payload = _load_json_if_exists(canonical_output_path)
        raw_payload = _load_json_if_exists(canonical_raw_output_path)
        if not prepared_payload and not raw_payload:
            return None
        if not prepared_payload and raw_payload:
            try:
                prepared_result = subprocess.run(
                    [
                        "python3",
                        str(PREPARE_NEWS_CONTEXT_SCRIPT),
                        "--news-json",
                        str(canonical_raw_output_path),
                        "--trade-date",
                        trade_date_text,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=cfg.network("timeout_seconds", default=30.0),

                    check=False,
                )
            except Exception as exc:
                return None, {
                    "mode": "auto",
                    "status": "failed",
                    "reason": f"prepare_existing_canonical_news_exec_error: {exc}",
                }
            if prepared_result.returncode == 0:
                try:
                    prepared_payload = json.loads(prepared_result.stdout)
                except Exception:
                    prepared_payload = None
                if isinstance(prepared_payload, dict):
                    canonical_output_path.parent.mkdir(parents=True, exist_ok=True)
                    canonical_output_path.write_text(
                        json.dumps(prepared_payload, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
        prepared_status, prepared_reason = _classify_prepared_news(
            prepared_payload if isinstance(prepared_payload, dict) else None,
            raw_payload,
            fetch_log_path,
        )
        if not prepared_payload and not raw_payload:
            return None
        return str(canonical_output_path if canonical_output_path.exists() else canonical_raw_output_path), {
            "mode": "auto",
            "status": prepared_status,
            "reason": prepared_reason,
            "path": str(canonical_output_path) if canonical_output_path.exists() else None,
            "raw_path": str(canonical_raw_output_path) if canonical_raw_output_path.exists() else None,
            "source": "existing_quant_data_news",
            "stock_name": stock_name,
            "industry": industry or None,
        }

    def _latest_valid_pipeline_for_symbol() -> tuple[str | None, dict[str, Any]] | None:
        pure = full_symbol.split(".")[0]
        candidates = sorted(
            NEWS_PIPELINE_ROOT.rglob(f"news_pipeline_{pure}_*.json"), reverse=True
        )
        for candidate in candidates:
            prepared_payload = _load_json_if_exists(candidate)
            if not prepared_payload:
                continue
            prepared_status, prepared_reason = _classify_prepared_news(
                prepared_payload,
                None,
                fetch_log_path,
            )
            if prepared_status != "generated":
                continue
            return str(candidate), {
                "mode": "auto",
                "status": "generated",
                "reason": None,
                "path": str(candidate),
                "raw_path": None,
                "source": "latest_valid_cached_news",
                "stock_name": stock_name,
                "industry": industry or None,
            }
        return None

    def _run_pipeline(executor: str, timeout: int) -> tuple[str | None, dict[str, Any]]:
        cmd = list(base_pipeline_cmd)
        cmd.extend(["--executor", executor, "--timeout", str(timeout)])
        # 预先创建年/月/日输出目录，确保外部脚本可以直接写入
        canonical_output_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_raw_output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except Exception as exc:
            return None, {
                "mode": "auto",
                "status": "failed",
                "reason": f"news_pipeline_{executor}_exec_error: {exc}",
                "path": str(canonical_output_path),
                "raw_path": str(canonical_raw_output_path),
                "session_id": session_id,
                "message_id": message_id,
            }
        prepared_payload = _load_json_if_exists(canonical_output_path)
        raw_payload = _load_json_if_exists(canonical_raw_output_path)
        prepared_status, prepared_reason = _classify_prepared_news(
            prepared_payload if isinstance(prepared_payload, dict) else None,
            raw_payload,
            fetch_log_path,
        )
        return (
            str(canonical_output_path if canonical_output_path.exists() else canonical_raw_output_path)
            if canonical_output_path.exists() or canonical_raw_output_path.exists()
            else None
        ), {
            "mode": "auto",
            "status": prepared_status,
            "reason": prepared_reason,
            "path": str(canonical_output_path) if canonical_output_path.exists() else None,
            "raw_path": str(canonical_raw_output_path) if canonical_raw_output_path.exists() else None,
            "session_id": session_id,
            "message_id": message_id,
            "executor": executor,
            "returncode": result.returncode,
            "stderr": (result.stderr or "")[-400:],
            "stock_name": stock_name,
            "industry": industry or None,
        }

    existing_canonical = _existing_canonical_news()
    existing_meta: dict[str, Any] | None = None
    if existing_canonical:
        existing_path, existing_meta = existing_canonical
        if existing_meta.get("status") == "generated":
            return existing_path, existing_meta

    requested_executor = os.getenv("MARKET_NEWS_EXECUTOR", "hermes").strip().lower()
    if requested_executor not in {"hermes", "local"}:
        requested_executor = "hermes"

    first_path, first_meta = _run_pipeline(
        requested_executor, 90 if requested_executor == "hermes" else 60
    )
    if first_meta.get("status") == "generated":
        first_meta["source"] = f"{requested_executor}_sync_pipeline"
        return first_path, first_meta

    # 如果设置了跳过fallback（盘中模式），直接返回first结果
    if os.environ.get("MARKET_NEWS_SKIP_FALLBACK") == "1":
        first_meta["skip_fallback"] = True
        return first_path, first_meta

    fallback_executor = "local" if requested_executor == "hermes" else "hermes"
    second_path, second_meta = _run_pipeline(
        fallback_executor, 60 if fallback_executor == "local" else 90
    )
    if second_meta.get("status") == "generated":
        second_meta["source"] = f"{fallback_executor}_fallback_pipeline"
        second_meta["requested_executor"] = requested_executor
        second_meta["fallback_from"] = requested_executor
        return second_path, second_meta

    latest_valid = _latest_valid_pipeline_for_symbol()
    if latest_valid:
        latest_path, latest_meta = latest_valid
        latest_meta["requested_executor"] = requested_executor
        latest_meta["first_attempt"] = first_meta
        latest_meta["second_attempt"] = second_meta
        return latest_path, latest_meta

    latest_path = (
        str(canonical_output_path)
        if canonical_output_path.exists()
        else str(canonical_raw_output_path)
        if canonical_raw_output_path.exists()
        else None
    )
    return latest_path, {
        "mode": "auto",
        "status": second_meta.get("status") or first_meta.get("status") or "failed",
        "reason": second_meta.get("reason") or first_meta.get("reason") or "news_pipeline_unavailable",
        "path": str(canonical_output_path) if canonical_output_path.exists() else None,
        "raw_path": str(canonical_raw_output_path) if canonical_raw_output_path.exists() else None,
        "requested_executor": requested_executor,
        "first_attempt": first_meta,
        "second_attempt": second_meta,
        "existing_result": existing_meta,
        "stock_name": stock_name,
        "industry": industry or None,
    }
