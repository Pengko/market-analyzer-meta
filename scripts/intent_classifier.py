#!/usr/bin/env python3
"""
意图识别器：判断用户输入属于哪个分析方向。

用法：
    python intent_classifier.py "京东方怎么样"
    python intent_classifier.py "大盘怎么看"
    python intent_classifier.py "有什么利好消息"

输出：
    {"direction": "stock"|"market"|"news", "confidence": 0-100, "reason": "..."}
"""

import re
import sys
from typing import Any


# 股票代码模式：6位数字，可带 .SH/.SZ 后缀
STOCK_CODE_PATTERN = re.compile(r'\b\d{6}(\.(SH|SZ|sh|sz))?\b')

# 常见股票名称关键词（部分列表，实际可扩展）
STOCK_NAME_KEYWORDS = [
    '京东方', '青山纸业', '泰尔股份', '彩虹股份', '三安光电', '协鑫能科',
    '美利云', '再升科技', '太极实业', '永鼎股份', '中际旭创', '长飞光纤',
    '蓝思科技', 'TCL科技', '维信诺', '深天马', '华星光电',
]

# 大盘/板块关键词
MARKET_KEYWORDS = [
    '大盘', '市场', '指数', '上证', '深证', '创业板', '沪指', '深成指',
    '板块', '题材', '热点', '轮动', '风格', '情绪', '涨停', '跌停',
    '北向', '外资', '成交额', '放量', '缩量', '风险偏好',
]

# 消息面关键词
NEWS_KEYWORDS = [
    '消息', '新闻', '公告', '利好', '利空', '政策', '央行', '发改委',
    '证监会', '国务院', '回购', '增持', '减持', '并购', '重组',
    '涨价', '供需', '产能', '订单', '业绩', '预增', '预减',
    '有什么', '最近', '今天', '昨天',
]


def classify_intent(text: str) -> dict[str, Any]:
    """
    判断用户输入的分析方向。

    Returns:
        {
            "direction": "stock" | "market" | "news",
            "confidence": 0-100,
            "reason": "判断依据",
            "extracted": {...}  # 提取的关键信息
        }
    """
    text = text.strip()
    if not text:
        return {
            "direction": "market",
            "confidence": 50,
            "reason": "空输入，默认大盘方向",
            "extracted": {},
        }

    scores = {"stock": 0, "market": 0, "news": 0}
    reasons = {"stock": [], "market": [], "news": []}
    extracted = {"stock_codes": [], "stock_names": [], "keywords": []}

    # 1. 检查股票代码
    codes = [m.group() for m in STOCK_CODE_PATTERN.finditer(text)]
    if codes:
        scores["stock"] += 40
        reasons["stock"].append(f"包含股票代码: {codes}")
        extracted["stock_codes"] = codes

    # 2. 检查股票名称
    for name in STOCK_NAME_KEYWORDS:
        if name in text:
            scores["stock"] += 30
            reasons["stock"].append(f"包含股票名称: {name}")
            extracted["stock_names"].append(name)

    # 3. 检查大盘/板块关键词
    market_hits = []
    for kw in MARKET_KEYWORDS:
        if kw in text:
            market_hits.append(kw)
    if market_hits:
        scores["market"] += min(30, len(market_hits) * 10)
        reasons["market"].append(f"包含大盘关键词: {market_hits}")
        extracted["keywords"].extend(market_hits)

    # 4. 检查消息面关键词
    news_hits = []
    for kw in NEWS_KEYWORDS:
        if kw in text:
            news_hits.append(kw)
    if news_hits:
        scores["news"] += min(30, len(news_hits) * 10)
        reasons["news"].append(f"包含消息面关键词: {news_hits}")
        extracted["keywords"].extend(news_hits)

    # 5. 特殊模式加分
    # "XX怎么样" / "XX能买吗" → 倾向个股
    if re.search(r'[\u4e00-\u9fa5]{2,}(怎么样|能买吗|适合买|分析|怎么看)', text):
        # 如果前面没有大盘/板块关键词，则倾向个股
        if not any(kw in text for kw in ['大盘', '市场', '板块']):
            scores["stock"] += 20
            reasons["stock"].append("个股分析模式: XX怎么样/能买吗")

    # "大盘/市场怎么看" → 倾向大盘
    if re.search(r'(大盘|市场|指数).*怎么看', text):
        scores["market"] += 30
        reasons["market"].append("大盘分析模式: 大盘怎么看")

    # "有什么消息/利好" → 倾向消息面
    if re.search(r'有什么.*(消息|利好|利空|新闻)', text):
        scores["news"] += 30
        reasons["news"].append("消息面分析模式: 有什么消息")

    # "利好/利空哪些" → 倾向消息面
    if re.search(r'(利好|利空).*(哪些|什么)', text):
        scores["news"] += 25
        reasons["news"].append("消息面分析模式: 利好哪些")

    # 6. 判断结果
    max_direction = max(scores, key=scores.get)
    max_score = scores[max_direction]

    # 如果分数太低，用关键词数量做最终判断
    if max_score < 10:
        # 默认大盘方向
        max_direction = "market"
        max_score = 50
        reasons["market"].append("无明确意图，默认大盘方向")

    # 计算置信度
    total = sum(scores.values())
    confidence = int(max_score / max(total, 1) * 100) if total > 0 else 50
    confidence = min(95, max(30, confidence))

    return {
        "direction": max_direction,
        "confidence": confidence,
        "reason": "; ".join(reasons[max_direction]),
        "scores": scores,
        "extracted": extracted,
    }


def main():
    if len(sys.argv) < 2:
        print("用法: python intent_classifier.py '用户输入'")
        sys.exit(1)

    text = " ".join(sys.argv[1:])
    result = classify_intent(text)

    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
