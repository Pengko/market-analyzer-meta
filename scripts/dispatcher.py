#!/usr/bin/env python3
"""
聚合层调度器：识别意图 → 路由到子 skill → 输出报告。

用法：
    python dispatcher.py "京东方怎么样"
    python dispatcher.py "大盘怎么看"
    python dispatcher.py "有什么利好消息"
    python dispatcher.py --json "面板板块分析"
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
META_ROOT = SCRIPT_DIR.parent
SDA_SCRIPTS = META_ROOT / "skills" / "stock-deep-analysis" / "scripts"

# 子 skill 脚本路径
STOCK_SCRIPT = SDA_SCRIPTS / "build_stock_report.py"
MARKET_SCRIPT = META_ROOT / "skills" / "market-macro-analysis" / "scripts" / "market_macro_runner.py"
NEWS_SCRIPT = META_ROOT / "skills" / "news-driven-analysis" / "scripts" / "news_driven_runner.py"

# 意图识别器
sys.path.insert(0, str(SCRIPT_DIR))
from intent_classifier import classify_intent


def extract_stock_code(text: str, extracted: dict) -> str | None:
    """从提取的信息中获取股票代码。"""
    codes = extracted.get("stock_codes", [])
    if codes:
        return codes[0]

    names = extracted.get("stock_names", [])
    if names:
        # 尝试通过名称解析代码
        try:
            sys.path.insert(0, str(SDA_SCRIPTS))
            from financing_analyzer import resolve_symbol
            return resolve_symbol(names[0])
        except Exception:
            pass

    return None


def run_sub_skill(direction: str, user_input: str, extracted: dict, output_format: str = "markdown") -> str:
    """运行对应的子 skill。"""
    env = {"PYTHONPATH": str(SDA_SCRIPTS)}

    if direction == "stock":
        stock_code = extract_stock_code(user_input, extracted)
        if not stock_code:
            return "无法识别股票代码或名称，请提供有效的股票信息。"

        cmd = [
            sys.executable, str(STOCK_SCRIPT),
            "--symbol", stock_code,
            "--trade-date", "today",
            "--format", output_format,
        ]
    elif direction == "market":
        cmd = [
            sys.executable, str(MARKET_SCRIPT),
            "--format", output_format,
        ]
    elif direction == "news":
        # 提取关键词
        keywords = extracted.get("keywords", [])
        keyword = keywords[0] if keywords else None

        cmd = [
            sys.executable, str(NEWS_SCRIPT),
            "--format", output_format,
        ]
        if keyword:
            cmd.extend(["--keyword", keyword])
    else:
        return f"未知方向: {direction}"

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env={**dict(__import__("os").environ), **env},
        )
        if result.returncode == 0:
            return result.stdout
        else:
            return f"执行失败 (exit {result.returncode}):\n{result.stderr[:500]}"
    except subprocess.TimeoutExpired:
        return "执行超时（>300秒）"
    except Exception as e:
        return f"执行异常: {e}"


def main():
    parser = argparse.ArgumentParser(description="市场分析聚合层调度器")
    parser.add_argument("query", nargs="?", help="用户输入")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--direction", choices=("stock", "market", "news"), help="手动指定方向")
    args = parser.parse_args()

    if not args.query:
        print("用法: python dispatcher.py '用户输入'")
        sys.exit(1)

    # 意图识别
    intent = classify_intent(args.query)
    direction = args.direction or intent["direction"]

    print(f"## 路由信息", file=sys.stderr)
    print(f"  输入: {args.query}", file=sys.stderr)
    print(f"  方向: {direction} (置信度: {intent['confidence']}%)", file=sys.stderr)
    print(f"  依据: {intent['reason']}", file=sys.stderr)
    print(file=sys.stderr)

    # 运行子 skill
    output = run_sub_skill(direction, args.query, intent.get("extracted", {}), "json" if args.json else "markdown")
    print(output)


if __name__ == "__main__":
    main()
