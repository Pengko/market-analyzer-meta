#!/usr/bin/env python3
"""
DragonTiger Analyst Agent 主入口。

用法:
    python dragon_tiger_analyst.py --input /path/to/dragon_tiger_summary.json --output-dir /path/to/output/

输入: 经 Orchestrator 预处理后的龙虎榜结构化摘要 JSON
输出: 
    - {output_dir}/dragon_tiger_analysis.md   人类可读报告
    - {output_dir}/dragon_tiger_summary.json  Meta-Reviewer 可消费的摘要
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any

# 添加项目根目录到路径
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

DT_TASK = """分析龙虎榜数据，判断资金性质。
返回 JSON:
{
  "signal": "游资接力"|"机构出货"|"量化进出"|"散户主导"|"分歧加大"|"中性",
  "overall_score": 0-10,
  "confidence": 0-100,
  "reasoning": "简要推理",
  "key_seats": ["席位1", "席位2"]
}"""


def load_prompt(template_path: Optional[str] = None) -> str:
    """加载 prompt 模板"""
    if template_path is None:
        template_path = SCRIPT_DIR / "prompts" / "dragon_tiger_prompt.md"
    with open(template_path, "r", encoding="utf-8") as f:
        return f.read()


def build_llm_prompt(summary: dict, prompt_template: str) -> str:
    """将龙虎榜摘要与 prompt 模板组合成完整的 LLM 输入"""
    # 构造结构化输入部分
    input_section = "\n## 龙虎榜摘要数据\n\n```json\n"
    input_section += json.dumps(summary, ensure_ascii=False, indent=2)
    input_section += "\n```\n"
    return prompt_template + "\n" + input_section


def write_outputs(
    output_dir: Path,
    markdown_content: str,
    json_summary: dict,
    symbol: str,
) -> None:
    """写出 Markdown 报告和 JSON 摘要"""
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / "dragon_tiger_analysis.md"
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_content)

    json_path = output_dir / "dragon_tiger_summary.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_summary, f, ensure_ascii=False, indent=2)

    print(f"[DragonTiger] 报告已写入: {md_path}")
    print(f"[DragonTiger] 摘要已写入: {json_path}")


def generate_fallback_output(summary: dict) -> tuple[str, dict]:
    """当 LLM 不可用时，基于规则生成降级输出"""
    symbol = summary.get("symbol", "unknown")
    consecutive = summary.get("consecutive_days", 0)
    dates = summary.get("dates_on_list", [])
    fund_trend = summary.get("fund_trend", "")
    hm_matched = summary.get("hm_matched", [])
    persistent = summary.get("persistent_exalters", [])
    left = summary.get("left_exalters", [])
    new_coming = summary.get("new_exalters", [])

    # 基于规则的简单评分
    score = 5.0
    signal = "无显著信号"
    recommendation = "HOLD"
    risk_flags = []
    observations = []

    if consecutive == 0:
        score = 5.0
        signal = "无数据"
        recommendation = "HOLD"
    else:
        # 根据资金趋势判断
        if "出货" in fund_trend or "走路" in fund_trend:
            score = 3.0
            signal = "机构出货"
            recommendation = "SELL"
            risk_flags.append("量化/游资出货")
        elif "散户" in fund_trend or "T王" in str(hm_matched):
            score = 3.5
            signal = "散户主导"
            recommendation = "REDUCE"
            risk_flags.append("散户化进程")
        elif "接力" in fund_trend:
            score = 7.0
            signal = "游资接力"
            recommendation = "BUY"
        elif left and not new_coming:
            score = 3.0
            signal = "机构出货"
            recommendation = "SELL"
            risk_flags.append("旧席位离场无新接力")

        if consecutive >= 3:
            observations.append(f"连续{consecutive}天上榜，流动性溢出")
        if persistent:
            observations.append(f"{len(persistent)}家席位持续参与")
        if left:
            observations.append(f"{len(left)}家席位已离场")
        if new_coming:
            observations.append(f"{len(new_coming)}家新进席位")

    # 生成 Markdown
    md_lines = [
        f"# 龙虎榜深度分析 — {symbol}",
        "",
        "## 上榜概况",
        f"- 连续上榜天数: {consecutive}天",
        f"- 上榜日期: {', '.join(dates) if dates else '无'}",
        "",
        "## 席位明细",
        f"- 持续席位: {len(persistent)} 家",
        f"- 离场席位: {len(left)} 家",
        f"- 新进席位: {len(new_coming)} 家",
        "",
        "## 资金流向趋势",
        fund_trend or "暂无明确趋势",
        "",
        "## 综合判断",
        f"| 维度 | 判断 |",
        f"|------|------|",
        f"| 龙虎榜评分 | {score}/10 |",
        f"| 主力信号 | {signal} |",
        f"| 操作建议 | {recommendation} |",
        "",
        "## 结论",
        f"基于规则降级输出: {signal}。{' '.join(risk_flags) if risk_flags else '暂无明确风险信号。'}",
    ]

    json_summary = {
        "dimension": "dragon_tiger",
        "overall_score": round(score, 1),
        "recommendation": recommendation,
        "signal": signal,
        "confidence": 5 if score == 5.0 else 7,
        "key_observations": observations,
        "risk_flags": risk_flags,
        "summary": f"{signal}，{' '.join(risk_flags) if risk_flags else '风险不明确'}",
        "fallback": True,
    }

    return "\n".join(md_lines), json_summary


def format_dt_markdown(summary: dict, llm_result: dict) -> str:
    lines = [f"# 龙虎榜分析: {summary.get('symbol', 'unknown')}"]
    lines.append(f"信号: {llm_result.get('signal', 'N/A')}")
    lines.append(f"评分: {llm_result.get('overall_score', 'N/A')}/10")
    lines.append(f"置信度: {llm_result.get('confidence', 0)}%")
    lines.append(f"推理: {llm_result.get('reasoning', '')}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="DragonTiger Analyst Agent")
    parser.add_argument(
        "--input", "-i", required=True, help="输入 JSON 文件路径（预处理后的龙虎榜摘要）"
    )
    parser.add_argument(
        "--output-dir", "-o", required=True, help="输出目录路径"
    )
    parser.add_argument(
        "--prompt", "-p", default=None, help="自定义 prompt 模板路径"
    )
    parser.add_argument(
        "--fallback", action="store_true", help="使用规则降级输出，不调用 LLM"
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)

    if not input_path.exists():
        print(f"[DragonTiger] 错误: 输入文件不存在: {input_path}", file=sys.stderr)
        sys.exit(1)

    with open(input_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    symbol = summary.get("symbol", "unknown")
    print(f"[DragonTiger] 开始分析: {symbol}")
    print(f"[DragonTiger] 连续上榜天数: {summary.get('consecutive_days', 0)}")

    if args.fallback:
        print("[DragonTiger] 使用规则降级输出模式")
        md_content, json_summary = generate_fallback_output(summary)
    else:
        from llm.llm_client import llm_judge
        result = llm_judge(DT_TASK, summary)
        md_content = format_dt_markdown(summary, result)
        json_summary = {**summary, 'llm_analysis': result}

    write_outputs(output_dir, md_content, json_summary, symbol)
    print(f"[DragonTiger] 分析完成: {symbol}")


if __name__ == "__main__":
    main()
