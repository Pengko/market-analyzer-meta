#!/usr/bin/env python3
"""
Agent-消息热点：分析当前市场热点方向、最高标、风险评估。

数据源：TrendRadar MCP + 涨停数据 + DC 题材
分析：热点方向、连板最高标、板块类型、回调风险
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
META_ROOT = SCRIPT_DIR.parents[2]
SDA_SCRIPTS = META_ROOT / "skills" / "stock-deep-analysis" / "scripts"
sys.path.insert(0, str(SDA_SCRIPTS))

import pyarrow.parquet as pq


THEME_DATA_ROOT = Path.home() / "quant-data" / "tushare" / "股票数据" / "theme_data"
STOCK_DATA_ROOT = Path.home() / "quant-data" / "tushare" / "股票数据"


# ═══════════════════════════════════════════════════════
# 数据加载
# ═══════════════════════════════════════════════════════

def load_dc_concept(trade_date_compact: str) -> list[dict]:
    """加载 DC 题材数据。"""
    path = THEME_DATA_ROOT / "dc_concept" / "2026.parquet"
    if not path.exists():
        return []
    df = pq.read_table(path).to_pandas()
    day_df = df[df["trade_date"] == trade_date_compact]
    if day_df.empty:
        latest = df["trade_date"].max()
        day_df = df[df["trade_date"] == latest]
    return [
        {
            "theme_code": str(r.get("theme_code", "")),
            "theme_name": str(r.get("name", "")),
            "hot": float(r.get("hot", 0) or 0),
            "pct_change": float(r.get("pct_change", 0) or 0),
            "lead_stock": str(r.get("lead_stock", "")),
            "lead_stock_pct_change": float(r.get("lead_stock_pct_change", 0) or 0),
        }
        for _, r in day_df.iterrows()
    ]


def load_limit_step(trade_date_compact: str) -> list[dict]:
    """加载连板阶梯数据。"""
    year = trade_date_compact[:4]
    path = STOCK_DATA_ROOT / "limit_step" / f"{year}.parquet"
    if not path.exists():
        return []
    df = pq.read_table(path).to_pandas()
    day_df = df[df["trade_date"].astype(str) == trade_date_compact]
    if day_df.empty:
        return []
    records = [{"ts_code": str(r["ts_code"]), "name": str(r["name"]), "nums": int(r["nums"])} for _, r in day_df.iterrows()]
    records.sort(key=lambda x: x["nums"], reverse=True)
    return records


def load_limit_list_d(trade_date_compact: str) -> list[dict]:
    """加载涨停个股明细。"""
    path = STOCK_DATA_ROOT / "limit_list_d" / "limit_list_d.parquet"
    if not path.exists():
        return []
    df = pq.read_table(path).to_pandas()
    day_df = df[df["trade_date"].astype(str) == trade_date_compact]
    if day_df.empty:
        return []
    return [
        {
            "ts_code": str(r.get("ts_code", "")),
            "name": str(r.get("name", "")),
            "industry": str(r.get("industry", "")),
            "pct_chg": float(r.get("pct_chg", 0) or 0),
            "amount": float(r.get("amount", 0) or 0),
            "first_time": str(r.get("first_time", "")),
            "last_time": str(r.get("last_time", "")),
            "open_times": int(r.get("open_times", 0) or 0),
            "up_stat": str(r.get("up_stat", "")),
            "limit_times": float(r.get("limit_times", 0) or 0),
        }
        for _, r in day_df.iterrows()
    ]


def load_news_summary(trade_date_text: str) -> list[dict]:
    """加载本地新闻摘要。"""
    news_root = Path.home() / "quant-data" / "tushare" / "消息面数据" / "raw" / "news_pipeline"
    td_parts = trade_date_text.split("-")
    news_dir = news_root / td_parts[0] / td_parts[1] / td_parts[2]
    if not news_dir.exists():
        return []
    items = []
    for f in news_dir.glob("*.json"):
        try:
            import json
            with f.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    items.extend(data)
                elif isinstance(data, dict):
                    items.append(data)
        except Exception:
            continue
    return items


# ═══════════════════════════════════════════════════════
# 分析逻辑
# ═══════════════════════════════════════════════════════

SECTOR_TYPE_MAP = {
    "科技": ["AI", "芯片", "半导体", "人工智能", "算力", "光模块", "服务器", "数据", "软件", "互联网"],
    "消费": ["白酒", "食品", "医药", "家电", "零售", "旅游", "教育", "农业", "养殖"],
    "周期": ["钢铁", "煤炭", "有色", "黄金", "铜", "铝", "化工", "稀土", "锂"],
    "金融": ["银行", "券商", "保险", "地产"],
    "制造": ["汽车", "新能源", "光伏", "风电", "储能", "电池", "军工"],
}


def classify_sector_type(theme_name: str) -> str:
    """判断板块类型。"""
    for sector_type, keywords in SECTOR_TYPE_MAP.items():
        if any(kw in theme_name for kw in keywords):
            return sector_type
    return "其他"


def analyze_hotspot_risk(
    themes: list[dict],
    limit_step: list[dict],
    limit_d: list[dict],
) -> dict[str, Any]:
    """分析热点风险。"""
    hot_directions = []
    for t in themes[:10]:
        sector_type = classify_sector_type(t["theme_name"])
        # 风险评估
        risk = "低"
        if t.get("lead_stock_pct_change", 0) > 9:
            risk = "中等"  # 龙头涨停，可能是高位
        if any(s["name"] == t.get("lead_stock") and s["nums"] >= 3 for s in limit_step):
            risk = "高"  # 龙头连板3天以上

        hot_directions.append({
            "name": t["theme_name"],
            "type": sector_type,
            "hot": t.get("hot", 0),
            "pct_change": t.get("pct_change", 0),
            "lead_stock": t.get("lead_stock", ""),
            "lead_stock_pct_change": t.get("lead_stock_pct_change", 0),
            "risk": risk,
        })

    # 最高标
    highest_boards = []
    for s in limit_step[:5]:
        highest_boards.append({
            "name": s["name"],
            "code": s["ts_code"],
            "days": s["nums"],
            "type": classify_sector_type(s["name"]),
        })

    # 整体风险
    high_risk_count = sum(1 for h in hot_directions if h["risk"] == "高")
    overall_risk = "低"
    if high_risk_count >= 3:
        overall_risk = "高"
    elif high_risk_count >= 1:
        overall_risk = "中等"

    return {
        "hot_directions": hot_directions,
        "highest_boards": highest_boards,
        "overall_risk": overall_risk,
        "high_risk_sectors": [h["name"] for h in hot_directions if h["risk"] == "高"],
        "low_risk_sectors": [h["name"] for h in hot_directions if h["risk"] == "低"],
    }


# ═══════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════

def analyze_news_hotspot(trade_date_compact: str) -> dict[str, Any]:
    """消息热点分析主函数。"""
    trade_date_text = f"{trade_date_compact[:4]}-{trade_date_compact[4:6]}-{trade_date_compact[6:8]}"

    # 加载数据
    concepts = load_dc_concept(trade_date_compact)
    limit_step = load_limit_step(trade_date_compact)
    limit_d = load_limit_list_d(trade_date_compact)
    news = load_news_summary(trade_date_text)

    if not concepts and not limit_step:
        return {"status": "missing", "reason": "无题材和涨停数据"}

    # 热点分析
    concepts.sort(key=lambda x: x.get("hot", 0), reverse=True)
    risk_analysis = analyze_hotspot_risk(concepts, limit_step, limit_d)

    # 涨停统计
    total_lu = len(limit_d) if limit_d else 0
    first_board = len([r for r in limit_d if r.get("up_stat", "") == "首板"]) if limit_d else 0
    continuous_board = len([r for r in limit_d if r.get("limit_times", 0) > 1]) if limit_d else 0

    # 资金流向预判
    money_flow_prediction = _predict_money_flow(risk_analysis, concepts)

    return {
        "status": "available",
        "trade_date": trade_date_compact,
        "hot_directions": risk_analysis["hot_directions"],
        "highest_boards": risk_analysis["highest_boards"],
        "lu_stats": {
            "total": total_lu,
            "first_board": first_board,
            "continuous": continuous_board,
        },
        "risk_assessment": {
            "overall_risk": risk_analysis["overall_risk"],
            "high_risk_sectors": risk_analysis["high_risk_sectors"],
            "low_risk_sectors": risk_analysis["low_risk_sectors"],
        },
        "money_flow_prediction": money_flow_prediction,
        "news_count": len(news),
    }


def _predict_money_flow(risk_analysis: dict, concepts: list[dict]) -> dict[str, Any]:
    """预判资金流向。"""
    high_risk = risk_analysis.get("high_risk_sectors", [])
    low_risk = risk_analysis.get("low_risk_sectors", [])

    flow_from = []
    flow_to = []

    if high_risk:
        flow_from = high_risk[:2]
    if low_risk:
        flow_to = low_risk[:2]

    # 从热度变化判断
    rising = [c for c in concepts if c.get("pct_change", 0) > 1]
    falling = [c for c in concepts if c.get("pct_change", 0) < -1]
    if rising and not flow_to:
        flow_to = [r["theme_name"] for r in rising[:2]]
    if falling and not flow_from:
        flow_from = [f["theme_name"] for f in falling[:2]]

    return {
        "from": flow_from,
        "to": flow_to,
        "reasoning": f"高位板块{','.join(flow_from)}面临回调，资金可能流向{','.join(flow_to)}",
    }


if __name__ == "__main__":
    import json
    date = sys.argv[1] if len(sys.argv) > 1 else "20260529"
    result = analyze_news_hotspot(date)
    print(json.dumps(result, ensure_ascii=False, indent=2))
