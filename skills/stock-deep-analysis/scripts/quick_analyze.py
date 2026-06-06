#!/usr/bin/env python3
"""
快速分析脚本 - 一步到位获取所有分析需要的数据

优化点：
1. 并行获取实时行情+K线+分时数据
2. 自动处理CSV编码（gbk/utf-8-sig）
3. 生成结构化JSON供Agent直接使用
4. 消息面不阻塞主流程

用法：
  python3 quick_analyze.py --symbol 600103.SH --date 2026-04-22
  python3 quick_analyze.py --symbol 600103 --date 2026-04-22 --no-browser
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

# 加载配置
sys.path.insert(0, str(Path(__file__).resolve().parent))
from data.config_loader import cfg

from data.data_access import (
    load_yearly_or_flat_rows,
    read_top_list,
    read_top_inst,
    _read_stock_parquet,
    load_daily_rows_bulk,
    load_daily_basic_rows_bulk,
    load_moneyflow_rows_bulk,
)

# 导入龙虎榜分析器
try:
    from dragon_tiger_analyzer import DragonTigerAnalyzer
except ImportError:
    DragonTigerAnalyzer = None

# 导入新闻上下文处理（代理到 market-news-intelligence）
try:
    from news_context import load_news_payload, normalize_news_sentiment, narrative_context_from_news
except ImportError:
    load_news_payload = None
    normalize_news_sentiment = None
    narrative_context_from_news = None

STOCK_DATA_ROOT = Path(cfg.paths("stock_data_root"))

# ============ 常量 ============
TENCENT_SNAPSHOT_URL = "https://qt.gtimg.cn/q={code}"
TENCENT_KLINE_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    "?param={code},day,{start},{end},10,qfq"
)
TENCENT_MINUTE_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
    "?_var=min_data_{code}&code={code}&day={date}"
)

# 经验证的腾讯行情字段索引（与SKILL.md一致）
TENCENT_FIELD_MAP = {
    "market_id": 0,
    "name": 1,
    "code": 2,
    "current": 3,
    "prev_close": 4,
    "open": 5,
    "volume": 6,
    "out_vol": 7,
    "in_vol": 8,
    "bid1_price": 9,
    "bid1_vol": 10,
    "ask1_price": 19,
    "ask1_vol": 20,
    "timestamp": 30,
    "change_amount": 31,
    "change_pct": 32,
    "high": 33,
    "low": 34,
    "summary": 35,
    "turnover": 36,
    "turnover_rate": 37,
    "volume_ratio": 38,
    "pe": 39,
    "total_mv": 44,
    "circ_mv": 45,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="快速分析数据获取")
    parser.add_argument("--symbol", required=True, help="如 600103 或 600103.SH")
    parser.add_argument(
        "--date", required=True, help="交易日期 YYYY-MM-DD"
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="跳过浏览器消息面获取"
    )
    parser.add_argument(
        "--output", "-o", help="输出JSON文件路径，默认标准输出"
    )
    parser.add_argument(
        "--checkpoint",
        default="post_market",
        help="时段标识 (pre_market/midday/post_market/intraday)，默认post_market",
    )
    return parser.parse_args()


def normalize_symbol(symbol: str) -> tuple[str, str]:
    """标准化股票代码，返回 (tencent_code, ts_code)"""
    symbol = symbol.strip().upper()
    if ".SH" in symbol or ".SZ" in symbol:
        ts_code = symbol
        code = symbol.replace(".SH", "").replace(".SZ", "")
    else:
        code = symbol
        # 上海: 600/601/603/605/688; 深圳: 000/002/003/300
        if code.startswith(("60", "68")):
            ts_code = f"{code}.SH"
        else:
            ts_code = f"{code}.SZ"

    tencent_code = f"sh{code}" if ts_code.endswith(".SH") else f"sz{code}"
    return tencent_code, ts_code


def get_latest_trade_date(rows: list[dict], field: str = "trade_date") -> str | None:
    """从CSV行中获取最新交易日期"""
    if not rows:
        return None
    dates = []
    for r in rows:
        d = (r.get(field) or "").strip()
        if d:
            dates.append(d)
    return max(dates) if dates else None


# ============ 数据获取函数 ============

def fetch_tencent_snapshot(tencent_code: str) -> dict:
    """获取腾讯实时行情"""
    url = TENCENT_SNAPSHOT_URL.format(code=tencent_code)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        return {"error": str(e)}

    var_name = f"v_{tencent_code}"
    start = text.find(f'{var_name}="')
    if start == -1:
        return {"error": "解析失败：未找到数据"}

    start += len(f'{var_name}="')
    end = text.find('"', start)
    fields = text[start:end].split("~")

    result = {}
    for key, idx in TENCENT_FIELD_MAP.items():
        try:
            val = fields[idx] if idx < len(fields) else ""
            # 尝试数值转换
            if key in ("name", "code", "timestamp", "summary"):
                result[key] = val
            else:
                try:
                    result[key] = float(val) if val else None
                except ValueError:
                    result[key] = val
        except (IndexError, ValueError):
            result[key] = None

    # 计算涨跌幅（验证）
    if result.get("current") and result.get("prev_close"):
        result["computed_change_pct"] = round(
            (result["current"] - result["prev_close"]) / result["prev_close"] * 100, 2
        )

    return result


def fetch_tencent_kline(tencent_code: str, start_date: str, end_date: str) -> list:
    """获取腾讯K线数据（前复权10日）"""
    url = TENCENT_KLINE_URL.format(
        code=tencent_code, start=start_date, end=end_date
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    if "data" not in data or tencent_code not in data["data"]:
        return []

    klines = data["data"][tencent_code].get("qfqday", [])
    return klines


def fetch_tencent_minute(tencent_code: str, date_str: str) -> list[dict]:
    """获取腾讯当日分时数据"""
    url = TENCENT_MINUTE_URL.format(code=tencent_code, date=date_str)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
    except Exception:
        return []

    var_name = f"min_data_{tencent_code}"
    if f"{var_name}=" not in text:
        return []

    try:
        json_str = text.split("=", 1)[1].rstrip(";")
        data = json.loads(json_str)
    except Exception:
        return []

    if "data" not in data or tencent_code not in data["data"]:
        return []

    raw = data["data"][tencent_code]["data"]["data"]
    parsed = []
    for line in raw:
        parts = line.split()
        if len(parts) >= 4:
            parsed.append(
                {
                    "time": parts[0],
                    "price": float(parts[1]),
                    "volume": int(parts[2]),
                    "amount": float(parts[3]),
                }
            )
    return parsed


def fetch_local_daily(ts_code: str) -> dict:
    """获取本地日线数据（最近10日）—— parquet only"""
    rows = _read_stock_parquet("daily", ts_code)
    if not rows:
        return {"status": "missing", "rows": [], "latest_date": None}

    rows_sorted = sorted(rows, key=lambda r: r.get("trade_date", ""))
    latest = get_latest_trade_date(rows_sorted)
    return {"status": "available", "rows": rows_sorted[-10:], "latest_date": latest}


def fetch_local_factors(ts_code: str) -> dict:
    """获取本地技术指标因子 —— parquet only"""
    rows = _read_stock_parquet("stk_factor_pro", ts_code)
    if not rows:
        return {
            "status": "missing",
            "latest": None,
            "dataset": "stk_factor_pro",
        }

    latest = rows[-1]
    key_fields = [
        "kdj_k_bfq",
        "kdj_d_bfq",
        "macd_bfq",
        "macd_dea_bfq",
        "macd_dif_bfq",
        "rsi_bfq_6",
        "rsi_bfq_12",
        "rsi_bfq_24",
        "boll_lower_bfq",
        "boll_mid_bfq",
        "boll_upper_bfq",
        "cci_bfq",
        "ma_bfq_5",
        "ma_bfq_10",
        "ma_bfq_20",
        "ma_bfq_60",
        "turnover_rate",
        "turnover_rate_f",
    ]

    result = {"trade_date": latest.get("trade_date", "N/A")}
    for k in key_fields:
        result[k] = latest.get(k, "N/A")

    return {
        "status": "available",
        "latest": result,
        "latest_date": latest.get("trade_date", ""),
        "dataset": "stk_factor_pro",
    }


def fetch_local_chips(ts_code: str) -> dict:
    """获取本地筹码分布 —— parquet only"""
    rows = _read_stock_parquet("cyq_chips", ts_code)
    if not rows:
        return {"status": "missing", "rows": [], "latest_date": None}

    latest = get_latest_trade_date(rows)
    latest_rows = [r for r in rows if r.get("trade_date") == latest]
    return {"status": "available", "rows": latest_rows, "latest_date": latest}


def fetch_local_daily_basic(ts_code: str) -> dict:
    """获取本地每日基本面 —— parquet only"""
    rows = _read_stock_parquet("daily_basic", ts_code)
    if not rows:
        return {"status": "missing", "latest": None}

    latest = rows[-1]
    return {
        "status": "available",
        "latest": {
            "trade_date": latest.get("trade_date", "N/A"),
            "turnover_rate": latest.get("turnover_rate", "N/A"),
            "turnover_rate_f": latest.get("turnover_rate_f", "N/A"),
            "pe": latest.get("pe", "N/A"),
            "pb": latest.get("pb", "N/A"),
            "total_mv": latest.get("total_mv", "N/A"),
            "circ_mv": latest.get("circ_mv", "N/A"),
        },
        "latest_date": latest.get("trade_date", ""),
    }


def fetch_local_moneyflow(ts_code: str, end_date: str = "") -> dict:
    """获取本地资金流向数据（近5日）—— parquet only，tushare 格式优先"""
    rows = _read_stock_parquet("moneyflow_data/individual/tushare", ts_code)
    if not rows:
        return {"status": "missing", "rows": [], "latest_date": None}

    rows_sorted = sorted(rows, key=lambda r: r.get("trade_date", ""))
    recent = rows_sorted[-5:]

    formatted = []
    for r in recent:
        net_mf = r.get("net_mf_amount", "0")
        try:
            net_mf_val = float(net_mf)
            net_label = f"净流入 {net_mf_val:.0f}万" if net_mf_val > 0 else f"净流出 {abs(net_mf_val):.0f}万"
        except (ValueError, TypeError):
            net_label = "N/A"

        try:
            big_buy = float(r.get("buy_lg_amount", 0)) + float(r.get("buy_elg_amount", 0))
            big_sell = float(r.get("sell_lg_amount", 0)) + float(r.get("sell_elg_amount", 0))
            big_net = big_buy - big_sell
        except (ValueError, TypeError):
            big_net = 0

        try:
            mid_net = float(r.get("buy_md_amount", 0)) - float(r.get("sell_md_amount", 0))
        except (ValueError, TypeError):
            mid_net = 0

        try:
            sm_net = float(r.get("buy_sm_amount", 0)) - float(r.get("sell_sm_amount", 0))
        except (ValueError, TypeError):
            sm_net = 0

        formatted.append({
            "date": r.get("trade_date", "N/A"),
            "net_flow": net_label,
            "big_order": f"{big_net:+.0f}万",
            "mid_order": f"{mid_net:+.0f}万",
            "small_order": f"{sm_net:+.0f}万",
        })

    latest = get_latest_trade_date(rows_sorted)
    return {"status": "available", "rows": formatted, "latest_date": latest}


def fetch_local_top_list(ts_code: str, trade_date: str) -> dict:
    """获取本地龙虎榜明细（指定日期）
    trade_date 格式: YYYY-MM-DD 或 YYYYMMDD
    """
    date_compact = trade_date.replace("-", "")
    rows = read_top_list(date_compact)
    if not rows:
        return {"status": "missing", "record": None, "inst_summary": None}

    # 筛选该股票记录
    for row in rows:
        if row.get("ts_code") == ts_code:
            record = {
                "trade_date": row.get("trade_date", "N/A"),
                "name": row.get("name", "N/A"),
                "close": row.get("close", "N/A"),
                "pct_change": row.get("pct_change", "N/A"),
                "turnover_rate": row.get("turnover_rate", "N/A"),
                "amount": row.get("amount", "N/A"),
                "l_sell": row.get("l_sell", "N/A"),
                "l_buy": row.get("l_buy", "N/A"),
                "l_amount": row.get("l_amount", "N/A"),
                "net_amount": row.get("net_amount", "N/A"),
                "net_rate": row.get("net_rate", "N/A"),
                "amount_rate": row.get("amount_rate", "N/A"),
                "float_values": row.get("float_values", "N/A"),
                "reason": row.get("reason", "N/A"),
            }
            return {"status": "available", "record": record, "latest_date": date_compact}

    return {"status": "missing", "record": None, "latest_date": None}


def fetch_local_top_inst(ts_code: str, trade_date: str) -> dict:
    """获取本地龙虎榜机构席位（指定日期）
    trade_date 格式: YYYY-MM-DD 或 YYYYMMDD
    """
    date_compact = trade_date.replace("-", "")
    rows = read_top_inst(date_compact)
    if not rows:
        return {"status": "missing", "records": [], "latest_date": None}

    # 筛选该股票所有席位
    records = []
    for row in rows:
        if row.get("ts_code") == ts_code:
            records.append({
                "trade_date": row.get("trade_date", "N/A"),
                "exalter": row.get("exalter", "N/A"),
                "side": row.get("side", "N/A"),
                "buy": row.get("buy", "N/A"),
                "buy_rate": row.get("buy_rate", "N/A"),
                "sell": row.get("sell", "N/A"),
                "sell_rate": row.get("sell_rate", "N/A"),
                "net_buy": row.get("net_buy", "N/A"),
                "reason": row.get("reason", "N/A"),
            })

    if records:
        return {"status": "available", "records": records, "latest_date": date_compact}
    return {"status": "missing", "records": [], "latest_date": None}


def fetch_local_news(ts_code: str, trade_date: str) -> dict:
    """
    获取本地新闻/消息面数据。
    路径规则: quant-data/tushare/消息面数据/raw/news_pipeline/{YYYY}/{MM}/{DD}/news_pipeline_{code}_{YYYY-MM-DD}.json
    (注意：文件名中股票代码不含 .SH/.SZ 后缀)
    """
    if not load_news_payload:
        return {"status": "missing", "reason": "news_context 模块未加载", "news_sentiment": None, "narrative_context": None}

    date_obj = datetime.strptime(trade_date.replace("-", ""), "%Y%m%d")
    yyyy, mm, dd = date_obj.strftime("%Y"), date_obj.strftime("%m"), date_obj.strftime("%d")
    date_iso = date_obj.strftime("%Y-%m-%d")

    # 文件名中的股票代码不含 .SH/.SZ 后缀
    code_for_filename = ts_code.replace(".SH", "").replace(".SZ", "")

    # 尝试读取已归一化的 news_pipeline 文件
    news_root = Path(cfg.paths("news_data_root"))
    pipeline_path = news_root / "raw" / "news_pipeline" / yyyy / mm / dd / f"news_pipeline_{code_for_filename}_{date_iso}.json"

    raw = {}
    if pipeline_path.exists():
        raw = load_news_payload(str(pipeline_path))

    # 如果没有 pipeline 文件，尝试读取原始 browser_news 文件
    if not raw:
        browser_path = news_root / "raw" / "browser_news" / yyyy / mm / dd / f"browser_news_{code_for_filename}_{date_iso}.json"
        if browser_path.exists():
            raw = load_news_payload(str(browser_path))

    if not raw:
        return {"status": "missing", "reason": "本地新闻文件不存在", "news_sentiment": None, "narrative_context": None}

    # 如果文件已经是归一化后的 pipeline 格式（含 news_sentiment / narrative_context），直接使用
    if isinstance(raw, dict) and "news_sentiment" in raw and "narrative_context" in raw:
        return {
            "status": "available",
            "source_file": str(pipeline_path if pipeline_path.exists() else browser_path),
            "news_sentiment": raw.get("news_sentiment", {}),
            "narrative_context": raw.get("narrative_context", {}),
        }

    # 否则对原始数据进行归一化（如 browser_news 原始抓取结果）
    news_sentiment = normalize_news_sentiment(raw, date_iso) if normalize_news_sentiment else {}
    narrative_context = narrative_context_from_news(news_sentiment) if narrative_context_from_news else {}

    return {
        "status": "available",
        "source_file": str(pipeline_path if pipeline_path.exists() else browser_path),
        "news_sentiment": news_sentiment,
        "narrative_context": narrative_context,
    }


# ============ 分析函数 ============

def analyze_top_list(top_list_data: dict, top_inst_data: dict) -> dict:
    """龙虎榜分析：基于龙虎榜明细和席位数据生成判断"""
    record = top_list_data.get("record")
    if not record:
        return {"on_list": False, "signal": "未上榜", "summary": "当日无龙虎榜数据"}

    try:
        net_rate = float(record.get("net_rate", 0) or 0)
        amount_rate = float(record.get("amount_rate", 0) or 0)
        turnover_rate = float(record.get("turnover_rate", 0) or 0)
        net_amount = float(record.get("net_amount", 0) or 0)
    except (ValueError, TypeError):
        net_rate = amount_rate = turnover_rate = net_amount = 0

    reason = record.get("reason", "N/A")

    # 席位分析
    inst_records = top_inst_data.get("records", [])
    buy_seats = [r for r in inst_records if r.get("side") == "buy"]
    sell_seats = [r for r in inst_records if r.get("side") == "sell"]

    # 判断逻辑
    if net_rate >= 35 and amount_rate >= 60:
        signal = "强锁仓接力"
        summary = f"龙虎榜净买占比 {net_rate:.2f}%，成交占比 {amount_rate:.2f}%，强锁仓特征明显"
    elif net_rate >= 12:
        signal = "正向确认"
        summary = f"龙虎榜净买占比 {net_rate:.2f}%，达到正向确认阈值"
    elif net_rate >= 8:
        signal = "新增主导"
        summary = f"龙虎榜净买占比 {net_rate:.2f}%，说明有新增主导资金介入"
    elif net_rate <= -5:
        signal = "派发兑现"
        summary = f"龙虎榜净卖占比较高 {net_rate:.2f}%，更像派发或高位兑现"
    else:
        signal = "边界模糊"
        summary = f"龙虎榜净买占比 {net_rate:.2f}%，属于边界模糊区间"

    return {
        "on_list": True,
        "signal": signal,
        "summary": summary,
        "net_rate": round(net_rate, 2),
        "amount_rate": round(amount_rate, 2),
        "turnover_rate": round(turnover_rate, 2),
        "net_amount": round(net_amount, 2),
        "reason": reason,
        "buy_seats_count": len(buy_seats),
        "sell_seats_count": len(sell_seats),
    }


def get_recent_trade_dates(end_date: str, n: int = 10) -> list[str]:
    """从交易日历获取最近n个交易日（含end_date），去重"""
    import csv
    cal_path = STOCK_DATA_ROOT / "trade_cal" / "trade_days.csv"
    if not cal_path.exists():
        return []
    try:
        with open(cal_path, "r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        # 去重：SSE/SZSE 可能重复
        seen = set()
        open_dates = []
        for r in rows:
            if r.get("is_open") == "1":
                d = r["cal_date"]
                if d not in seen:
                    seen.add(d)
                    open_dates.append(d)
        open_dates.sort()
    except Exception:
        return []

    # 找到end_date的位置，往前取n天
    try:
        idx = open_dates.index(end_date)
    except ValueError:
        # end_date可能不在日历中，找最接近的
        valid = [d for d in open_dates if d <= end_date]
        if not valid:
            return []
        idx = open_dates.index(valid[-1])

    start_idx = max(0, idx - n + 1)
    return open_dates[start_idx : idx + 1]


def fetch_local_top_list_for_dates(ts_code: str, dates: list[str]) -> dict:
    """读取多个日期的龙虎榜汇总数据"""
    all_records = []
    for d in dates:
        try:
            rows = read_top_list(d)
            if rows:
                filtered = [r for r in rows if r.get("ts_code") == ts_code]
                all_records.extend(filtered)
        except Exception:
            continue
    if not all_records:
        return {"status": "missing", "records": [], "count": 0}
    # 按日期排序
    all_records.sort(key=lambda r: r.get("trade_date", ""))
    return {"status": "available", "records": all_records, "count": len(all_records)}


def fetch_local_top_inst_for_dates(ts_code: str, dates: list[str]) -> dict:
    """读取多个日期的机构交易明细"""
    all_records = []
    for d in dates:
        try:
            rows = read_top_inst(d)
            if rows:
                filtered = [r for r in rows if r.get("ts_code") == ts_code]
                all_records.extend(filtered)
        except Exception:
            continue
    if not all_records:
        return {"status": "missing", "records": [], "count": 0}
    all_records.sort(key=lambda r: (r.get("trade_date", ""), r.get("exalter", "")))
    return {"status": "available", "records": all_records, "count": len(all_records)}


def analyze_top_list_series(
    ts_code: str, end_date: str, top_list_data: dict, top_inst_data: dict, lookback: int = 10
) -> dict:
    """
    分析近lookback个交易日内的龙虎榜连续性。
    返回：上榜日期、连续天数、席位持续性（谁在、谁走了）、资金流向趋势。
    """
    dates = get_recent_trade_dates(end_date, lookback)
    if not dates:
        return {
            "lookback_days": lookback,
            "trade_dates_checked": [],
            "dates_on_list": [],
            "consecutive_days": 0,
            "daily_details": [],
            "exalter_continuity": {},
            "trend": "unknown",
            "signal": "数据不足",
        }

    # 如果外部已传入多日期数据，直接用；否则重新拉取
    tl_records = top_list_data.get("records", []) if top_list_data.get("count", 0) > 1 else []
    ti_records = top_inst_data.get("records", []) if top_inst_data.get("count", 0) > 1 else []

    if not tl_records:
        multi_tl = fetch_local_top_list_for_dates(ts_code, dates)
        tl_records = multi_tl.get("records", [])
    if not ti_records:
        multi_ti = fetch_local_top_inst_for_dates(ts_code, dates)
        ti_records = multi_ti.get("records", [])

    # 哪些日期上了榜
    dates_on_list = sorted(set(r.get("trade_date", "") for r in tl_records if r.get("trade_date")))

    # 计算连续上榜天数（从最后一次上榜日期往前数，考虑 end_date 本身未上榜的情况）
    consecutive = 0
    if dates_on_list:
        last_listed = dates_on_list[-1]
        found = False
        for d in reversed(dates):
            if d == last_listed:
                found = True
            if found:
                if d in dates_on_list:
                    consecutive += 1
                else:
                    break
    else:
        consecutive = 0

    # 每日详情
    daily_details = []
    for d in dates_on_list:
        day_tl = [r for r in tl_records if r.get("trade_date") == d]
        day_ti = [r for r in ti_records if r.get("trade_date") == d]
        if not day_tl:
            continue
        rec = day_tl[0]
        net_amount = float(rec.get("net_amount", 0) or 0)
        pct_change = float(rec.get("pct_change", 0) or 0)
        reason = rec.get("reason", "N/A")

        # 席位汇总（该日）
        exalter_summary = []
        for ti in day_ti:
            side_str = "买入" if str(ti.get("side", "")) == "0" else ("卖出" if str(ti.get("side", "")) == "1" else "unknown")
            exalter_summary.append({
                "exalter": ti.get("exalter", "N/A"),
                "side": side_str,
                "buy": round(float(ti.get("buy", 0) or 0), 2),
                "sell": round(float(ti.get("sell", 0) or 0), 2),
                "net_buy": round(float(ti.get("net_buy", 0) or 0), 2),
            })

        daily_details.append({
            "date": d,
            "reason": reason,
            "net_amount": round(net_amount, 2),
            "pct_change": round(pct_change, 2),
            "exalter_count": len(exalter_summary),
            "exalter_summary": exalter_summary,
        })

    # 席位持续性分析
    exalter_by_date: dict[str, dict[str, dict]] = {}
    for ti in ti_records:
        d = ti.get("trade_date", "")
        name = ti.get("exalter", "")
        if not d or not name:
            continue
        if d not in exalter_by_date:
            exalter_by_date[d] = {}
        # 一个席位同一天可能有买+卖两条记录，合并
        if name not in exalter_by_date[d]:
            exalter_by_date[d][name] = {"buy": 0.0, "sell": 0.0, "net_buy": 0.0}
        exalter_by_date[d][name]["buy"] += float(ti.get("buy", 0) or 0)
        exalter_by_date[d][name]["sell"] += float(ti.get("sell", 0) or 0)
        exalter_by_date[d][name]["net_buy"] += float(ti.get("net_buy", 0) or 0)

    all_exalters = set()
    for d_map in exalter_by_date.values():
        all_exalters.update(d_map.keys())

    persistent = []
    left = []
    new_coming = []

    last_date = dates_on_list[-1] if dates_on_list else ""
    first_date = dates_on_list[0] if dates_on_list else ""

    for name in all_exalters:
        appearance_dates = sorted([d for d, m in exalter_by_date.items() if name in m])
        count = len(appearance_dates)
        total_buy = sum(exalter_by_date[d][name]["buy"] for d in appearance_dates)
        total_sell = sum(exalter_by_date[d][name]["sell"] for d in appearance_dates)
        total_net = sum(exalter_by_date[d][name]["net_buy"] for d in appearance_dates)

        info = {
            "name": name,
            "appearance_count": count,
            "appearance_dates": appearance_dates,
            "total_buy": round(total_buy, 2),
            "total_sell": round(total_sell, 2),
            "total_net_buy": round(total_net, 2),
        }

        if count >= 2:
            persistent.append(info)
        elif appearance_dates and appearance_dates[-1] != last_date:
            info["last_seen"] = appearance_dates[-1]
            left.append(info)
        elif appearance_dates and appearance_dates[0] == last_date:
            info["first_seen"] = appearance_dates[0]
            new_coming.append(info)

    # 按出现次数排序
    persistent.sort(key=lambda x: x["appearance_count"], reverse=True)
    left.sort(key=lambda x: x["last_seen"], reverse=True)
    new_coming.sort(key=lambda x: x["total_net_buy"], reverse=True)

    # 趋势判断
    if len(dates_on_list) >= 2:
        net_amounts = []
        for d in dates_on_list:
            day_rec = [r for r in tl_records if r.get("trade_date") == d]
            if day_rec:
                net_amounts.append(float(day_rec[0].get("net_amount", 0) or 0))
            else:
                net_amounts.append(0)

        # 简单趋势：最近3天平均值 vs 前3天
        recent_avg = sum(net_amounts[-3:]) / len(net_amounts[-3:]) if net_amounts else 0
        early_avg = sum(net_amounts[:3]) / len(net_amounts[:3]) if len(net_amounts) >= 3 else recent_avg

        if recent_avg > early_avg * 1.5 and recent_avg > 0:
            trend = "资金持续流入"
        elif recent_avg < early_avg * 0.5 and recent_avg < 0:
            trend = "资金流出加速"
        elif consecutive >= 3:
            trend = "游资接力"
        else:
            trend = "资金流出"
    else:
        trend = "数据不足"

    # 综合信号
    if consecutive >= 3:
        if persistent:
            signal = f"连续{consecutive}天上榜，有{len(persistent)}家席位持续参与"
        else:
            signal = f"连续{consecutive}天上榜，席位换手频繁"
    elif dates_on_list:
        signal = f"近{lookback}日上榜{len(dates_on_list)}次"
    else:
        signal = "近期未上榜"

    return {
        "lookback_days": lookback,
        "trade_dates_checked": dates,
        "dates_on_list": dates_on_list,
        "consecutive_days": consecutive,
        "daily_details": daily_details,
        "exalter_continuity": {
            "persistent_exalters": persistent,
            "left_exalters": left,
            "new_exalters": new_coming,
        },
        "trend": trend,
        "signal": signal,
    }


def analyze_minute_intent(minute_data: list[dict], prev_close: float) -> list[dict]:
    """生成分时主力意图分析表格"""
    if not minute_data:
        return []

    segments = [
        ("09:30-09:45", "0930", "0945"),
        ("09:45-10:00", "0945", "1000"),
        ("10:00-10:15", "1000", "1015"),
        ("10:15-10:30", "1015", "1030"),
        ("10:30-10:45", "1030", "1045"),
        ("10:45-11:00", "1045", "1100"),
        ("11:00-11:15", "1100", "1115"),
        ("11:15-11:30", "1115", "1130"),
        ("13:00-13:15", "1300", "1315"),
        ("13:15-13:30", "1315", "1330"),
        ("13:30-13:45", "1330", "1345"),
        ("13:45-14:00", "1345", "1400"),
        ("14:00-14:15", "1400", "1415"),
        ("14:15-14:30", "1415", "1430"),
        ("14:30-14:45", "1430", "1445"),
        ("14:45-15:00", "1445", "1500"),
    ]

    results = []
    for seg_name, start_t, end_t in segments:
        seg_points = [
            m for m in minute_data if start_t <= m["time"] <= end_t
        ]
        if not seg_points:
            continue

        start_price = seg_points[0]["price"]
        end_price = seg_points[-1]["price"]
        high = max(m["price"] for m in seg_points)
        low = min(m["price"] for m in seg_points)
        seg_vol = sum(m["volume"] for m in seg_points)
        seg_amt = sum(m["amount"] for m in seg_points) / 10000

        seg_change = round((end_price - start_price) / start_price * 100, 2)

        # 判断主力行为
        if seg_change > 3 and seg_vol > 200000:
            behavior = "主力拉升"
        elif seg_change < -3 and seg_vol > 200000:
            behavior = "主力出货/洗盘"
        elif abs(seg_change) <= 0.5 and seg_vol > 200000:
            behavior = "高位对倒"
        elif seg_vol < 50000:
            behavior = "成交萎缩"
        else:
            behavior = "正常交易"

        results.append(
            {
                "time_window": seg_name,
                "price_range": f"{low:.2f}~{high:.2f}",
                "volume": seg_vol,
                "amount_wan": round(seg_amt, 0),
                "change_pct": seg_change,
                "behavior": behavior,
            }
        )

    return results


def analyze_recent_trend(daily_rows: list[dict]) -> list[dict]:
    """生成最近10日走势表格"""
    if not daily_rows:
        return []

    results = []
    for r in daily_rows:
        try:
            close = float(r.get("close", 0))
            pre_close = float(r.get("pre_close", 0))
            pct = round((close - pre_close) / pre_close * 100, 2) if pre_close else 0
        except (ValueError, ZeroDivisionError):
            pct = 0

        results.append(
            {
                "date": r.get("trade_date", "N/A"),
                "close": r.get("close", "N/A"),
                "change_pct": pct,
                "turnover": r.get("turnover_rate", "N/A"),
                "volume": r.get("vol", "N/A"),
                "signal": "涨停" if pct >= 9.9 else ("跌停" if pct <= -9.9 else ""),
            }
        )

    return results


# ============ 主函数 ============

def main() -> int:
    args = parse_args()
    tencent_code, ts_code = normalize_symbol(args.symbol)
    trade_date = args.date.replace("-", "")

    print(f"[快速分析] {ts_code} @ {args.date}", file=sys.stderr)

    # 1. 并行获取所有数据
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        future_snapshot = executor.submit(fetch_tencent_snapshot, tencent_code)
        future_kline = executor.submit(
            fetch_tencent_kline, tencent_code, "2026-04-01", args.date
        )
        future_minute = executor.submit(
            fetch_tencent_minute, tencent_code, trade_date
        )

        # 本地数据也并行读取
        future_daily = executor.submit(fetch_local_daily, ts_code)
        future_factors = executor.submit(fetch_local_factors, ts_code)
        future_chips = executor.submit(fetch_local_chips, ts_code)
        future_basic = executor.submit(fetch_local_daily_basic, ts_code)
        future_moneyflow = executor.submit(fetch_local_moneyflow, ts_code, args.date)
        future_top_list = executor.submit(fetch_local_top_list, ts_code, args.date)
        future_top_inst = executor.submit(fetch_local_top_inst, ts_code, args.date)
        future_news = executor.submit(fetch_local_news, ts_code, args.date)

        # 龙虎榜分析器
        future_dragon_tiger = None
        if DragonTigerAnalyzer is not None:
            dt_analyzer = DragonTigerAnalyzer()
            future_dragon_tiger = executor.submit(
                dt_analyzer.analyze, ts_code, trade_date, 10
            )

        snapshot = future_snapshot.result()
        klines = future_kline.result()
        minute_data = future_minute.result()
        daily = future_daily.result()
        factors = future_factors.result()
        chips = future_chips.result()
        basic = future_basic.result()
        moneyflow = future_moneyflow.result()
        top_list = future_top_list.result()
        top_inst = future_top_inst.result()
        news_data = future_news.result()

        dragon_tiger_result = None
        if future_dragon_tiger:
            dragon_tiger_result = future_dragon_tiger.result()

    # 2. 数据状态汇总
    data_status = {
        "snapshot": "available" if "error" not in snapshot else "missing",
        "kline": "available" if klines else "missing",
        "minute": "available" if minute_data else "missing",
        "daily_local": daily["status"],
        "factors": factors["status"],
        "chips": chips["status"],
        "daily_basic": basic["status"],
        "moneyflow": moneyflow["status"],
        "top_list": top_list["status"],
        "top_inst": top_inst["status"],
        "news": news_data["status"],
    }

    # 3. 分析计算
    prev_close = snapshot.get("prev_close", 0) if "error" not in snapshot else 0
    minute_intent = analyze_minute_intent(minute_data, prev_close)
    recent_trend = analyze_recent_trend(daily["rows"])
    top_list_analysis = analyze_top_list(top_list, top_inst)
    top_list_series = analyze_top_list_series(ts_code, args.date, top_list, top_inst, lookback=10)

    # 4. 组装结果
    result = {
        "meta": {
            "symbol": ts_code,
            "tencent_code": tencent_code,
            "trade_date": args.date,
            "analysis_time": datetime.now().isoformat(),
        },
        "data_status": data_status,
        "snapshot": snapshot,
        "klines": klines[-10:] if klines else [],
        "minute_data_summary": {
            "count": len(minute_data),
            "first_time": minute_data[0]["time"] if minute_data else None,
            "last_time": minute_data[-1]["time"] if minute_data else None,
            "high": max(m["price"] for m in minute_data) if minute_data else None,
            "low": min(m["price"] for m in minute_data) if minute_data else None,
        },
        "minute_intent": minute_intent,
        "recent_trend": recent_trend,
        "daily_local": daily,
        "factors": factors,
        "chips": chips,
        "daily_basic": basic,
        "moneyflow": moneyflow,
        "top_list": top_list,
        "top_inst": top_inst,
        "top_list_analysis": top_list_analysis,
        "top_list_series": top_list_series,
        "dragon_tiger": dragon_tiger_result.to_dict() if dragon_tiger_result else None,
        "news": {
            "status": news_data.get("status", "missing"),
            "source_file": news_data.get("source_file"),
            "news_sentiment": news_data.get("news_sentiment"),
            "narrative_context": news_data.get("narrative_context"),
        },
    }

    # 5. 输出
    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"[完成] 结果已保存到 {args.output}", file=sys.stderr)
    else:
        print(output_json)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
