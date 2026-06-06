"""
统一数据入口 —— 整个系统的"数据总管"。

干啥的：
1. 读本地 parquet/CSV 文件（优先）
2. 本地没有 → 调 Tushare Pro API 补数据
3. 提供各种数据查询接口：日线、财务、资金流、龙虎榜、消息面...

谁用它：
- 几乎所有模块都用它：decision_engine, sector_analyzer, trend_analyzer...
- 它是唯一应该调 Tushare API 的地方

路径统一：
- 所有数据路径都从 common.py 导入，不硬编码
"""

import csv
import json
import sys
import calendar
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from common import STOCK_DATA_ROOT, NEWS_DATA_ROOT
from data.config_loader import cfg

# ---------------------------------------------------------------------------
# 对接 tushare_pro skill：将其路径加入 sys.path 以便复用客户端封装
# ---------------------------------------------------------------------------
_TUSHARE_PRO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent / "tushare_pro"
if str(_TUSHARE_PRO_ROOT) not in sys.path:
    sys.path.insert(0, str(_TUSHARE_PRO_ROOT))


def _read_parquet_rows(path: Path) -> list[dict[str, str]]:
    """读取 parquet 文件，返回 list[dict[str, str]] 格式，与旧 CSV 接口保持一致。"""
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
        # 统一转为字符串，保持与旧 CSV 输出一致
        return [
            {str(k): str(v) if v is not None else "" for k, v in row.items()}
            for row in df.to_dict("records")
        ]
    except Exception:
        return []


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    """仅保留交易日历等少数静态文件的 CSV 读取。"""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _trade_cal_file_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = cfg.get("paths", "trade_cal", default=[])
    if isinstance(configured, list):
        for item in configured:
            text = str(item or "").strip()
            if text:
                candidates.append(Path(text))
    try:
        legacy_dir = cfg.paths("trade_cal_dir")
        candidates.append(legacy_dir / "trade_days.csv")
        candidates.append(legacy_dir / "trade_cal_all.csv")
    except Exception:
        pass

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def trade_cal_path() -> Path | None:
    for candidate in _trade_cal_file_candidates():
        if candidate.exists():
            return candidate
    return None


def load_trade_calendar_index() -> tuple[set[str], dict[str, tuple[bool, str | None]]]:
    path = trade_cal_path()
    if not path:
        return set(), {}
    open_days: set[str] = set()
    by_day: dict[str, tuple[bool, str | None]] = {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            cal_date = str(row.get("cal_date") or "").strip()
            if len(cal_date) != 8 or not cal_date.isdigit():
                continue
            is_open = str(row.get("is_open") or "").strip() == "1"
            pretrade = str(row.get("pretrade_date") or "").strip()
            pretrade_val = pretrade if len(pretrade) == 8 and pretrade.isdigit() else None
            by_day[cal_date] = (is_open, pretrade_val)
            if is_open:
                open_days.add(cal_date)
    return open_days, by_day


def resolve_trade_date_by_calendar(trade_date_text: str) -> tuple[str, dict[str, Any]]:
    compact = trade_date_text.replace("-", "")
    open_days, by_day = load_trade_calendar_index()
    if not open_days:
        return trade_date_text, {"calendar_status": "calendar_missing", "adjusted": False}
    if compact in open_days:
        return trade_date_text, {"calendar_status": "open_day", "adjusted": False}
    row = by_day.get(compact)
    if row and not row[0] and row[1]:
        adjusted = f"{row[1][:4]}-{row[1][4:6]}-{row[1][6:8]}"
        return adjusted, {
            "calendar_status": "closed_day_use_pretrade",
            "adjusted": True,
            "requested_trade_date": trade_date_text,
            "resolved_trade_date": adjusted,
        }
    older = sorted(day for day in open_days if day <= compact)
    if older:
        fallback = older[-1]
        adjusted = f"{fallback[:4]}-{fallback[4:6]}-{fallback[6:8]}"
        return adjusted, {
            "calendar_status": "closed_day_use_latest_open",
            "adjusted": True,
            "requested_trade_date": trade_date_text,
            "resolved_trade_date": adjusted,
        }
    newer = sorted(open_days)
    if newer:
        fallback = newer[0]
        adjusted = f"{fallback[:4]}-{fallback[4:6]}-{fallback[6:8]}"
        return adjusted, {
            "calendar_status": "requested_before_calendar_range",
            "adjusted": True,
            "requested_trade_date": trade_date_text,
            "resolved_trade_date": adjusted,
        }
    return trade_date_text, {"calendar_status": "calendar_empty", "adjusted": False}


def next_trade_dates_compact(trade_date_text: str, count: int = 1) -> list[str]:
    compact = trade_date_text.replace("-", "")
    open_days, _ = load_trade_calendar_index()
    if open_days:
        future = sorted(day for day in open_days if day > compact)
        if future:
            return future[:count]
    base = datetime.strptime(trade_date_text, "%Y-%m-%d").date()
    return [(base + timedelta(days=i)).strftime("%Y%m%d") for i in range(1, count + 1)]


def latest_open_trade_date_on_or_before(reference_date_text: str) -> str | None:
    compact = reference_date_text.replace("-", "")
    open_days, _ = load_trade_calendar_index()
    if not open_days:
        return None
    older = sorted(day for day in open_days if day <= compact)
    if not older:
        return None
    latest = older[-1]
    return f"{latest[:4]}-{latest[4:6]}-{latest[6:8]}"


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _fmt_num(value: float | None, digits: int | None = None) -> str:
    if value is None:
        return ""
    if digits is None:
        digits = cfg.report("formats", "number_format", "digits", default=6)
    text = f"{float(value):.{digits}f}"
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _normalize_compact_date(value: Any) -> str:
    text = str(value or "").strip().split(".")[0]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _symbol_to_tencent_code(full_symbol: str) -> str:
    pure_symbol = full_symbol.split(".", 1)[0]
    return f"sh{pure_symbol}" if pure_symbol.startswith("6") else f"sz{pure_symbol}"


# ---------------------------------------------------------------------------
# 平底 parquet 读取工具
# ---------------------------------------------------------------------------

def _read_stock_parquet(subdir: str, ts_code: str) -> list[dict[str, str]]:
    """读取按股票分区的扁平 parquet：{root}/{subdir}/{ts_code}.parquet"""
    return _read_parquet_rows(STOCK_DATA_ROOT / subdir / f"{ts_code}.parquet")


def _read_prefixed_stock_parquet(subdir: str, prefix: str, ts_code: str) -> list[dict[str, str]]:
    """读取带 prefix 的按股票分区 parquet：{root}/{subdir}/{prefix}_{ts_code}.parquet"""
    return _read_parquet_rows(STOCK_DATA_ROOT / subdir / f"{prefix}_{ts_code}.parquet")


def _read_year_parquet(subdir: str, year: str) -> list[dict[str, str]]:
    """读取按年份全市场 parquet：{root}/{subdir}/{year}.parquet"""
    return _read_parquet_rows(STOCK_DATA_ROOT / subdir / f"{year}.parquet")


def _read_single_parquet(subdir: str, filename: str) -> list[dict[str, str]]:
    """读取单一文件 parquet：{root}/{subdir}/{filename}"""
    return _read_parquet_rows(STOCK_DATA_ROOT / subdir / filename)


def _filter_by_date_range(rows: list[dict[str, str]], start_compact: str | None = None, end_compact: str | None = None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for row in rows:
        td = str(row.get("trade_date") or "").strip()
        if len(td) != 8 or not td.isdigit():
            continue
        if start_compact and td < start_compact:
            continue
        if end_compact and td > end_compact:
            continue
        result.append(row)
    result.sort(key=lambda r: str(r.get("trade_date") or ""), reverse=True)
    return result


def _find_row_by_date(rows: list[dict[str, str]], trade_date_compact: str) -> dict[str, Any] | None:
    for row in rows:
        if str(row.get("trade_date") or "").strip() == trade_date_compact:
            return row
    return None


def _find_latest_before(rows: list[dict[str, str]], trade_date_compact: str) -> dict[str, Any] | None:
    """找到不大于指定日期的最新一条记录。"""
    matched: dict[str, Any] | None = None
    for row in rows:
        td = str(row.get("trade_date") or "").strip()
        if len(td) != 8 or not td.isdigit() or td > trade_date_compact:
            continue
        matched = row
    return matched


# ---------------------------------------------------------------------------
# 核心数据访问接口（保持签名不变，内部改为 parquet-only）
# ---------------------------------------------------------------------------

def load_daily_row(full_symbol: str, trade_date_compact: str) -> dict[str, Any] | None:
    rows = _read_stock_parquet("daily", full_symbol)
    return _find_row_by_date(rows, trade_date_compact)


def load_daily_basic_row(full_symbol: str, trade_date_compact: str) -> dict[str, Any] | None:
    rows = _read_stock_parquet("daily_basic", full_symbol)
    return _find_latest_before(rows, trade_date_compact)


def load_margin_rows(full_symbol: str) -> list[dict[str, str]]:
    return _read_stock_parquet("margin", full_symbol)


def load_daily_rows_bulk(full_symbol: str, start_date_compact: str | None = None, end_date_compact: str | None = None) -> list[dict[str, str]]:
    rows = _read_stock_parquet("daily", full_symbol)
    return _filter_by_date_range(rows, start_date_compact, end_date_compact)


def load_daily_basic_rows_bulk(full_symbol: str, start_date_compact: str | None = None, end_date_compact: str | None = None) -> list[dict[str, str]]:
    rows = _read_stock_parquet("daily_basic", full_symbol)
    return _filter_by_date_range(rows, start_date_compact, end_date_compact)


def load_moneyflow_rows_bulk(full_symbol: str, start_date_compact: str | None = None, end_date_compact: str | None = None) -> list[dict[str, Any]]:
    """从本地 ths 资金流向 parquet 读取。
    注：原先查 SQLite moneyflow 表（Tushare 格式），现在统一为 ths 格式 parquet。
    """
    rows = _read_stock_parquet("moneyflow_data/individual/ths", full_symbol)
    return _filter_by_date_range(rows, start_date_compact, end_date_compact)


def load_daily_rows_for_symbols(symbols: list[str], trade_date_compact: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for symbol in symbols:
        row = load_daily_row(symbol, trade_date_compact)
        if row:
            result[symbol] = row
    return result


def read_top_list(trade_date_compact: str) -> list[dict[str, str]]:
    year = trade_date_compact[:4]
    rows = _read_year_parquet("top_list", year)
    return [row for row in rows if str(row.get("trade_date") or "").strip() == trade_date_compact]


def read_top_inst(trade_date_compact: str) -> list[dict[str, str]]:
    year = trade_date_compact[:4]
    rows = _read_year_parquet("top_inst", year)
    return [row for row in rows if str(row.get("trade_date") or "").strip() == trade_date_compact]


def load_dc_concepts_local(trade_date_compact: str) -> list[dict[str, str]]:
    """只从本地 theme_data/dc_concept 读取 —— parquet only"""
    root_dir = STOCK_DATA_ROOT / "theme_data" / "dc_concept"
    year = trade_date_compact[:4]
    pq_path = root_dir / f"{year}.parquet"
    if pq_path.exists():
        rows = _read_parquet_rows(pq_path)
        return [r for r in rows if _normalize_compact_date(r.get("trade_date")) <= trade_date_compact]
    return []


def load_dc_concept_constituents_local(stock_name: str | None, trade_date_compact: str) -> list[dict[str, str]]:
    """只从本地 theme_data/dc_concept_cons 读取 —— parquet only"""
    if not stock_name:
        return []
    base = STOCK_DATA_ROOT / "theme_data" / "dc_concept_cons"
    year = trade_date_compact[:4]
    pq_path = base / f"{year}.parquet"
    if not pq_path.exists():
        return []
    rows = _read_parquet_rows(pq_path)
    # 筛选该股票名称且日期 <= trade_date_compact 的记录
    candidate_dates = sorted({
        _normalize_compact_date(row.get("trade_date"))
        for row in rows
        if row.get("name") == stock_name
        and _normalize_compact_date(row.get("trade_date"))
        and _normalize_compact_date(row.get("trade_date")) <= trade_date_compact
    }, reverse=True)
    if not candidate_dates:
        return []
    target_date = candidate_dates[0]
    return [row for row in rows if row.get("name") == stock_name and _normalize_compact_date(row.get("trade_date")) == target_date]


def load_yearly_or_flat_rows(root_dir: Path, filename: str) -> list[dict[str, str]]:
    """兼容性接口：仅读取 parquet。
    对于按股票分区的数据，从 root_dir/filename 推断 parquet 路径。
    """
    if filename.endswith(".csv"):
        ts_code = filename[:-4]
        if ts_code.startswith("daily_"):
            ts_code = ts_code[6:]
        elif ts_code.startswith("daily_basic_"):
            ts_code = ts_code[12:]
        elif ts_code.startswith("margin_"):
            ts_code = ts_code[7:]
        elif ts_code.startswith("cyq_chips_"):
            ts_code = ts_code[10:]
        elif ts_code.startswith("cyq_perf_"):
            ts_code = ts_code[9:]
        elif ts_code.startswith("stk_factor_pro_"):
            ts_code = ts_code[15:]
        elif ts_code.startswith("stk_auction_o_"):
            ts_code = ts_code[14:]
        elif ts_code.startswith("stk_auction_c_"):
            ts_code = ts_code[14:]
        elif ts_code.startswith("weekly_"):
            ts_code = ts_code[7:]
        elif ts_code.startswith("monthly_"):
            ts_code = ts_code[8:]
        # 尝试直接读取同名子目录下的 parquet
        subdir = root_dir.name
        pq_path = root_dir / f"{ts_code}.parquet"
        if pq_path.exists():
            return _read_parquet_rows(pq_path)
        # 尝试根目录下的 parquet
        pq_path = STOCK_DATA_ROOT / subdir / f"{ts_code}.parquet"
        if pq_path.exists():
            return _read_parquet_rows(pq_path)
    # fallback：直接读取传入的 parquet 文件
    pq_path = root_dir / (filename[:-4] + ".parquet" if filename.endswith(".csv") else filename)
    if pq_path.exists():
        return _read_parquet_rows(pq_path)
    return []


# ---------------------------------------------------------------------------
# 周月线重建（保留签名，内部改为 parquet-only）
# ---------------------------------------------------------------------------

def _group_week_end(compact_trade_date: str) -> str:
    dt = datetime.strptime(compact_trade_date, "%Y%m%d").date()
    week_end = dt + timedelta(days=(4 - dt.weekday()))
    return week_end.strftime("%Y%m%d")


def _group_month_end(compact_trade_date: str) -> str:
    dt = datetime.strptime(compact_trade_date, "%Y%m%d").date()
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    return dt.replace(day=last_day).strftime("%Y%m%d")


def rebuild_weekly_monthly_from_daily(full_symbol: str) -> dict[str, Any]:
    weekly_rows = _read_stock_parquet("weekly", full_symbol)
    monthly_rows = _read_stock_parquet("monthly", full_symbol)
    if not weekly_rows and not monthly_rows:
        return {"status": "missing"}
    return {
        "status": "available",
        "weekly_rows": len(weekly_rows),
        "monthly_rows": len(monthly_rows),
        "latest_weekly_trade_date": weekly_rows[0]["trade_date"] if weekly_rows else None,
        "latest_monthly_trade_date": monthly_rows[0]["trade_date"] if monthly_rows else None,
    }


def rebuild_stk_factor_pro_from_daily(full_symbol: str) -> dict[str, Any]:
    rows = _read_stock_parquet("stk_factor_pro", full_symbol)
    if not rows:
        return {"status": "missing"}
    latest = rows[-1]
    return {
        "status": "available",
        "rows": len(rows),
        "latest_trade_date": latest.get("trade_date"),
        "latest_turnover_trade_date": latest.get("trade_date"),
        "latest_volume_ratio": latest.get("volume_ratio"),
        "latest_rsi_bfq_6": latest.get("rsi_bfq_6"),
    }


# ---------------------------------------------------------------------------
# 浏览器补抓 fallback（保留签名，但内部简化）
# ---------------------------------------------------------------------------

def sync_latest_daily_kline_via_browser(full_symbol: str, trade_date_text: str, reference_date_text: str | None = None) -> dict[str, Any]:
    reference_text = reference_date_text or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    latest_open = latest_open_trade_date_on_or_before(reference_text)
    if latest_open is None:
        return {"status": "calendar_missing", "reason": "trade_calendar_missing"}
    if trade_date_text != latest_open:
        return {
            "status": "skipped_not_latest_trade_date",
            "target_trade_date": trade_date_text,
            "latest_open_trade_date": latest_open,
        }

    compact = trade_date_text.replace("-", "")
    existing = load_daily_row(full_symbol, compact)
    if existing is not None:
        return {
            "status": "already_available",
            "target_trade_date": trade_date_text,
            "latest_open_trade_date": latest_open,
        }

    # 降级到腾讯 API
    pure_symbol = full_symbol.split(".", 1)[0]
    tencent_error: str | None = None

    try:
        from fetchers.get_quote_tencent import get_quote_tencent
        quote = get_quote_tencent(_symbol_to_tencent_code(full_symbol))
    except Exception as exc:
        tencent_error = str(exc)
        quote = None

    if quote:
        open_price = _to_float(quote.get("open"))
        high_price = _to_float(quote.get("high"))
        low_price = _to_float(quote.get("low"))
        close_price = _to_float(quote.get("current"))
        prev_close = _to_float(quote.get("prev_close"))
        volume = _to_float(quote.get("volume"))
        amount = _to_float(quote.get("amount"))
        if None not in (open_price, high_price, low_price, close_price, prev_close):
            change = close_price - prev_close
            pct_chg = (change / prev_close * 100.0) if prev_close else 0.0
            return {
                "status": "fetched_tencent_fallback",
                "target_trade_date": trade_date_text,
                "latest_open_trade_date": latest_open,
                "snapshot_source": "tencent_quote_api",
                "row": {
                    "ts_code": full_symbol,
                    "trade_date": compact,
                    "open": _fmt_num(open_price),
                    "high": _fmt_num(high_price),
                    "low": _fmt_num(low_price),
                    "close": _fmt_num(close_price),
                    "pre_close": _fmt_num(prev_close),
                    "change": _fmt_num(change),
                    "pct_chg": _fmt_num(pct_chg, 4),
                    "vol": _fmt_num(volume or 0.0),
                    "amount": _fmt_num(amount or 0.0),
                },
                "tencent_reason": tencent_error,
            }
    tencent_error = tencent_error or "invalid_tencent_quote"

    return {
        "status": "browser_fetch_failed",
        "reason": f"tencent: {tencent_error}",
        "target_trade_date": trade_date_text,
    }


def load_browser_margin_signal(full_symbol: str) -> dict[str, Any]:
    day = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
    path = STOCK_DATA_ROOT / "margin_eligibility_browser" / full_symbol / f"{day}.json"
    if not path.exists():
        return {"status": "unavailable", "eligibility": "unknown", "reason": "no_browser_snapshot_today", "path": str(path)}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "unavailable", "eligibility": "unknown", "reason": f"invalid_browser_snapshot: {exc}", "path": str(path)}
    if not isinstance(raw, dict):
        return {"status": "unavailable", "eligibility": "unknown", "reason": "invalid_browser_snapshot_type", "path": str(path)}
    return {
        "status": "available",
        "eligibility": str(raw.get("eligibility") or "unknown"),
        "reason": str(raw.get("reason") or "").strip() or "browser_signal_available",
        "checked_at": raw.get("checked_at"),
        "path": str(path),
        "source": raw.get("source"),
    }


# ---------------------------------------------------------------------------
# SQLite 消息库读取
# ---------------------------------------------------------------------------

NEWS_ROOT = NEWS_DATA_ROOT


def _match_keywords(text: str | None, keywords: set[str]) -> bool:
    if not text:
        return False
    t = str(text).lower()
    return any(kw.lower() in t for kw in keywords)


def _build_news_keywords(full_symbol: str, stock_name: str | None = None) -> set[str]:
    """生成消息匹配关键词：股票名称、纯代码、市场+代码组合"""
    pure = full_symbol.split(".", 1)[0] if "." in full_symbol else full_symbol
    kws = {pure}
    if stock_name:
        kws.add(stock_name)
        # 如果股票名有简称（前2-4字），也加入匹配
        if len(stock_name) >= 4:
            kws.add(stock_name[:4])
        if len(stock_name) >= 2:
            kws.add(stock_name[:2])
    return kws


def load_news_items_from_db(
    date_text: str,
    keywords: set[str],
) -> list[dict[str, Any]]:
    """
从 news/*.db 读取某日热搜新闻，按关键词匹配 title。
返回: [{title, platform_id, rank, url, first_crawl_time}, ...]
    """
    db_path = NEWS_ROOT / "news" / f"{date_text}.db"
    if not db_path.exists():
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT title, platform_id, rank, url, first_crawl_time FROM news_items"
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for title, platform_id, rank, url, first_crawl in rows:
        if _match_keywords(title, keywords):
            results.append({
                "title": title,
                "platform_id": platform_id,
                "rank": rank,
                "url": url,
                "first_crawl_time": first_crawl,
                "source_type": "news_hot",
            })
    # 按热度排序
    results.sort(key=lambda x: (x.get("rank") or 999, x.get("first_crawl_time") or ""))
    return results


def load_rss_items_from_db(
    date_text: str,
    keywords: set[str],
) -> list[dict[str, Any]]:
    """
从 rss/*.db 读取某日 RSS 订阅，按关键词匹配 title 或 summary。
返回: [{title, feed_id, published_at, summary, url}, ...]
    """
    db_path = NEWS_ROOT / "rss" / f"{date_text}.db"
    if not db_path.exists():
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute(
            "SELECT title, feed_id, published_at, summary, url FROM rss_items"
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for title, feed_id, published_at, summary, url in rows:
        if _match_keywords(title, keywords) or _match_keywords(summary, keywords):
            results.append({
                "title": title,
                "feed_id": feed_id,
                "published_at": published_at,
                "summary": summary,
                "url": url,
                "source_type": "rss",
            })
    # 按发布时间倒序
    results.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return results


def load_all_news_for_symbol(
    trade_date_text: str,
    full_symbol: str,
    stock_name: str | None = None,
    days_back: int = 2,
) -> dict[str, Any]:
    """
综合查询某股票在指定日期前后几天的所有消息。
返回: {news: [...], rss: [...], meta: {total, date_range, keywords}}
    """
    keywords = _build_news_keywords(full_symbol, stock_name)
    base = datetime.strptime(trade_date_text, "%Y-%m-%d").date()
    dates = [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days_back + 1)]

    all_news: list[dict[str, Any]] = []
    all_rss: list[dict[str, Any]] = []
    for d in dates:
        all_news.extend(load_news_items_from_db(d, keywords))
        all_rss.extend(load_rss_items_from_db(d, keywords))

    # 去重：同标题只保留一条（保留排名/时间最好的）
    seen_titles: set[str] = set()
    deduped_news: list[dict[str, Any]] = []
    for item in all_news:
        t = str(item.get("title") or "").strip()
        if t and t not in seen_titles:
            seen_titles.add(t)
            deduped_news.append(item)
    deduped_rss: list[dict[str, Any]] = []
    for item in all_rss:
        t = str(item.get("title") or "").strip()
        if t and t not in seen_titles:
            seen_titles.add(t)
            deduped_rss.append(item)

    return {
        "news": deduped_news,
        "rss": deduped_rss,
        "meta": {
            "total": len(deduped_news) + len(deduped_rss),
            "news_count": len(deduped_news),
            "rss_count": len(deduped_rss),
            "date_range": f"{dates[-1]} ~ {dates[0]}",
            "keywords": sorted(keywords),
        },
    }
