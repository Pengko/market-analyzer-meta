"""
风险信息搜索模块
通过 TrendRadar MCP 抓取股票的司法/监管风险信息
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# 添加 scripts 目录到 sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# TrendRadar MCP 路径
TRENDRADAR_MCP_CLI = Path("/Users/penghongming/agent-skills/custom/trendradar-mcp/scripts/trendradar_mcp_cli.py")
TRENDRADAR_PYTHON = Path("/Users/penghongming/Documents/TrendRadar/.venv/bin/python")

# 风险关键词列表
RISK_KEYWORDS = [
    "破产重整",
    "破产清算",
    "股份冻结",
    "司法拍卖",
    "行政处罚",
    "监管问询",
    "问询函",
    "诉讼",
    "仲裁",
    "债务违约",
    "ST",
    "*ST",
    "退市风险",
    "立案调查",
    "违规",
    "处罚",
    "警示",
    "风险提示",
]


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
        return {"status": "error", "error": wrapper.get("content", [{}])[0].get("text", "Unknown error")}

    # Extract text content from MCP response
    content = wrapper.get("content", [])
    if content and isinstance(content, list):
        text_content = content[0].get("text", "")
        try:
            return json.loads(text_content)
        except json.JSONDecodeError:
            return {"status": "ok", "data": text_content}

    return {"status": "ok", "data": wrapper}


def search_risk_trendradar(stock_name: str, stock_code: str) -> dict[str, Any]:
    """
    通过 TrendRadar MCP 搜索风险关键词
    返回: {
        "has_risk": bool,
        "risk_keywords": list[str],  # 命中的风险关键词
        "risk_summary": str,         # 风险摘要
        "raw_results": list[dict],   # 原始搜索结果
    }
    """
    results = {
        "has_risk": False,
        "risk_keywords": [],
        "risk_summary": "",
        "raw_results": [],
    }

    # 只搜索高风险关键词（减少搜索次数）
    high_risk_keywords = [
        "破产重整",
        "破产清算",
        "股份冻结",
        "司法拍卖",
        "行政处罚",
        "问询函",
        "立案调查",
        "退市风险",
    ]

    for keyword in high_risk_keywords:
        query = f"{stock_name} {keyword}"
        try:
            search_result = _call_trendradar_mcp("search_news", {
                "query": query,
                "search_mode": "keyword",
                "limit": 5,
                "include_url": True,
            })

            if search_result.get("status") == "ok":
                data = search_result.get("data", {})
                news_list = data.get("results", []) or data.get("data", [])

                if news_list and len(news_list) > 0:
                    results["has_risk"] = True
                    results["risk_keywords"].append(keyword)
                    for news in news_list[:3]:  # 只取前3条
                        results["raw_results"].append({
                            "keyword": keyword,
                            "title": news.get("title", ""),
                            "url": news.get("url", ""),
                            "snippet": news.get("content", "")[:200],
                            "platform": news.get("platform", ""),
                            "date": news.get("date", ""),
                        })
        except Exception as e:
            # 搜索失败不影响整体流程
            pass

    # 生成风险摘要
    if results["has_risk"]:
        keywords_str = "、".join(results["risk_keywords"])
        results["risk_summary"] = f"发现以下风险信号: {keywords_str}"
    else:
        results["risk_summary"] = "未发现明显风险信号"

    return results


def get_risk_info(stock_name: str, stock_code: str) -> dict[str, Any]:
    """
    获取股票风险信息的主入口
    通过 TrendRadar MCP 搜索风险信息
    """
    result = search_risk_trendradar(stock_name, stock_code)

    return {
        "has_risk": result.get("has_risk", False),
        "risk_keywords": result.get("risk_keywords", []),
        "risk_summary": result.get("risk_summary", ""),
        "detailed_risks": result.get("raw_results", []),
    }


if __name__ == "__main__":
    # 测试
    result = get_risk_info("京东方A", "000725")
    print(json.dumps(result, ensure_ascii=False, indent=2))
