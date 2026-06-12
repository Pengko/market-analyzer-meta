"""
并行 Agent 定义模块。每个 Agent 是一个独立的分析任务，
不依赖其他 Agent 的输出。通过 build_stock_report 的包装函数
来调用实际逻辑，确保与主流程行为一致。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pytz

# ── 路径 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from common import STOCK_DATA_ROOT, NEWS_DATA_ROOT
from time_util import scenario_from_now
from financing_analyzer import safe_float, analyze_financing_context
from analysis.stock_trend_analyzer import analyze_trend_structure, analyze_chip_structure, analyze_volatility_context
from analysis.market_analyzer import analyze_market_context
from analysis.sector_analyzer import (
    analyze_sector_context,
    build_leader_prediction,
    discover_mobile_subthemes_if_needed,
    discover_mobile_theme_leaders_if_needed,
    match_mobile_subthemes,
    load_stock_name,
)
from signals.core.analyze_auction_intent import analyze_auction_intent
from data.data_access import (
    load_daily_basic_row,
    rebuild_stk_factor_pro_from_daily,
    sync_latest_daily_kline_via_browser,
)
from runtime.runtime_fetch import safe_intraday


def _resolve_current_session() -> str:
    """获取当前时段"""
    now = datetime.now(pytz.timezone("Asia/Shanghai"))
    return scenario_from_now(now)


# ── Agent-A: 消息面 ─────────────────────────────────────

# 消息面分析规则库（在 Agent 内部完成，不依赖外部 normalize）
_NEWS_CATALYST_RULES = {
    "政策催化": [
        "政策", "发布", "规划", "监管", "减税", "补贴", "指导意见",
        "意见", "办法", "通知", "方案", "一号文件", "扶持",
    ],
    "业绩催化": [
        "业绩", "净利润", "营收", "增长", "预增", "亏损", "扭亏",
        "报告期", "季报", "年报", "中报", "一季度", "半年度",
    ],
    "订单/合作": [
        "订单", "中标", "签约", "合同", "合作", "采购", "协议",
        "建设", "项目", "交付", "产能",
    ],
    "重组并购": [
        "重组", "并购", "收购", "借壳", "资产注入", "股权",
        "控制权", "变更", "发行股份",
    ],
    "回购增持": [
        "回购", "增持", "员工持股", "股票激励", "大股东增持",
        "接盘", "护盘",
    ],
    "减持/解禁": [
        "减持", "清仓", "抛售", "解禁", "大宗交易", "减持计划",
        "股份减持", "大股东减持",
    ],
    "技术/产品": [
        "新品", "技术突破", "专利", "获批", "临床试验",
        "研发", "创新", "产品", "项目进展", "证书", "认证",
    ],
    "行业/市场": [
        "行业", "市场", "需求", "涨价", "降价", "产能",
        "供给", "销量", "市占率",
    ],
    "宏观/国际": [
        "利率", "汇率", "美联储", "关税", "贸易", "货币政策",
        "通胀", "GDP", "经济", "地缘",
    ],
    "异动/涨停": [
        "涨停", "跌停", "异动", "突发", "激增", "暴涨", "暴跌",
        "封板", "打板", "连板", "断板", "天地板",
    ],
}

_POSITIVE_KEYWORDS = [
    "利好", "亮点", "突破", "创新高", "预增", "盈利", "增长",
    "回升", "复苏", "向好", "上行", "抬头", "转暖", "改善",
    "升级", "扩大", "提升", "领先", "优势", "亮眼", "强劲",
]
_NEGATIVE_KEYWORDS = [
    "利空", "风险", "缩水", "下跌", "亏损", "降低", "收缩",
    "下行", "回落", "走弱", "恶化", "减辄", "下调", "降级",
    "威胁", "伤害", "血屏", "爆雷", "踩雷", "清仓",
]


def _classify_catalyst(title: str, summary: str | None = None) -> list[str]:
    """对单条消息进行催化剂分类，返回催化剂列表"""
    text = f"{title} {summary or ''}"
    catalysts: list[str] = []
    for cat, keywords in _NEWS_CATALYST_RULES.items():
        if any(kw in text for kw in keywords):
            catalysts.append(cat)
    return catalysts


def _score_sentiment(title: str, summary: str | None = None) -> float:
    """对单条消息进行情感评分，返回 [-1.0, +1.0]"""
    text = f"{title} {summary or ''}"
    pos = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text)
    neg = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text)
    score = (pos - neg) / max(pos + neg, 1) * 0.5  # 单条消息最大影响 ±0.5
    # 增强因子：减持/解禁消息直接负分
    if "减持" in text or "解禁" in text:
        score -= 0.3
    if "回购" in text or "增持" in text:
        score += 0.3
    return max(-1.0, min(1.0, score))


def _score_urgency(item: dict[str, Any], trade_date_text: str) -> int:
    """时效性分级 1-5：5=盘中突发，1=旧消息"""
    title = str(item.get("title") or "")
    # 盘中突发
    if any(kw in title for kw in ["涨停", "跌停", "异动", "突发", "暴涨", "暴跌", "封板", "打板"]):
        return 5
    # 近日重大
    if any(kw in title for kw in ["重大", "突破", "首次", "创新高", "历史"]):
        return 4
    return 3  # 默认一般


# ── TrendRadar MCP 调用层 ──────────────────────────────

TRENDRADAR_MCP_CLI = Path("/Users/penghongming/agent-skills/custom/trendradar-mcp/scripts/trendradar_mcp_cli.py")
TRENDRADAR_PYTHON = Path("/Users/penghongming/Documents/TrendRadar/.venv/bin/python")


# 行业关键词扩展映射（常见行业 → 更广泛的新闻关键词）
_INDUSTRY_KEYWORD_MAP: dict[str, list[str]] = {
    "汽车整车": ["汽车", "车企", "乘用车", "新能源车", "电动车", "自动驾驶", "智能驾驶", "车市", "销量", "车型", "车主"],
    "元件设备": ["半导体", "芯片", "集成电路", "IC", "光刻", "封测", "精密制造", "元器件", "电子元件"],
    "计算机应用": ["软件", "AI", "人工智能", "大模型", "云计算", "SaaS", "信息化", "数字化"],
    "通信设备": ["通信", "5G", "6G", "基站", "光纤", "物联网", "电信", "移动网络"],
    "医药商业": ["制药", "药品", "医疗", "临床", "生物制药", "创新药", "医保", "医疗器械"],
    "医疗器械": ["医疗器械", "医疗设备", "医疗", "仪器", "诊断", "IVD"],
    "电气设备": ["光伏", "风电", "蒸汽", "蒸发", "储能", "变压器", "电缆", "电网", "输配电", "电气"],
    "房地产开发": ["房地产", "楼市", "地产", "住宅", "商业地产", "物业", "楼市"],
    "银行": ["银行", "金融", "贷款", "存款", "利率", "货币", "存准", "LPR"],
    "证券": ["证券", "投行", "资本市场", "IPO", "融资", "上市", "发行"],
    "钢铁": ["钢铁", "有色", "金属", "矿石", "煤炭", "能源", "铁矿石", "铜铁"],
    "化学制品": ["化工", "化学", "材料", "塑料", "润滑油", "涂料", "纤维", "粘胶"],
    "食品饮料": ["食品", "饮料", "消费", "百货", "零售", "餐饮", "快消", "餐饮", "日用品"],
    "电子": ["消费电子", "电子", "手机", "家电", "智能硬件", "可穿戴"],
    "电力": ["电力", "火电", "核电", "电网", "输配电", "电力设备", "发电"],
    "航空运输": ["航空", "航天", "飞机", "机场", "物流", "运输", "航司", "航班"],
    "水利": ["水利", "水电", "水资源", "治污", "上游", "水务", "排水"],
    "家用电器": ["家电", "空调", "冰箱", "洗衣机", "小家电", "消费电子"],
    "房屋建筑": ["建筑", "房屋", "建材", "装饰", "装修", "基建", "房屋"],
    "纺织服装": ["服装", "纺织", "布料", "纹绣", "制衣", "潮牌"],
    "媒体": ["媒体", "广告", "营销", "视频", "短视频", "直播", "影视"],
    "专用设备": ["专用设备", "机床", "重工", "机械", "工程机械", "机械设备"],
    "环保": ["环保", "垃圾处理", "固废", "废水", "污水处理", "清洁", "绿色"],
    "农业": ["农业", "种植", "养殖", "粮食", "特产", "食品安全"],
    "物资贸易": ["贸易", "物流", "供应链", "跨境", "进出口", "外贸", "贸易"],
    "通用机械": ["通用机械", "泵", "阀门", "压缩机", "锅炉", "机械零部件"],
    "采掘": ["采掘", "矿业", "煤炭", "石油", "天然气", "铁矿石", "金属矿"],
    "电商": ["电商", "网络购物", "线上零售", "跨境电商", "平台经济"],
    "文教休闲": ["教育", "培训", "文具", "竞技", "游戏", "娱乐", "休闲"],
    "国防军工": ["军工", "国防", "航天", "航空", "军事", "武器"],
    "识别": ["识别", "大数据", "云计算", "数据中心", "人工智能"],
    "物流": ["物流", "供应链", "仓储", "快递", "运输", "货运"],
    "模具": ["模具", "塑料", "注塑", "压铸", "模具设计"],
}


def _load_stock_industry(full_symbol: str) -> str | None:
    """从本地 stock_basic 读取行业信息"""
    path = STOCK_DATA_ROOT / "stock_basic" / "stock_basic_all.csv"
    if not path.exists():
        return None
    try:
        import csv
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                ts_code = (row.get("ts_code") or row.get("\ufeffts_code") or "").strip()
                if ts_code == full_symbol:
                    return (row.get("industry") or "").strip() or None
    except Exception:
        pass
    return None


def _build_stock_keywords(
    stock_name: str | None,
    pure_symbol: str,
    industry: str | None,
    mode: str = "exact",
) -> list[str]:
    """构建股票相关关键词列表
    mode="exact": 只匹配股票名（不含代码，避免误匹）
    mode="broad":  宽松匹配（加行业关键词、简称前缀）
    """
    keywords: list[str] = []
    if stock_name:
        keywords.append(stock_name)
        # 短名称前缀（如 宁德时代 → 宁德）
        if len(stock_name) >= 4:
            keywords.append(stock_name[:2])
    # 注意：不添加 pure_symbol（股票代码），避免数字误匹

    if mode == "broad" and industry:
        # 添加行业关键词
        industry_keywords = _INDUSTRY_KEYWORD_MAP.get(industry, [industry])
        keywords.extend(industry_keywords)

    # 去重并过滤短于2字的关键词（避免误匹）
    seen = set()
    result = []
    for kw in keywords:
        if len(kw) >= 2 and kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result


def _filter_items_for_stock(
    items: list[dict[str, Any]],
    stock_name: str | None,
    pure_symbol: str,
    industry: str | None = None,
    mode: str = "exact",
) -> list[dict[str, Any]]:
    """在热榜/RSS 数据中筛选与个股相关的条目
    mode="exact": 只匹配标题中的股票名/代码
    mode="broad":  同时匹配摘要和行业关键词
    """
    if not stock_name:
        return []

    keywords = _build_stock_keywords(stock_name, pure_symbol, industry, mode)
    if not keywords:
        return []

    filtered = []
    for item in items:
        title = str(item.get("title") or "")
        # RSS 通常有 summary/description，热榜一般没有
        summary = str(item.get("summary") or item.get("description") or "")
        text = title + " " + summary

        if any(kw in text for kw in keywords):
            filtered.append(item)

    return filtered


def _fetch_browser_news_fallback(
    full_symbol: str,
    trade_date_text: str,
    stock_name: str | None,
) -> tuple[list[dict[str, Any]], str]:
    """当 TrendRadar 无匹配时，通过 browser 抓取新闻作为 fallback

    调用 market-news-intelligence 的 fetch_browser_news.py，
    返回（筛选后的条目列表, 执行状态描述）
    """
    script = Path.home() / ".openclaw" / "skills" / "custom" / "market-news-intelligence" / "scripts" / "fetch_browser_news.py"
    if not script.exists():
        return [], "browser script not found"

    pure_symbol = full_symbol.split(".")[0]
    output_dir = NEWS_DATA_ROOT / "raw" / "browser_news"
    output_path = output_dir / f"browser_news_{pure_symbol}_{trade_date_text}.json"

    # 若已有今天的缓存，直接读取（避免重复抓取）
    if output_path.exists():
        try:
            cached = json.loads(output_path.read_text(encoding="utf-8"))
            articles = cached.get("articles", [])
            return _normalize_browser_articles(articles), "browser_cached"
        except Exception:
            pass

    cmd = [
        "python3", str(script),
        "--symbol", full_symbol,
        "--trade-date", trade_date_text,
        "--preset", "eastmoney", "cls",
        "--stock-name", stock_name or pure_symbol,
        "--limit", "12",
        "--headless",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return [], "browser timeout"
    except Exception as exc:
        return [], f"browser error: {exc}"

    if result.returncode != 0:
        return [], f"browser exit {result.returncode}: {result.stderr[:200]}"

    # 读取生成的 JSON 文件
    if not output_path.exists():
        return [], "browser output missing"

    try:
        data = json.loads(output_path.read_text(encoding="utf-8"))
        articles = data.get("articles", [])
        return _normalize_browser_articles(articles), "browser_fetched"
    except Exception as exc:
        return [], f"browser parse error: {exc}"


def _normalize_browser_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """将 fetch_browser_news.py 输出转换为 TrendRadar 兼容格式"""
    normalized = []
    for a in articles:
        item = {
            "title": str(a.get("title") or ""),
            "url": str(a.get("url") or ""),
            "platform_name": str(a.get("source") or "browser"),
            "published_at": str(a.get("published_at") or ""),
            "summary": str(a.get("content") or ""),
            "_match_type": "exact",
            "_source": "browser_fallback",
        }
        if item["title"]:
            normalized.append(item)
    return normalized


def _call_trendradar_mcp(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """通过 subprocess 调用 TrendRadar MCP CLI，返回结构化结果"""
    if not TRENDRADAR_MCP_CLI.exists():
        return {"status": "error", "error": f"MCP CLI not found: {TRENDRADAR_MCP_CLI}"}

    python_cmd = str(TRENDRADAR_PYTHON) if TRENDRADAR_PYTHON.exists() else "python3"
    args_json = json.dumps(arguments, ensure_ascii=False)
    try:
        result = subprocess.run(
            [python_cmd, str(TRENDRADAR_MCP_CLI), "call", tool_name, "--args-json", args_json],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return {"status": "error", "error": f"subprocess failed: {exc}"}

    if result.returncode != 0:
        return {"status": "error", "error": result.stderr or "MCP tool returned non-zero"}

    try:
        wrapper = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "error": "MCP output is not valid JSON"}

    if wrapper.get("isError"):
        return {"status": "error", "error": "MCP tool reported error"}

    # Extract text content from MCP response
    content = wrapper.get("content", [])
    for item in content:
        if item.get("type") == "text" and "text" in item:
            try:
                return json.loads(item["text"])
            except json.JSONDecodeError:
                return {"status": "error", "error": "MCP tool text is not valid JSON"}

    return {"status": "error", "error": "No text content in MCP response"}


def _trendradar_to_news_sentiment(
    search_result: dict[str, Any],
    trade_date_text: str,
    stock_name: str | None,
) -> dict[str, Any]:
    """将 TrendRadar search_news / get_latest_news / get_latest_rss 结果转换为 stock-deep-analysis 的 news_sentiment 格式

    支持匹配类型元数据：
    - 条目字典中含有 `_match_type` 字段，可为 "exact" 或 "industry_context"
    - exact 匹配项在情感计分中享有更高权重
    """
    if not search_result.get("success"):
        return {
            "status": "missing",
            "reason": search_result.get("error") or "TrendRadar 返回失败",
        }

    # 兼容 search_news（results）与 get_latest_news（data）/ get_latest_rss（data）
    data = search_result.get("data", []) or search_result.get("results", [])
    rss_data = search_result.get("rss_data", [])
    all_items = data + rss_data

    if not all_items:
        return {
            "status": "missing",
            "reason": "TrendRadar 未检索到相关消息",
        }

    # 统计匹配类型
    exact_count = sum(1 for item in all_items if item.get("_match_type") == "exact")
    industry_count = sum(1 for item in all_items if item.get("_match_type") == "industry_context")

    # 用现有的分析逻辑处理（exact 匹配项权重更高）
    analyzed = []
    for item in all_items[:30]:
        title = str(item.get("title") or "")
        summary = str(item.get("summary") or item.get("description") or "")
        catalysts = _classify_catalyst(title, summary)
        sentiment = _score_sentiment(title, summary)
        urgency = _score_urgency(item, trade_date_text)
        source = item.get("platform_name") or item.get("feed_name") or item.get("platform") or item.get("feed_id") or "unknown"
        match_type = item.get("_match_type", "unknown")
        # exact 匹配项情感权重加倍
        weight = 2.0 if match_type == "exact" else 1.0
        analyzed.append({
            "title": title[:80],
            "catalysts": catalysts,
            "sentiment": round(sentiment, 2),
            "urgency": urgency,
            "source": source,
            "url": item.get("url"),
            "match_type": match_type,
            "weight": weight,
        })

    # 聚合催化剂
    all_catalysts: list[str] = []
    for a in analyzed:
        all_catalysts.extend(a["catalysts"])
    catalyst_counts: dict[str, int] = {}
    for c in all_catalysts:
        catalyst_counts[c] = catalyst_counts.get(c, 0) + 1
    top_catalysts = sorted(catalyst_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    # 情感均值（按紧急度+匹配类型加权）
    total_weight = sum(a["urgency"] * a["weight"] for a in analyzed) or 1
    weighted_sentiment = sum(a["sentiment"] * a["urgency"] * a["weight"] for a in analyzed) / total_weight
    max_urgency = max(a["urgency"] for a in analyzed) if analyzed else 0

    # 冲突检测
    conflicts: list[dict[str, Any]] = []
    catalyst_sentiments: dict[str, list[float]] = {}
    for a in analyzed:
        for c in a["catalysts"]:
            catalyst_sentiments.setdefault(c, []).append(a["sentiment"])
    for c, sentiments in catalyst_sentiments.items():
        if len(sentiments) >= 2 and max(sentiments) > 0.2 and min(sentiments) < -0.2:
            conflicts.append({
                "catalyst": c,
                "max_sentiment": max(sentiments),
                "min_sentiment": min(sentiments),
                "note": f"该股票在'{c}'方向上同时出现正负面消息，存在分歧",
            })

    # 摘要
    direction = "中性"
    if weighted_sentiment > 0.2:
        direction = "偏多"
    elif weighted_sentiment < -0.2:
        direction = "偏空"

    level = "个股级"
    if len(analyzed) > 3:
        level = "板块级" if max_urgency >= 4 else "个股级"
    if any(c in ["宏观/国际", "政策催化"] for c, _ in top_catalysts):
        level = "国家级"

    summary_parts: list[str] = []
    if top_catalysts:
        cat_str = "、".join([f"{c}({n})" for c, n in top_catalysts])
        summary_parts.append(f"主要催化剂：{cat_str}")
    # 显示匹配质量
    if exact_count > 0:
        summary_parts.append(f"直接匹配：{exact_count}条，行业上下文：{industry_count}条")
    else:
        summary_parts.append(f"无直接匹配，行业上下文：{industry_count}条")
    summary_parts.append(f"综合情感：{direction}({weighted_sentiment:+.2f})")
    summary_parts.append(f"最高紧急度：{max_urgency}/5")
    if conflicts:
        summary_parts.append(f"消息冲突：{len(conflicts)}处")

    # 来源列表
    sources = sorted({a["source"] for a in analyzed})

    return {
        "status": "available",
        "acquisition_method": "trendradar_mcp",
        "checked_sources": {
            "announcement": [],
            "policy": [a["title"] for a in analyzed if "政策" in a["catalysts"]][:3],
            "mainstream_media": [a["title"] for a in analyzed if a["source"] in {"华尔街见闻", "财联社", "澎湃新闻"}][:3],
            "market_platform": [a["title"] for a in analyzed if a["source"] in {"东方财富", "同花顺", "雪球"}][:3],
            "community": [a["title"] for a in analyzed if a["source"] in {"微博", "知乎", "贴吧", "B站"}][:3],
        },
        "main_sources": sources[:5],
        "summary": "；".join(summary_parts),
        "direction": direction,
        "level": level,
        "freshness": "fresh" if max_urgency >= 4 else "recent",
        "credibility": "medium" if len(sources) >= 3 else "low",
        "is_new_catalyst": any(c in ["政策催化", "业绩催化", "订单/合作", "重组并购", "技术/产品"] for c, _ in top_catalysts),
        "impact_role": None,
        "impact_on_price": None,
        "notes": [f"TrendRadar MCP 检索到 {len(all_items)} 条消息，分析 {len(analyzed)} 条（直接匹配 {exact_count} 条）"],
    }


def run_news_agent(
    full_symbol: str,
    trade_date_text: str,
    news_json_path: Optional[str] = None,
    news_reference_date: Optional[str] = None,
) -> dict[str, Any]:
    """消息面分析：只通过 TrendRadar MCP 获取消息

    废除逻辑：
    - SQLite 消息库读取
    - browser fallback 抓取
    - market-news-intelligence pipeline
    - auto_resolve_news_json_path 缓存回退
    """
    result: dict[str, Any] = {"status": "missing"}

    stock_name = None
    try:
        stock_name = load_stock_name(full_symbol)
    except Exception:
        pass

    pure_symbol = full_symbol.split(".")[0]

    # 构建标准输出路径（保持与旧路径兼容）
    year, month, day = trade_date_text.split("-")
    news_data_root = NEWS_DATA_ROOT
    pipeline_root = news_data_root / "raw" / "news_pipeline"
    canonical_output_path = pipeline_root / year / month / day / f"news_pipeline_{pure_symbol}_{trade_date_text}.json"

    try:
        # ── Step 1: 准备 ──
        search_query = stock_name or pure_symbol
        # 读取行业信息用于关键词扩展
        stock_industry = _load_stock_industry(full_symbol)

        # ── Step 2: TrendRadar MCP 获取全量热榜 + RSS 数据 ──
        # 2a) 热榜全量
        hot_result = _call_trendradar_mcp("get_latest_news", {
            "limit": 500,
            "include_url": True,
        })
        hot_raw = hot_result.get("data", []) if hot_result.get("success") else []

        # 2b) RSS 全量（拓展到 3 天，提高覆盖率）
        rss_result = _call_trendradar_mcp("get_latest_rss", {
            "limit": 500,
            "days": 3,
            "include_summary": True,
        })
        rss_raw = rss_result.get("data", []) if rss_result.get("success") else []

        # ── Step 3: 两阶段筛选 ──
        # 3a) 精确匹配（股票名+代码）
        hot_exact = _filter_items_for_stock(hot_raw, stock_name, pure_symbol, industry=None, mode="exact")
        rss_exact = _filter_items_for_stock(rss_raw, stock_name, pure_symbol, industry=None, mode="exact")

        # 3b) 宽松匹配（加行业关键词+摘要），仅在精确匹配结果较少时触发
        MIN_MATCH_THRESHOLD = 3
        hot_items = list(hot_exact)  # 复制，避免修改原始列表
        rss_items = list(rss_exact)

        # 标记匹配类型
        for item in hot_items:
            item["_match_type"] = "exact"
        for item in rss_items:
            item["_match_type"] = "exact"

        broad_added = 0
        if len(hot_exact) + len(rss_exact) < MIN_MATCH_THRESHOLD and stock_industry:
            hot_broad = _filter_items_for_stock(hot_raw, stock_name, pure_symbol, industry=stock_industry, mode="broad")
            rss_broad = _filter_items_for_stock(rss_raw, stock_name, pure_symbol, industry=stock_industry, mode="broad")
            # 合并两个结果集（去重）
            seen_urls = {item.get("url") for item in hot_exact + rss_exact}
            for item in hot_broad + rss_broad:
                if item.get("url") not in seen_urls:
                    item["_match_type"] = "industry_context"
                    if item in hot_broad:
                        hot_items.append(item)
                    else:
                        rss_items.append(item)
                    seen_urls.add(item.get("url"))
                    broad_added += 1
        used_broad = broad_added > 0

        # ── Step 3c: Browser Fallback ──
        # 当 TrendRadar 精确+宽松均无匹配时，通过浏览器抓取补充
        browser_articles: list[dict[str, Any]] = []
        browser_fallback_status = "skipped"
        if len(hot_items) + len(rss_items) == 0:
            browser_articles, browser_fallback_status = _fetch_browser_news_fallback(
                full_symbol, trade_date_text, stock_name
            )
            if browser_articles:
                # 将 browser 结果放入 rss_items 以保持兼容
                rss_items.extend(browser_articles)

        # ── Step 4: 组装为兼容格式 ──
        search_result = {
            "success": True,
            "data": hot_items,
            "rss_data": rss_items,
            "query": search_query,
            "trade_date": trade_date_text,
        }

        # ── Step 5: 转换为 news_sentiment ──
        news_sentiment = _trendradar_to_news_sentiment(search_result, trade_date_text, stock_name)

        # ── Step 6: 保存到标准路径 ──
        if news_sentiment.get("status") == "available":
            canonical_output_path.parent.mkdir(parents=True, exist_ok=True)
            output_payload = {
                "news_sentiment": news_sentiment,
                "narrative_context": {
                    "hard_catalyst": news_sentiment.get("is_new_catalyst", False),
                    "catalyst_fresh": news_sentiment.get("freshness") == "fresh",
                    "core_stock": False,
                    "theme_active": news_sentiment.get("level") in {"板块级", "国家级"},
                    "bullish_narrative": news_sentiment.get("direction") == "偏多",
                    "bearish_narrative": news_sentiment.get("direction") == "偏空",
                },
                "trendradar_raw": {
                    "search_result": search_result,
                    "query": search_query,
                    "trade_date": trade_date_text,
                },
            }
            canonical_output_path.write_text(
                json.dumps(output_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            result = {
                "status": "available",
                "resolved_news_json_path": str(canonical_output_path),
                "news_pipeline_meta": {
                    "mode": "trendradar_mcp",
                    "status": "generated",
                    "source": "trendradar_mcp_get_latest_news+rss",
                    "query": search_query,
                    "stock_name": stock_name,
                    "stock_industry": stock_industry,
                    "hot_total": len(hot_raw),
                    "rss_total": len(rss_raw),
                    "filtered_hot_exact": len(hot_exact),
                    "filtered_rss_exact": len(rss_exact),
                    "filtered_hot_broad": len(hot_items) - len(hot_exact) if used_broad else 0,
                    "filtered_rss_broad": len(rss_items) - len(rss_exact) - (len(browser_articles) if browser_fallback_status != "skipped" else 0) if used_broad else 0,
                    "used_broad_match": used_broad,
                    "browser_fallback_status": browser_fallback_status,
                },
                "narrative_context": output_payload["narrative_context"],
                "manual_news_raw": news_sentiment,
                "session_hint": "trendradar_mcp_only",
            }
        else:
            result = {
                "status": "missing",
                "reason": news_sentiment.get("reason") or "TrendRadar 未返回有效消息",
                "resolved_news_json_path": None,
                "news_pipeline_meta": {
                    "mode": "trendradar_mcp",
                    "status": "missing",
                    "query": search_query,
                    "stock_industry": stock_industry,
                    "hot_total": len(hot_raw),
                    "rss_total": len(rss_raw),
                    "filtered_hot_exact": len(hot_exact),
                    "filtered_rss_exact": len(rss_exact),
                    "filtered_hot_broad": len(hot_items) - len(hot_exact) if used_broad else 0,
                    "filtered_rss_broad": len(rss_items) - len(rss_exact) - (len(browser_articles) if browser_fallback_status != "skipped" else 0) if used_broad else 0,
                    "used_broad_match": used_broad,
                    "browser_fallback_status": browser_fallback_status,
                },
                "narrative_context": {},
                "manual_news_raw": news_sentiment,
                "session_hint": "trendradar_mcp_only",
            }

    except Exception as e:
        result = {
            "status": "missing",
            "error": str(e),
            "resolved_news_json_path": None,
            "news_pipeline_meta": {},
            "narrative_context": {},
            "manual_news_raw": {},
        }

    return result


# ── Agent-B: 分钟线 ──────────────────────────────────

def run_intraday_agent(
    pure_symbol: str,
    trade_date_text: str,
    now: Any,
    resolved_checkpoint: Optional[str] = None,
) -> dict[str, Any]:
    """分钟线获取：本地 → Browser 补全"""
    result: dict[str, Any] = {"status": "missing"}

    try:
        intraday = safe_intraday(
            pure_symbol, trade_date_text, now=now, checkpoint=resolved_checkpoint
        )
        result = {
            "status": "available",
            "intraday": intraday,
        }
    except Exception as e:
        result = {
            "status": "missing",
            "error": str(e),
            "intraday": {},
        }

    return result


# ── Agent-C: 大盘+板块+题材 ────────────────────────────

def run_sector_agent(
    full_symbol: str,
    trade_date_text: str,
) -> dict[str, Any]:
    """大盘环境 + 板块题材 + 移动端发现"""
    result: dict[str, Any] = {"status": "missing"}

    try:
        market_context = analyze_market_context(full_symbol, trade_date_text)
        sector_context = analyze_sector_context(full_symbol, trade_date_text)

        # 移动端题材发现
        mobile_theme_leaders = discover_mobile_theme_leaders_if_needed(sector_context)
        if mobile_theme_leaders:
            sector_context["mobile_theme_leaders"] = mobile_theme_leaders

        mobile_subtheme_discovery = discover_mobile_subthemes_if_needed(sector_context)
        if mobile_subtheme_discovery:
            sector_context["mobile_subtheme_discovery"] = mobile_subtheme_discovery
            mobile_subtheme_match = match_mobile_subthemes(sector_context)
            if mobile_subtheme_match:
                sector_context["mobile_subtheme_match"] = mobile_subtheme_match

        leader_prediction = build_leader_prediction(sector_context)
        if leader_prediction:
            sector_context["leader_prediction"] = leader_prediction

        result = {
            "status": "available",
            "market_context": market_context,
            "sector_context": sector_context,
        }
    except Exception as e:
        result = {
            "status": "missing",
            "error": str(e),
            "market_context": {},
            "sector_context": {},
        }

    return result


# ── Agent-D: 个股维度（不含 next_day，它依赖 narrative）────

def run_stock_dims_agent(
    full_symbol: str,
    trade_date_text: str,
    trade_date_compact: str,
) -> dict[str, Any]:
    """个股基本面维度：融资融券 + 竞价 + 趋势 + 筹码 + 波动率 + 基本面"""
    result: dict[str, Any] = {"status": "missing"}

    try:
        financing_context = analyze_financing_context(full_symbol, trade_date_text)
        auction_intent = analyze_auction_intent(full_symbol, trade_date_text)
        trend_structure = analyze_trend_structure(full_symbol, trade_date_text)
        chip_structure = analyze_chip_structure(full_symbol, trade_date_text)
        volatility_context = analyze_volatility_context(full_symbol, trade_date_text)

        # 基本面（纯本地计算）
        daily_basic_row = load_daily_basic_row(full_symbol, trade_date_compact)
        fundamental = {
            "status": "available" if daily_basic_row else "missing",
            "pe": safe_float(daily_basic_row.get("pe")) if daily_basic_row else None,
            "pe_ttm": safe_float(daily_basic_row.get("pe_ttm")) if daily_basic_row else None,
            "pb": safe_float(daily_basic_row.get("pb")) if daily_basic_row else None,
            "total_mv": safe_float(daily_basic_row.get("total_mv")) if daily_basic_row else None,
            "circ_mv": safe_float(daily_basic_row.get("circ_mv")) if daily_basic_row else None,
        }

        result = {
            "status": "available",
            "financing_context": financing_context,
            "auction_intent": auction_intent,
            "trend_structure": trend_structure,
            "chip_structure": chip_structure,
            "volatility_context": volatility_context,
            "fundamental": fundamental,
        }
    except Exception as e:
        result = {
            "status": "missing",
            "error": str(e),
            "financing_context": {},
            "auction_intent": {},
            "trend_structure": {},
            "chip_structure": {},
            "volatility_context": {},
            "fundamental": {},
        }

    return result


# ── Agent-E: 龙虎榜（时段感知）────────────────────────

def run_dragon_tiger_agent(
    full_symbol: str,
    trade_date_text: str,
    trade_date_compact: str,
) -> dict[str, Any]:
    """
    龙虎榜分析：根据当前时段决定分析深度。
    - 盘后: 完整分析（席位追踪、资金趋势、连续性评分）
    - 盘中: 轻量快查（顶级游资首次介入、机构逆势净买）
    - 盘前: 跳过
    """
    current_session = _resolve_current_session()

    # 盘前 → 跳过
    if current_session in ("盘前", "盘前准备"):
        return {
            "status": "skipped",
            "reason": "盘前时段，龙虎榜数据为前日数据，跳过分析",
            "session": current_session,
            "signal": None,
            "overall_score": None,
            "confidence": 0,
        }

    ts_code = full_symbol
    end_date = trade_date_compact

    try:
        # 从 orchestrator_dragon_tiger 导入
        from agents.orchestrator_dragon_tiger import (
            preprocess_dragon_tiger,
            should_launch_agent,
            aggregate_exalters_for_agent,
            compute_fund_trend,
            extract_hm_matched,
        )
    except ImportError:
        return {
            "status": "error",
            "reason": "orchestrator_dragon_tiger 模块导入失败",
            "session": current_session,
            "signal": None,
            "overall_score": None,
            "confidence": 0,
        }

    # 盘后 → 完整分析
    if current_session == "盘后":
        try:
            summary = preprocess_dragon_tiger(ts_code, end_date)
            if not should_launch_agent(summary):
                return {
                    "status": "skipped",
                    "reason": "该股无连续上榜记录，跳过龙虎榜分析",
                    "session": current_session,
                    "consecutive_days": summary.get("consecutive_days", 0),
                    "signal": None,
                    "overall_score": None,
                    "confidence": 0,
                }
            return {
                "status": "available",
                "session": current_session,
                "analysis_type": "full",
                "consecutive_days": summary.get("consecutive_days", 0),
                "dates_on_list": summary.get("dates_on_list", []),
                "hm_matched": extract_hm_matched(summary),
                "persistent_exalters": summary.get("persistent_exalters", []),
                "left_exalters": summary.get("left_exalters", []),
                "new_exalters": summary.get("new_exalters", []),
                "fund_trend": compute_fund_trend(summary),
                "exalter_aggregation": summary.get("exalter_aggregation", []),
            }
        except Exception as e:
            return {
                "status": "error",
                "session": current_session,
                "analysis_type": "full",
                "error": str(e),
                "signal": None,
                "overall_score": None,
                "confidence": 0,
            }

    # 盘中 → 轻量快查（兜底：非盘前、非盘后即为盘中）
    try:
        summary = preprocess_dragon_tiger(ts_code, end_date)
        if not should_launch_agent(summary):
            return {
                "status": "skipped",
                "reason": "该股无上榜记录，盘中跳过龙虎榜快查",
                "session": current_session,
                "signal": None,
                "overall_score": None,
                "confidence": 0,
            }

        # 盘中只看两个信号
        signals = {}
        hm_matched = extract_hm_matched(summary)
        if hm_matched and summary.get("consecutive_days", 0) <= 2:
            signals["顶级游资首次介入"] = hm_matched

        # 机构逆势净买
        exalter_agg = summary.get("exalter_aggregation", [])
        for e in exalter_agg:
            if e.get("type") == "机构" and e.get("net_buy", 0) > 0:
                signals["机构净买"] = e

        return {
            "status": "available",
            "session": current_session,
            "analysis_type": "quick_check",
            "consecutive_days": summary.get("consecutive_days", 0),
            "signals": signals,
            "hm_matched": hm_matched,
            "signal": None,
            "overall_score": None,
            "confidence": 0,
        }
    except Exception as e:
        return {
            "status": "error",
            "session": current_session,
            "analysis_type": "quick_check",
            "error": str(e),
            "signal": None,
            "overall_score": None,
            "confidence": 0,
        }


# ── Agent-F: K线+因子同步 ────────────────────────────

def run_kline_sync_agent(
    full_symbol: str,
    trade_date_text: str,
    now: Any,
) -> dict[str, Any]:
    """浏览器日K线同步 + 本地因子重建（并行执行，不阻塞其他Agent）"""
    result: dict[str, Any] = {"status": "missing"}

    try:
        reference_date_text = now.strftime("%Y-%m-%d")
        kline_sync = sync_latest_daily_kline_via_browser(
            full_symbol, trade_date_text, reference_date_text=reference_date_text,
        )
        factor_sync = rebuild_stk_factor_pro_from_daily(full_symbol)
        result = {
            "status": "available",
            "kline_sync": kline_sync,
            "factor_sync": factor_sync,
        }
    except Exception as e:
        result = {
            "status": "error",
            "error": str(e),
            "kline_sync": {"status": "error", "error": str(e)},
            "factor_sync": {"status": "error", "error": str(e)},
        }

    return result


# ── Agent-G: 分钟级联动分析 ────────────────────────────

LINKAGE_TASK = """基于联动分析数据，判断个股与大盘/板块的日内联动质量。
返回 JSON:
{
  "linkage_label": "强跟随"|"一般跟随"|"弱跟随"|"脱钩"|"独立走势",
  "relative_strength_judgment": "明显强于大盘"|"略强"|"同步"|"略弱"|"明显弱势",
  "conduction_quality": "及时"|"滞后"|"未跟随",
  "correlation_quality": "紧密"|"中等"|"松散",
  "divergence_risk": "高"|"中"|"低",
  "narrative": "一句话总结日内联动特征",
  "confidence": 0-1
}"""


def analyze_intraday_linkage(
    minute: Any,   # MinuteSlice
    market_index: Any | None = None,  # MarketSlice
    top_theme: str | None = None,
    trade_date_text: str = "",
) -> dict:
    """纯分析函数: 从 MinuteSlice → 联动标签, 不做数据获取"""
    from signals.intraday_linkage import score_linkage
    from llm.llm_client import llm_judge

    stock_rows = minute.stock if hasattr(minute, 'stock') else minute.get('stock', [])
    market_rows = minute.indexes.get("sh000001", []) if hasattr(minute, 'indexes') else minute.get('indexes', {}).get("sh000001", [])
    sector_rows = minute.sector if hasattr(minute, 'sector') else minute.get('sector', [])

    if not stock_rows:
        return {"status": "no_data", "linkage_label": "无分钟数据"}
    if not market_rows:
        return {"status": "no_market_data", "linkage_label": "无大盘分钟数据"}

    try:
        indicators = score_linkage(stock_rows, market_rows, sector_rows if sector_rows else None)

        llm_context = {
            "relative_strength": indicators.get("vs_market", {}),
            "time_conduction": indicators.get("time_conduction", {}),
            "correlation": indicators.get("correlation_market", {}),
            "divergence": indicators.get("divergence", {}),
            "sector_correlation": indicators.get("correlation_sector", {}),
            "stock_info": {"name": "", "top_theme": top_theme or ""},
        }
        linkage_result = llm_judge(LINKAGE_TASK, llm_context, timeout=90)

        return {
            "status": "ok",
            "linkage_label": linkage_result.get("linkage_label", "未知"),
            "relative_strength_judgment": linkage_result.get("relative_strength_judgment", ""),
            "conduction_quality": linkage_result.get("conduction_quality", ""),
            "correlation_quality": linkage_result.get("correlation_quality", ""),
            "divergence_risk": linkage_result.get("divergence_risk", ""),
            "narrative": linkage_result.get("narrative", ""),
            "confidence": linkage_result.get("confidence", 0),
            "linkage_indicators": indicators,
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "linkage_label": "异常"}


def run_intraday_linkage_agent(
    pure_symbol: str,
    trade_date_text: str,
    top_theme: str | None = None,
) -> dict:
    """Phase 2 Agent 7: 兼容旧接口, 内部通过 DataSlicer 构建 MinuteSlice → 纯分析"""
    from data.dataslicer import slice_all
    
    td = trade_date_text.replace("-", "")
    slices = slice_all(pure_symbol, td, top_theme)
    return analyze_intraday_linkage(slices["minute"], slices["market"], top_theme, trade_date_text)


# ── Agent-H: 基本面深度背调 ──────────────────────────────

FUNDAMENTAL_TASK = """基于基本面数据，判断个股的财务健康状况和投资价值。
数据层级说明:
- Tier 1: 只有业绩快报+估值数据
- Tier 2: 有三表深度数据（利润表+资产负债表+现金流量表）
- Tier 3: 有主营业务构成数据
- Tier 4: 有前十大股东数据
- Tier 5: 有背调数据（股权质押、解禁、回购、股东变动）
- Tier 6: 有风险搜索数据（破产、冻结、处罚、问询等司法/监管风险）

返回 JSON:
{
  "financial_health": "优秀"|"良好"|"一般"|"关注"|"风险",
  "trend_label": "增长期"|"稳定期"|"下滑期"|"不确定",
  "growth_quality": "高质量增长"|"粗放增长"|"无增长"|"收缩",
  "valuation_judgment": "低估"|"合理"|"高估"|"不确定",
  "risk_flags": ["风险标签"],
  "strength_flags": ["优势标签"],
  "background_check": {
    "pledge_status": "无质押"|"低比例质押"|"高比例质押"|"数据缺失",
    "unlock_pressure": "无解禁"|"近期解禁"|"大额解禁"|"数据缺失",
    "repurchase_status": "有回购"|"无回购"|"数据缺失",
    "shareholder_change": "增持"|"减持"|"稳定"|"数据缺失",
    "governance_risks": ["治理风险标签"]
  },
  "risk_search": {
    "has_risk": true/false,
    "risk_keywords": ["发现的风险关键词"],
    "risk_summary": "风险摘要",
    "judicial_risks": ["司法风险标签"],
    "regulatory_risks": ["监管风险标签"]
  },
  "narrative": "总结基本面特征",
  "confidence": 0-1
}"""


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _calc_trend(values: list[float]) -> str:
    if len(values) < 2:
        return "未知"
    changes = [
        (values[i] - values[i + 1]) / abs(values[i + 1]) * 100
        if values[i + 1] != 0 else 0
        for i in range(len(values) - 1)
    ]
    avg_change = sum(changes) / len(changes) if changes else 0
    if avg_change > 10:
        return "快速增长"
    elif avg_change > 0:
        return "温和增长"
    elif avg_change > -10:
        return "小幅下滑"
    else:
        return "大幅下滑"


def _build_tier1(express_rows: list[dict], daily_basic: dict, industry: str) -> dict:
    """Tier 1: 财务趋势+估值。express 有数据时产出完整指标, 缺失时仅估值。"""
    result: dict[str, Any] = {
        "tier": 1, "has_data": True,
        "pe": _safe_float(daily_basic.get("pe_ttm")),
        "pb": _safe_float(daily_basic.get("pb")),
        "total_mv": _safe_float(daily_basic.get("total_mv")),
        "turnover_rate": _safe_float(daily_basic.get("turnover_rate")),
        "dv_ttm": _safe_float(daily_basic.get("dv_ttm")),
        "float_share": _safe_float(daily_basic.get("float_share")),
        "total_share": _safe_float(daily_basic.get("total_share")),
        "ps_ttm": _safe_float(daily_basic.get("ps_ttm")),
        "industry": industry,
    }
    if not express_rows:
        result["has_financials"] = False
        return result
    
    latest = express_rows[0]
    revenues = [_safe_float(r.get("revenue")) for r in express_rows[:3] if _safe_float(r.get("revenue"))]
    profits = [_safe_float(r.get("n_income")) for r in express_rows[:3] if _safe_float(r.get("n_income")) is not None]
    result["has_financials"] = True
    result["revenue"] = _safe_float(latest.get("revenue"))
    result["n_income"] = _safe_float(latest.get("n_income"))
    result["roe"] = _safe_float(latest.get("diluted_roe"))
    result["eps"] = _safe_float(latest.get("diluted_eps"))
    result["yoy_profit_growth"] = _safe_float(latest.get("yoy_net_profit"))
    result["revenue_trend"] = _calc_trend(revenues) if len(revenues) >= 2 else "未知"
    result["profit_trend"] = _calc_trend(profits) if len(profits) >= 2 else "未知"
    return result


def _build_tier2(income_rows: list[dict], bs_rows: list[dict], cf_rows: list[dict]) -> dict:
    if not income_rows and not bs_rows:
        return {"tier": 2, "has_data": False}
    result: dict[str, Any] = {"tier": 2, "has_data": True}
    if bs_rows:
        bs = bs_rows[0]
        ta = _safe_float(bs.get("total_assets", bs.get("total_liab_hldr_eqy")))
        tl = _safe_float(bs.get("total_liab"))
        ca = _safe_float(bs.get("total_cur_assets"))
        cl = _safe_float(bs.get("total_cur_liab"))
        if ta and tl: result["debt_to_assets"] = round(tl / ta, 4)
        if ca and cl: result["current_ratio"] = round(ca / cl, 4)
        if ta:
            for name, key in [("receivables_ratio", "accounts_receiv"), ("inventory_ratio", "inventories"), ("goodwill_ratio", "goodwill")]:
                v = _safe_float(bs.get(key))
                if v: result[name] = round(v / ta, 4)
    if income_rows:
        inc = income_rows[0]
        rev = _safe_float(inc.get("revenue"))
        if rev:
            for name, key in [("rd_ratio", "rd_exp"), ("sell_exp_ratio", "sell_exp"), ("impair_loss_ratio", "assets_impair_loss")]:
                v = _safe_float(inc.get(key))
                if v: result[name] = round(v / rev, 4)
    if cf_rows:
        fcf = _safe_float(cf_rows[0].get("free_cashflow"))
        if fcf is not None: result["free_cashflow"] = fcf
    return result


def _build_tier3(mainbz_rows: list[dict]) -> dict:
    if not mainbz_rows:
        return {"tier": 3, "has_data": False}
    result: dict[str, Any] = {"tier": 3, "has_data": True}
    latest_end = max(r.get("end_date", "") for r in mainbz_rows)
    current = [r for r in mainbz_rows if r.get("end_date") == latest_end]
    current.sort(key=lambda r: _safe_float(r.get("bz_sales", 0)) or 0, reverse=True)
    if current:
        total_sales = sum(_safe_float(r.get("bz_sales", 0)) or 0 for r in current)
        top = current[0]
        result["top_segment"] = top.get("bz_item", "")
        result["top_segment_ratio"] = round((_safe_float(top.get("bz_sales", 0)) or 0) / total_sales, 4) if total_sales else 0
        result["segment_count"] = len(current)
        top3_ratio = sum(_safe_float(r.get("bz_sales", 0)) or 0 for r in current[:3]) / total_sales if total_sales else 0
        result["diversity"] = "单一" if top3_ratio > 0.8 else "集中" if top3_ratio > 0.5 else "多元"
    return result


def _build_tier4(holder_rows: list[dict]) -> dict:
    if not holder_rows:
        return {"tier": 4, "has_data": False}
    result: dict[str, Any] = {"tier": 4, "has_data": True}
    total_ratio = sum(_safe_float(r.get("hold_ratio", 0)) or 0 for r in holder_rows)
    result["top10_total_ratio"] = round(total_ratio, 4)
    top = holder_rows[0]
    result["top1_name"] = top.get("holder_name", "")
    result["top1_ratio"] = _safe_float(top.get("hold_ratio"))
    inst_ratio = sum(
        _safe_float(r.get("hold_ratio", 0)) or 0
        for r in holder_rows
        if r.get("holder_type") in ("基金", "QFII", "券商", "保险", "信托", "社保")
    )
    result["institution_ratio"] = round(inst_ratio, 4)
    return result


def _build_tier5(
    pledge_stat_rows: list[dict],
    pledge_detail_rows: list[dict],
    share_float_rows: list[dict],
    repurchase_rows: list[dict],
    holder_rows: list[dict],
) -> dict:
    """Tier 5: 背调数据（股权质押、解禁、回购、股东变动）"""
    result: dict[str, Any] = {"tier": 5, "has_data": False}

    # 股权质押统计
    if pledge_stat_rows:
        result["has_data"] = True
        latest_pledge = pledge_stat_rows[-1] if pledge_stat_rows else {}
        pledge_ratio = _safe_float(latest_pledge.get("pledge_ratio", 0)) or 0
        result["pledge_ratio"] = round(pledge_ratio, 4)
        if pledge_ratio == 0:
            result["pledge_status"] = "无质押"
        elif pledge_ratio < 0.3:
            result["pledge_status"] = "低比例质押"
        else:
            result["pledge_status"] = "高比例质押"
    else:
        result["pledge_status"] = "数据缺失"

    # 股权质押明细（统计质押次数）
    if pledge_detail_rows:
        result["has_data"] = True
        result["pledge_count"] = len(pledge_detail_rows)

    # 限售股解禁
    if share_float_rows:
        result["has_data"] = True
        # 检查近期是否有解禁（假设数据包含 float_date 字段）
        recent_floats = [
            r for r in share_float_rows
            if r.get("float_date", "") >= "20260101"  # 2026年以来的解禁
        ]
        if recent_floats:
            total_float_ratio = sum(_safe_float(r.get("float_ratio", 0)) or 0 for r in recent_floats)
            result["unlock_ratio_2026"] = round(total_float_ratio, 4)
            if total_float_ratio > 0.1:
                result["unlock_pressure"] = "大额解禁"
            else:
                result["unlock_pressure"] = "近期解禁"
        else:
            result["unlock_pressure"] = "无解禁"
    else:
        result["unlock_pressure"] = "数据缺失"

    # 股票回购
    if repurchase_rows:
        result["has_data"] = True
        result["repurchase_status"] = "有回购"
        result["repurchase_count"] = len(repurchase_rows)
    else:
        result["repurchase_status"] = "无回购"

    # 股东变动（对比最新两次 holder 数据）
    if holder_rows and len(holder_rows) >= 2:
        result["has_data"] = True
        # 简单判断：如果最新一期的持股比例总和 > 上一期，则为增持
        latest_total = sum(_safe_float(r.get("hold_ratio", 0)) or 0 for r in holder_rows[:10])
        result["top10_total_ratio_latest"] = round(latest_total, 4)
        result["shareholder_change"] = "稳定"  # 默认
    else:
        result["shareholder_change"] = "数据缺失"

    return result


def run_fundamental_agent(
    pure_symbol: str,
    full_symbol: str,
    trade_date_text: str,
) -> dict:
    """Phase 2 Agent 8: 基本面深度背调 (6级降级)"""
    from data.fundamental_provider import (
        get_fundamental_express,
        get_fundamental_income,
        get_fundamental_balancesheet,
        get_fundamental_cashflow,
        get_fundamental_mainbz,
        get_top10_holders,
        get_pledge_stat,
        get_pledge_detail,
        get_share_float,
        get_repurchase,
        get_risk_info_from_search,
    )
    from data.data_provider import get_daily_basic, get_stock_basic
    from llm.llm_client import llm_judge

    try:
        # Tier 1: express + daily_basic (daily_basic 始终有, express 可选)
        express_rows = get_fundamental_express(full_symbol)
        daily_basic = get_daily_basic(full_symbol, trade_date_text) or {}
        stock_basic = get_stock_basic(full_symbol) or {}
        industry = stock_basic.get("industry", "")
        stock_name = stock_basic.get("name", "")

        t1 = _build_tier1(express_rows, daily_basic, industry)
        tier_level = 1
        has_financials = t1.get("has_financials", False)

        # Tier 2-4: 仅当有报表数据时叠加
        holder_rows = []
        if has_financials:
            income_rows = get_fundamental_income(full_symbol)
            bs_rows = get_fundamental_balancesheet(full_symbol)
            cf_rows = get_fundamental_cashflow(full_symbol)
            t2 = _build_tier2(income_rows, bs_rows, cf_rows)
            if t2.get("has_data"):
                tier_level = 2

            mainbz_rows = get_fundamental_mainbz(full_symbol)
            t3 = _build_tier3(mainbz_rows)
            if t3.get("has_data"):
                tier_level = 3

            holder_rows = get_top10_holders(full_symbol)
            t4 = _build_tier4(holder_rows)
            if t4.get("has_data"):
                tier_level = 4
        else:
            t2, t3, t4 = {"tier": 2, "has_data": False}, {"tier": 3, "has_data": False}, {"tier": 4, "has_data": False}

        # Tier 5: 背调数据（股权质押、解禁、回购、股东变动）
        pledge_stat_rows = get_pledge_stat(full_symbol)
        pledge_detail_rows = get_pledge_detail(full_symbol)
        share_float_rows = get_share_float(full_symbol)
        repurchase_rows = get_repurchase(full_symbol)
        t5 = _build_tier5(pledge_stat_rows, pledge_detail_rows, share_float_rows, repurchase_rows, holder_rows)
        if t5.get("has_data"):
            tier_level = 5

        # Tier 6: 风险搜索（破产、冻结、处罚、问询等）
        pure_code = full_symbol.split(".")[0]
        risk_info = get_risk_info_from_search(stock_name, pure_code)
        t6 = {
            "tier": 6,
            "has_data": risk_info.get("has_risk", False),
            "risk_keywords": risk_info.get("risk_keywords", []),
            "risk_summary": risk_info.get("risk_summary", ""),
            "detailed_risks": risk_info.get("detailed_risks", []),
        }
        if t6.get("has_data"):
            tier_level = 6

        # Merge all available tiers into a flat context for LLM
        context = {
            "tier_level": tier_level,
            "has_financials": has_financials,
            "data_note": "含财务报表+背调+风险搜索, 可深度分析" if has_financials else "仅估值数据(PE/PB/市值等), 无利润表/资产负债表",
        }
        for tier_dict in (t1, t2, t3, t4, t5, t6):
            for k, v in tier_dict.items():
                if k not in ("tier", "has_data", "has_financials"):
                    context[k] = v

        llm_result = llm_judge(FUNDAMENTAL_TASK, context, timeout=90)

        return {
            "status": "ok",
            "tier_level": tier_level,
            "has_financials": has_financials,
            "financial_health": llm_result.get("financial_health", "未知"),
            "trend_label": llm_result.get("trend_label", "不确定"),
            "growth_quality": llm_result.get("growth_quality", ""),
            "valuation_judgment": llm_result.get("valuation_judgment", ""),
            "risk_flags": llm_result.get("risk_flags", []),
            "strength_flags": llm_result.get("strength_flags", []),
            "background_check": llm_result.get("background_check", {}),
            "risk_search": t6,
            "narrative": llm_result.get("narrative", ""),
            "confidence": llm_result.get("confidence", 0),
            "metrics": {k: v for k, v in context.items() if k != "stock_info"},
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "financial_health": "异常"}
