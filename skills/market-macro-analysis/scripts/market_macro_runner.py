#!/usr/bin/env python3
"""
大盘板块分析入口（DC 数据驱动）。

用法：
    python market_macro_runner.py                    # 分析当前大盘
    python market_macro_runner.py --date 2026-05-28  # 指定日期
    python market_macro_runner.py --format json       # JSON 输出
    python market_macro_runner.py --top 10            # 显示 TOP 10 板块

数据源：
    - 腾讯 API：大盘指数实时行情
    - dc_concept：题材热度排行
    - dc_daily：概念板块日行情（OHLCV）
    - dc_member：概念板块成分股
    - dc_index：概念指数基本信息
    - kpl_list：涨停数据（情绪指标）
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ── 路径设置 ──────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
META_ROOT = SCRIPT_DIR.parents[2]  # market-analyzer-meta/
SDA_SCRIPTS = META_ROOT / "skills" / "stock-deep-analysis" / "scripts"
sys.path.insert(0, str(SDA_SCRIPTS))

from common import normalize_trade_date
from data.data_access import resolve_trade_date_by_calendar, latest_open_trade_date_on_or_before
from time_util import scenario_from_now
from runtime.runtime_fetch import resolve_now_china

# DC 数据根目录
THEME_DATA_ROOT = Path.home() / "quant-data" / "tushare" / "股票数据" / "theme_data"


# ═══════════════════════════════════════════════════════
# Step 1: 大盘环境
# ═══════════════════════════════════════════════════════

def fetch_index_quotes(trade_date_compact: str) -> dict[str, dict]:
    """获取指数行情：
    - 盘中/最近交易日：用 Tushare rt_idx_k（实时日线）
    - 历史日期：用本地 parquet，缺失时用 Tushare index_daily 补全
    """
    import pyarrow.parquet as pq

    # 判断是否为最近交易日（盘中可用实时数据）
    now, _ = resolve_now_china()
    latest_open = latest_open_trade_date_on_or_before(now.strftime("%Y-%m-%d"))
    is_latest = trade_date_compact == (latest_open or "").replace("-", "")
    session = scenario_from_now(now)
    is_intraday = session in ("上午盘中", "下午盘中", "午间休盘")

    if is_latest and is_intraday:
        # 盘中：用 rt_idx_k 实时日线
        return _fetch_realtime_idx()

    # 非盘中或历史日期：本地 parquet 优先
    index_root = Path.home() / "quant-data" / "tushare" / "指数数据" / "index_daily"
    codes = {"上证指数": "000001.SH", "深证成指": "399001.SZ", "创业板指": "399006.SZ"}
    result = {}
    missing = []

    for name, code in codes.items():
        path = index_root / f"{code}.parquet"
        if not path.exists():
            missing.append((name, code))
            continue
        try:
            df = pq.read_table(path).to_pandas()
            row = df[df["trade_date"].astype(str) == trade_date_compact]
            if row.empty:
                missing.append((name, code))
                continue
            r = row.iloc[0]
            result[name] = {
                "close": float(r.get("close", 0) or 0),
                "pct_change": float(r.get("pct_chg", 0) or 0),
                "amount_yi": round(float(r.get("amount", 0) or 0) / 1e5, 2),
                "data_type": "local",
            }
        except Exception:
            missing.append((name, code))

    # 本地缺失时用 Tushare index_daily API 补全
    if missing:
        _supplement_with_tushare(result, trade_date_compact, {name: code for name, code in missing})

    return result


def _fetch_realtime_idx() -> dict[str, dict]:
    """用 Tushare rt_idx_k 获取实时指数行情。"""
    try:
        import sys
        tushare_dir = str(META_ROOT / "skills" / "tushare-pro")
        if tushare_dir not in sys.path:
            sys.path.insert(0, tushare_dir)
        from utils.tushare_client import create_pro_api
        pro = create_pro_api(timeout=15)
        df = pro.rt_idx_k(ts_code="000001.SH,399001.SZ,399006.SZ")
    except Exception:
        return {}

    if df.empty:
        return {}

    name_map = {"000001.SH": "上证指数", "399001.SZ": "深证成指", "399006.SZ": "创业板指"}
    result = {}
    for _, r in df.iterrows():
        ts_code = str(r.get("ts_code", ""))
        name = name_map.get(ts_code, ts_code)
        close = float(r.get("close", 0) or 0)
        pre_close = float(r.get("pre_close", 0) or 0)
        pct_change = round((close - pre_close) / pre_close * 100, 2) if pre_close else None
        amount = float(r.get("amount", 0) or 0)
        result[name] = {
            "close": close,
            "pct_change": pct_change,
            "amount_yi": round(amount / 1e8, 2),
            "data_type": "realtime",
        }
    return result


def _supplement_with_tushare(result: dict, trade_date_compact: str, missing_names: dict) -> None:
    """用 Tushare index_daily API 补全缺失的指数数据。"""
    try:
        import sys
        tushare_dir = str(META_ROOT / "skills" / "tushare-pro")
        if tushare_dir not in sys.path:
            sys.path.insert(0, tushare_dir)
        from utils.tushare_client import create_pro_api
        pro = create_pro_api(timeout=15)
    except Exception:
        return

    for name, ts_code in missing_names.items():
        try:
            df = pro.index_daily(ts_code=ts_code, trade_date=trade_date_compact)
            if df.empty:
                continue
            r = df.iloc[0]
            result[name] = {
                "close": float(r.get("close", 0) or 0),
                "pct_change": float(r.get("pct_chg", 0) or 0),
                "amount_yi": round(float(r.get("amount", 0) or 0) / 1e5, 2),  # Tushare amount 单位是千元
                "data_type": "tushare",
            }
        except Exception:
            continue


def analyze_market_environment(trade_date_compact: str) -> dict[str, Any]:
    """Step 1: 大盘环境分析。"""
    quotes = fetch_index_quotes(trade_date_compact)
    if not quotes:
        return {"status": "error", "reason": "无法获取指数数据"}

    pct_values = [q.get("pct_change", 0) for q in quotes.values() if q.get("pct_change") is not None]
    avg_pct = sum(pct_values) / len(pct_values) if pct_values else 0

    if avg_pct > 1.0:
        strength = "偏强"
    elif avg_pct > 0:
        strength = "中性偏强"
    elif avg_pct > -1.0:
        strength = "中性偏弱"
    else:
        strength = "偏弱"

    if all(p > 0 for p in pct_values):
        resonance = "三大指数共振上涨"
    elif all(p < 0 for p in pct_values):
        resonance = "三大指数共振下跌"
    else:
        resonance = "指数分化"

    total_amount = sum(q.get("amount_yi") or 0 for q in quotes.values())

    return {
        "status": "available",
        "quotes": quotes,
        "strength": strength,
        "resonance": resonance,
        "avg_pct_change": round(avg_pct, 2),
        "total_amount_yi": round(total_amount, 2),
        "summary": f"市场整体{strength}，{resonance}，两市成交额约{total_amount:.0f}亿",
    }


# ═══════════════════════════════════════════════════════
# Step 2: 板块热点分析（DC 数据驱动）
# ═══════════════════════════════════════════════════════

def load_dc_concept(trade_date_compact: str) -> list[dict]:
    """加载 DC 题材数据（dc_concept）。"""
    import pyarrow.parquet as pq

    path = THEME_DATA_ROOT / "dc_concept" / "2026.parquet"
    if not path.exists():
        return []

    df = pq.read_table(path).to_pandas()
    day_df = df[df["trade_date"] == trade_date_compact]
    if day_df.empty:
        latest = df["trade_date"].max()
        day_df = df[df["trade_date"] == latest]

    records = []
    for _, row in day_df.iterrows():
        records.append({
            "theme_code": str(row.get("theme_code", "")),
            "theme_name": str(row.get("name", "")),
            "theme_hot": float(row.get("hot", 0) or 0),
            "theme_top": str(row.get("lead_stock", "")),
            "theme_num": 0,
            "pct_change": float(row.get("pct_change", 0) or 0),
            "hot": float(row.get("hot", 0) or 0),
            "strength": float(row.get("strength", 0) or 0),
            "z_t_num": float(row.get("z_t_num", 0) or 0),
            "lead_stock": str(row.get("lead_stock", "")),
            "lead_stock_code": str(row.get("lead_stock_code", "")),
            "lead_stock_pct_change": float(row.get("lead_stock_pct_change", 0) or 0),
        })

    return records


# ── 涨停数据加载 ──────────────────────────────────────

STOCK_DATA_ROOT = Path.home() / "quant-data" / "tushare" / "股票数据"


def load_limit_cpt_list(trade_date_compact: str) -> list[dict]:
    """加载板块级涨停统计（limit_cpt_list）。"""
    import pyarrow.parquet as pq

    path = STOCK_DATA_ROOT / "limit_cpt_list" / "limit_cpt_list.parquet"
    if not path.exists():
        return []

    df = pq.read_table(path).to_pandas()
    day_df = df[df["trade_date"].astype(str) == trade_date_compact]
    if day_df.empty:
        return []

    records = []
    for _, row in day_df.iterrows():
        records.append({
            "ts_code": str(row.get("ts_code", "")),
            "name": str(row.get("name", "")),
            "days": int(row.get("days", 0) or 0),
            "up_stat": str(row.get("up_stat", "")),
            "cons_nums": int(row.get("cons_nums", 0) or 0),
            "up_nums": int(row.get("up_nums", 0) or 0),
            "pct_chg": float(row.get("pct_chg", 0) or 0),
            "rank": int(row.get("rank", 0) or 0),
        })
    records.sort(key=lambda x: x["up_nums"], reverse=True)
    return records


def load_limit_list_d(trade_date_compact: str) -> list[dict]:
    """加载涨停个股明细（limit_list_d 东财格式）。"""
    import pyarrow.parquet as pq

    path = STOCK_DATA_ROOT / "limit_list_d" / "limit_list_d.parquet"
    if not path.exists():
        return []

    df = pq.read_table(path).to_pandas()
    day_df = df[df["trade_date"].astype(str) == trade_date_compact]
    if day_df.empty:
        return []

    records = []
    for _, row in day_df.iterrows():
        records.append({
            "ts_code": str(row.get("ts_code", "")),
            "name": str(row.get("name", "")),
            "industry": str(row.get("industry", "")),
            "close": float(row.get("close", 0) or 0),
            "pct_chg": float(row.get("pct_chg", 0) or 0),
            "amount": float(row.get("amount", 0) or 0),
            "first_time": str(row.get("first_time", "")),
            "last_time": str(row.get("last_time", "")),
            "open_times": int(row.get("open_times", 0) or 0),
            "up_stat": str(row.get("up_stat", "")),
            "limit_times": float(row.get("limit_times", 0) or 0),
            "limit": str(row.get("limit", "")),
            "turnover_ratio": float(row.get("turnover_ratio", 0) or 0),
        })
    return records


def load_limit_list_ths(trade_date_compact: str) -> list[dict]:
    """加载涨停个股明细（limit_list_ths 同花顺格式，含封单比/强度）。"""
    import pyarrow.parquet as pq

    year = trade_date_compact[:4]
    path = STOCK_DATA_ROOT / "limit_list_ths" / f"{year}.parquet"
    if not path.exists():
        return []

    df = pq.read_table(path).to_pandas()
    day_df = df[df["trade_date"].astype(str) == trade_date_compact]
    if day_df.empty:
        return []

    records = []
    for _, row in day_df.iterrows():
        limit_val = str(row.get("limit", ""))
        # 只取涨停（U），跳过跌停（D）
        if limit_val != "U":
            continue
        records.append({
            "ts_code": str(row.get("ts_code", "")),
            "name": str(row.get("name", "")),
            "close": float(row.get("close", 0) or 0),
            "pct_chg": float(row.get("pct_chg", 0) or 0),
            "fc_ratio": float(row.get("fc_ratio", 0) or 0),
            "fl_ratio": float(row.get("fl_ratio", 0) or 0),
            "strth": float(row.get("strth", 0) or 0),
            "lu_desc": str(row.get("lu_desc", "")),
            "tag": str(row.get("tag", "")),
            "status": str(row.get("status", "")),
            "first_time": str(row.get("first_time", "")),
            "last_time": str(row.get("last_time", "")),
            "open_times": int(row.get("open_times", 0) or 0),
            "turnover_rate": float(row.get("turnover_rate", 0) or 0),
        })
    return records


def load_limit_step(trade_date_compact: str) -> list[dict]:
    """加载连板阶梯数据（limit_step）。"""
    import pyarrow.parquet as pq

    year = trade_date_compact[:4]
    path = STOCK_DATA_ROOT / "limit_step" / f"{year}.parquet"
    if not path.exists():
        # 尝试 CSV
        csv_path = STOCK_DATA_ROOT / "limit_step" / "limit_step.csv"
        if csv_path.exists():
            import pandas as pd
            df = pd.read_csv(csv_path)
        else:
            return []
    else:
        df = pq.read_table(path).to_pandas()

    day_df = df[df["trade_date"].astype(str) == trade_date_compact]
    if day_df.empty:
        # 尝试最新日期
        latest = df["trade_date"].max()
        day_df = df[df["trade_date"] == latest]

    records = []
    for _, row in day_df.iterrows():
        records.append({
            "ts_code": str(row.get("ts_code", "")),
            "name": str(row.get("name", "")),
            "nums": int(row.get("nums", 0) or 0),
        })
    records.sort(key=lambda x: x["nums"], reverse=True)
    return records


def load_dc_daily(trade_date_compact: str) -> list[dict]:
    """加载 DC 概念板块日行情（dc_daily）。"""
    daily_root = THEME_DATA_ROOT / "dc_daily"
    if not daily_root.exists():
        return []

    records = []
    csv_files = list(daily_root.glob("*.csv"))

    for f in csv_files[:500]:  # 限制文件数避免太慢
        try:
            import pandas as pd
            df = pd.read_csv(f)
            day_df = df[df["trade_date"].astype(str) == trade_date_compact]
            if day_df.empty:
                continue
            row = day_df.iloc[-1]
            records.append({
                "ts_code": str(row.get("ts_code", "")),
                "close": float(row.get("close", 0) or 0),
                "pct_change": float(row.get("pct_change", 0) or 0),
                "vol": float(row.get("vol", 0) or 0),
                "amount": float(row.get("amount", 0) or 0),
                "swing": float(row.get("swing", 0) or 0),
                "turnover_rate": float(row.get("turnover_rate", 0) or 0),
                "category": str(row.get("category", "")),
            })
        except Exception:
            continue

    return records


def load_dc_member_concept(trade_date_compact: str, theme_code: str) -> list[dict]:
    """加载 DC 概念成分股（dc_member）。"""
    import pandas as pd

    member_root = THEME_DATA_ROOT / "dc_member"
    if not member_root.exists():
        return []

    # 尝试 parquet
    parquet_path = member_root / "2026.parquet"
    if parquet_path.exists():
        import pyarrow.parquet as pq
        df = pq.read_table(parquet_path).to_pandas()
        filtered = df[df["ts_code"] == theme_code]
        if not filtered.empty:
            # 取最新日期
            latest = filtered["trade_date"].max()
            filtered = filtered[filtered["trade_date"] == latest]
            return [
                {"con_code": str(r["con_code"]), "name": str(r["name"])}
                for _, r in filtered.iterrows()
            ]

    # 尝试 CSV
    csv_path = member_root / f"{theme_code}.csv"
    if csv_path.exists():
        df = pd.read_csv(csv_path)
        latest = df["trade_date"].max()
        filtered = df[df["trade_date"] == latest]
        return [
            {"con_code": str(r["con_code"]), "name": str(r["name"])}
            for _, r in filtered.iterrows()
        ]

    return []


def analyze_sector_hotspots(trade_date_compact: str, top_n: int = 10) -> dict[str, Any]:
    """Step 2: 板块热点分析（DC 题材 + 概念板块 + 涨停数据）。"""

    # ── 题材层（dc_concept）──
    concepts = load_dc_concept(trade_date_compact)
    if not concepts:
        return {"status": "missing", "reason": "无 DC 题材数据", "themes": [], "sectors": []}

    concepts.sort(key=lambda x: x.get("hot", 0), reverse=True)
    hot_themes = concepts[:top_n]

    # ── 概念板块层（dc_daily）──
    daily_records = load_dc_daily(trade_date_compact)
    daily_records.sort(key=lambda x: x.get("pct_change", 0), reverse=True)
    hot_sectors = daily_records[:top_n]

    # ── 涨停数据 ──
    limit_cpt = load_limit_cpt_list(trade_date_compact)  # 板块级涨停统计
    limit_d = load_limit_list_d(trade_date_compact)       # 涨停个股明细（东财）
    limit_ths = load_limit_list_ths(trade_date_compact)   # 涨停个股明细（同花顺）
    limit_step = load_limit_step(trade_date_compact)       # 连板阶梯
    kpl_data = load_kpl_list(trade_date_compact)           # 开盘啦涨停

    # ── 涨停统计 ──
    total_lu = len(limit_d) if limit_d else 0
    first_board = len([r for r in limit_d if r.get("up_stat", "") == "首板"]) if limit_d else 0
    continuous_board = len([r for r in limit_d if r.get("limit_times", 0) > 1]) if limit_d else 0

    # 涨停题材分布（从 limit_cpt_list 取）
    lu_themes = []
    if limit_cpt:
        for r in limit_cpt[:5]:
            lu_themes.append({
                "name": r["name"],
                "up_nums": r["up_nums"],
                "up_stat": r["up_stat"],
            })

    # ── 板块轮动阶段判断 ──
    total_hot = sum(t.get("hot", 0) for t in concepts[:10])
    top3_hot = sum(t.get("hot", 0) for t in concepts[:3])
    concentration = top3_hot / total_hot if total_hot > 0 else 0

    if concentration > 0.5:
        cycle = "加强"
    elif concentration > 0.35:
        cycle = "分化"
    else:
        cycle = "轮动"

    return {
        "status": "available",
        "trade_date": trade_date_compact,
        "themes": hot_themes,
        "sectors": hot_sectors,
        "limit_cpt": limit_cpt,
        "limit_d": limit_d,
        "limit_ths": limit_ths,
        "limit_step": limit_step,
        "kpl": kpl_data,
        "lu_stats": {
            "total": total_lu,
            "first_board": first_board,
            "continuous": continuous_board,
            "themes": lu_themes,
        },
        "cycle": cycle,
        "concentration": round(concentration, 2),
        "top_theme": hot_themes[0]["theme_name"] if hot_themes else None,
        "top_sector": hot_sectors[0]["ts_code"] if hot_sectors else None,
    }


# ═══════════════════════════════════════════════════════
# Step 3: 龙头个股
# ═══════════════════════════════════════════════════════

def find_leading_stocks(sector_result: dict, trade_date_compact: str, top_n: int = 5) -> list[dict]:
    """Step 3: 从热点板块 + 连板数据中找龙头个股。"""
    leading = []
    seen_codes = set()

    # ── 连板股优先（limit_step）──
    limit_step = sector_result.get("limit_step", [])
    for s in limit_step[:5]:
        code = s.get("ts_code", "")
        if code and code not in seen_codes and s.get("nums", 0) >= 2:
            seen_codes.add(code)
            leading.append({
                "ts_code": code,
                "name": s.get("name", ""),
                "source_theme": "连板",
                "role": f"{s['nums']}连板",
                "pct_change": 0,
            })

    # ── 涨停强度股（limit_list_ths，封单比高）──
    limit_ths = sector_result.get("limit_ths", [])
    if limit_ths:
        # 按封单比排序
        ths_sorted = sorted(limit_ths, key=lambda x: x.get("fc_ratio", 0), reverse=True)
        for r in ths_sorted[:3]:
            code = r.get("ts_code", "")
            if code and code not in seen_codes:
                seen_codes.add(code)
                leading.append({
                    "ts_code": code,
                    "name": r.get("name", ""),
                    "source_theme": r.get("lu_desc", "涨停"),
                    "role": "强封板",
                    "pct_change": r.get("pct_chg", 0),
                    "fc_ratio": r.get("fc_ratio", 0),
                    "strth": r.get("strth", 0),
                })

    # ── 从题材层取 lead_stock ──
    for theme in sector_result.get("themes", [])[:5]:
        code = theme.get("lead_stock_code", "")
        name = theme.get("lead_stock", "")
        if code and code not in seen_codes:
            seen_codes.add(code)
            leading.append({
                "ts_code": code,
                "name": name,
                "source_theme": theme.get("theme_name", ""),
                "role": "题材龙头",
                "pct_change": theme.get("lead_stock_pct_change", 0),
            })

    # ── 从概念板块成分股中补充 ──
    for theme in sector_result.get("themes", [])[:3]:
        theme_code = theme.get("theme_code", "")
        if not theme_code:
            continue
        members = load_dc_member_concept(trade_date_compact, theme_code)
        for m in members[:3]:
            code = m.get("con_code", "")
            if code and code not in seen_codes:
                seen_codes.add(code)
                leading.append({
                    "ts_code": code,
                    "name": m.get("name", ""),
                    "source_theme": theme.get("theme_name", ""),
                    "role": "板块前排",
                    "pct_change": 0,
                })

    return leading[:top_n]


# ═══════════════════════════════════════════════════════
# 涨停数据（kpl_list）
# ═══════════════════════════════════════════════════════

def load_kpl_list(trade_date_compact: str) -> dict[str, Any]:
    """加载涨停数据（kpl_list）。"""
    import pandas as pd

    kpl_root = THEME_DATA_ROOT / "kpl_list"
    if not kpl_root.exists():
        return {"status": "missing"}

    year = trade_date_compact[:4]
    csv_path = kpl_root / year / f"{trade_date_compact}.csv"
    parquet_path = kpl_root / f"{year}.parquet"

    df = None
    if csv_path.exists():
        df = pd.read_csv(csv_path)
    elif parquet_path.exists():
        import pyarrow.parquet as pq
        df = pq.read_table(parquet_path).to_pandas()
        df = df[df["trade_date"].astype(str) == trade_date_compact]

    if df is None or df.empty:
        return {"status": "missing", "reason": "无涨停数据"}

    # 统计
    total_lu = len(df)
    first_board = len(df[df.get("status", "") == "首板"]) if "status" in df.columns else 0
    themes = []
    if "theme" in df.columns:
        theme_counts = df["theme"].value_counts().head(5)
        themes = [{"name": t, "count": int(c)} for t, c in theme_counts.items() if pd.notna(t)]

    return {
        "status": "available",
        "total_lu": total_lu,
        "first_board": first_board,
        "themes": themes,
    }


# ═══════════════════════════════════════════════════════
# Step 4: 消息催化
# ═══════════════════════════════════════════════════════

def analyze_news_catalysts(trade_date_text: str) -> dict[str, Any]:
    """Step 4: 消息催化分析（本地 news_pipeline）。"""
    news_root = Path.home() / "quant-data" / "tushare" / "消息面数据" / "raw" / "news_pipeline"
    td_parts = trade_date_text.split("-")
    if len(td_parts) != 3:
        return {"status": "missing", "reason": "日期格式错误"}

    news_dir = news_root / td_parts[0] / td_parts[1] / td_parts[2]
    if not news_dir.exists():
        return {"status": "missing", "reason": "本地新闻目录不存在"}

    news_files = list(news_dir.glob("*.json"))
    if not news_files:
        return {"status": "missing", "reason": "无新闻文件"}

    all_items = []
    for f in news_files:
        try:
            with f.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    all_items.extend(data)
                elif isinstance(data, dict):
                    all_items.append(data)
        except Exception:
            continue

    return {
        "status": "available" if all_items else "missing",
        "count": len(all_items),
        "files": len(news_files),
    }


# ═══════════════════════════════════════════════════════
# Step 4: 成分股深度分析（复用 stock-deep-analysis 模块）
# ═══════════════════════════════════════════════════════

def analyze_stock_details(leading: list[dict], trade_date_compact: str) -> list[dict]:
    """对龙头个股做深度分析（跳过市场/板块步骤，直接分析个股）。"""
    from analysis.stock_trend_analyzer import (
        analyze_trend_structure,
        analyze_chip_structure,
        analyze_volatility_context,
    )
    from financing_analyzer import (
        build_fundamental,
        analyze_financing_context,
    )

    details = []
    for stock in leading:
        ts_code = stock.get("ts_code", "")
        if not ts_code:
            continue

        trade_date_text = f"{trade_date_compact[:4]}-{trade_date_compact[4:6]}-{trade_date_compact[6:8]}"
        detail = {"ts_code": ts_code, "name": stock.get("name", "")}

        # 趋势结构
        try:
            trend = analyze_trend_structure(ts_code, trade_date_text)
            detail["trend"] = trend
        except Exception:
            detail["trend"] = {"status": "error"}

        # 筹码分析
        try:
            chip = analyze_chip_structure(ts_code, trade_date_text)
            detail["chip"] = chip
        except Exception:
            detail["chip"] = {"status": "error"}

        # 波动率
        try:
            vol = analyze_volatility_context(ts_code, trade_date_text)
            detail["volatility"] = vol
        except Exception:
            detail["volatility"] = {"status": "error"}

        # 基本面（PE/PB/估值）
        try:
            fund = build_fundamental(ts_code, trade_date_compact)
            detail["fundamental"] = fund
        except Exception:
            detail["fundamental"] = {"status": "error"}

        # 融资融券
        try:
            financing = analyze_financing_context(ts_code, trade_date_text)
            detail["financing"] = financing
        except Exception:
            detail["financing"] = {"status": "error"}

        details.append(detail)

    return details


# ═══════════════════════════════════════════════════════
# Step 6: 交易结论
# ═══════════════════════════════════════════════════════

def generate_conclusion(
    market: dict,
    sectors: dict,
    leading: list,
    news: dict,
    stock_details: list[dict] | None = None,
    agent_direction: dict | None = None,
    agent_news: dict | None = None,
    agent_rotation: dict | None = None,
) -> dict[str, Any]:
    """Step 7: 生成交易结论（融合三个 Agent 结果）。"""
    strength = market.get("strength", "中性")
    top_theme = sectors.get("top_theme", "无")
    cycle = sectors.get("cycle", "未知")
    kpl = sectors.get("kpl", {})
    lu_count = kpl.get("total_lu", 0)

    # 从 Agent-市场方向获取多空判断
    overall = (agent_direction or {}).get("overall", {})
    direction = overall.get("direction", "中性")
    pullback_status = overall.get("pullback_status", "未知")

    # 从 Agent-消息热点获取风险评估
    risk = (agent_news or {}).get("risk_assessment", {})
    overall_risk = risk.get("overall_risk", "未知")
    high_risk_sectors = risk.get("high_risk_sectors", [])
    money_flow = (agent_news or {}).get("money_flow_prediction", {})

    # 从 Agent-板块轮动获取轮动预判
    rotation = (agent_rotation or {}).get("rotation_status", "未知")
    rotation_pred = (agent_rotation or {}).get("rotation_prediction", {})
    pullback_ready = [p["name"] for p in (agent_rotation or {}).get("pullback_ready", [])[:3]]

    # 综合判断
    if direction in ("偏多", "中性偏多") and overall_risk == "低":
        action = "可积极参与"
    elif direction == "中性" and overall_risk == "中等":
        action = "观望为主，等方向确认"
    elif direction in ("偏弱", "中性偏弱") or overall_risk == "高":
        action = "防守为主"
    else:
        action = "观望为主"

    summary_parts = [
        f"市场{direction}，{action}",
        f"热点题材：{top_theme}（{cycle}阶段）",
    ]
    if lu_count > 0:
        summary_parts.append(f"涨停{lu_count}家")
    if high_risk_sectors:
        summary_parts.append(f"高位风险：{','.join(high_risk_sectors[:2])}")
    if pullback_ready:
        summary_parts.append(f"回调到位：{','.join(pullback_ready[:2])}")
    if money_flow.get("flow_direction"):
        summary_parts.append(money_flow["flow_direction"])

    return {
        "direction": direction,
        "action": action,
        "top_theme": top_theme,
        "cycle": cycle,
        "rotation": rotation,
        "risk_level": overall_risk,
        "pullback_status": pullback_status,
        "pullback_ready": pullback_ready,
        "money_flow": money_flow,
        "rotation_prediction": rotation_pred,
        "leading_stocks": leading[:5],
        "summary": "；".join(summary_parts),
    }


# ═══════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════

def build_market_macro_report(trade_date_text: str, top_n: int = 10) -> dict[str, Any]:
    """大盘板块分析主流程。"""
    now, time_source = resolve_now_china()
    # 交易日校准
    resolved_date, cal_meta = resolve_trade_date_by_calendar(trade_date_text)
    trade_date_compact = resolved_date.replace("-", "")

    # session 判断：先看今天是不是交易日，非交易日一律盘后
    today_open = latest_open_trade_date_on_or_before(now.strftime("%Y-%m-%d"))
    is_today_trade_day = today_open == now.strftime("%Y-%m-%d")
    if is_today_trade_day:
        raw_session = scenario_from_now(now)
        session = raw_session if raw_session in ("上午盘中", "午间休盘", "下午盘中") else "盘后"
    else:
        session = "盘后"

    # Step 1: 大盘环境（传入交易日，区分实时/历史）
    market = analyze_market_environment(trade_date_compact)

    # Step 2: 板块热点
    sectors = analyze_sector_hotspots(trade_date_compact, top_n)

    # Step 3: 龙头个股
    leading = find_leading_stocks(sectors, trade_date_compact)

    # Step 4: 三个 Agent 并行执行
    from concurrent.futures import ThreadPoolExecutor
    from agents.market_direction_agent import analyze_market_direction
    from agents.news_hotspot_agent import analyze_news_hotspot
    from agents.sector_rotation_agent import analyze_sector_rotation

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_direction = executor.submit(analyze_market_direction, trade_date_compact)
        future_news = executor.submit(analyze_news_hotspot, trade_date_compact)
        future_rotation = executor.submit(analyze_sector_rotation, trade_date_compact)

        agent_direction = future_direction.result()
        agent_news = future_news.result()
        agent_rotation = future_rotation.result()

    # Step 5: 成分股深度分析（复用 stock-deep-analysis 模块）
    stock_details = analyze_stock_details(leading[:5], trade_date_compact)

    # Step 6: 消息催化
    news = analyze_news_catalysts(trade_date_text)

    # Step 7: 交易结论（融合三个 Agent 结果）
    conclusion = generate_conclusion(market, sectors, leading, news, stock_details, agent_direction, agent_news, agent_rotation)

    return {
        "analysis_type": "market_macro",
        "trade_date": resolved_date,
        "requested_trade_date": trade_date_text,
        "calendar_resolution": cal_meta,
        "analysis_time": now.isoformat(timespec="seconds"),
        "time_source": time_source,
        "session": session,
        "market_environment": market,
        "sector_hotspots": sectors,
        "leading_stocks": leading,
        "stock_details": stock_details,
        "news_catalysts": news,
        "agent_direction": agent_direction,
        "agent_news": agent_news,
        "agent_rotation": agent_rotation,
        "conclusion": conclusion,
    }


# ═══════════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════════

def render_markdown(report: dict) -> str:
    """渲染为 Markdown 报告。"""
    lines = [
        "# 大盘板块分析报告",
        "",
        f"> 分析时间：{report.get('analysis_time', 'N/A')}",
        f"> 数据日期：{report.get('trade_date', 'N/A')}",
        f"> 当前时段：{report.get('session', 'N/A')}",
    ]
    # 交易日校准说明
    cal = report.get("calendar_resolution", {})
    if cal.get("adjusted"):
        lines.append(f"> 请求日期：{cal.get('requested_trade_date', 'N/A')}（已校准到最近交易日）")
    # 指数数据类型
    market = report.get("market_environment", {})
    if market.get("quotes"):
        data_type = list(market["quotes"].values())[0].get("data_type", "")
        if data_type == "realtime":
            lines.append(f"> 指数数据：实时行情")
        elif data_type == "historical":
            lines.append(f"> 指数数据：历史 K-line")
    lines.extend(["", "---", ""])

    # Step 1: 大盘环境
    market = report.get("market_environment", {})
    lines.append("## 一、大盘环境")
    lines.append("")
    if market.get("status") == "available":
        lines.append("| 指数 | 收盘 | 涨跌幅 | 成交额 |")
        lines.append("|------|------|--------|--------|")
        for name, q in market.get("quotes", {}).items():
            pct = q.get("pct_change")
            close = q.get("close", "N/A")
            amount = q.get("amount_yi")
            if pct is not None:
                sign = "+" if pct >= 0 else ""
                pct_text = f"{sign}{pct:.2f}%"
            else:
                pct_text = "-"
            amount_text = f"{amount:.0f}亿" if amount else "-"
            lines.append(f"| {name} | {close} | {pct_text} | {amount_text} |")
        lines.append("")
        lines.append(f"- 市场整体：{market.get('strength', 'N/A')}")
        lines.append(f"- 共振状态：{market.get('resonance', 'N/A')}")
    else:
        lines.append(f"- 获取失败：{market.get('reason', '未知错误')}")
    lines.append("")

    # Agent-市场方向
    agent_dir = report.get("agent_direction", {})
    if agent_dir.get("status") == "available":
        overall = agent_dir.get("overall", {})
        lines.append("### 市场方向判断")
        lines.append("")
        lines.append(f"- 多空方向：**{overall.get('direction', 'N/A')}**")
        lines.append(f"- 回踩状态：{overall.get('pullback_status', 'N/A')}")
        levels = overall.get("key_levels", {})
        if levels.get("support"):
            lines.append(f"- 支撑位：{', '.join(str(s) for s in levels['support'])}")
        if levels.get("resistance"):
            lines.append(f"- 压力位：{', '.join(str(r) for r in levels['resistance'])}")
        lines.append("")
        for idx_name, idx_data in agent_dir.get("indices", {}).items():
            if idx_data.get("status") != "available":
                continue
            rsi = idx_data.get("rsi")
            macd_sig = idx_data.get("macd_signal", "")
            ma_rel = idx_data.get("ma_relation", "")
            lines.append(f"- {idx_name}：RSI {rsi}（{idx_data.get('rsi_signal', '')}），MACD {macd_sig}，{ma_rel}")
        lines.append("")

    # Agent-消息热点
    agent_news = report.get("agent_news", {})
    if agent_news.get("status") == "available":
        lines.append("### 消息热点分析")
        lines.append("")
        risk = agent_news.get("risk_assessment", {})
        lines.append(f"- 热点风险：**{risk.get('overall_risk', 'N/A')}**")
        if risk.get("high_risk_sectors"):
            lines.append(f"- 高位风险板块：{', '.join(risk['high_risk_sectors'])}")
        hot_dirs = agent_news.get("hot_directions", [])
        if hot_dirs:
            lines.append("")
            lines.append("| 方向 | 类型 | 热度 | 今日涨幅 | 风险 |")
            lines.append("|------|------|------|----------|------|")
            for h in hot_dirs[:8]:
                lines.append(f"| {h['name']} | {h['type']} | {h['hot']:.0f} | {h['pct_change']:+.2f}% | {h['risk']} |")
        boards = agent_news.get("highest_boards", [])
        if boards:
            lines.append("")
            lines.append("**最高标：**")
            for b in boards[:5]:
                lines.append(f"- {b['name']}（{b['code']}）{b['days']}连板 — {b['type']}")
        mf = agent_news.get("money_flow_prediction", {})
        if mf.get("flow_direction"):
            lines.append(f"\n- 资金流向：{mf['flow_direction']}")
        lines.append("")

    # Agent-板块轮动
    agent_rot = report.get("agent_rotation", {})
    if agent_rot.get("status") == "available":
        lines.append("### 板块轮动分析")
        lines.append("")
        lines.append(f"- 轮动阶段：**{agent_rot.get('rotation_status', 'N/A')}**")
        hot_sectors = agent_rot.get("hot_sectors", [])
        if hot_sectors:
            lines.append("")
            lines.append("**近5日强势板块：**")
            lines.append("")
            lines.append("| 板块 | 今日涨幅 | 5日涨幅 | 热度 | 龙头 |")
            lines.append("|------|----------|---------|------|------|")
            for s in hot_sectors[:8]:
                lines.append(f"| {s['name']} | {s['pct_today']:+.2f}% | {s['pct_5d']:+.1f}% | {s['hot']:.0f} | {s.get('lead_stock', '')} |")
        pullback = agent_rot.get("pullback_ready", [])
        if pullback:
            lines.append("")
            lines.append("**回调到位板块：**")
            for p in pullback:
                lines.append(f"- {p['name']}：{p['pullback_reason']}")
        pred = agent_rot.get("rotation_prediction", {})
        if pred.get("prediction"):
            lines.append(f"\n- 轮动预判：{pred['prediction']}")
        lines.append("")

    # Step 2: 板块热点
    sectors = report.get("sector_hotspots", {})
    lines.append("## 二、板块热点")
    lines.append("")
    if sectors.get("status") == "available":
        lines.append(f"- 轮动阶段：**{sectors.get('cycle', 'N/A')}**（热度集中度 {sectors.get('concentration', 0):.0%}）")
        lines.append(f"- 当前热点题材：**{sectors.get('top_theme', 'N/A')}**")
        lines.append("")

        # 涨停统计
        lu_stats = sectors.get("lu_stats", {})
        if lu_stats.get("total", 0) > 0:
            lines.append("### 涨停统计")
            lines.append("")
            lines.append(f"- 涨停家数：**{lu_stats['total']}**（首板 {lu_stats.get('first_board', 0)}，连板 {lu_stats.get('continuous', 0)}）")
            if lu_stats.get("themes"):
                lines.append("- 涨停板块：")
                for t in lu_stats["themes"][:5]:
                    lines.append(f"  - {t['name']}：{t['up_nums']}家涨停（{t['up_stat']}）")
            lines.append("")

        # 连板阶梯
        limit_step = sectors.get("limit_step", [])
        multi_board = [s for s in limit_step if s.get("nums", 0) >= 2]
        if multi_board:
            lines.append("### 连板阶梯")
            lines.append("")
            lines.append("| 股票 | 代码 | 连板天数 |")
            lines.append("|------|------|----------|")
            for s in multi_board[:10]:
                lines.append(f"| {s['name']} | {s['ts_code']} | {s['nums']}连板 |")
            lines.append("")

        # 题材排行
        themes = sectors.get("themes", [])
        if themes:
            lines.append("### 题材热度排行")
            lines.append("")
            lines.append("| 排名 | 题材 | 热度 | 涨幅 | 龙头 | 龙头涨幅 | 涨停数 |")
            lines.append("|------|------|------|------|------|----------|--------|")
            for i, t in enumerate(themes[:10], 1):
                lead = t.get("lead_stock", "N/A")
                lead_pct = t.get("lead_stock_pct_change", 0)
                z_t = int(t.get("z_t_num", 0))
                lines.append(f"| {i} | {t['theme_name']} | {t['hot']:.0f} | {t['pct_change']:+.2f}% | {lead} | {lead_pct:+.2f}% | {z_t} |")
            lines.append("")

        # 概念板块行情
        hot_sectors = sectors.get("sectors", [])
        if hot_sectors:
            lines.append("### 概念板块行情 TOP 10")
            lines.append("")
            lines.append("| 代码 | 涨跌幅 | 振幅 | 换手率 | 成交额 |")
            lines.append("|------|--------|------|--------|--------|")
            for s in hot_sectors[:10]:
                amt_yi = s.get("amount", 0) / 1e8 if s.get("amount", 0) > 0 else 0
                lines.append(f"| {s['ts_code']} | {s['pct_change']:+.2f}% | {s['swing']:.2f}% | {s['turnover_rate']:.2f}% | {amt_yi:.1f}亿 |")
            lines.append("")
    else:
        lines.append(f"- 板块数据缺失：{sectors.get('reason', '未知')}")
    lines.append("")

    # Step 3: 龙头个股
    leading = report.get("leading_stocks", [])
    lines.append("## 三、龙头个股")
    lines.append("")
    if leading:
        lines.append("| 股票 | 代码 | 来源 | 角色 | 涨幅 | 备注 |")
        lines.append("|------|------|------|------|------|------|")
        for s in leading:
            pct = s.get("pct_change", 0)
            note = ""
            if s.get("fc_ratio"):
                note = f"封单比{s['fc_ratio']:.0f}"
            elif s.get("strth"):
                note = f"强度{s['strth']:.0f}"
            lines.append(f"| {s['name']} | {s['ts_code']} | {s['source_theme']} | {s['role']} | {pct:+.2f}% | {note} |")
    else:
        lines.append("- 未找到龙头个股数据")
    lines.append("")

    # Step 4: 成分股深度分析
    stock_details = report.get("stock_details", [])
    if stock_details:
        lines.append("### 成分股深度分析")
        lines.append("")
        lines.append("| 股票 | PE | PE估值 | PB | 获利盘 | 套牢盘 | 集中度 |")
        lines.append("|------|-----|--------|-----|--------|--------|--------|")
        for d in stock_details:
            fund = d.get("fundamental", {})
            chip = d.get("chip", {})

            pe = f"{fund.get('pe_ttm', 0):.1f}" if fund.get("status") == "available" and fund.get("pe_ttm") else "-"
            pe_val = fund.get("pe_valuation", "-") if fund.get("status") == "available" else "-"
            pb = f"{fund.get('pb', 0):.1f}" if fund.get("status") == "available" and fund.get("pb") else "-"

            wr = chip.get("winner_rate")
            winner = f"{wr:.0f}%" if wr is not None and chip.get("status") == "available" else "-"
            trapped = f"{100-wr:.0f}%" if wr is not None and chip.get("status") == "available" else "-"
            conc = chip.get("details", {}).get("cost_concentration")
            concentration = f"{conc:.0%}" if conc is not None else "-"

            lines.append(f"| {d['name']} | {pe} | {pe_val} | {pb} | {winner} | {trapped} | {concentration} |")
        lines.append("")

    # Step 5: 消息催化
    news = report.get("news_catalysts", {})
    lines.append("## 四、消息催化")
    lines.append("")
    if news.get("status") == "available":
        lines.append(f"- 本地新闻：{news.get('files', 0)}个文件，{news.get('count', 0)}条")
    else:
        lines.append(f"- 消息面缺失：{news.get('reason', '未知')}")
    lines.append("")

    # Step 5: 交易结论
    conclusion = report.get("conclusion", {})
    lines.append("## 五、交易结论")
    lines.append("")
    lines.append(f"- 方向判断：{conclusion.get('direction', 'N/A')}")
    lines.append(f"- 操作建议：{conclusion.get('action', 'N/A')}")
    lines.append(f"- 热点题材：{conclusion.get('top_theme', 'N/A')}（{conclusion.get('cycle', 'N/A')}阶段）")
    if conclusion.get("leading_stocks"):
        lines.append("- 关注个股：")
        for s in conclusion["leading_stocks"][:3]:
            lines.append(f"  - {s['name']}（{s['ts_code']}）— {s['source_theme']} {s['role']}")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="大盘板块分析（DC 数据驱动）")
    parser.add_argument("--date", default=None, help="分析日期 YYYY-MM-DD，默认当天")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--top", type=int, default=10, help="显示 TOP N 板块")
    args = parser.parse_args()

    if args.date:
        _, trade_date_text = normalize_trade_date(args.date)
    else:
        now, _ = resolve_now_china()
        trade_date_text = now.strftime("%Y-%m-%d")

    report = build_market_macro_report(trade_date_text, top_n=args.top)

    # 保存报告到文件（独立路径，不混在个股分析目录）
    report_dir = Path.home() / "quant-data" / "市场分析" / "reports" / "大盘分析报告"
    td_parts = trade_date_text.split("-")
    out_dir = report_dir / td_parts[0] / td_parts[1] / td_parts[2]
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.format == "json":
        out_path = out_dir / f"market_macro_{trade_date_text}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        md = render_markdown(report)
        out_path = out_dir / f"market_macro_{trade_date_text}.md"
        out_path.write_text(md, encoding="utf-8")
        print(md)

    print(f"\n报告已保存到: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
