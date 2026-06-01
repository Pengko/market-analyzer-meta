#!/usr/bin/env python3
"""
Agent-板块轮动：分析板块轮动、哪些板块回调到位、资金流向。

数据源：DC 概念板块日行情 + 板块资金流向
分析：轮动方向、回调到位、资金流向
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
        }
        for _, r in day_df.iterrows()
    ]


def load_dc_concept_history(trade_date_compact: str, days: int = 5) -> dict[str, list[dict]]:
    """加载 DC 题材历史数据（用于计算近N日趋势）。"""
    path = THEME_DATA_ROOT / "dc_concept" / "2026.parquet"
    if not path.exists():
        return {}
    df = pq.read_table(path).to_pandas()
    dates = sorted(df["trade_date"].unique())[-days:]
    history = {}
    for d in dates:
        day_df = df[df["trade_date"] == d]
        for _, r in day_df.iterrows():
            name = str(r.get("name", ""))
            if name not in history:
                history[name] = []
            history[name].append({
                "trade_date": str(d),
                "pct_change": float(r.get("pct_change", 0) or 0),
                "hot": float(r.get("hot", 0) or 0),
            })
    return history


# ═══════════════════════════════════════════════════════
# 分析逻辑
# ═══════════════════════════════════════════════════════

def analyze_rotation_status(concepts: list[dict]) -> str:
    """判断板块轮动阶段。"""
    if not concepts:
        return "数据不足"

    top5 = concepts[:5]
    top1_pct = top5[0].get("pct_change", 0) if top5 else 0
    avg_pct = sum(c.get("pct_change", 0) for c in top5) / len(top5) if top5 else 0

    # 轮动判断
    if top1_pct > 3 and avg_pct > 1.5:
        return "加强"
    elif top1_pct > 2 and avg_pct > 0:
        return "分化"
    elif top1_pct > 0 and avg_pct < 0:
        return "轮动"
    elif top1_pct < 0:
        return "退潮"
    else:
        return "震荡"


def identify_pullback_ready(
    concepts: list[dict],
    history: dict[str, list[dict]],
) -> list[dict]:
    """识别回调到位的板块。"""
    ready = []

    for c in concepts:
        name = c.get("theme_name", "")
        hist = history.get(name, [])
        if len(hist) < 3:
            continue

        # 计算近5日累计涨跌幅
        pct_5d = sum(h.get("pct_change", 0) for h in hist[-5:])
        # 计算近3日累计涨跌幅
        pct_3d = sum(h.get("pct_change", 0) for h in hist[-3:])

        # 判断是否回调到位
        # 条件：近5日涨>2%但近3日跌>1.5%（高位回调）
        # 或者：近5日涨>5%但近3日跌>2%（强势回调）
        pullback_ready = False
        if pct_5d > 2 and pct_3d < -1.5:
            pullback_ready = True
        elif pct_5d > 5 and pct_3d < -2:
            pullback_ready = True

        if pullback_ready:
            ready.append({
                "name": name,
                "pct_5d": round(pct_5d, 2),
                "pct_3d": round(pct_3d, 2),
                "hot": c.get("hot", 0),
                "lead_stock": c.get("lead_stock", ""),
                "pullback_reason": f"近5日+{pct_5d:.1f}%但近3日{pct_3d:.1f}%，高位回调",
            })

    ready.sort(key=lambda x: x["pct_5d"], reverse=True)
    return ready[:5]


def analyze_money_flow_direction(concepts: list[dict], history: dict[str, list[dict]]) -> dict[str, Any]:
    """分析板块资金流向方向。"""
    rising = []
    falling = []

    for c in concepts[:20]:
        name = c.get("theme_name", "")
        pct = c.get("pct_change", 0)
        hist = history.get(name, [])
        pct_5d = sum(h.get("pct_change", 0) for h in hist[-5:]) if hist else 0

        if pct > 1 and pct_5d > 2:
            rising.append({"name": name, "pct_today": pct, "pct_5d": round(pct_5d, 2)})
        elif pct < -1 and pct_5d < -2:
            falling.append({"name": name, "pct_today": pct, "pct_5d": round(pct_5d, 2)})

    return {
        "rising": rising[:5],
        "falling": falling[:5],
        "flow_direction": f"资金从{','.join([f['name'] for f in falling[:2]])}流向{','.join([r['name'] for r in rising[:2]])}" if rising and falling else "无明显流向",
    }


# ═══════════════════════════════════════════════════════
# 主函数
# ═══════════════════════════════════════════════════════

def analyze_sector_rotation(trade_date_compact: str) -> dict[str, Any]:
    """板块轮动分析主函数。"""
    # 加载数据
    concepts = load_dc_concept(trade_date_compact)
    history = load_dc_concept_history(trade_date_compact, days=5)

    if not concepts:
        return {"status": "missing", "reason": "无 DC 题材数据"}

    # 排序
    concepts.sort(key=lambda x: x.get("hot", 0), reverse=True)

    # 轮动阶段
    rotation_status = analyze_rotation_status(concepts)

    # 回调到位板块
    pullback_ready = identify_pullback_ready(concepts, history)

    # 资金流向
    money_flow = analyze_money_flow_direction(concepts, history)

    # 强势板块（近5日累计涨幅最大）
    hot_sectors = []
    for c in concepts[:15]:
        name = c.get("theme_name", "")
        hist = history.get(name, [])
        pct_5d = sum(h.get("pct_change", 0) for h in hist[-5:]) if hist else 0
        if pct_5d > 0:
            hot_sectors.append({
                "name": name,
                "pct_today": c.get("pct_change", 0),
                "pct_5d": round(pct_5d, 2),
                "hot": c.get("hot", 0),
                "lead_stock": c.get("lead_stock", ""),
            })
    hot_sectors.sort(key=lambda x: x["pct_5d"], reverse=True)

    # 轮动预判
    rotation_prediction = _predict_rotation(hot_sectors, pullback_ready, money_flow)

    return {
        "status": "available",
        "trade_date": trade_date_compact,
        "rotation_status": rotation_status,
        "hot_sectors": hot_sectors[:10],
        "pullback_ready": pullback_ready,
        "money_flow": money_flow,
        "rotation_prediction": rotation_prediction,
    }


def _predict_rotation(
    hot_sectors: list[dict],
    pullback_ready: list[dict],
    money_flow: dict,
) -> dict[str, Any]:
    """轮动预判。"""
    # 高位板块风险
    high_risk = [s for s in hot_sectors if s.get("pct_5d", 0) > 8]

    # 回调到位板块机会
    opportunities = [p["name"] for p in pullback_ready[:3]]

    # 预判
    if high_risk and pullback_ready:
        prediction = f"高位板块{high_risk[0]['name']}面临回调，资金可能流向{opportunities[0] if opportunities else '未知'}"
    elif high_risk:
        prediction = f"高位板块{high_risk[0]['name']}面临回调，暂无明确接棒方向"
    elif pullback_ready:
        prediction = f"回调到位板块{opportunities[0]}可能接力反弹"
    else:
        prediction = "板块轮动方向不明确"

    return {
        "high_risk_sectors": [s["name"] for s in high_risk],
        "opportunities": opportunities,
        "prediction": prediction,
    }


if __name__ == "__main__":
    import json
    date = sys.argv[1] if len(sys.argv) > 1 else "20260529"
    result = analyze_sector_rotation(date)
    print(json.dumps(result, ensure_ascii=False, indent=2))
