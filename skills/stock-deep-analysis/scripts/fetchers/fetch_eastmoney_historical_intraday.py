#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import subprocess
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
LEGACY_SCRIPTS_DIR = SCRIPT_ROOT.parent
if str(LEGACY_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(LEGACY_SCRIPTS_DIR))

from common import STOCK_DATA_ROOT, normalize_symbol, normalize_trade_date
from data.config_loader import cfg


DATA_ROOT = STOCK_DATA_ROOT / "分钟数据"
KLT_CHOICES = (5, 15, 30, 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="抓取东方财富历史分钟K线并落地到本地")
    parser.add_argument("--symbol", required=True, help="如 600110 或 600110.SH")
    parser.add_argument("--trade-date", required=True, help="YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--klt", type=int, choices=KLT_CHOICES, default=cfg.fetcher("minute_klt", default=5), help="分钟K粒度，默认 5")
    parser.add_argument("--output", help="输出文件路径；默认写入 分钟数据/YYYY/MM/DD/{symbol}_{klt}m.csv")
    return parser.parse_args()


def secid_from_symbol(symbol: str) -> str:
    pure = symbol.split(".")[0]
    return f"1.{pure}" if pure.startswith("6") else f"0.{pure}"


def build_url(full_symbol: str, trade_date_text: str, klt: int) -> str:
    params = {
        "secid": secid_from_symbol(full_symbol),
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": str(klt),
        "fqt": "1",
        "beg": trade_date_text.replace("-", ""),
        "end": trade_date_text.replace("-", ""),
        "lmt": "1000",
    }
    return f"https://push2his.eastmoney.com/api/qt/stock/kline/get?{urllib.parse.urlencode(params)}"


def build_quote_page_url(full_symbol: str) -> str:
    return f"https://quote.eastmoney.com/q/{secid_from_symbol(full_symbol)}.html"


def fetch_json_by_playwright(page_url: str, api_url: str) -> dict:
    python_script = f"""
import json
from playwright.sync_api import sync_playwright
from data.config_loader import cfg

try:
    from playwright_stealth import Stealth
except Exception:
    Stealth = None

PAGE_URL = {json.dumps(page_url)}
API_URL = {json.dumps(api_url)}

JS = '''
async ({{ apiUrl }}) => {{
  const response = await fetch(apiUrl, {{
    method: "GET",
    credentials: "include",
    headers: {{
      "Accept": "application/json,text/plain,*/*"
    }}
  }});
  return await response.text();
}}
'''

def capture(playwright):
    browser = playwright.chromium.launch(channel="chrome", headless=True)
    page = browser.new_page()
    page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=cfg.network('browser', 'timeout_ms', default=60000))
    page.wait_for_timeout(2500)
    payload = page.evaluate(JS, {{"apiUrl": API_URL}})
    browser.close()
    return payload

if Stealth is not None:
    with Stealth().use_sync(sync_playwright()) as p:
        payload = capture(p)
else:
    with sync_playwright() as p:
        payload = capture(p)

if not payload:
    raise SystemExit("FAILED_TO_FETCH_PLAYWRIGHT")

print(payload)
"""
    out = subprocess.run(
        ["python3", "-c", python_script],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "ALL_PROXY": "",
            "HTTPS_PROXY": "",
            "HTTP_PROXY": "",
            "all_proxy": "",
            "https_proxy": "",
            "http_proxy": "",
        },
    )
    return json.loads(out.stdout)


def fetch_json(url: str, page_url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://quote.eastmoney.com/",
    }
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=cfg.network('timeout_seconds', default=30)) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        try:
            result = subprocess.run(
                [
                    "curl",
                    "-sS",
                    "--http1.1",
                    "--retry",
                    "3",
                    "--retry-delay",
                    "1",
                    "--retry-all-errors",
                    "-H",
                    f"User-Agent: {headers['User-Agent']}",
                    "-H",
                    f"Accept: {headers['Accept']}",
                    "-H",
                    f"Referer: {headers['Referer']}",
                    url,
                ],
                check=True,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "ALL_PROXY": "",
                    "HTTPS_PROXY": "",
                    "HTTP_PROXY": "",
                    "all_proxy": "",
                    "https_proxy": "",
                    "http_proxy": "",
                },
            )
            try:
                return json.loads(result.stdout)
            except Exception:
                return fetch_json_by_playwright(page_url, url)
        except subprocess.CalledProcessError:
            return fetch_json_by_playwright(page_url, url)


def target_path(full_symbol: str, trade_date_text: str, klt: int, output: str | None) -> Path:
    if output:
        return Path(output).expanduser()
    y, m, d = trade_date_text.split("-")
    return DATA_ROOT / y / m / d / full_symbol / f"{klt}m.csv"


def main() -> None:
    args = parse_args()
    pure_symbol, full_symbol = normalize_symbol(args.symbol)
    _, trade_date_text = normalize_trade_date(args.trade_date)
    url = build_url(full_symbol, trade_date_text, args.klt)
    payload = fetch_json(url, build_quote_page_url(full_symbol))
    klines = ((payload.get("data") or {}).get("klines")) or []
    if not klines:
        raise SystemExit(f"no kline rows returned for {full_symbol} {trade_date_text} klt={args.klt}")

    rows: list[dict[str, str | float]] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        dt_text, open_, close, high, low, volume, amount = parts[:7]
        rows.append(
            {
                "datetime": dt_text,
                "open": float(open_),
                "close": float(close),
                "high": float(high),
                "low": float(low),
                "volume": float(volume),
                "amount": float(amount),
                "avg": round((float(high) + float(low) + float(close)) / 3, 4),
            }
        )

    path = target_path(full_symbol, trade_date_text, args.klt, args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["datetime", "open", "close", "high", "low", "volume", "amount", "avg"])
        writer.writeheader()
        writer.writerows(rows)
    print(str(path))


if __name__ == "__main__":
    main()
