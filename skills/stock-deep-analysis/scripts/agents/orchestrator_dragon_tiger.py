#!/usr/bin/env python3
"""
Orchestrator 龙虎榜预处理模块。

这个模块负责：
1. 调用 quick_analyze.analyze_top_list_series 进行龙虎榜数据预处理
2. 将原始输出转换为 DragonTiger Agent 的结构化输入
3. 进行游资标签匹配
4. 返回是否启动 DragonTiger Agent 的决策 + 预处理后的 JSON

用法（被主 Orchestrator 调用）：
    from orchestrator_dragon_tiger import preprocess_dragon_tiger, should_launch_agent
    summary = preprocess_dragon_tiger(ts_code, end_date, lookback=10)
    if should_launch_agent(summary):
        # 启动 DragonTiger Agent
        pass
"""

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

# 添加项目根目录
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from quick_analyze import analyze_top_list_series, get_recent_trade_dates


# 游资席位关键词映射（简化版，实际可从 hm_list 加载完整映射）
HM_KEYWORDS: dict[str, list[str]] = {
    "量化基金": [
        "摩根士胆利沪港通", "中金沪港通", "中信沪港通",
        "国泰君安总部", "瑞士银行花园桥路",
    ],
    "量化打板": [
        "开源证券西安太华路",
    ],
    "T王": [
        "东方财富拉萨东环路第一证券营业部",
        "东方财富拉萨东环路第二证券营业部",
    ],
    "沪股通专用": [
        "沪股通专用",
    ],
    "深股通专用": [
        "深股通专用",
    ],
    "机构专用": [
        "机构专用",
    ],
}


def match_hm_label(exalter_name: str) -> Optional[str]:
    """根据席位名称匹配游资标签"""
    if not exalter_name:
        return None
    for label, keywords in HM_KEYWORDS.items():
        for kw in keywords:
            if kw in exalter_name:
                return label
    return None


def aggregate_exalters_for_agent(raw_result: dict) -> list[dict]:
    """
将 analyze_top_list_series 的 exalter_continuity 转换为 Agent 输入格式
    """
    agg = []
    continuity = raw_result.get("exalter_continuity", {})

    for category in ["persistent_exalters", "left_exalters", "new_exalters"]:
        for info in continuity.get(category, []):
            name = info.get("name", "")
            label = match_hm_label(name)
            dates = info.get("appearance_dates", [])
            trend = "持续介入" if category == "persistent_exalters" else (
                "已离场" if category == "left_exalters" else "新进场"
            )
            agg.append({
                "name": name,
                "label": label or "未匹配",
                "days_present": info.get("appearance_count", 0),
                "total_buy": info.get("total_buy", 0),
                "total_sell": info.get("total_sell", 0),
                "net": info.get("total_net_buy", 0),
                "first_date": dates[0] if dates else None,
                "last_date": dates[-1] if dates else None,
                "trend": trend,
            })

    # 按出现天数排序
    agg.sort(key=lambda x: x["days_present"], reverse=True)
    return agg


def compute_fund_trend(raw_result: dict) -> str:
    """基于连续性和席位变化生成资金流向趋势描述"""
    trend = raw_result.get("trend", "")
    continuity = raw_result.get("exalter_continuity", {})
    persistent = continuity.get("persistent_exalters", [])
    left = continuity.get("left_exalters", [])
    new_coming = continuity.get("new_exalters", [])
    consecutive = raw_result.get("consecutive_days", 0)

    parts = []
    if trend:
        parts.append(trend)

    if persistent:
        p_names = [p["name"][:10] for p in persistent[:3]]
        parts.append(f"{len(persistent)}家席位持续参与({', '.join(p_names)}...)")

    if left:
        parts.append(f"{len(left)}家席位离场")
    if new_coming:
        parts.append(f"{len(new_coming)}家新进席位")

    if consecutive >= 3:
        parts.append(f"连续{consecutive}天上榜，注意分散风险")

    return "；".join(parts) if parts else "暂无明确趋势"


def extract_hm_matched(raw_result: dict) -> list[str]:
    """提取匹配到的游资类型列表"""
    continuity = raw_result.get("exalter_continuity", {})
    labels = set()
    for category in ["persistent_exalters", "left_exalters", "new_exalters"]:
        for info in continuity.get(category, []):
            label = match_hm_label(info.get("name", ""))
            if label:
                labels.add(label)
    return sorted(labels)


def preprocess_dragon_tiger(
    ts_code: str,
    end_date: str,
    top_list_data: Optional[dict] = None,
    top_inst_data: Optional[dict] = None,
    lookback: int = 10,
) -> dict:
    """
对指定股票进行龙虎榜预处理，返回适合 DragonTiger Agent 的结构化输入。

    Args:
        ts_code: 股票代码，如 "600103.SH"
        end_date: 截止日期，格式 "20260424"
        top_list_data: 已有的 top_list 数据（可选）
        top_inst_data: 已有的 top_inst 数据（可选）
        lookback: 回望天数

    Returns:
        dict: 结构化输入，字段见 prompt 模板
    """
    # 调用现有分析函数
    raw = analyze_top_list_series(ts_code, end_date, top_list_data or {}, top_inst_data or {}, lookback)

    # 构建 Agent 输入
    daily_summary = []
    for dd in raw.get("daily_details", []):
        daily_summary.append({
            "date": dd.get("date"),
            "net_amount": dd.get("net_amount"),
            "pct_chg": dd.get("pct_change"),
            "reason": dd.get("reason"),
        })

    continuity = raw.get("exalter_continuity", {})
    exalter_aggregation = aggregate_exalters_for_agent(raw)

    summary = {
        "symbol": ts_code,
        "consecutive_days": raw.get("consecutive_days", 0),
        "dates_on_list": raw.get("dates_on_list", []),
        "daily_summary": daily_summary,
        "exalter_aggregation": exalter_aggregation,
        "hm_matched": extract_hm_matched(raw),
        "persistent_exalters": [e["name"] for e in continuity.get("persistent_exalters", [])],
        "left_exalters": [e["name"] for e in continuity.get("left_exalters", [])],
        "new_exalters": [e["name"] for e in continuity.get("new_exalters", [])],
        "fund_trend": compute_fund_trend(raw),
    }

    return summary


def should_launch_agent(summary: dict) -> bool:
    """根据预处理结果判断是否应该启动 DragonTiger Agent。

    规则：
    - 连续上榜天数 > 0 时启动
    - 否则跳过（节省计算资源）
    """
    return summary.get("consecutive_days", 0) > 0 or len(summary.get("dates_on_list", [])) > 0


def build_agent_input_file(summary: dict, output_path: Path) -> None:
    """将预处理结果写入文件，供 DragonTiger Agent 使用"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="DragonTiger Orchestrator Preprocessor")
    parser.add_argument("--ts-code", required=True, help="股票代码，如 600103.SH")
    parser.add_argument("--end-date", required=True, help="截止日期，如 20260424")
    parser.add_argument("--output", "-o", required=True, help="输出 JSON 路径")
    parser.add_argument("--lookback", type=int, default=10, help="回望天数")

    args = parser.parse_args()

    print(f"[Orchestrator-DT] 开始预处理: {args.ts_code} 截止 {args.end_date}")
    summary = preprocess_dragon_tiger(args.ts_code, args.end_date, lookback=args.lookback)

    build_agent_input_file(summary, Path(args.output))

    launch = should_launch_agent(summary)
    print(f"[Orchestrator-DT] 连续上榜天数: {summary['consecutive_days']}")
    print(f"[Orchestrator-DT] 上榜日期: {summary['dates_on_list']}")
    print(f"[Orchestrator-DT] 匹配游资: {summary['hm_matched']}")
    print(f"[Orchestrator-DT] 应启动 Agent: {launch}")
    print(f"[Orchestrator-DT] 输出已写入: {args.output}")

    # 返回退出码供外部脚本判断
    sys.exit(0 if launch else 1)


if __name__ == "__main__":
    main()
