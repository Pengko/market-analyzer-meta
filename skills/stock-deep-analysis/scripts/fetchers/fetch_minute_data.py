#!/usr/bin/env python3
"""
统一分钟线抓取入口。

职责：
1. 接收业务侧统一参数（symbol/trade-date/timeout/max-rounds）。
2. 调用更稳的 Eastmoney Node 抓取链（https -> curl -> Playwright SSE）。
3. 将分钟线落盘到标准结构：分钟数据/YYYY/MM/DD/{symbol}/1m.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 添加脚本目录到路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import STOCK_DATA_ROOT


def get_last_trade_date() -> str:
    """获取上一个交易日（简化版兜底）。"""
    today = datetime.now()
    if today.weekday() == 0:
        return (today - timedelta(days=3)).strftime("%Y-%m-%d")
    return (today - timedelta(days=1)).strftime("%Y-%m-%d")


def normalize_symbol(symbol: str) -> str:
    text = str(symbol or "").strip().upper()
    if "." in text:
        return text
    if text.startswith(("6", "9")):
        return f"{text}.SH"
    return f"{text}.SZ"


def normalize_trade_date(trade_date: str | None) -> str:
    if not trade_date:
        return get_last_trade_date()
    text = str(trade_date).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def output_path(symbol: str, trade_date: str) -> Path:
    y, m, d = trade_date.split("-")
    return STOCK_DATA_ROOT / "分钟数据" / y / m / d / symbol / "1m.csv"


def minute_file_complete(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return False

    times: list[str] = []
    first_text = ""
    last_text = ""
    for idx, row in enumerate(rows):
        dt_text = str(row.get("datetime") or "").strip()
        if dt_text:
            hhmm = dt_text[-5:]
            if idx == 0:
                first_text = dt_text
            last_text = dt_text
        else:
            raw_time = str(row.get("time") or "").strip()
            if len(raw_time) == 4 and raw_time.isdigit():
                hhmm = f"{raw_time[:2]}:{raw_time[2:]}"
            elif len(raw_time) == 5 and raw_time[2] == ":":
                hhmm = raw_time
            else:
                continue
            synthesized = f"{path.parent.parent.parent.name}-{path.parent.parent.name}-{path.parent.name} {hhmm}"
            if idx == 0:
                first_text = synthesized
            last_text = synthesized
        times.append(hhmm)

    if not times:
        return False

    required = {
        "open_window": any("09:30" <= t <= "09:35" for t in times),
        "first_push_window": any("09:48" <= t <= "09:56" for t in times),
        "pre_noon_window": any("11:25" <= t <= "11:30" for t in times),
        "pm_open_window": any("13:01" <= t <= "13:30" for t in times),
        "pm_tail_window": any("14:30" <= t <= "15:00" for t in times),
    }
    return (
        len(times) >= 200
        and first_text.endswith(("09:30", "09:31", "09:32", "09:33", "09:34", "09:35"))
        and last_text[-5:] >= "14:59"
        and all(required.values())
    )


def run_node_fetch(symbol: str, trade_date: str, timeout: int) -> dict[str, Any]:
    script_path = Path(__file__).resolve().parent / "fetch_eastmoney_minute_kline.mjs"
    target = output_path(symbol, trade_date)
    pure_symbol = symbol.split(".", 1)[0]
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(
            [
                "node",
                str(script_path),
                pure_symbol,
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "status": "error",
            "message": f"node_fetch_timeout_after_{timeout}s",
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "filename": str(target),
            "source": "eastmoney_node",
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": str(exc),
            "filename": str(target),
            "source": "eastmoney_node",
        }

    if proc.returncode != 0:
        return {
            "status": "error",
            "message": (proc.stderr or proc.stdout or "").strip() or f"node_exit_{proc.returncode}",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "filename": str(target),
            "source": "eastmoney_node",
        }

    return {
        "status": "success",
        "filename": str(target),
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "source": "eastmoney_node",
    }


def _tencent_code(symbol: str) -> str:
    return f"sh{symbol[:-3]}" if symbol.endswith(".SH") else f"sz{symbol[:-3]}"


def fetch_tencent_minute_rows(symbol: str, trade_date: str, timeout: int) -> list[dict[str, Any]]:
    tencent_code = _tencent_code(symbol)
    compact_date = trade_date.replace("-", "")
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
        f"?_var=min_data_{tencent_code}&code={tencent_code}&day={compact_date}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="ignore")

    marker = f"min_data_{tencent_code}="
    if marker not in text:
        return []
    payload = json.loads(text[text.find(marker) + len(marker):])
    raw_rows = (((payload.get("data") or {}).get(tencent_code) or {}).get("data") or {}).get("data") or []
    if not isinstance(raw_rows, list):
        return []

    rows: list[dict[str, Any]] = []
    prev_volume = 0.0
    prev_amount = 0.0
    for line in raw_rows:
        if not isinstance(line, str):
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        raw_time, raw_price, raw_volume, raw_amount = parts[:4]
        if len(raw_time) != 4 or not raw_time.isdigit():
            continue
        hhmm = f"{raw_time[:2]}:{raw_time[2:]}"
        price = float(raw_price)
        cum_volume = float(raw_volume)
        cum_amount = float(raw_amount)
        volume = max(cum_volume - prev_volume, 0.0)
        amount = max(cum_amount - prev_amount, 0.0)
        prev_volume = cum_volume
        prev_amount = cum_amount
        rows.append(
            {
                "datetime": f"{trade_date} {hhmm}",
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "amount": amount,
                "avg": price,
            }
        )
    return rows


def save_standard_minute_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["datetime", "open", "high", "low", "close", "volume", "amount", "avg"],
        )
        writer.writeheader()
        writer.writerows(rows)


def fetch_and_save_minute_data(
    symbol: str,
    trade_date: str | None = None,
    timeout: int = 30,
    max_rounds: int = 1,
    round_sleep: float = 0.0,
) -> dict[str, Any]:
    """获取并保存分钟线数据。"""
    normalized_symbol = normalize_symbol(symbol)
    normalized_trade_date = normalize_trade_date(trade_date)

    attempts: list[dict[str, Any]] = []
    for round_idx in range(1, max_rounds + 1):
        result = run_node_fetch(normalized_symbol, normalized_trade_date, timeout)
        target = Path(result["filename"])
        complete = minute_file_complete(target) if target.exists() else False
        attempts.append(
            {
                "round": round_idx,
                "status": result["status"],
                "source": result.get("source"),
                "filename": result["filename"],
                "complete": complete,
                "message": result.get("message"),
            }
        )

        if result["status"] == "success" and complete:
            with target.open("r", encoding="utf-8", newline="") as f:
                count = len(list(csv.DictReader(f)))
            return {
                "status": "success",
                "symbol": normalized_symbol,
                "trade_date": normalized_trade_date,
                "count": count,
                "filename": str(target),
                "source": result.get("source"),
                "attempts": attempts,
            }

        if round_idx < max_rounds and round_sleep > 0:
            time.sleep(round_sleep)

    target = output_path(normalized_symbol, normalized_trade_date)
    try:
        tencent_rows = fetch_tencent_minute_rows(normalized_symbol, normalized_trade_date, timeout)
    except Exception as exc:
        attempts.append(
            {
                "round": "tencent_fallback",
                "status": "error",
                "source": "tencent_minute",
                "filename": str(target),
                "complete": False,
                "message": str(exc),
            }
        )
    else:
        if tencent_rows:
            save_standard_minute_csv(target, tencent_rows)
            complete = minute_file_complete(target)
            attempts.append(
                {
                    "round": "tencent_fallback",
                    "status": "success",
                    "source": "tencent_minute",
                    "filename": str(target),
                    "complete": complete,
                    "message": None,
                }
            )
            if complete:
                return {
                    "status": "success",
                    "symbol": normalized_symbol,
                    "trade_date": normalized_trade_date,
                    "count": len(tencent_rows),
                    "filename": str(target),
                    "source": "tencent_minute",
                    "attempts": attempts,
                }
        else:
            attempts.append(
                {
                    "round": "tencent_fallback",
                    "status": "error",
                    "source": "tencent_minute",
                    "filename": str(target),
                    "complete": False,
                    "message": "tencent_returned_no_rows",
                }
            )

    final = attempts[-1] if attempts else {}
    return {
        "status": "error",
        "symbol": normalized_symbol,
        "trade_date": normalized_trade_date,
        "message": final.get("message") or "minute_fetch_failed",
        "filename": final.get("filename"),
        "source": final.get("source"),
        "attempts": attempts,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="统一分钟线抓取入口")
    parser.add_argument("--symbol", required=True, help="股票代码，如 600103.SH")
    parser.add_argument("--trade-date", help="交易日期，支持 YYYY-MM-DD / YYYYMMDD")
    parser.add_argument("--timeout", type=int, default=30, help="单轮抓取超时秒数")
    parser.add_argument("--max-rounds", type=int, default=1, help="最多重试轮数")
    parser.add_argument("--round-sleep", type=float, default=0.0, help="轮次间等待秒数")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = fetch_and_save_minute_data(
        symbol=args.symbol,
        trade_date=args.trade_date,
        timeout=args.timeout,
        max_rounds=args.max_rounds,
        round_sleep=args.round_sleep,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
