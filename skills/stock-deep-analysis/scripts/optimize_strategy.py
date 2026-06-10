#!/usr/bin/env python3
"""
策略因子优化脚本

基于验证结果分析命中率，提出策略优化建议并测试优化效果：
1. 分析 T+1 / T+2 预测的准确率
2. 找出哪些因子组合命中率更高
3. 分析误判案例的原因
4. 输出优化建议
5. 测试优化后的命中率对比

用法：
    python3 optimize_strategy.py
    python3 optimize_strategy.py --input validation-report-2026-04-15.md
    python3 optimize_strategy.py --format json
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
PENDING_DIR = Path.home() / "quant-data" / "市场分析" / "reports" / "个股分析报告"
VALIDATIONS_DIR = PENDING_DIR / "validations"
STRATEGY_DIR = SKILL_DIR / "references" / "strategy-analysis"
from data.config_loader import cfg
from data.data_access import _read_stock_parquet

DATA_ROOT = cfg.paths('stock_data_root')
DAILY_DIR = DATA_ROOT / "daily"
TRADE_CAL_PATH = DATA_ROOT / "trade_cal" / "trade_cal_all.csv"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="策略因子优化分析")
    parser.add_argument("--input", default=None, help="输入验证报告路径")
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="输出格式"
    )
    parser.add_argument("--symbol", default=None, help="只分析指定股票")
    return parser.parse_args()


def load_validation_report(input_path: str = None) -> Optional[Dict]:
    if input_path:
        path = Path(input_path)
        if path.exists():
            content = path.read_text(encoding="utf-8")
            return parse_validation_content(content)

    today = datetime.now().strftime("%Y-%m-%d")
    latest = VALIDATIONS_DIR / f"validation-report-{today}.md"
    if latest.exists():
        content = latest.read_text(encoding="utf-8")
        return parse_validation_content(content)

    reports = sorted(VALIDATIONS_DIR.glob("validation-report-*.md"), reverse=True)
    if reports:
        content = reports[0].read_text(encoding="utf-8")
        return parse_validation_content(content)

    return None


def load_previous_validation() -> Optional[Dict]:
    reports = sorted(VALIDATIONS_DIR.glob("validation-report-*.md"))
    if len(reports) >= 2:
        content = reports[-2].read_text(encoding="utf-8")
        return parse_validation_content(content)
    return None


def parse_validation_content(content: str) -> Dict:
    result = {
        "t1_stats": {},
        "t2_stats": {},
        "validations": [],
    }

    t1_total = re.search(r"### T\+1 次日预期.*?样本数：`(\d+)`", content, re.DOTALL)
    if t1_total:
        result["t1_stats"]["total"] = int(t1_total.group(1))

    t1_exact = re.search(
        r"T\+1.*?精确命中：`(\d+) / (\d+) = ([\d.]+)%`", content, re.DOTALL
    )
    if t1_exact:
        result["t1_stats"]["exact_hits"] = int(t1_exact.group(1))
        result["t1_stats"]["exact_hit_rate"] = float(t1_exact.group(3))

    t1_dir = re.search(
        r"T\+1.*?方向命中：`(\d+) / (\d+) = ([\d.]+)%`", content, re.DOTALL
    )
    if t1_dir:
        result["t1_stats"]["direction_hits"] = int(t1_dir.group(1))
        result["t1_stats"]["direction_hit_rate"] = float(t1_dir.group(3))

    t2_total = re.search(r"### T\+2 预期.*?样本数：`(\d+)`", content, re.DOTALL)
    if t2_total:
        result["t2_stats"]["total"] = int(t2_total.group(1))

    t2_exact = re.search(
        r"T\+2.*?精确命中：`(\d+) / (\d+) = ([\d.]+)%`", content, re.DOTALL
    )
    if t2_exact:
        result["t2_stats"]["exact_hits"] = int(t2_exact.group(1))
        result["t2_stats"]["exact_hit_rate"] = float(t2_exact.group(3))

    t2_dir = re.search(
        r"T\+2.*?方向命中：`(\d+) / (\d+) = ([\d.]+)%`", content, re.DOTALL
    )
    if t2_dir:
        result["t2_stats"]["direction_hits"] = int(t2_dir.group(1))
        result["t2_stats"]["direction_hit_rate"] = float(t2_dir.group(3))

    section_pattern = r"### (\d{6}\.[A-Z]{2}) \(([^)]+)\).*?(?=###|\Z)"
    for match in re.finditer(section_pattern, content, re.DOTALL):
        symbol = match.group(1)
        date = match.group(2)

        item = {"symbol": symbol, "target_date": date}

        pred_match = re.search(r"- T\+1 预测：`([^`]+)`", match.group(0))
        if pred_match:
            item["t1_prediction"] = pred_match.group(1)

        actual_match = re.search(r"T\+1 实际：`([^`]+)`", match.group(0))
        if actual_match:
            item["t1_actual"] = actual_match.group(1)

        result["validations"].append(item)

    return result


def classify_by_pct(pct: float) -> str:
    if pct >= 7:
        return "次日强延续"
    if pct >= 2:
        return "次日偏强"
    if pct > -2:
        return "次日分歧"
    if pct > -7:
        return "次日偏弱"
    return "次日高位兑现"


def coarse_direction(label: str) -> str:
    if label in ("次日强延续", "次日偏强"):
        return "偏多"
    if label in ("次日偏弱", "次日高位兑现"):
        return "偏空"
    return "中性"


def analyze_prediction_patterns(validations: List[Dict]) -> Dict:
    patterns = {
        "强延续": {"total": 0, "hits": 0},
        "偏强": {"total": 0, "hits": 0},
        "分歧": {"total": 0, "hits": 0},
        "偏弱": {"total": 0, "hits": 0},
        "兑现": {"total": 0, "hits": 0},
    }

    for v in validations:
        pred = v.get("t1_prediction", "")
        actual = v.get("t1_actual", "")

        for key in patterns:
            if key in pred:
                patterns[key]["total"] += 1
                if key in actual:
                    patterns[key]["hits"] += 1

    for key in patterns:
        if patterns[key]["total"] > 0:
            patterns[key]["rate"] = round(
                patterns[key]["hits"] / patterns[key]["total"] * 100, 1
            )
        else:
            patterns[key]["rate"] = 0.0

    return patterns


def run_backtest_on_validated_samples() -> Dict:
    """对已验证样本进行回测，计算各项因子命中率"""
    import csv

    results = {
        "total_samples": 0,
        "t1_exact_hits": 0,
        "t1_direction_hits": 0,
        "factor_stats": {},
        "weak_patterns": [],
        "strong_patterns": [],
    }

    # 遍历 YYYY/MM/DD 目录结构
    for year_dir in sorted(PENDING_DIR.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for month_dir in sorted(year_dir.iterdir()):
            if not month_dir.is_dir() or not month_dir.name.isdigit():
                continue
            for day_dir in sorted(month_dir.iterdir()):
                if not day_dir.is_dir() or not day_dir.name.isdigit():
                    continue
                for md_file in sorted(day_dir.glob("pending-validation-*.md")):
                    content = md_file.read_text(encoding="utf-8")

                    symbol_match = re.search(r"#\s*(\d{6}\.[A-Z]{2})\s*", content)
                    target_match = re.search(r"目标交易日[：:]\s*(\d{4}-\d{2}-\d{2})", content)

                    if not symbol_match or not target_match:
                        continue

                    symbol = symbol_match.group(1)
                    target_date = target_match.group(1).replace("-", "")

                    pred_match = re.search(
                        r"隔夜次日预期[:：]\s*\n\s*-\s*预测[：:]\s*(.+?)(?:\n|$)", content
                    )
                    if not pred_match:
                        continue

                    prediction = pred_match.group(1).strip()

                    daily_rows = _read_stock_parquet("daily", symbol)
                    rows = []
                    for row in daily_rows:
                        row_symbol = row.get("ts_code", "")
                        if row_symbol != symbol:
                            continue
                        rows.append(
                            {
                                "trade_date": str(row.get("trade_date", "")),
                                "close": float(row.get("close", 0) or 0),
                                "pct": float(row.get("pct_chg", 0) or 0),
                            }
                        )

                    rows.sort(key=lambda x: x["trade_date"])

                    target_row = None
                    next_row = None
                    for i, row in enumerate(rows):
                        if row["trade_date"] == target_date:
                            target_row = row
                            if i + 1 < len(rows):
                                next_row = rows[i + 1]
                            break

                    if not target_row or not next_row:
                        continue

                    results["total_samples"] += 1
                    next_pct = next_row["pct"]
                    actual_label = classify_by_pct(next_pct)
                    actual_direction = coarse_direction(actual_label)

                    exact_hit = False
                    direction_hit = False

                    pred_lower = prediction.lower()
                    actual_lower = actual_label.lower()

                    if "强" in prediction and "强" in actual_lower:
                        exact_hit = True
                    elif "偏强" in prediction and "偏强" in actual_lower:
                        exact_hit = True
                    elif "分歧" in prediction and "分歧" in actual_lower:
                        exact_hit = True
                    elif "偏弱" in prediction and "偏弱" in actual_lower:
                        exact_hit = True
                    elif "兑现" in prediction and "兑现" in actual_lower:
                        exact_hit = True

                    if (
                        "偏多" in pred_lower or "强" in pred_lower
                    ) and actual_direction == "偏多":
                        direction_hit = True
                    elif (
                        "偏空" in pred_lower or "弱" in pred_lower or "兑现" in pred_lower
                    ) and actual_direction == "偏空":
                        direction_hit = True
                    elif "分歧" in pred_lower and actual_direction == "中性":
                        direction_hit = True
                    elif "分歧" in pred_lower and abs(next_pct) < 2:
                        direction_hit = True

                    if exact_hit:
                        results["t1_exact_hits"] += 1
                    if direction_hit:
                        results["t1_direction_hits"] += 1

                    for pattern in ["强", "偏强", "分歧", "偏弱", "兑现"]:
                        if pattern in prediction:
                            if pattern not in results["factor_stats"]:
                                results["factor_stats"][pattern] = {"total": 0, "hits": 0}
                            results["factor_stats"][pattern]["total"] += 1
                            if pattern in actual_label or (
                                pattern == "强" and "强" in actual_lower
                            ):
                                results["factor_stats"][pattern]["hits"] += 1

    for pattern, stats in results["factor_stats"].items():
        if stats["total"] > 0:
            rate = round(stats["hits"] / stats["total"] * 100, 1)
            stats["rate"] = rate
            if rate < 50 and stats["total"] >= 2:
                results["weak_patterns"].append(
                    {
                        "pattern": pattern,
                        "rate": rate,
                        "total": stats["total"],
                    }
                )
            elif rate >= 60 and stats["total"] >= 2:
                results["strong_patterns"].append(
                    {
                        "pattern": pattern,
                        "rate": rate,
                        "total": stats["total"],
                    }
                )

    if results["total_samples"] > 0:
        results["t1_exact_hit_rate"] = round(
            results["t1_exact_hits"] / results["total_samples"] * 100, 1
        )
        results["t1_direction_hit_rate"] = round(
            results["t1_direction_hits"] / results["total_samples"] * 100, 1
        )
    else:
        results["t1_exact_hit_rate"] = 0.0
        results["t1_direction_hit_rate"] = 0.0

    return results


def generate_optimization_report(current: Dict, previous: Dict, backtest: Dict) -> Dict:
    suggestions = []
    action_items = []
    improvements = []
    regressions = []

    current_t1_rate = current.get("t1_stats", {}).get("exact_hit_rate", 0)
    previous_t1_rate = (
        previous.get("t1_stats", {}).get("exact_hit_rate", 0) if previous else 0
    )

    t1_change = current_t1_rate - previous_t1_rate

    if t1_change > 5:
        improvements.append(
            f"T+1 精确命中率提升 {t1_change:.1f}% ({previous_t1_rate}% -> {current_t1_rate}%)"
        )
    elif t1_change < -5:
        regressions.append(
            f"T+1 精确命中率下降 {abs(t1_change):.1f}% ({previous_t1_rate}% -> {current_t1_rate}%)"
        )

    if current_t1_rate < 50:
        suggestions.append("⚠️ T+1 精确命中率偏低（<50%），建议检查预测逻辑是否过于乐观")
    elif current_t1_rate >= 60:
        suggestions.append("✅ T+1 精确命中率良好（≥60%），当前预测模型有效")

    current_dir_rate = current.get("t1_stats", {}).get("direction_hit_rate", 0)
    if current_dir_rate >= 70:
        suggestions.append("✅ T+1 方向命中率较高（≥70%），方向判断比精确值更可靠")

    patterns = analyze_prediction_patterns(current.get("validations", []))

    for key, stats in patterns.items():
        if stats["total"] >= 2:
            if stats["rate"] < 40:
                suggestions.append(
                    f"⚠️ '{key}' 预测模式命中率偏低（{stats['rate']}%），建议加强该场景的判断条件"
                )
                action_items.append(f"分析 '{key}' 预测偏差原因，调整评分权重")
            elif stats["rate"] >= 70:
                suggestions.append(
                    f"✅ '{key}' 预测模式命中率良好（{stats['rate']}%），可作为优先参考"
                )

    if backtest.get("weak_patterns"):
        for wp in backtest["weak_patterns"]:
            action_items.append(
                f"优化 '{wp['pattern']}' 场景：当前命中率 {wp['rate']}%，需提升"
            )

    if backtest.get("strong_patterns"):
        for sp in backtest["strong_patterns"]:
            action_items.append(
                f"保持 '{sp['pattern']}' 场景优势：当前命中率 {sp['rate']}%"
            )

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "current_report": current.get("source"),
        "previous_report": previous.get("source") if previous else None,
        "comparison": {
            "t1_exact_rate_current": current_t1_rate,
            "t1_exact_rate_previous": previous_t1_rate,
            "t1_exact_rate_change": t1_change,
            "improvements": improvements,
            "regressions": regressions,
        },
        "stats": {
            "current": current.get("t1_stats", {}),
            "backtest": {
                "total_samples": backtest.get("total_samples", 0),
                "t1_exact_hits": backtest.get("t1_exact_hits", 0),
                "t1_exact_hit_rate": backtest.get("t1_exact_hit_rate", 0),
                "t1_direction_hit_rate": backtest.get("t1_direction_hit_rate", 0),
            },
        },
        "prediction_patterns": patterns,
        "suggestions": suggestions,
        "action_items": action_items,
    }


def render_text(report: Dict) -> str:
    lines = [
        f"# 策略优化分析报告",
        f"",
        f"- 分析时间：{report['generated_at']}",
        f"- 数据来源：{report.get('current_report', '最新验证报告')}",
        f"",
        f"## 命中率对比",
        f"",
    ]

    comparison = report.get("comparison", {})
    improvements = comparison.get("improvements", [])
    regressions = comparison.get("regressions", [])

    if improvements:
        for imp in improvements:
            lines.append(f"✅ {imp}")

    if regressions:
        for reg in regressions:
            lines.append(f"❌ {reg}")

    if not improvements and not regressions:
        lines.append("📊 命中率变化不明显，继续监控")

    lines.append(f"")

    current_stats = report.get("stats", {}).get("current", {})
    backtest = report.get("stats", {}).get("backtest", {})

    lines.append(f"| 维度 | 当前值 |")
    lines.append(f"|------|--------|")
    lines.append(f"| 验证样本精确率 | {current_stats.get('exact_hit_rate', 0)}% |")
    lines.append(f"| 验证样本方向率 | {current_stats.get('direction_hit_rate', 0)}% |")
    lines.append(f"| 回测样本数 | {backtest.get('total_samples', 0)} |")
    lines.append(f"| 回测精确率 | {backtest.get('t1_exact_hit_rate', 0)}% |")
    lines.append(f"| 回测方向率 | {backtest.get('t1_direction_hit_rate', 0)}% |")

    patterns = report.get("prediction_patterns", {})
    if patterns:
        lines.append(f"")
        lines.append(f"## 预测模式分析")
        lines.append(f"")
        lines.append(f"| 预测模式 | 样本 | 命中 | 命中率 | 评估 |")
        lines.append(f"|---------|------|------|-------|------|")

        for key, stats in patterns.items():
            if stats["total"] > 0:
                rate = stats.get("rate", 0)
                if rate >= 70:
                    eval_mark = "✅ 强"
                elif rate >= 50:
                    eval_mark = "⚡ 中"
                else:
                    eval_mark = "⚠️ 弱"
                lines.append(
                    f"| {key} | {stats['total']} | {stats['hits']} | {rate}% | {eval_mark} |"
                )

    suggestions = report.get("suggestions", [])
    if suggestions:
        lines.append(f"")
        lines.append(f"## 优化建议")
        lines.append(f"")
        for s in suggestions:
            lines.append(f"- {s}")

    action_items = report.get("action_items", [])
    if action_items:
        lines.append(f"")
        lines.append(f"## 行动项")
        lines.append(f"")
        for item in action_items:
            lines.append(f"- [ ] {item}")

    return "\n".join(lines)


def save_optimization_report(report: Dict) -> Path:
    STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    output_file = STRATEGY_DIR / f"strategy-optimization-{today}.md"

    content = render_text(report)
    content += "\n\n---\n\n"
    content += f"_此报告由 optimize_strategy.py 自动生成_\n"
    content += f"_分析时间：{report['generated_at']}_"

    output_file.write_text(content, encoding="utf-8")
    return output_file


def main() -> None:
    args = parse_args()

    current_report = load_validation_report(args.input)

    if not current_report:
        print(
            "# 策略优化分析\n\n❌ 未找到验证报告\n\n请先运行 validate_pending_reports.py 生成验证报告"
        )
        return

    previous_report = load_previous_validation()
    backtest_results = run_backtest_on_validated_samples()

    optimization = generate_optimization_report(
        current_report, previous_report, backtest_results
    )

    if args.format == "json":
        print(json.dumps(optimization, ensure_ascii=False, indent=2))
    else:
        print(render_text(optimization))

    output_file = save_optimization_report(optimization)
    print(f"\n\n优化报告已保存至：{output_file}")


if __name__ == "__main__":
    main()
