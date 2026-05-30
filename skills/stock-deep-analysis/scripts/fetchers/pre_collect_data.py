#!/usr/bin/env python3
"""
一键数据预收集脚本 - Pre-Collect Data for stock-deep-analysis

设计目标：
1. 一次性收集分析所需的全部数据（本地+API），不阻塞、不浏览器
2. 输出结构化JSON，供后续分析Agent直接消费
3. 支持并行获取，控制在10秒内完成

用法：
  python3 pre_collect_data.py --symbol 600103.SH --date 2026-04-24
  python3 pre_collect_data.py --symbol 600103.SH --date 2026-04-24 --with-news

输出：
  ~/quant-data/tushare/股票数据/pre_collected/600103.SH/2026-04-24.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# 加载配置
SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))
from data.config_loader import cfg

STOCK_DATA_ROOT = Path(cfg.paths("stock_data_root"))
PRE_COLLECTED_ROOT = STOCK_DATA_ROOT / "pre_collected"

# ============ 常量 ============
ENCODINGS = ["utf-8-sig", "gbk", "gb2312", "utf-8"]
TENCENT_SNAPSHOT_URL = "https://qt.gtimg.cn/q={code}"
TENCENT_KLINE_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    "?param={code},day,{start},{end},10,qfq"
)
TENCENT_MINUTE_URL = (
    "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
    "?_var=min_data_{code}&code={code}&day={date}"
)
INDEX_CODES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
}

TENCENT_FIELD_MAP = {
    "market_id": 0, "name": 1, "code": 2, "current": 3, "prev_close": 4,
    "open": 5, "volume": 36, "out_vol": 7, "in_vol": 8,
    "bid1_price": 9, "bid1_vol": 10, "ask1_price": 19, "ask1_vol": 20,
    "timestamp": 30, "change_amount": 31, "change_pct": 32,
    "high": 33, "low": 34, "summary": 35,
    "turnover_rate": 38, "pe": 39, "total_mv": 45, "circ_mv": 44,
    "volume_ratio": 43, "pb": 46,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="一键预收集分析数据")
    parser.add_argument("--symbol", required=True, help="如 600103 或 600103.SH")
    parser.add_argument("--date", required=True, help="交易日期 YYYY-MM-DD")
    parser.add_argument("--with-news", action="store_true", help="是否包含浏览器新闻抓取（默认跳过，较慢）")
    parser.add_argument("--output", "-o", help="输出JSON路径，默认标准路径")
    parser.add_argument("--checkpoint", default="post_market",
                        help="时段标识 (pre_market/midday/post_market/intraday)")
    return parser.parse_args()


def normalize_symbol(symbol: str) -> tuple[str, str]:
    symbol = symbol.strip().upper()
    if ".SH" in symbol or ".SZ" in symbol:
        ts_code = symbol
        code = symbol.replace(".SH", "").replace(".SZ", "")
    else:
        code = symbol
        ts_code = f"{code}.SH" if code.startswith(("60", "68")) else f"{code}.SZ"
    tencent_code = f"sh{code}" if ts_code.endswith(".SH") else f"sz{code}"
    return tencent_code, ts_code


def read_csv_robust(path: Path) -> list[dict]:
    if not path.exists():
        return []
    for enc in ENCODINGS:
        try:
            with path.open("r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except (UnicodeDecodeError, csv.Error):
            continue
    return []


def fetch_tencent_snapshot(tencent_code: str) -> dict:
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
            if key in ("name", "code", "timestamp", "summary"):
                result[key] = val
            else:
                try:
                    result[key] = float(val) if val else None
                except ValueError:
                    result[key] = val
        except (IndexError, ValueError):
            result[key] = None
    if result.get("current") and result.get("prev_close"):
        result["computed_change_pct"] = round(
            (result["current"] - result["prev_close"]) / result["prev_close"] * 100, 2
        )
    return result


def fetch_tencent_kline(tencent_code: str, start_date: str, end_date: str) -> list:
    url = TENCENT_KLINE_URL.format(code=tencent_code, start=start_date, end=end_date)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []
    if "data" not in data or tencent_code not in data["data"]:
        return []
    return data["data"][tencent_code].get("qfqday", [])


def fetch_tencent_minute(tencent_code: str, date_str: str) -> list[dict]:
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
            parsed.append({
                "time": parts[0], "price": float(parts[1]),
                "volume": int(parts[2]), "amount": float(parts[3]),
            })
    return parsed


def fetch_local_daily(ts_code: str) -> dict:
    from data.data_provider import get_daily_rows
    rows = get_daily_rows(ts_code, datetime.now().strftime("%Y%m%d"), limit=10)
    if not rows:
        return {"status": "missing", "rows": [], "latest_date": None}
    latest = max(r.get("trade_date", "") for r in rows)
    return {"status": "available", "rows": rows, "latest_date": latest}


def fetch_local_factors(ts_code: str) -> dict:
    from data.data_provider import get_factors
    factor = get_factors(ts_code, datetime.now().strftime("%Y%m%d"))
    if not factor:
        return {"status": "missing", "latest": None}
    return {"status": "available", "latest": factor, "latest_date": factor.get("trade_date", "")}


def fetch_local_chips(ts_code: str) -> dict:
    from data.data_provider import get_chips
    rows = get_chips(ts_code, datetime.now().strftime("%Y%m%d"))
    if not rows:
        return {"status": "missing", "rows": [], "latest_date": None}
    latest = max(r.get("trade_date", "") for r in rows)
    return {"status": "available", "rows": rows[:10], "latest_date": latest}


def fetch_local_daily_basic(ts_code: str) -> dict:
    from data.data_provider import get_daily_basic
    basic = get_daily_basic(ts_code, datetime.now().strftime("%Y%m%d"))
    if not basic:
        return {"status": "missing", "latest": None}
    return {"status": "available", "latest": basic, "latest_date": basic.get("trade_date", "")}


def fetch_local_moneyflow(ts_code: str, end_date: str = "") -> dict:
    from data.data_provider import _STOCK_ROOT
    import pandas as pd
    path = _STOCK_ROOT / "moneyflow_data" / "individual" / "tushare" / f"{ts_code}.parquet"
    try:
        df = pd.read_parquet(path)
    except Exception:
        return {"status": "missing", "rows": [], "latest_date": None}
    if end_date:
        end_compact = end_date.replace("-", "")
        df = df[df["trade_date"] <= end_compact]
    if df.empty:
        return {"status": "missing", "rows": [], "latest_date": None}
    df = df.sort_values("trade_date").tail(5)
    formatted = []
    for _, r in df.iterrows():
        net_mf_val = r.get("net_mf_amount", 0) or 0
        net_label = f"净流入 {net_mf_val:.0f}万" if net_mf_val > 0 else f"净流出 {abs(net_mf_val):.0f}万"
        big_net = (float(r.get("buy_lg_amount", 0) or 0) + float(r.get("buy_elg_amount", 0) or 0)
                   - float(r.get("sell_lg_amount", 0) or 0) - float(r.get("sell_elg_amount", 0) or 0))
        mid_net = float(r.get("buy_md_amount", 0) or 0) - float(r.get("sell_md_amount", 0) or 0)
        sm_net = float(r.get("buy_sm_amount", 0) or 0) - float(r.get("sell_sm_amount", 0) or 0)
        formatted.append({
            "date": r.get("trade_date", "N/A"),
            "net_flow": net_label,
            "big_order": f"{big_net:+.0f}万",
            "mid_order": f"{mid_net:+.0f}万",
            "small_order": f"{sm_net:+.0f}万",
        })
    latest = df["trade_date"].max()
    return {"status": "available", "rows": formatted, "latest_date": str(latest)}


def fetch_local_top_list(ts_code: str, trade_date: str) -> dict:
    from data.data_access import read_top_list
    date_compact = trade_date.replace("-", "")
    rows = read_top_list(date_compact)
    for row in rows:
        if row.get("ts_code") == ts_code:
            record = {k: row.get(k, "N/A") for k in [
                "trade_date", "name", "close", "pct_change", "turnover_rate",
                "amount", "l_sell", "l_buy", "l_amount", "net_amount",
                "net_rate", "amount_rate", "float_values", "reason",
            ]}
            return {"status": "available", "record": record, "latest_date": date_compact}
    return {"status": "missing", "record": None, "latest_date": None}


def fetch_local_top_inst(ts_code: str, trade_date: str) -> dict:
    from data.data_access import read_top_inst
    date_compact = trade_date.replace("-", "")
    rows = read_top_inst(date_compact)
    records = []
    for row in rows:
        if row.get("ts_code") == ts_code:
            records.append({k: row.get(k, "N/A") for k in [
                "trade_date", "exalter", "side", "buy", "buy_rate",
                "sell", "sell_rate", "net_buy", "reason",
            ]})
    if records:
        return {"status": "available", "records": records, "latest_date": date_compact}
    return {"status": "missing", "records": [], "latest_date": None}


def fetch_index_snapshots() -> dict:
    """获取大盘指数实时行情"""
    codes = list(INDEX_CODES.keys())
    url = TENCENT_SNAPSHOT_URL.format(code=",".join(codes))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        return {"error": str(e)}

    results = {}
    for code in codes:
        var_name = f"v_{code}"
        start = text.find(f'{var_name}="')
        if start == -1:
            continue
        start += len(f'{var_name}="')
        end = text.find('"', start)
        fields = text[start:end].split("~")
        try:
            results[code] = {
                "name": INDEX_CODES[code],
                "current": float(fields[3]) if len(fields) > 3 else None,
                "prev_close": float(fields[4]) if len(fields) > 4 else None,
                "open": float(fields[5]) if len(fields) > 5 else None,
                "high": float(fields[41]) if len(fields) > 41 else None,
                "low": float(fields[42]) if len(fields) > 42 else None,
                "change_pct": float(fields[32]) if len(fields) > 32 else None,
                "volume": float(fields[6]) if len(fields) > 6 else None,
            }
        except (ValueError, IndexError):
            continue
    return results


def fetch_industry_concept(ts_code: str) -> dict:
    from data.data_provider import get_stock_basic
    import pandas as pd
    from data.data_provider import _STOCK_ROOT

    basic = get_stock_basic(ts_code)
    industries = [basic.get("industry")] if basic and basic.get("industry") else []
    stock_name = basic.get("name") if basic else None

    concepts = []
    for year_pq in sorted(_STOCK_ROOT.glob("theme_data/kpl_concept_cons/20*.parquet"), reverse=True):
        try:
            df = pd.read_parquet(year_pq)
            match = df[df["con_code"] == ts_code]
            if not match.empty:
                concepts = match["name"].dropna().unique().tolist()
                break
        except Exception:
            continue

    if not concepts:
        for year_pq in sorted(_STOCK_ROOT.glob("theme_data/dc_concept_cons/20*.parquet"), reverse=True):
            try:
                df = pd.read_parquet(year_pq)
                match = df[df["con_code"] == ts_code] if "con_code" in df.columns else df[df["ts_code"] == ts_code]
                if not match.empty:
                    col = "industry" if "industry" in match.columns else "name"
                    concepts = match[col].dropna().unique().tolist()
                    break
            except Exception:
                continue

    if industries or concepts:
        return {"status": "available", "industry": industries, "concept": concepts}
    return {"status": "missing", "industry": [], "concept": []}


def fetch_sector_moneyflow(trade_date: str) -> dict:
    """获取当日行业涨跌幅排行（用于大盘环境）"""
    date_compact = trade_date.replace("-", "")
    candidates = [
        STOCK_DATA_ROOT / f"moneyflow_data/sector/ths_industry/moneyflow_industry_ths_{date_compact}.csv",
        STOCK_DATA_ROOT / f"moneyflow_data/sector/ths_industry/moneyflow_industry_ths_{date_compact}.parquet",
    ]
    for p in candidates:
        if p.exists():
            rows = read_csv_robust(p)
            if rows:
                # 只取涨跌幅前5后5
                sorted_rows = sorted(rows, key=lambda r: float(r.get("pct_change", 0) or 0), reverse=True)
                top5 = [{"name": r.get("name", "N/A"),
                         "pct_change": r.get("pct_change", "N/A"),
                         "net_mf_amount": r.get("net_mf_amount", "N/A")}
                        for r in sorted_rows[:5]]
                bottom5 = [{"name": r.get("name", "N/A"),
                            "pct_change": r.get("pct_change", "N/A"),
                            "net_mf_amount": r.get("net_mf_amount", "N/A")}
                           for r in sorted_rows[-5:]]
                return {"status": "available", "top5": top5, "bottom5": bottom5}
    return {"status": "missing"}


def main() -> int:
    args = parse_args()
    tencent_code, ts_code = normalize_symbol(args.symbol)
    trade_date = args.date

    print(f"[预收集] {ts_code} @ {trade_date}", file=sys.stderr)

    # 并行获取所有数据
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        future_snapshot = executor.submit(fetch_tencent_snapshot, tencent_code)
        future_kline = executor.submit(
            fetch_tencent_kline, tencent_code,
            (datetime.strptime(trade_date, "%Y-%m-%d").replace(day=1)).strftime("%Y-%m-%d"),
            trade_date
        )
        future_minute = executor.submit(fetch_tencent_minute, tencent_code, trade_date.replace("-", ""))
        future_index = executor.submit(fetch_index_snapshots)

        future_daily = executor.submit(fetch_local_daily, ts_code)
        future_factors = executor.submit(fetch_local_factors, ts_code)
        future_chips = executor.submit(fetch_local_chips, ts_code)
        future_basic = executor.submit(fetch_local_daily_basic, ts_code)
        future_moneyflow = executor.submit(fetch_local_moneyflow, ts_code)
        future_top_list = executor.submit(fetch_local_top_list, ts_code, trade_date)
        future_top_inst = executor.submit(fetch_local_top_inst, ts_code, trade_date)
        future_ind_con = executor.submit(fetch_industry_concept, ts_code)
        future_sector = executor.submit(fetch_sector_moneyflow, trade_date)

        snapshot = future_snapshot.result()
        klines = future_kline.result()
        minute_data = future_minute.result()
        index_data = future_index.result()
        daily = future_daily.result()
        factors = future_factors.result()
        chips = future_chips.result()
        basic = future_basic.result()
        moneyflow = future_moneyflow.result()
        top_list = future_top_list.result()
        top_inst = future_top_inst.result()
        ind_con = future_ind_con.result()
        sector = future_sector.result()

    # 数据状态汇总
    data_status = {
        "snapshot": "available" if "error" not in snapshot else "missing",
        "kline": "available" if klines else "missing",
        "minute": "available" if minute_data else "missing",
        "index": "available" if "error" not in index_data else "missing",
        "daily_local": daily["status"],
        "factors": factors["status"],
        "chips": chips["status"],
        "daily_basic": basic["status"],
        "moneyflow": moneyflow["status"],
        "top_list": top_list["status"],
        "top_inst": top_inst["status"],
        "industry_concept": ind_con["status"],
        "sector_moneyflow": sector["status"],
    }

    # 组装结果
    result = {
        "meta": {
            "symbol": ts_code,
            "tencent_code": tencent_code,
            "trade_date": trade_date,
            "collected_at": datetime.now().isoformat(),
            "version": "1.0",
        },
        "data_status": data_status,
        "snapshot": snapshot,
        "klines": klines[-10:] if klines else [],
        "minute_data": {
            "count": len(minute_data),
            "first_time": minute_data[0]["time"] if minute_data else None,
            "last_time": minute_data[-1]["time"] if minute_data else None,
            "high": max(m["price"] for m in minute_data) if minute_data else None,
            "low": min(m["price"] for m in minute_data) if minute_data else None,
            "bars": minute_data,
        },
        "index_snapshots": index_data,
        "daily_local": daily,
        "factors": factors,
        "chips": chips,
        "daily_basic": basic,
        "moneyflow": moneyflow,
        "top_list": top_list,
        "top_inst": top_inst,
        "industry_concept": ind_con,
        "sector_moneyflow": sector,
    }

    # 确定输出路径
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = PRE_COLLECTED_ROOT / ts_code / f"{trade_date}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_json = json.dumps(result, ensure_ascii=False, indent=2)
    output_path.write_text(output_json, encoding="utf-8")

    # 打印状态摘要（标准错误输出，不影响正常stdout的JSON）
    print(f"[完成] 结果已保存到 {output_path}", file=sys.stderr)
    available = sum(1 for v in data_status.values() if v == "available")
    missing = sum(1 for v in data_status.values() if v == "missing")
    print(f"[数据状态] 可用: {available} 项, 缺失: {missing} 项", file=sys.stderr)
    for k, v in data_status.items():
        if v == "missing":
            print(f"  ⚠️  {k}: missing", file=sys.stderr)

    # 标准输出路径，便于脚本链式调用
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
