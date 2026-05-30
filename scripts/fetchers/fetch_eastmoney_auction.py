#!/usr/bin/env python3
"""
通过浏览器轮询东方财富 quote_api，抓取早盘/尾盘集合竞价快照并汇总。

用法示例：
  python3 fetch_eastmoney_auction.py --type open --symbol 002639
  python3 fetch_eastmoney_auction.py --type close --symbol 002639 --trade-date 2026-04-08
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from common import STOCK_DATA_ROOT, normalize_symbol
from data.config_loader import cfg

DATA_ROOT = STOCK_DATA_ROOT
TYPE_LABEL = {"open": "开盘集合竞价", "close": "尾盘集合竞价"}
TYPE_DIR = {"open": "stk_auction_o", "close": "stk_auction_c"}
FILE_PREFIX = {"open": "stk_auction_o", "close": "stk_auction_c"}
FIELDS = ",".join(
    [
        "f19",
        "f39",
        "f43",
        "f44",
        "f45",
        "f46",
        "f47",
        "f48",
        "f49",
        "f50",
        "f57",
        "f58",
        "f59",
        "f60",
        "f71",
        "f84",
        "f85",
        "f86",
        "f152",
        "f161",
        "f168",
        "f169",
        "f170",
        "f171",
        "f600",
        "f601",
    ]
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取东方财富竞价数据快照")
    parser.add_argument("--type", choices=("open", "close"), required=True)
    parser.add_argument("--symbol", required=True, help="6位股票代码，如 002639")
    parser.add_argument(
        "--trade-date",
        help="交易日期，格式 YYYY-MM-DD；默认使用 Asia/Shanghai 当天日期字符串",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=cfg.fetcher("eastmoney_auction", "samples", default=8),
        help="采样次数，默认 8",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=cfg.fetcher("eastmoney_auction", "interval_seconds", default=2.0),
        help="采样间隔秒数，默认 2.0",
    )
    return parser.parse_args()


def symbol_to_secid(symbol: str) -> str:
    return f"1.{symbol}" if symbol.startswith("6") else f"0.{symbol}"


def resolve_trade_date(value: str | None) -> str:
    if value:
        return value
    return datetime.now().astimezone().strftime("%Y-%m-%d")


def build_expected_window(auction_type: str, trade_date: str) -> tuple[int, int]:
    if auction_type == "open":
        start_text = f"{trade_date}T09:15:00+08:00"
        end_text = f"{trade_date}T09:25:59+08:00"
    else:
        start_text = f"{trade_date}T14:57:00+08:00"
        end_text = f"{trade_date}T15:00:59+08:00"
    start_ts = int(datetime.fromisoformat(start_text).timestamp())
    end_ts = int(datetime.fromisoformat(end_text).timestamp())
    return start_ts, end_ts


def resolve_output_path(auction_type: str, full_symbol: str, trade_date: str) -> Path:
    compact = trade_date.replace("-", "")
    base_dir = DATA_ROOT / TYPE_DIR[auction_type]
    filename = f"{FILE_PREFIX[auction_type]}_{full_symbol}.csv"
    direct = base_dir / filename
    if direct.exists():
        direct.parent.mkdir(parents=True, exist_ok=True)
        return direct
    target = base_dir / compact[:4] / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def to_num(value):
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except Exception:
        return None


def scale_num(value, decimals):
    raw = to_num(value)
    if raw is None:
        return None
    try:
        factor = 10 ** int(decimals)
    except Exception:
        factor = 1
    if factor == 0:
        factor = 1
    return raw / factor


def decode_payload(raw: str):
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass

    left = text.find("(")
    right = text.rfind(")")
    if left != -1 and right > left:
        inner = text[left + 1 : right].strip()
        try:
            return json.loads(inner)
        except Exception:
            return None
    return None


def fetch_samples(secid: str, samples: int, interval: float) -> list[dict]:
    quote_url = f"https://quote.eastmoney.com/q/{secid}.html"
    api_url = (
        "https://push2.eastmoney.com/api/qt/stock/get"
        f"?fields={FIELDS}&invt=2&fltt=1&dect=1&secid={secid}"
        "&ut=fa5fd1943c7b386f172d6893dbfba10b&wbp2u=%7C0%7C0%7C0%7Cweb"
    )

    snapshots: list[dict] = []
    with sync_playwright() as p:
        cdp_url = os.environ.get("PLAYWRIGHT_CDP_URL", "http://127.0.0.1:9222")
        using_cdp = False
        browser = None
        page = None
        context = None

        try:
            browser = p.chromium.connect_over_cdp(cdp_url)
            using_cdp = True
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
        except Exception:
            browser = p.chromium.launch(channel="chrome", headless=True)
            context = browser.new_context()
            page = context.new_page()

        captured_payloads: list[str] = []

        def handle_response(response):
            try:
                url = response.url
                if "/api/qt/stock/get" not in url:
                    return
                if secid not in url and "quotedelaytip0" not in url:
                    return
                text = response.text()
                if text:
                    captured_payloads.append(text)
            except Exception:
                return

        page.on("response", handle_response)
        page.goto(quote_url, wait_until="domcontentloaded", timeout=cfg.network('browser', 'timeout_ms', default=60000))
        page.wait_for_timeout(2500)

        request_context = page.context.request
        page_fetch_js = """
async ({ apiUrl }) => {
  const res = await fetch(apiUrl, {
    credentials: "include",
    headers: {
      "Accept": "application/json,text/plain,*/*"
    }
  });
  return await res.text();
}
"""
        page_ajax_jsonp_js = """
async ({ apiUrl, fields, secid }) => {
  if (!window.$ || typeof window.$.ajax !== "function") {
    throw new Error("jquery ajax unavailable");
  }
  return await new Promise((resolve, reject) => {
    window.$.ajax({
      url: apiUrl,
      type: "GET",
      dataType: "jsonp",
      jsonp: "cb",
      timeout: 10000,
      data: {
        fields,
        invt: 2,
        fltt: 1,
        dect: 1,
        secid,
        ut: "fa5fd1943c7b386f172d6893dbfba10b",
        wbp2u: "|0|0|0|web"
      },
      success: (payload) => resolve(JSON.stringify(payload)),
      error: (_xhr, status, err) => reject(new Error(String(err || status || "ajax jsonp failed")))
    });
  });
}
"""
        page_jsonp_js = """
async ({ apiUrl }) => {
  return await new Promise((resolve, reject) => {
    const cbName = "__em_cb_" + Date.now() + "_" + Math.floor(Math.random() * 100000);
    const joiner = apiUrl.includes("?") ? "&" : "?";
    const script = document.createElement("script");
    const timer = setTimeout(() => {
      try { delete window[cbName]; } catch (e) {}
      try { script.remove(); } catch (e) {}
      reject(new Error("jsonp timeout"));
    }, 10000);
    window[cbName] = (payload) => {
      clearTimeout(timer);
      try { delete window[cbName]; } catch (e) {}
      try { script.remove(); } catch (e) {}
      resolve(JSON.stringify(payload));
    };
    script.onerror = () => {
      clearTimeout(timer);
      try { delete window[cbName]; } catch (e) {}
      try { script.remove(); } catch (e) {}
      reject(new Error("jsonp load error"));
    };
    script.src = apiUrl + joiner + "cb=" + cbName;
    document.head.appendChild(script);
  });
}
"""

        for index in range(samples):
            raw = None
            errors = []

            captured_start = len(captured_payloads)
            if index > 0:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=cfg.network('browser', 'timeout_ms', default=60000))
                    page.wait_for_timeout(2000)
                except Exception as exc:
                    errors.append(f"page.reload: {exc}")

            new_payloads = captured_payloads[captured_start:]
            if not new_payloads and index == 0:
                new_payloads = captured_payloads
            for candidate in reversed(new_payloads):
                payload = decode_payload(candidate)
                if payload and (payload.get("data") or {}).get("f57"):
                    raw = json.dumps(payload, ensure_ascii=False)
                    break

            for _attempt in range(3):
                if raw:
                    break
                try:
                    response = request_context.get(
                        api_url,
                        headers={
                            "Referer": quote_url,
                            "User-Agent": (
                                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                            ),
                            "Accept": "application/json,text/plain,*/*",
                        },
                        fail_on_status_code=False,
                    )
                    raw = response.text()
                    if raw:
                        break
                except Exception as exc:
                    errors.append(f"request_context.get: {exc}")
                    page.wait_for_timeout(400)

            if not raw:
                try:
                    raw = page.evaluate(page_fetch_js, {"apiUrl": api_url})
                except Exception as exc:
                    errors.append(f"page.fetch: {exc}")

            if not raw:
                try:
                    raw = page.evaluate(
                        page_ajax_jsonp_js,
                        {"apiUrl": "https://push2.eastmoney.com/api/qt/stock/get", "fields": FIELDS, "secid": secid},
                    )
                except Exception as exc:
                    errors.append(f"jquery.jsonp: {exc}")

            if not raw:
                try:
                    raw = page.evaluate(page_jsonp_js, {"apiUrl": api_url})
                except Exception as exc:
                    errors.append(f"script.jsonp: {exc}")

            if not raw:
                raise RuntimeError("failed to fetch quote snapshot: " + " | ".join(errors))

            payload = decode_payload(raw)
            if not payload:
                raise RuntimeError("failed to decode quote payload")
            data = payload.get("data") or {}
            price_decimals = int(data.get("f59") or 2)
            pct_decimals = int(data.get("f152") or 2)
            snapshots.append(
                {
                    "captured_at": datetime.now().astimezone().isoformat(),
                    "code": data.get("f57"),
                    "name": data.get("f58"),
                    "price": scale_num(data.get("f43"), price_decimals),
                    "high": scale_num(data.get("f44"), price_decimals),
                    "low": scale_num(data.get("f45"), price_decimals),
                    "open": scale_num(data.get("f46"), price_decimals),
                    "volume": to_num(data.get("f47")),
                    "amount": to_num(data.get("f48")),
                    "buy1_price": scale_num(data.get("f19"), price_decimals),
                    "sell1_price": scale_num(data.get("f39"), price_decimals),
                    "buy_queue": to_num(data.get("f49")),
                    "avg_price": scale_num(data.get("f71"), price_decimals),
                    "prev_close": scale_num(data.get("f60"), price_decimals),
                    "change": scale_num(data.get("f169"), price_decimals),
                    "change_pct": scale_num(data.get("f170"), pct_decimals),
                    "amplitude_pct": scale_num(data.get("f171"), pct_decimals),
                    "turnover_pct": scale_num(data.get("f168"), pct_decimals),
                    "volume_ratio": to_num(data.get("f50")),
                    "trade_ts": data.get("f86"),
                    "trade_status": data.get("f600"),
                    "trade_status_text": data.get("f601"),
                }
            )
            page.wait_for_timeout(int(interval * 1000))

        if page is not None:
            try:
                page.close()
            except Exception:
                pass
        if context is not None and not using_cdp:
            try:
                context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
    return snapshots


def build_summary(auction_type: str, symbol: str, trade_date: str, snapshots: list[dict]) -> dict:
    valid_prices = [s["price"] for s in snapshots if s["price"] is not None]
    valid_highs = [s["high"] for s in snapshots if s["high"] is not None]
    valid_lows = [s["low"] for s in snapshots if s["low"] is not None]
    last = snapshots[-1] if snapshots else {}
    first = snapshots[0] if snapshots else {}
    window_start_ts, window_end_ts = build_expected_window(auction_type, trade_date)
    trade_timestamps = [int(s["trade_ts"]) for s in snapshots if s.get("trade_ts") not in (None, "")]
    invalid_trade_timestamps = [
        ts for ts in trade_timestamps if ts < window_start_ts or ts > window_end_ts
    ]
    is_time_window_valid = bool(trade_timestamps) and not invalid_trade_timestamps

    return {
        "auction_type": auction_type,
        "auction_type_label": TYPE_LABEL[auction_type],
        "symbol": symbol,
        "trade_date": trade_date,
        "name": last.get("name") or first.get("name"),
        "samples": len(snapshots),
        "first_capture_at": first.get("captured_at"),
        "last_capture_at": last.get("captured_at"),
        "open": first.get("open"),
        "close": last.get("price"),
        "high": max(valid_highs) if valid_highs else None,
        "low": min(valid_lows) if valid_lows else None,
        "last_price": last.get("price"),
        "last_avg_price": last.get("avg_price"),
        "last_volume": last.get("volume"),
        "last_amount": last.get("amount"),
        "last_buy1_price": last.get("buy1_price"),
        "last_sell1_price": last.get("sell1_price"),
        "last_buy_queue": last.get("buy_queue"),
        "last_change": last.get("change"),
        "last_change_pct": last.get("change_pct"),
        "last_trade_status": last.get("trade_status"),
        "last_trade_status_text": last.get("trade_status_text"),
        "price_span": (max(valid_prices) - min(valid_prices)) if valid_prices else None,
        "expected_trade_ts_start": window_start_ts,
        "expected_trade_ts_end": window_end_ts,
        "observed_trade_ts_min": min(trade_timestamps) if trade_timestamps else None,
        "observed_trade_ts_max": max(trade_timestamps) if trade_timestamps else None,
        "invalid_trade_timestamps": invalid_trade_timestamps,
        "is_time_window_valid": is_time_window_valid,
    }


def build_csv_row(full_symbol: str, trade_date: str, summary: dict) -> dict[str, str]:
    compact = trade_date.replace("-", "")
    return {
        "ts_code": full_symbol,
        "trade_date": compact,
        "close": "" if summary.get("close") is None else str(summary.get("close")),
        "open": "" if summary.get("open") is None else str(summary.get("open")),
        "high": "" if summary.get("high") is None else str(summary.get("high")),
        "low": "" if summary.get("low") is None else str(summary.get("low")),
        "vol": "" if summary.get("last_volume") is None else str(summary.get("last_volume")),
        "amount": "" if summary.get("last_amount") is None else str(summary.get("last_amount")),
        "vwap": "" if summary.get("last_avg_price") is None else str(summary.get("last_avg_price")),
    }


def upsert_csv_row(path: Path, row: dict[str, str]) -> None:
    fieldnames = ["ts_code", "trade_date", "close", "open", "high", "low", "vol", "amount", "vwap"]
    rows: list[dict[str, str]] = []
    if path.exists():
        with path.open("r", encoding="utf-8", newline="") as f:
            rows = [dict(item) for item in csv.DictReader(f)]
    rows = [item for item in rows if str(item.get("trade_date") or "").strip() != row["trade_date"]]
    rows.append(row)
    rows.sort(key=lambda item: str(item.get("trade_date") or ""), reverse=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    trade_date = resolve_trade_date(args.trade_date)
    secid = symbol_to_secid(args.symbol)
    _, full_symbol = normalize_symbol(args.symbol)
    target_path = resolve_output_path(args.type, full_symbol, trade_date)

    snapshots = fetch_samples(secid, args.samples, args.interval)
    if not snapshots:
        print("message: no snapshots captured")
        return 1

    summary = build_summary(args.type, args.symbol, trade_date, snapshots)
    upsert_csv_row(target_path, build_csv_row(full_symbol, trade_date, summary))

    print(f"type: {TYPE_LABEL[args.type]}")
    print(f"symbol: {full_symbol}")
    print(f"trade_date: {trade_date}")
    print(f"samples: {len(snapshots)}")
    print(
        f"close={summary['close']} high={summary['high']} low={summary['low']} "
        f"amount={summary['last_amount']} avg={summary['last_avg_price']}"
    )
    print(f"file: {target_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
