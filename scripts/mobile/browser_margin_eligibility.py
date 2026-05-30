#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import parse, request

from zoneinfo import ZoneInfo
from playwright.sync_api import sync_playwright

from common import STOCK_DATA_ROOT, normalize_symbol
from data.config_loader import cfg


RESULT_DIR = STOCK_DATA_ROOT / "margin_eligibility_browser"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="通过浏览器识别股票是否为融资融券标的（启发式）")
    parser.add_argument("--symbol", required=True, help="如 002806 或 002806.SZ")
    parser.add_argument("--headless", action="store_true", help="使用无头模式")
    parser.add_argument("--timeout-ms", type=int, default=cfg.network("browser", "timeout_ms", default=30000), help="页面加载超时，默认 30000")
    parser.add_argument("--wait-ms", type=int, default=cfg.network("browser", "wait_ms", default=3500), help="页面加载后额外等待，默认 3500")
    parser.add_argument("--refresh", action="store_true", help="忽略当日缓存，强制重新抓取")
    parser.add_argument("--format", choices=("json",), default="json")
    return parser.parse_args()


def quote_url(full_symbol: str) -> str:
    pure, _ = normalize_symbol(full_symbol)
    if full_symbol.endswith(".SH"):
        return f"https://quote.eastmoney.com/sh{pure}.html"
    return f"https://quote.eastmoney.com/sz{pure}.html"


def today_text() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")


def cache_path(full_symbol: str, day: str) -> Path:
    return RESULT_DIR / full_symbol / f"{day}.json"


def extract_signals(page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
            const text = (document.body && document.body.innerText ? document.body.innerText : "").replace(/\\s+/g, " ");
            const hasRzrq = /融资融券/.test(text);
            const hasRzye = /融资余额/.test(text);
            const hasRqye = /融券余额/.test(text);
            const hasRzmre = /融资买入额/.test(text);
            const hasExplicitNon = /不是融资融券标的|非融资融券标的|暂无融资融券数据|不属于融资融券/.test(text);
            return {
                hasRzrq, hasRzye, hasRqye, hasRzmre, hasExplicitNon,
                title: document.title || "",
                snippet: text.slice(0, 400)
            };
        }"""
    )


def infer_eligibility(signals: dict[str, Any]) -> tuple[str, str]:
    if signals.get("hasExplicitNon"):
        return "non_margin", "页面存在明确非融资融券标的提示"
    has_rzrq = bool(signals.get("hasRzrq"))
    has_metrics = bool(signals.get("hasRzye")) or bool(signals.get("hasRqye")) or bool(signals.get("hasRzmre"))
    if has_rzrq and has_metrics:
        return "margin", "页面存在融资融券栏目与融资/融券指标"
    if has_rzrq:
        return "unknown", "页面存在融资融券关键词，但未识别到明确指标"
    return "unknown", "页面未识别到融资融券关键信号"


def query_eastmoney_rzrq_api(pure_symbol: str) -> dict[str, Any]:
    base = "https://datacenter-web.eastmoney.com/api/data/v1/get"
    params = {
        "reportName": "RPTA_WEB_RZRQ_GGMX",
        "columns": "ALL",
        "source": "WEB",
        "sortColumns": "DATE",
        "sortTypes": "-1",
        "pageNumber": "1",
        "pageSize": "1",
        "filter": f'(SCODE="{pure_symbol}")',
    }
    url = f"{base}?{parse.urlencode(params)}"
    try:
        with request.urlopen(url, timeout=cfg.network('timeout_seconds', default=10.0)) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        return {"status": "error", "reason": str(exc), "url": url}

    success = bool(raw.get("success"))
    result = raw.get("result") or {}
    count = result.get("count")
    data = result.get("data") or []
    has_data = success and isinstance(count, int) and count > 0 and isinstance(data, list) and len(data) > 0
    latest = data[0].get("DATE") if has_data else None
    return {
        "status": "ok",
        "url": url,
        "success": success,
        "count": count,
        "has_data": has_data,
        "latest_date": latest,
        "message": raw.get("message"),
        "code": raw.get("code"),
    }


def merge_judgment(signals: dict[str, Any], page_eligibility: str, page_reason: str, api_signal: dict[str, Any]) -> tuple[str, str]:
    if api_signal.get("status") == "ok":
        if api_signal.get("has_data") is True:
            return "margin", "东财融资接口返回该股有明细数据"
        if api_signal.get("success") is False:
            return "non_margin", "东财融资接口未命中该股明细（更偏非融资标的）"
        if api_signal.get("has_data") is False and api_signal.get("count") == 0:
            return "non_margin", "东财融资接口返回该股明细为空（更偏非融资标的）"

    # API unavailable or inconclusive, fallback to page signal.
    return page_eligibility, page_reason


def detect(symbol: str, headless: bool, timeout_ms: int, wait_ms: int) -> dict[str, Any]:
    pure, full_symbol = normalize_symbol(symbol)
    url = quote_url(full_symbol)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_timeout(wait_ms)
            signals = extract_signals(page)
            page_eligibility, page_reason = infer_eligibility(signals)
            api_signal = query_eastmoney_rzrq_api(pure)
            eligibility, reason = merge_judgment(signals, page_eligibility, page_reason, api_signal)
            return {
                "status": "ok",
                "symbol": full_symbol,
                "pure_symbol": pure,
                "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                "source": "browser_eastmoney_quote",
                "url": url,
                "eligibility": eligibility,  # margin | non_margin | unknown
                "reason": reason,
                "page_judgment": {
                    "eligibility": page_eligibility,
                    "reason": page_reason,
                },
                "api_judgment": api_signal,
                "signals": signals,
            }
        finally:
            browser.close()


def main() -> None:
    args = parse_args()
    _, full_symbol = normalize_symbol(args.symbol)
    day = today_text()
    output = cache_path(full_symbol, day)
    output.parent.mkdir(parents=True, exist_ok=True)

    if output.exists() and not args.refresh:
        data = json.loads(output.read_text(encoding="utf-8"))
    else:
        data = detect(args.symbol, args.headless, args.timeout_ms, args.wait_ms)
        output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
