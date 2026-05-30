"""
shim: delegate auction analysis to the external auction-analysis skill via subprocess.

Pre-reads auction CSV data from local storage and passes it to the subprocess
via --open-data / --close-data / --daily-data JSON arguments, so the child
process only runs the analysis logic (no local I/O for data fetching).
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

AUCTION_SKILL_SCRIPT = (
    Path.home()
    / "agent-skills"
    / "custom"
    / "auction-analysis"
    / "scripts"
    / "analyze_auction_intent.py"
)

# Try to import data-access utilities for pre-reading auction rows.
# If unavailable, fall back to subprocess-without-data (child reads locally).
try:
    from data.data_access import load_daily_row as _load_daily_row
    from common import STOCK_DATA_ROOT, normalize_symbol

    def _load_auction_row(full_symbol: str, trade_date_text: str, auction_type: str) -> Optional[dict[str, Any]]:
        """Minimal re-implementation of load_auction_row (avoids importing auction-analysis internals)."""
        import csv
        td = trade_date_text.replace("-", "")
        dir_name = "stk_auction_o" if auction_type == "open" else "stk_auction_c"
        base = STOCK_DATA_ROOT / dir_name
        # Try flat path first, then year-subdir
        path = base / f"{dir_name}_{full_symbol}.csv"
        if not path.exists():
            path = base / td[:4] / f"{dir_name}_{full_symbol}.csv"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                d = str(row.get("trade_date") or "").strip()
                if d == td:
                    return row
        return None

    _CAN_PRE_READ = True
except ImportError:
    _CAN_PRE_READ = False


def call_auction_analysis(full_symbol: str, trade_date_text: str) -> dict:
    """
    Call the standalone auction-analysis skill script with pre-read data.

    Pre-reads auction CSV rows locally, passes them as CLI JSON args so the
    child subprocess only does analytical work. Falls back to child reading
    locally if pre-read unavailable (module imports fail).
    """
    if not AUCTION_SKILL_SCRIPT.is_file():
        err_msg = f"auction-analysis skill script not found: {AUCTION_SKILL_SCRIPT}"
        print(f"[WARN] {err_msg}", file=sys.stderr)
        return {
            "status": "missing_skill",
            "summary": err_msg,
            "overall_intent": "未知",
            "score": 0,
            "open": None,
            "close": None,
        }

    cmd = [
        sys.executable,
        str(AUCTION_SKILL_SCRIPT),
        "--symbol", full_symbol,
        "--trade-date", trade_date_text,
        "--format", "json",
    ]

    # Pre-read data if possible
    open_data: Optional[str] = None
    close_data: Optional[str] = None
    daily_data: Optional[str] = None
    _, pure_symbol = normalize_symbol(full_symbol)

    if _CAN_PRE_READ:
        try:
            td = trade_date_text.replace("-", "")
            daily_row = _load_daily_row(full_symbol, td)
            if daily_row is not None:
                daily_data = json.dumps(daily_row, ensure_ascii=False)

            open_row = _load_auction_row(full_symbol, trade_date_text, "open")
            if open_row is not None:
                open_data = json.dumps(open_row, ensure_ascii=False)

            close_row = _load_auction_row(full_symbol, trade_date_text, "close")
            if close_row is not None:
                close_data = json.dumps(close_row, ensure_ascii=False)
        except Exception as exc:
            # Pre-read failed, don't pass data — child will read locally
            print(f"[WARN] auction-analysis pre-read failed for {full_symbol}: {exc}", file=sys.stderr)
            open_data = close_data = daily_data = None

    # 不传预读数据 — shim的_path_setup可能解析到旧版analyze_auction_intent.py
    # 让子进程自己读本地数据（0.4s足够快）
    pass

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            err_msg = result.stderr.strip() or f"exit code {result.returncode}"
            print(
                f"[WARN] auction-analysis failed for {full_symbol} {trade_date_text}: {err_msg}",
                file=sys.stderr,
            )
            return {
                "status": "error",
                "summary": f"竞价分析调用失败: {err_msg}",
                "overall_intent": "未知",
                "score": 0,
                "open": None,
                "close": None,
            }

        parsed = json.loads(result.stdout)
        return parsed
    except subprocess.TimeoutExpired:
        print(
            f"[WARN] auction-analysis timed out for {full_symbol} {trade_date_text}",
            file=sys.stderr,
        )
        return {
            "status": "timeout",
            "summary": "竞价分析调用超时",
            "overall_intent": "未知",
            "score": 0,
            "open": None,
            "close": None,
        }
    except json.JSONDecodeError as e:
        print(
            f"[WARN] auction-analysis JSON parse error for {full_symbol} {trade_date_text}: {e}",
            file=sys.stderr,
        )
        return {
            "status": "parse_error",
            "summary": f"竞价分析返回解析失败: {e}",
            "overall_intent": "未知",
            "score": 0,
            "open": None,
            "close": None,
        }
    except Exception as e:
        print(
            f"[WARN] auction-analysis unexpected error for {full_symbol} {trade_date_text}: {e}",
            file=sys.stderr,
        )
        return {
            "status": "error",
            "summary": f"竞价分析异常: {e}",
            "overall_intent": "未知",
            "score": 0,
            "open": None,
            "close": None,
        }
