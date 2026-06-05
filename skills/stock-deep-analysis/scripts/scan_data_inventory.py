#!/usr/bin/env python3
"""
本地数据资产自动扫描脚本

用途：定期扫描 stock-deep-analysis 依赖的所有本地数据源，
      生成可用/缺失/结构不匹配报告，更新 references/data-inventory.md。

执行：python3 scripts/scan_data_inventory.py [--symbol 600103.SH]
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from data.config_loader import cfg

STOCK_BASE = str(cfg.paths("stock_data_root"))
INDEX_BASE = str(cfg.paths("index_data_root"))
NEWS_BASE = str(cfg.paths("news_data_root"))
FINANCIAL_BASE = str(cfg.paths("financial_data_root"))

# SKILL.md 中定义的所有本地数据依赖及实际路径
DATA_DEFS = [
    {"name": "daily_ohlcv",      "desc": "日线行情",                "paths": [f"{STOCK_BASE}/daily/**/*.csv"], "type": "glob_recursive", "critical": True, "root": "stock_data_root"},
    {"name": "daily_basic",      "desc": "日线基础(PE/PB/市值)",    "paths": [f"{STOCK_BASE}/daily_basic/**/*.csv"], "type": "glob_recursive", "critical": True, "root": "stock_data_root"},
    {"name": "moneyflow_individual_tushare","desc": "个股资金流向(Tushare按日期全市场表)", "paths": [f"{STOCK_BASE}/moneyflow_data/individual/tushare/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "moneyflow_market", "desc": "大盘历史资金流",         "paths": [f"{STOCK_BASE}/moneyflow_data/market/**/*.csv", f"{STOCK_BASE}/moneyflow_data/market/**/*.parquet"], "type": "glob_recursive", "critical": True, "root": "stock_data_root"},
    {"name": "moneyflow_sector", "desc": "板块历史资金流",         "paths": [f"{STOCK_BASE}/moneyflow_data/sector/**/*.csv", f"{STOCK_BASE}/moneyflow_data/sector/**/*.parquet"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "moneyflow_individual","desc": "个股历史资金流(按日期)","paths": [f"{STOCK_BASE}/moneyflow_data/individual/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "index_daily",      "desc": "大盘指数日线",           "paths": [f"{INDEX_BASE}/index_daily/**/*.csv"], "type": "glob_recursive", "critical": True, "root": "index_data_root"},
    {"name": "margin_by_stock",  "desc": "融资融券(按股票结构)",    "paths": [f"{STOCK_BASE}/margin/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "margin_detail",    "desc": "融资融券明细(年份子目录)",      "paths": [f"{STOCK_BASE}/margin_detail/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "cyq_chips",        "desc": "筹码分布",               "paths": [f"{STOCK_BASE}/cyq_chips/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "stk_auction_o",    "desc": "开盘集合竞价",           "paths": [f"{STOCK_BASE}/stk_auction_o/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "stk_auction_c",    "desc": "收盘集合竞价",           "paths": [f"{STOCK_BASE}/stk_auction_c/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "theme_dc",         "desc": "东财概念成分",           "paths": [f"{STOCK_BASE}/theme_data/dc_concept*"], "type": "glob", "critical": False, "root": "stock_data_root"},
    {"name": "theme_kpl",        "desc": "开盘啦概念成分",         "paths": [f"{STOCK_BASE}/theme_data/kpl_concept_cons*"], "type": "glob", "critical": False, "root": "stock_data_root"},
    {"name": "top_list",         "desc": "龙虎榜明细",             "paths": [f"{STOCK_BASE}/top_list/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "top_inst",         "desc": "龙虎榜机构明细",         "paths": [f"{STOCK_BASE}/top_inst/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "hm_list",          "desc": "游资榜单",               "paths": [f"{STOCK_BASE}/hm_list/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "limit_list_ths",   "desc": "涨停列表(同花顺)",       "paths": [f"{STOCK_BASE}/limit_list_ths/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "block_trade",      "desc": "大宗交易",               "paths": [f"{STOCK_BASE}/block_trade/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "minute_flat",      "desc": "分钟线(扁平结构)",      "paths": [f"{STOCK_BASE}/minute_kline*.csv"], "type": "glob", "critical": False, "root": "stock_data_root"},
    {"name": "minute_tree",      "desc": "分钟线(树形结构)",      "paths": [f"{STOCK_BASE}/分钟数据/*/*/*/*/*m.csv"], "type": "glob", "critical": False, "root": "stock_data_root"},
    {"name": "trade_cal",        "desc": "交易日历",               "paths": [f"{STOCK_BASE}/trade_cal/*.csv"], "type": "glob", "critical": True, "root": "stock_data_root"},
    {"name": "industry_daily",   "desc": "行业指数日线",           "paths": [f"{INDEX_BASE}/sw_daily/**/*.csv", f"{INDEX_BASE}/sw_industry/**/*.csv", f"{INDEX_BASE}/zx_industry/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "index_data_root"},
    {"name": "concept_daily",    "desc": "概念题材数据",           "paths": [f"{STOCK_BASE}/theme_data/dc_concept/**/*.csv", f"{STOCK_BASE}/theme_data/dc_concept_cons/**/*.csv", f"{STOCK_BASE}/theme_data/kpl_concept_cons/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "industry_concept", "desc": "行业/概念映射",          "paths": [f"{STOCK_BASE}/theme_data/dc_concept_cons/**/*.csv", f"{STOCK_BASE}/theme_data/dc_concept/**/*.csv", f"{STOCK_BASE}/theme_data/kpl_concept_cons/**/*.csv", f"{STOCK_BASE}/industry_concept/**/*.csv"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
    {"name": "announcement",     "desc": "公司公告",               "paths": [f"{NEWS_BASE}/announcement/**/*.json", f"{NEWS_BASE}/raw/announcement/**/*.json"], "type": "glob_recursive", "critical": False, "root": "news_data_root"},
    {"name": "stock_basic",      "desc": "股票基础信息",           "paths": [f"{STOCK_BASE}/stock_basic/*.csv"], "type": "glob", "critical": True, "root": "stock_data_root"},
    {"name": "news_raw",         "desc": "消息面原始数据",         "paths": [f"{NEWS_BASE}/raw/news_pipeline/*.json"], "type": "glob", "critical": False, "root": "news_data_root"},
    {"name": "financial_income", "desc": "财务数据-利润表",         "paths": [f"{FINANCIAL_BASE}/income/*.csv", f"{FINANCIAL_BASE}/income/*.parquet"], "type": "glob", "critical": False, "root": "financial_data_root"},
    {"name": "financial_balancesheet", "desc": "财务数据-资产负债表", "paths": [f"{FINANCIAL_BASE}/balancesheet/*.csv", f"{FINANCIAL_BASE}/balancesheet/*.parquet"], "type": "glob", "critical": False, "root": "financial_data_root"},
    {"name": "financial_cashflow", "desc": "财务数据-现金流量表",     "paths": [f"{FINANCIAL_BASE}/cashflow/*.csv", f"{FINANCIAL_BASE}/cashflow/*.parquet"], "type": "glob", "critical": False, "root": "financial_data_root"},
    {"name": "financial_disclosure_date", "desc": "财务数据-财报披露日期", "paths": [f"{FINANCIAL_BASE}/disclosure_date/*.csv", f"{FINANCIAL_BASE}/disclosure_date/*.parquet"], "type": "glob", "critical": False, "root": "financial_data_root"},
    {"name": "financial_express", "desc": "财务数据-业绩快报",       "paths": [f"{FINANCIAL_BASE}/express/*.csv", f"{FINANCIAL_BASE}/express/*.parquet"], "type": "glob", "critical": False, "root": "financial_data_root"},
    {"name": "financial_fina_audit", "desc": "财务数据-审计意见",    "paths": [f"{FINANCIAL_BASE}/fina_audit/*.csv", f"{FINANCIAL_BASE}/fina_audit/*.parquet"], "type": "glob", "critical": False, "root": "financial_data_root"},
    {"name": "financial_fina_indicator", "desc": "财务数据-财务指标", "paths": [f"{FINANCIAL_BASE}/fina_indicator/*.csv", f"{FINANCIAL_BASE}/fina_indicator/*.parquet"], "type": "glob", "critical": False, "root": "financial_data_root"},
    {"name": "financial_fina_mainbz", "desc": "财务数据-主营业务构成", "paths": [f"{FINANCIAL_BASE}/fina_mainbz/*.csv", f"{FINANCIAL_BASE}/fina_mainbz/*.parquet"], "type": "glob", "critical": False, "root": "financial_data_root"},
    {"name": "financial_forecast", "desc": "财务数据-业绩预告",       "paths": [f"{FINANCIAL_BASE}/forecast/*.csv", f"{FINANCIAL_BASE}/forecast/*.parquet"], "type": "glob", "critical": False, "root": "financial_data_root"},
    {"name": "pre_collected",    "desc": "预收集数据",             "paths": [f"{STOCK_BASE}/pre_collected/**"], "type": "glob_recursive", "critical": False, "root": "stock_data_root"},
]

DATE_FIELDS = ("trade_date", "ann_date", "end_date", "date", "cal_date")
FILENAME_DATE_RE = re.compile(r"(20\d{6})")

def scan_one(definition: dict) -> dict:
    """
扫描单个数据类型的存在性和规模"""
    matches = _collect_matches(definition)
    found = bool(matches)
    sample = matches[0] if matches else ""
    count = len(matches)
    total_size = 0
    latest_trade_date = None
    latest_file = ""

    if found:
        try:
            total_size = sum(os.path.getsize(path) for path in matches if os.path.isfile(path))
        except OSError:
            total_size = 0
        latest_trade_date, latest_file = _resolve_latest_trade_date(matches)

    return {
        "name": definition["name"],
        "desc": definition["desc"],
        "critical": definition["critical"],
        "root": definition.get("root", "stock_data_root"),
        "exists": found,
        "count": count,
        "sample": sample,
        "total_size": total_size,
        "latest_trade_date": latest_trade_date,
        "latest_file": latest_file,
    }


def _collect_matches(definition: dict) -> list[str]:
    matches: list[str] = []
    recursive = definition["type"] == "glob_recursive"
    for pattern in definition["paths"]:
        matches.extend(glob.glob(pattern, recursive=recursive))
    return sorted(set(matches))


from typing import Optional

def _normalize_date_text(raw: str) -> Optional[str]:
    digits = "".join(ch for ch in str(raw).strip() if ch.isdigit())
    if len(digits) < 8:
        return None
    compact = digits[:8]
    if not compact.startswith("20"):
        return None
    return compact


def _date_from_filename(path: str) -> Optional[str]:
    hit = FILENAME_DATE_RE.search(Path(path).name)
    if not hit:
        return None
    return hit.group(1)


def _candidate_dates_from_path(path: str) -> list[str]:
    """从文件路径中提取可能的日期候选。

    关键陷阱：大量核心数据（daily、stk_factor_pro、margin_detail 等）采用
    ``{prefix}_{ts_code}.csv`` 命名 + 按年份分子目录的结构，文件名本身不含日期。
    因此不能只依赖 ``FILENAME_DATE_RE`` 匹配文件名，还必须识别路径中的年份目录
    以及 CSV 内容中的 ``trade_date`` 列（见 ``_read_latest_date_from_csv``）。
    """
    candidates: list[str] = []
    filename_date = _date_from_filename(path)
    if filename_date:
        candidates.append(filename_date)

    parts = Path(path).parts
    for idx in range(len(parts) - 3):
        y, m, d = parts[idx:idx + 3]
        if y.isdigit() and len(y) == 4 and m.isdigit() and len(m) == 2 and d.isdigit() and len(d) == 2:
            candidates.append(f"{y}{m}{d}")

    # 识别按年份分目录的结构，如 daily/2026/daily_600103.SH.csv
    for part in parts:
        if re.fullmatch(r"20\d{2}", part):
            candidates.append(f"{part}0101")

    return candidates


def _read_latest_date_from_csv(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return None
            fields = [field for field in DATE_FIELDS if field in reader.fieldnames]
            if not fields:
                return None
            latest: Optional[str] = None
            for row in reader:
                for field in fields:
                    normalized = _normalize_date_text(row.get(field) or "")
                    if normalized and (latest is None or normalized > latest):
                        latest = normalized
            return latest
    except Exception:
        return None


def _resolve_latest_trade_date(matches: list[str]) -> tuple[Optional[str], str]:
    latest: Optional[str] = None
    latest_file = ""
    csv_files: list[str] = []
    for path in matches:
        path_latest: Optional[str] = None
        for candidate in _candidate_dates_from_path(path):
            if path_latest is None or candidate > path_latest:
                path_latest = candidate
        if path.endswith(".csv"):
            csv_files.append(path)
        if path_latest and (latest is None or path_latest > latest):
            latest = path_latest
            latest_file = path

    def _mtime_key(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    for path in sorted(csv_files, key=_mtime_key, reverse=True)[:20]:
        path_latest = _read_latest_date_from_csv(path)
        if path_latest and (latest is None or path_latest > latest):
            latest = path_latest
            latest_file = path
    return latest, latest_file


def scan_symbol_specific(symbol: str) -> dict:
    """扫描特定股票在各类数据中的具体文件"""
    checks = {
        "daily": f"{STOCK_BASE}/daily/**/daily_{symbol}.csv",
        "daily_basic": f"{STOCK_BASE}/daily_basic/**/daily_basic_{symbol}.csv",
        "moneyflow_individual_tushare": f"{STOCK_BASE}/moneyflow_data/individual/tushare/**/moneyflow_*.csv",
        "cyq_chips": f"{STOCK_BASE}/cyq_chips/**/cyq_chips_{symbol}.csv",
        "margin_by_stock": f"{STOCK_BASE}/margin/**/margin_{symbol}.csv",
        "margin_detail": f"{STOCK_BASE}/margin_detail/**/margin_detail_{symbol}*",
        "stk_auction_o": f"{STOCK_BASE}/stk_auction_o/**/stk_auction_o_{symbol}.csv",
        "stk_auction_c": f"{STOCK_BASE}/stk_auction_c/**/stk_auction_c_{symbol}.csv",
    }
    results = {}
    for name, pattern in checks.items():
        matches = glob.glob(pattern, recursive=True)
        latest_trade_date, latest_file = _resolve_latest_trade_date(matches) if matches else (None, "")
        results[name] = {
            "exists": bool(matches),
            "count": len(matches),
            "sample": matches[0] if matches else "",
            "latest_trade_date": latest_trade_date,
            "latest_file": latest_file,
        }
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="本地数据资产扫描")
    parser.add_argument("--symbol", default="", help="指定股票代码进行精确扫描 (如 600103.SH)")
    parser.add_argument("--json", action="store_true", help="输出 JSON 格式")
    args = parser.parse_args()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    results = []

    # 1. 扫描所有数据类型
    for d in DATA_DEFS:
        r = scan_one(d)
        results.append(r)

    # 2. 如果指定了股票，做精确扫描
    symbol_results = {}
    if args.symbol:
        symbol_results = scan_symbol_specific(args.symbol)

    # 输出
    if args.json:
        output = {
            "scan_time": now,
            "stock_data_root": STOCK_BASE,
            "index_data_root": INDEX_BASE,
            "news_data_root": NEWS_BASE,
            "summary": {
                "total": len(results),
                "available": sum(1 for r in results if r["exists"]),
                "missing": sum(1 for r in results if not r["exists"]),
                "critical_missing": [r["desc"] for r in results if not r["exists"] and r["critical"]],
            },
            "data_types": results,
            "symbol_specific": symbol_results if args.symbol else None,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    # 人类可读输出
    print(f"\n【本地数据资产扫描报告】")
    print(f"扫描时间: {now}")
    print(f"stock_data_root: {STOCK_BASE}")
    print(f"index_data_root: {INDEX_BASE}")
    print(f"news_data_root: {NEWS_BASE}")
    if args.symbol:
        print(f"精确股票: {args.symbol}")
    print("=" * 80)

    print(f"\n{'数据类型':<28} {'根目录':<18} {'状态':<8} {'重要':<6} {'数量':<8} {'最新日期':<10} {'示例路径'}")
    print("-" * 80)
    for r in results:
        status = "可用" if r["exists"] else "缺失"
        critical = "*" if r["critical"] else ""
        count = str(r["count"]) if r["exists"] else "0"
        latest = r["latest_trade_date"] or "-"
        sample = r["sample"][-50:] if r["sample"] else ""
        print(f"{r['desc']:<28} {r['root']:<18} {status:<8} {critical:<6} {count:<8} {latest:<10} {sample}")
    print("=" * 80)

    total = len(results)
    available = sum(1 for r in results if r["exists"])
    missing = total - available
    critical_missing = [r["desc"] for r in results if not r["exists"] and r["critical"]]
    print(f"\n总计: {total} 类数据 | 可用: {available} | 缺失: {missing}")
    if critical_missing:
        print(f"\n⚠️ 严重缺失 (强制前置模块): {', '.join(critical_missing)}")

    # 精确股票扫描
    if args.symbol and symbol_results:
        print(f"\n【{args.symbol} 精确扫描】")
        for name, r in symbol_results.items():
            status = "EXISTS" if r["exists"] else "MISSING"
            latest = r.get("latest_trade_date") or "-"
            print(f"  {name}: {status} ({r['count']} files, latest={latest})")
            if r["sample"]:
                print(f"    -> {r['sample']}")

    print("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
