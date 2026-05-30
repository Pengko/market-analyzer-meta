#!/usr/bin/env python3
"""
大盘板块分析入口。

用法：
    python market_macro_runner.py                    # 分析当前大盘
    python market_macro_runner.py --date 2026-05-29  # 指定日期
    python market_macro_runner.py --format json       # JSON 输出

输出：大盘环境→板块热点→龙头个股→消息催化→交易结论
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ── 路径设置 ──────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
META_ROOT = SCRIPT_DIR.parents[1]  # market-analyzer-meta/
SDA_SCRIPTS = META_ROOT / "skills" / "stock-deep-analysis" / "scripts"
sys.path.insert(0, str(SDA_SCRIPTS))

from common import normalize_symbol, normalize_trade_date
from data.config_loader import cfg
from data.data_access import (
    load_daily_rows_bulk,
)
from time_util import scenario_from_now
from runtime.runtime_fetch import resolve_now_china
from analysis.market_analyzer import analyze_market_context
from analysis.sector_analyzer import analyze_sector_context


# ── Step 1: 大盘环境 ──────────────────────────────────

def fetch_index_quotes() -> dict[str, dict]:
    """从腾讯 API 获取三大指数实时行情。"""
    import urllib.request

    codes = {"上证指数": "sh000001", "深证成指": "sz399001", "创业板指": "sz399006"}
    url = f"http://qt.gtimg.cn/q={','.join(codes.values())}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("gb2312", errors="replace")
    except Exception as e:
        return {"error": str(e)}

    result = {}
    for name, code in codes.items():
        marker = f'v_{code}="'
        start = raw.find(marker)
        if start < 0:
            continue
        start += len(marker)
        end = raw.find('"', start)
        fields = raw[start:end].split("~")
        if len(fields) < 38:
            continue
        try:
            result[name] = {
                "close": float(fields[3]) if fields[3] else None,
                "pre_close": float(fields[4]) if fields[4] else None,
                "pct_change": float(fields[32]) if fields[32] else None,
                "amount_yi": round(float(fields[37]) / 10000, 2) if fields[37] else None,
            }
        except (ValueError, IndexError):
            continue
    return result


def analyze_market_environment(trade_date_text: str) -> dict[str, Any]:
    """Step 1: 大盘环境分析。"""
    quotes = fetch_index_quotes()
    if "error" in quotes:
        return {"status": "error", "reason": quotes["error"]}

    # 判断强弱
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

    # 判断共振
    if all(p > 0 for p in pct_values):
        resonance = "三大指数共振上涨"
    elif all(p < 0 for p in pct_values):
        resonance = "三大指数共振下跌"
    else:
        resonance = "指数分化"

    # 量能判断（简化：用成交额绝对值判断）
    total_amount = sum(q.get("amount_yi", 0) for q in quotes.values())

    summary_parts = [f"市场整体{strength}，{resonance}"]
    if total_amount > 0:
        summary_parts.append(f"两市成交额约{total_amount:.0f}亿")

    return {
        "status": "available",
        "quotes": quotes,
        "strength": strength,
        "resonance": resonance,
        "avg_pct_change": round(avg_pct, 2),
        "total_amount_yi": round(total_amount, 2),
        "summary": "，".join(summary_parts),
    }


# ── Step 2: 板块热点 ──────────────────────────────────

def analyze_sector_hotspots(trade_date_text: str) -> dict[str, Any]:
    """Step 2: 板块热点分析。"""
    trade_date_compact = trade_date_text.replace("-", "")

    # 尝试读取 KPL 概念数据
    from data.data_access import _read_parquet_rows
    kpl_root = cfg.paths("stock_data_root") / "theme_data" / "kpl_concept_cons" / "by_concept"
    dc_root = cfg.paths("stock_data_root") / "theme_data" / "dc_concept"

    hotspots = []

    # 读取 DC 概念行情
    dc_path = dc_root / f"dc_concept_{trade_date_compact[:4]}.parquet"
    if dc_path.exists():
        rows = _read_parquet_rows(dc_path)
        day_rows = [r for r in rows if str(r.get("trade_date", "")).strip() == trade_date_compact]
        day_rows.sort(key=lambda r: float(r.get("pct_change", 0) or 0), reverse=True)
        for r in day_rows[:10]:
            hotspots.append({
                "name": r.get("name", ""),
                "pct_change": float(r.get("pct_change", 0) or 0),
                "source": "dc_concept",
            })

    # 如果 DC 没数据，用 KPL
    if not hotspots and kpl_root.exists():
        concept_files = sorted(kpl_root.glob("*.csv"), key=lambda f: f.stat().st_mtime, reverse=True)
        for f in concept_files[:20]:
            try:
                import csv
                with f.open("r", encoding="utf-8-sig") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        if str(row.get("trade_date", "")).strip() == trade_date_compact:
                            hotspots.append({
                                "name": row.get("con_name", f.stem),
                                "hot_num": int(row.get("hot_num", 0) or 0),
                                "source": "kpl",
                            })
            except Exception:
                continue
        hotspots.sort(key=lambda x: x.get("hot_num", 0), reverse=True)
        hotspots = hotspots[:10]

    if not hotspots:
        return {"status": "missing", "reason": "无板块数据", "hotspots": []}

    return {
        "status": "available",
        "hotspots": hotspots[:10],
        "top_sector": hotspots[0]["name"] if hotspots else None,
        "summary": f"当日热点：{hotspots[0]['name']}" if hotspots else "无热点数据",
    }


# ── Step 3: 龙头个股 ──────────────────────────────────

def find_leading_stocks(hotspots: list[dict], trade_date_text: str) -> list[dict]:
    """Step 3: 从热点板块中找龙头个股。"""
    if not hotspots:
        return []

    trade_date_compact = trade_date_text.replace("-", "")
    leading = []

    # 读取 DC 概念成分股
    from data.data_access import _read_parquet_rows
    cons_root = cfg.paths("stock_data_root") / "theme_data" / "dc_concept_cons"

    for sector in hotspots[:3]:
        sector_name = sector.get("name", "")
        if not sector_name:
            continue

        # 查找该板块的成分股
        cons_files = list(cons_root.rglob(f"*{sector_name}*.parquet"))
        if not cons_files:
            continue

        try:
            rows = _read_parquet_rows(cons_files[0])
            # 取最新的成分股
            day_rows = [r for r in rows if str(r.get("trade_date", "")).strip() <= trade_date_compact]
            if not day_rows:
                continue

            # 按涨幅排序取前3
            for r in day_rows[:3]:
                ts_code = r.get("ts_code", "")
                name = r.get("name", "")
                if ts_code and name:
                    leading.append({
                        "ts_code": ts_code,
                        "name": name,
                        "sector": sector_name,
                        "role": "龙头" if len(leading) == 0 else "前排",
                    })
        except Exception:
            continue

    return leading[:5]


# ── Step 4: 消息催化 ──────────────────────────────────

def analyze_news_catalysts(trade_date_text: str) -> dict[str, Any]:
    """Step 4: 消息催化分析（简化版，读取本地 news_pipeline）。"""
    import os

    news_root = Path(os.path.expanduser("~/quant-data/tushare/消息面数据/raw/news_pipeline"))
    td_parts = trade_date_text.split("-")
    if len(td_parts) != 3:
        return {"status": "missing", "reason": "日期格式错误"}

    news_dir = news_root / td_parts[0] / td_parts[1] / td_parts[2]
    if not news_dir.exists():
        return {"status": "missing", "reason": f"本地新闻目录不存在: {news_dir}"}

    news_files = list(news_dir.glob("*.json"))
    if not news_files:
        return {"status": "missing", "reason": "无新闻文件"}

    # 读取新闻文件
    all_news = []
    for f in news_files[:10]:
        try:
            with f.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    all_news.extend(data)
                elif isinstance(data, dict):
                    all_news.append(data)
        except Exception:
            continue

    if not all_news:
        return {"status": "missing", "reason": "新闻文件为空"}

    return {
        "status": "available",
        "count": len(all_news),
        "files": len(news_files),
        "summary": f"本地新闻{len(news_files)}个文件，共{len(all_news)}条",
    }


# ── Step 5: 交易结论 ──────────────────────────────────

def generate_conclusion(
    market: dict,
    sectors: dict,
    leading: list,
    news: dict,
) -> dict[str, Any]:
    """Step 5: 生成交易结论。"""
    strength = market.get("strength", "中性")
    top_sector = sectors.get("top_sector", "无")
    leading_count = len(leading)

    # 简单规则判断
    if strength in ("偏强", "中性偏强"):
        direction = "偏多"
        action = "可积极参与"
    elif strength == "中性偏弱":
        direction = "中性"
        action = "观望为主"
    else:
        direction = "偏空"
        action = "防守为主"

    summary_parts = [
        f"市场{strength}，{direction}",
        f"热点板块：{top_sector}",
    ]
    if leading_count > 0:
        names = [s["name"] for s in leading[:3]]
        summary_parts.append(f"关注个股：{'、'.join(names)}")

    return {
        "direction": direction,
        "action": action,
        "top_sector": top_sector,
        "leading_stocks": leading[:5],
        "summary": "；".join(summary_parts),
    }


# ── 主流程 ────────────────────────────────────────────

def build_market_macro_report(trade_date_text: str) -> dict[str, Any]:
    """大盘板块分析主流程。"""
    now, time_source = resolve_now_china()
    session = scenario_from_now(now)

    # Step 1: 大盘环境
    market = analyze_market_environment(trade_date_text)

    # Step 2: 板块热点
    sectors = analyze_sector_hotspots(trade_date_text)

    # Step 3: 龙头个股
    leading = find_leading_stocks(sectors.get("hotspots", []), trade_date_text)

    # Step 4: 消息催化
    news = analyze_news_catalysts(trade_date_text)

    # Step 5: 交易结论
    conclusion = generate_conclusion(market, sectors, leading, news)

    return {
        "analysis_type": "market_macro",
        "trade_date": trade_date_text,
        "analysis_time": now.isoformat(timespec="seconds"),
        "time_source": time_source,
        "session": session,
        "market_environment": market,
        "sector_hotspots": sectors,
        "leading_stocks": leading,
        "news_catalysts": news,
        "conclusion": conclusion,
    }


def render_markdown(report: dict) -> str:
    """渲染为 Markdown 报告。"""
    lines = [
        f"# 大盘板块分析报告",
        "",
        f"> 分析时间：{report.get('analysis_time', 'N/A')}",
        f"> 数据日期：{report.get('trade_date', 'N/A')}",
        f"> 当前时段：{report.get('session', 'N/A')}",
        "",
        "---",
        "",
    ]

    # Step 1: 大盘环境
    market = report.get("market_environment", {})
    lines.append("## 一、大盘环境")
    lines.append("")
    if market.get("status") == "available":
        for name, q in market.get("quotes", {}).items():
            pct = q.get("pct_change", 0)
            sign = "+" if pct >= 0 else ""
            lines.append(f"| {name} | {q.get('close', 'N/A')} | {sign}{pct:.2f}% | {q.get('amount_yi', 'N/A')}亿 |")
        lines.append("")
        lines.append(f"- 市场整体：{market.get('strength', 'N/A')}")
        lines.append(f"- 共振状态：{market.get('resonance', 'N/A')}")
        lines.append(f"- 两市成交额：{market.get('total_amount_yi', 'N/A')}亿")
    else:
        lines.append(f"- 获取失败：{market.get('reason', '未知错误')}")
    lines.append("")

    # Step 2: 板块热点
    sectors = report.get("sector_hotspots", {})
    lines.append("## 二、板块热点")
    lines.append("")
    if sectors.get("status") == "available":
        lines.append("| 板块 | 涨幅/热度 | 来源 |")
        lines.append("|------|-----------|------|")
        for h in sectors.get("hotspots", [])[:5]:
            if h.get("source") == "dc_concept":
                lines.append(f"| {h['name']} | +{h.get('pct_change', 0):.2f}% | DC概念 |")
            else:
                lines.append(f"| {h['name']} | 热度{h.get('hot_num', 0)} | KPL |")
    else:
        lines.append(f"- 板块数据缺失：{sectors.get('reason', '未知')}")
    lines.append("")

    # Step 3: 龙头个股
    leading = report.get("leading_stocks", [])
    lines.append("## 三、龙头个股")
    lines.append("")
    if leading:
        lines.append("| 股票 | 代码 | 板块 | 角色 |")
        lines.append("|------|------|------|------|")
        for s in leading:
            lines.append(f"| {s['name']} | {s['ts_code']} | {s['sector']} | {s['role']} |")
    else:
        lines.append("- 未找到龙头个股数据")
    lines.append("")

    # Step 4: 消息催化
    news = report.get("news_catalysts", {})
    lines.append("## 四、消息催化")
    lines.append("")
    if news.get("status") == "available":
        lines.append(f"- {news.get('summary', 'N/A')}")
    else:
        lines.append(f"- 消息面缺失：{news.get('reason', '未知')}")
    lines.append("")

    # Step 5: 交易结论
    conclusion = report.get("conclusion", {})
    lines.append("## 五、交易结论")
    lines.append("")
    lines.append(f"- 方向判断：{conclusion.get('direction', 'N/A')}")
    lines.append(f"- 操作建议：{conclusion.get('action', 'N/A')}")
    lines.append(f"- 热点板块：{conclusion.get('top_sector', 'N/A')}")
    if conclusion.get("leading_stocks"):
        lines.append("- 关注个股：")
        for s in conclusion["leading_stocks"][:3]:
            lines.append(f"  - {s['name']}（{s['ts_code']}）— {s['sector']} {s['role']}")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="大盘板块分析")
    parser.add_argument("--date", default=None, help="分析日期 YYYY-MM-DD，默认当天")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()

    if args.date:
        _, trade_date_text = normalize_trade_date(args.date)
    else:
        now, _ = resolve_now_china()
        trade_date_text = now.strftime("%Y-%m-%d")

    report = build_market_macro_report(trade_date_text)

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
