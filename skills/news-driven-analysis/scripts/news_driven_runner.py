#!/usr/bin/env python3
"""
消息面驱动分析入口。

用法：
    python news_driven_runner.py                         # 分析当日消息
    python news_driven_runner.py --date 2026-05-29       # 指定日期
    python news_driven_runner.py --keyword "面板涨价"    # 关键词过滤
    python news_driven_runner.py --format json            # JSON 输出

输出：消息获取→消息解读→板块映射→受益个股→大盘环境→交易结论
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
from data.data_access import _read_parquet_rows
from time_util import scenario_from_now
from runtime.runtime_fetch import resolve_now_china


# ── Step 1: 消息获取 ──────────────────────────────────

def fetch_local_news(trade_date_text: str) -> dict[str, Any]:
    """Step 1: 从本地 news_pipeline 获取消息。"""
    news_root = Path.home() / "quant-data" / "tushare" / "消息面数据" / "raw" / "news_pipeline"
    td_parts = trade_date_text.split("-")
    if len(td_parts) != 3:
        return {"status": "error", "reason": "日期格式错误", "items": []}

    news_dir = news_root / td_parts[0] / td_parts[1] / td_parts[2]
    if not news_dir.exists():
        # 尝试 browser_news 目录
        browser_dir = Path.home() / "quant-data" / "tushare" / "消息面数据" / "raw" / "browser_news"
        browser_dir = browser_dir / td_parts[0] / td_parts[1] / td_parts[2]
        if browser_dir.exists():
            news_dir = browser_dir
        else:
            return {"status": "missing", "reason": f"本地新闻目录不存在", "items": []}

    news_files = list(news_dir.glob("*.json"))
    if not news_files:
        return {"status": "missing", "reason": "无新闻文件", "items": []}

    all_items = []
    for f in news_files:
        try:
            with f.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    all_items.extend(data)
                elif isinstance(data, dict):
                    # news_pipeline 格式：news_sentiment.main_sources
                    sentiment = data.get("news_sentiment", {})
                    sources = sentiment.get("main_sources", [])
                    if sources:
                        for s in sources:
                            all_items.append({
                                "title": s.get("title", ""),
                                "source": s.get("source", ""),
                                "published_at": s.get("published_at", ""),
                            })
                    elif "articles" in data:
                        all_items.extend(data["articles"])
                    elif "news" in data:
                        all_items.extend(data["news"])
                    else:
                        all_items.append(data)
        except Exception:
            continue

    return {
        "status": "available" if all_items else "missing",
        "count": len(all_items),
        "files": len(news_files),
        "items": all_items,
    }


def fetch_trendradar_news(trade_date_text: str) -> dict[str, Any]:
    """通过 TrendRadar MCP 获取消息（与个股分析相同的数据渠道）。"""
    import subprocess
    import json as _json

    # 查找 TrendRadar MCP CLI
    tredarar_cli = Path.home() / "agent-skills" / "custom" / "trendradar-mcp" / "scripts" / "trendradar_mcp_cli.py"
    tredarar_python = Path.home() / "Documents" / "TrendRadar" / ".venv" / "bin" / "python"

    if not tredarar_cli.exists():
        return {"status": "missing", "reason": "TrendRadar MCP 未安装", "items": []}

    python_cmd = str(tredarar_python) if tredarar_python.exists() else "python3"

    def _call_mcp(tool_name: str, arguments: dict) -> dict:
        """调用 TrendRadar MCP CLI。"""
        args_json = _json.dumps(arguments, ensure_ascii=False)
        try:
            result = subprocess.run(
                [python_cmd, str(tredarar_cli), "call", tool_name, "--args-json", args_json],
                capture_output=True, text=True, timeout=30, check=False,
            )
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

        if result.returncode != 0:
            return {"status": "error", "error": result.stderr or "non-zero exit"}

        try:
            wrapper = _json.loads(result.stdout)
        except _json.JSONDecodeError:
            return {"status": "error", "error": "invalid JSON"}

        content = wrapper.get("content", [])
        for item in content:
            if item.get("type") == "text" and "text" in item:
                try:
                    return _json.loads(item["text"])
                except _json.JSONDecodeError:
                    return {"status": "error", "error": "invalid text JSON"}
        return {"status": "error", "error": "no text content"}

    # 热榜全量
    hot_result = _call_mcp("get_latest_news", {"limit": 500, "include_url": True})
    hot_raw = hot_result.get("data", []) if hot_result.get("success") else []

    # RSS 全量
    rss_result = _call_mcp("get_latest_rss", {"limit": 500, "days": 3, "include_summary": True})
    rss_raw = rss_result.get("data", []) if rss_result.get("success") else []

    all_items = hot_raw + rss_raw

    if not all_items:
        return {"status": "missing", "reason": "TrendRadar MCP 返回空数据", "items": []}

    return {
        "status": "available",
        "count": len(all_items),
        "hot_count": len(hot_raw),
        "rss_count": len(rss_raw),
        "items": all_items,
    }


def fetch_browser_news(trade_date_text: str) -> dict[str, Any]:
    """浏览器 fallback：通过 fetch_browser_news.py 抓取东财/财联社新闻。"""
    import subprocess

    script = Path.home() / ".openclaw" / "skills" / "custom" / "market-news-intelligence" / "scripts" / "fetch_browser_news.py"
    if not script.exists():
        return {"status": "missing", "reason": "browser script not found", "items": []}

    output_dir = Path.home() / "quant-data" / "tushare" / "消息面数据" / "raw" / "browser_news"
    td_parts = trade_date_text.split("-")
    output_path = output_dir / td_parts[0] / td_parts[1] / td_parts[2] / f"browser_news_general_{trade_date_text}.json"

    # 已有缓存直接读取
    if output_path.exists():
        try:
            cached = json.loads(output_path.read_text(encoding="utf-8"))
            articles = cached.get("articles", [])
            return {
                "status": "available",
                "count": len(articles),
                "items": [{"title": a.get("title", ""), "source": a.get("source", ""), "published_at": a.get("published_at", "")} for a in articles],
                "source": "browser_cached",
            }
        except Exception:
            pass

    # 调用 fetch_browser_news.py
    cmd = [
        "python3", str(script),
        "--trade-date", trade_date_text,
        "--preset", "eastmoney", "cls",
        "--stock-name", "A股大盘",
        "--limit", "20",
        "--headless",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "reason": "browser timeout", "items": []}
    except Exception as exc:
        return {"status": "error", "reason": str(exc), "items": []}

    if result.returncode != 0:
        return {"status": "error", "reason": result.stderr[:200], "items": []}

    if output_path.exists():
        try:
            cached = json.loads(output_path.read_text(encoding="utf-8"))
            articles = cached.get("articles", [])
            return {
                "status": "available",
                "count": len(articles),
                "items": [{"title": a.get("title", ""), "source": a.get("source", ""), "published_at": a.get("published_at", "")} for a in articles],
                "source": "browser",
            }
        except Exception:
            pass

    return {"status": "missing", "reason": "browser output not found", "items": []}


# ── Step 2: 消息解读 ──────────────────────────────────

def classify_news(items: list[dict]) -> list[dict]:
    """Step 2: 对消息进行分类和情感分析。"""
    classified = []

    for item in items:
        title = str(item.get("title", "") or item.get("content", "")).strip()
        if not title:
            continue

        # 情感判断
        positive_keywords = ["利好", "增长", "突破", "创新", "涨停", "预增", "回购", "增持", "订单"]
        negative_keywords = ["利空", "下跌", "亏损", "减持", "违规", "处罚", "预减", "退市"]

        pos_count = sum(1 for kw in positive_keywords if kw in title)
        neg_count = sum(1 for kw in negative_keywords if kw in title)

        if pos_count > neg_count:
            sentiment = "偏利多"
        elif neg_count > pos_count:
            sentiment = "偏利空"
        else:
            sentiment = "中性"

        # 级别判断
        market_keywords = ["央行", "国务院", "发改委", "证监会", "GDP", "CPI"]
        sector_keywords = ["行业", "板块", "涨价", "供需", "产能"]

        if any(kw in title for kw in market_keywords):
            level = "市场级"
        elif any(kw in title for kw in sector_keywords):
            level = "板块级"
        else:
            level = "个股级"

        # 新鲜度
        source = str(item.get("source", "")).strip()
        if "盘中" in source or "实时" in source:
            freshness = "盘中新增"
        else:
            freshness = "当日新增"

        classified.append({
            "title": title[:100],
            "sentiment": sentiment,
            "level": level,
            "freshness": freshness,
            "source": source,
        })

    return classified


# ── Step 3: 板块映射 ──────────────────────────────────

def map_to_sectors(classified_news: list[dict]) -> dict[str, Any]:
    """Step 3: 将消息映射到板块。"""
    # 行业关键词映射（映射到实际 DC 概念名称）
    sector_mapping = {
        "AI芯片": ["AI芯片", "人工智能", "AI", "大模型", "算力", "GPU", "芯片"],
        "折叠屏": ["面板", "LCD", "OLED", "显示", "京东方", "华映", "彩虹股份", "TCL"],
        "消费电子": ["消费电子", "手机", "苹果", "华为", "小米"],
        "半导体": ["半导体", "集成电路", "晶圆", "封测", "中芯"],
        "光伏": ["光伏", "风电", "储能", "新能源", "锂电", "宁德时代", "比亚迪"],
        "医药": ["医药", "创新药", "疫苗", "医疗器械"],
        "银行": ["银行", "券商", "保险", "金融", "地产", "房地产"],
        "有色金属": ["钢铁", "煤炭", "有色", "黄金", "铜", "铝", "化工", "稀土", "锂"],
    }

    sector_hits: dict[str, int] = {}
    sector_news: dict[str, list[str]] = {}

    for news in classified_news:
        title = news.get("title", "")
        for sector, keywords in sector_mapping.items():
            if any(kw in title for kw in keywords):
                sector_hits[sector] = sector_hits.get(sector, 0) + 1
                if sector not in sector_news:
                    sector_news[sector] = []
                sector_news[sector].append(title[:50])

    # 按命中次数排序
    sorted_sectors = sorted(sector_hits.items(), key=lambda x: x[1], reverse=True)

    return {
        "status": "available" if sorted_sectors else "missing",
        "sectors": [
            {
                "name": name,
                "hits": count,
                "related_news": sector_news.get(name, [])[:3],
            }
            for name, count in sorted_sectors[:5]
        ],
        "top_sector": sorted_sectors[0][0] if sorted_sectors else None,
    }


# ── Step 4: 受益个股 ──────────────────────────────────

def find_beneficiary_stocks(sector_mapping: dict, trade_date_text: str) -> list[dict]:
    """Step 4: 从映射板块中找受益个股。"""
    if sector_mapping.get("status") != "available":
        return []

    trade_date_compact = trade_date_text.replace("-", "")
    stock_data_root = cfg.paths("stock_data_root")
    dc_concept_root = stock_data_root / "theme_data" / "dc_concept"
    dc_concept_cons_root = stock_data_root / "theme_data" / "dc_concept_cons"

    # 读取概念名称→代码映射
    concept_name_to_code = {}
    dc_concept_parquet = dc_concept_root / "2026.parquet"
    if dc_concept_parquet.exists():
        try:
            import pyarrow.parquet as pq
            df = pq.read_table(dc_concept_parquet).to_pandas()
            for _, r in df.iterrows():
                name = str(r.get("name", ""))
                code = str(r.get("theme_code", ""))
                if name and code:
                    concept_name_to_code[name] = code
        except Exception:
            pass

    # 读取 DC 成分股数据（单个 parquet 文件）
    cons_parquet = dc_concept_cons_root / "2026.parquet"
    cons_df = None
    if cons_parquet.exists():
        try:
            import pyarrow.parquet as pq
            cons_df = pq.read_table(cons_parquet).to_pandas()
        except Exception:
            pass

    if cons_df is None:
        return []

    beneficiaries = []
    seen_codes = set()

    for sector_info in sector_mapping.get("sectors", [])[:3]:
        sector_name = sector_info.get("name", "")
        if not sector_name:
            continue

        # 查找该板块的概念代码
        theme_code = concept_name_to_code.get(sector_name)
        if not theme_code:
            for name, code in concept_name_to_code.items():
                if sector_name in name or name in sector_name:
                    theme_code = code
                    break
        if not theme_code:
            continue

        # 从 dc_concept_cons 中筛选该概念的成分股
        try:
            concept_rows = cons_df[cons_df["theme_code"] == theme_code]
            if concept_rows.empty:
                continue

            # 取最新日期
            latest = concept_rows["trade_date"].max()
            concept_rows = concept_rows[concept_rows["trade_date"] == latest]

            for _, r in concept_rows.head(5).iterrows():
                ts_code = str(r.get("ts_code", ""))
                name = str(r.get("name", ""))
                if ts_code and ts_code not in seen_codes:
                    seen_codes.add(ts_code)
                    beneficiaries.append({
                        "ts_code": ts_code,
                        "name": name,
                        "sector": sector_name,
                        "reason": f"{sector_name}板块成分股",
                    })
        except Exception:
            continue

    return beneficiaries[:10]


# ── Step 5: 大盘环境验证 ──────────────────────────────

def verify_market_environment(trade_date_text: str) -> dict[str, Any]:
    """Step 5: 验证大盘环境是否支持消息驱动的交易机会。"""
    import urllib.request

    codes = {"上证": "sh000001", "深成": "sz399001", "创业板": "sz399006"}
    url = f"http://qt.gtimg.cn/q={','.join(codes.values())}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("gb2312", errors="replace")
    except Exception as e:
        return {"status": "error", "reason": str(e)}

    pct_values = []
    for name, code in codes.items():
        marker = f'v_{code}="'
        start = raw.find(marker)
        if start < 0:
            continue
        start += len(marker)
        end = raw.find('"', start)
        fields = raw[start:end].split("~")
        if len(fields) > 32:
            try:
                pct_values.append(float(fields[32]))
            except (ValueError, IndexError):
                pass

    if not pct_values:
        return {"status": "error", "reason": "无法解析指数数据"}

    avg_pct = sum(pct_values) / len(pct_values)
    if avg_pct > 0.5:
        env = "顺风环境"
    elif avg_pct > -0.5:
        env = "中性环境"
    else:
        env = "逆风环境"

    return {
        "status": "available",
        "avg_pct_change": round(avg_pct, 2),
        "environment": env,
        "summary": f"大盘{env}（均涨幅{avg_pct:+.2f}%）",
    }


# ── Step 6: 交易结论 ──────────────────────────────────

def generate_conclusion(
    news_summary: dict,
    sector_mapping: dict,
    beneficiaries: list,
    market_env: dict,
) -> dict[str, Any]:
    """Step 6: 生成交易结论。"""
    total_news = news_summary.get("count", 0)
    positive_count = sum(1 for n in news_summary.get("classified", []) if n.get("sentiment") == "偏利多")
    negative_count = sum(1 for n in news_summary.get("classified", []) if n.get("sentiment") == "偏利空")

    top_sector = sector_mapping.get("top_sector", "无")
    env = market_env.get("environment", "中性环境")

    # 消息评级
    if positive_count > negative_count * 2:
        catalyst = "强催化"
        direction = "偏多"
    elif positive_count > negative_count:
        catalyst = "中等催化"
        direction = "中性偏多"
    elif negative_count > positive_count * 2:
        catalyst = "强利空"
        direction = "偏空"
    else:
        catalyst = "中性"
        direction = "中性"

    summary_parts = [
        f"消息评级：{catalyst}",
        f"方向：{direction}",
        f"热点板块：{top_sector}",
    ]
    if beneficiaries:
        names = [s["name"] for s in beneficiaries[:3]]
        summary_parts.append(f"关注：{'、'.join(names)}")

    return {
        "catalyst_level": catalyst,
        "direction": direction,
        "top_sector": top_sector,
        "beneficiary_stocks": beneficiaries[:5],
        "market_environment": env,
        "summary": "；".join(summary_parts),
    }


# ── 主流程 ────────────────────────────────────────────

def build_news_driven_report(
    trade_date_text: str,
    keyword: str | None = None,
) -> dict[str, Any]:
    """消息面驱动分析主流程。"""
    now, time_source = resolve_now_china()
    session = scenario_from_now(now)

    # Step 1: 消息获取（本地优先 → TrendRadar MCP → 浏览器 fallback）
    news = fetch_local_news(trade_date_text)
    if news.get("status") != "available":
        trendar = fetch_trendradar_news(trade_date_text)
        if trendar.get("status") == "available":
            news = trendar
        else:
            # 浏览器 fallback
            browser = fetch_browser_news(trade_date_text)
            if browser.get("status") == "available":
                news = browser

    items = news.get("items", [])

    # 关键词过滤
    if keyword:
        items = [i for i in items if keyword in str(i.get("title", "") or i.get("content", ""))]

    # Step 2: 消息解读
    classified = classify_news(items)

    # Step 3: 板块映射
    sector_mapping = map_to_sectors(classified)

    # Step 4: 受益个股
    beneficiaries = find_beneficiary_stocks(sector_mapping, trade_date_text)

    # Step 5: 大盘环境验证
    market_env = verify_market_environment(trade_date_text)

    # Step 6: 交易结论
    news_summary = {
        "count": len(classified),
        "classified": classified,
    }
    conclusion = generate_conclusion(news_summary, sector_mapping, beneficiaries, market_env)

    return {
        "analysis_type": "news_driven",
        "trade_date": trade_date_text,
        "analysis_time": now.isoformat(timespec="seconds"),
        "time_source": time_source,
        "session": session,
        "keyword_filter": keyword,
        "news_overview": {
            "total": len(items),
            "classified": len(classified),
            "files": news.get("files", 0),
        },
        "classified_news": classified[:20],
        "sector_mapping": sector_mapping,
        "beneficiary_stocks": beneficiaries,
        "market_environment": market_env,
        "conclusion": conclusion,
    }


def render_markdown(report: dict) -> str:
    """渲染为 Markdown 报告。"""
    lines = [
        "# 消息面驱动分析报告",
        "",
        f"> 分析时间：{report.get('analysis_time', 'N/A')}",
        f"> 数据日期：{report.get('trade_date', 'N/A')}",
        f"> 当前时段：{report.get('session', 'N/A')}",
    ]
    if report.get("keyword_filter"):
        lines.append(f"> 关键词过滤：{report['keyword_filter']}")
    lines.extend(["", "---", ""])

    # Step 1: 消息概览
    overview = report.get("news_overview", {})
    lines.append("## 一、消息概览")
    lines.append("")
    lines.append(f"- 消息总量：{overview.get('total', 0)}条")
    lines.append(f"- 有效分类：{overview.get('classified', 0)}条")
    lines.append(f"- 数据文件：{overview.get('files', 0)}个")
    lines.append("")

    # Step 2: 消息解读
    classified = report.get("classified_news", [])
    lines.append("## 二、消息解读")
    lines.append("")
    if classified:
        lines.append("| 消息 | 情感 | 级别 | 新鲜度 |")
        lines.append("|------|------|------|--------|")
        for n in classified[:10]:
            lines.append(f"| {n['title'][:40]} | {n['sentiment']} | {n['level']} | {n['freshness']} |")
    else:
        lines.append("- 无有效消息")
    lines.append("")

    # Step 3: 板块映射
    sector_mapping = report.get("sector_mapping", {})
    lines.append("## 三、板块映射")
    lines.append("")
    if sector_mapping.get("status") == "available":
        lines.append("| 板块 | 命中次数 | 相关消息 |")
        lines.append("|------|----------|----------|")
        for s in sector_mapping.get("sectors", [])[:5]:
            news_preview = "、".join(s.get("related_news", [])[:2])
            lines.append(f"| {s['name']} | {s['hits']} | {news_preview[:30]} |")
    else:
        lines.append("- 未找到板块映射")
    lines.append("")

    # Step 4: 受益个股
    beneficiaries = report.get("beneficiary_stocks", [])
    lines.append("## 四、受益个股")
    lines.append("")
    if beneficiaries:
        lines.append("| 股票 | 代码 | 板块 | 受益逻辑 |")
        lines.append("|------|------|------|----------|")
        for s in beneficiaries:
            lines.append(f"| {s['name']} | {s['ts_code']} | {s['sector']} | {s['reason']} |")
    else:
        lines.append("- 未找到受益个股")
    lines.append("")

    # Step 5: 大盘环境验证
    market_env = report.get("market_environment", {})
    lines.append("## 五、大盘环境验证")
    lines.append("")
    lines.append(f"- {market_env.get('summary', 'N/A')}")
    lines.append("")

    # Step 6: 交易结论
    conclusion = report.get("conclusion", {})
    lines.append("## 六、交易结论")
    lines.append("")
    lines.append(f"- 消息评级：{conclusion.get('catalyst_level', 'N/A')}")
    lines.append(f"- 交易方向：{conclusion.get('direction', 'N/A')}")
    lines.append(f"- 热点板块：{conclusion.get('top_sector', 'N/A')}")
    lines.append(f"- 大盘环境：{conclusion.get('market_environment', 'N/A')}")
    if conclusion.get("beneficiary_stocks"):
        lines.append("- 关注个股：")
        for s in conclusion["beneficiary_stocks"][:3]:
            lines.append(f"  - {s['name']}（{s['ts_code']}）— {s['reason']}")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="消息面驱动分析")
    parser.add_argument("--date", default=None, help="分析日期 YYYY-MM-DD，默认当天")
    parser.add_argument("--keyword", default=None, help="关键词过滤")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()

    if args.date:
        _, trade_date_text = normalize_trade_date(args.date)
    else:
        now, _ = resolve_now_china()
        trade_date_text = now.strftime("%Y-%m-%d")

    report = build_news_driven_report(trade_date_text, keyword=args.keyword)

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
