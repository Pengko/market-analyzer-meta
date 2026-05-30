#!/usr/bin/env python3
"""
每月策略因子优化脚本

汇总一月的验证报告，进行全面策略有效性评估，生成月度优化建议。

用法：
    python3 monthly_optimizer.py
    python3 monthly_optimizer.py --month 2026-04
    python3 monthly_optimizer.py --format json
"""

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent.parent
VALIDATIONS_DIR = SKILL_DIR / "references" / "validations"
PENDING_VALIDATIONS_DIR = SKILL_DIR / "references" / "pending-validations"
STRATEGY_DIR = SKILL_DIR / "references" / "strategy-analysis"
from data.config_loader import cfg

DATA_ROOT = cfg.paths('stock_data_root')
DAILY_DIR = DATA_ROOT / "daily"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="每月策略因子优化")
    parser.add_argument(
        "--month", 
        default=None, 
        help="指定月份，格式：YYYY-MM"
    )
    parser.add_argument(
        "--format", 
        choices=("text", "json"), 
        default="text", 
        help="输出格式"
    )
    return parser.parse_args()


def get_month_range(month_str: Optional[str] = None) -> tuple[str, str]:
    """获取月范围（1号到月末）"""
    if month_str:
        year, month = map(int, month_str.split("-"))
        start_date = datetime(year, month, 1)
    else:
        # 默认本月
        today = datetime.now()
        start_date = datetime(today.year, today.month, 1)
    
    # 计算月末
    if start_date.month == 12:
        end_date = datetime(start_date.year + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = datetime(start_date.year, start_date.month + 1, 1) - timedelta(days=1)
    
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


def load_pending_validations_for_date(date_str: str) -> List[Dict]:
    """从 pending-validations 加载某日的 meta.json 报告"""
    validations = []
    date_dir = PENDING_VALIDATIONS_DIR / date_str
    
    if not date_dir.exists():
        return validations
    
    for meta_file in date_dir.glob("*-meta.json"):
        try:
            content = meta_file.read_text(encoding="utf-8")
            meta = json.loads(content)
            validation = parse_pending_meta(meta, date_str)
            if validation:
                validations.append(validation)
        except (json.JSONDecodeError, OSError):
            continue
    
    return validations


def parse_pending_meta(meta: Dict, date_str: str) -> Optional[Dict]:
    """解析 pending-validation meta.json 为统一验证格式"""
    result = {
        "date": date_str,
        "t1_stats": {},
        "t2_stats": {},
        "validations": [],
        "source": "pending-validations",
    }
    
    # 提取股票信息
    symbol = meta.get("symbol") or (meta.get("stock_info", {}) or {}).get("symbol", "")
    stock_name = meta.get("name") or (meta.get("stock_info", {}) or {}).get("name", "")
    analysis_type = meta.get("analysis_type") or meta.get("report_type", "")
    
    if not symbol:
        return None
    
    # 提取预测信息
    predictions = meta.get("predictions", {})
    prediction_text = ""
    if isinstance(predictions, dict):
        direction = predictions.get("next_day_direction", "")
        bias = predictions.get("next_day_bias", "")
        confidence = predictions.get("confidence", "")
        prediction_text = f"{direction}({bias})" if bias else direction
    elif isinstance(predictions, list) and predictions:
        preds = []
        for p in predictions:
            dim = p.get("dimension", "")
            pred = p.get("prediction", "")
            if pred:
                preds.append(f"{dim}:{pred}")
        prediction_text = "; ".join(preds)
    
    # 提取上下文信息
    context = meta.get("context_propagation", {})
    if isinstance(context, dict):
        market = context.get("market", context.get("market_env_score", ""))
        sector = context.get("sector", context.get("sector_momentum_score", ""))
        stock_sentiment = context.get("stock", context.get("stock_structure_score", ""))
    else:
        market = sector = stock_sentiment = ""
    
    item = {
        "symbol": symbol,
        "target_date": date_str,
        "stock_name": stock_name,
        "analysis_type": analysis_type,
        "t1_prediction": prediction_text,
        "market_context": str(market),
        "sector_context": str(sector),
        "stock_context": str(stock_sentiment),
    }
    
    result["validations"].append(item)
    
    # 统计：每个 meta.json 算一个 pending 样本
    result["t1_stats"]["total"] = 1
    result["t1_stats"]["exact_hits"] = 0
    result["t1_stats"]["direction_hits"] = 0
    result["t1_stats"]["exact_hit_rate"] = 0.0
    result["t1_stats"]["direction_hit_rate"] = 0.0
    
    return result


def load_monthly_validations(start_date: str, end_date: str) -> List[Dict]:
    """加载一月的验证报告（同时检查 validations 和 pending-validations）"""
    validations = []
    
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    
    current = start
    while current <= end:
        date_str = current.strftime("%Y-%m-%d")
        
        # 优先加载已完成的验证报告
        report_path = VALIDATIONS_DIR / f"validation-report-{date_str}.md"
        if report_path.exists():
            content = report_path.read_text(encoding="utf-8")
            validation = parse_validation_content(content)
            if validation:
                validation["date"] = date_str
                validations.append(validation)
        else:
            # 回退到 pending-validations
            pending = load_pending_validations_for_date(date_str)
            validations.extend(pending)
        
        current += timedelta(days=1)
    
    return validations


def parse_validation_content(content: str) -> Optional[Dict]:
    """解析验证报告内容"""
    result = {
        "t1_stats": {},
        "t2_stats": {},
        "validations": [],
    }
    
    # 提取T+1统计
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
    
    # 提取T+2统计
    t2_total = re.search(r"### T\+2 预期.*?样本数：`(\d+)`", content, re.DOTALL)
    if t2_total:
        result["t2_stats"]["total"] = int(t2_total.group(1))
    
    t2_exact = re.search(
        r"T\+2.*?精确命中：`(\d+) / (\d+) = ([\d.]+)%`", content, re.DOTALL
    )
    if t2_exact:
        result["t2_stats"]["exact_hits"] = int(t2_exact.group(1))
        result["t2_stats"]["exact_hit_rate"] = float(t2_exact.group(3))
    
    # 提取个股验证
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
    
    return result if result["validations"] else None


def analyze_monthly_trends(validations: List[Dict]) -> Dict:
    """分析月度趋势"""
    monthly_stats = {
        "total_samples": 0,
        "t1_exact_hits": 0,
        "t1_direction_hits": 0,
        "t2_total_samples": 0,
        "t2_exact_hits": 0,
        "weekly_trends": [],
        "pattern_stats": {},
        "symbol_stats": {},
    }
    
    # 按周分组
    weekly_data = {}
    
    for validation in validations:
        date_str = validation.get("date", "unknown")
        date = datetime.strptime(date_str, "%Y-%m-%d")
        week_num = date.isocalendar()[1]
        
        if week_num not in weekly_data:
            weekly_data[week_num] = {
                "week": week_num,
                "total_samples": 0,
                "t1_exact_hits": 0,
                "t1_direction_hits": 0,
            }
        
        t1_stats = validation.get("t1_stats", {})
        t2_stats = validation.get("t2_stats", {})
        
        total = t1_stats.get("total", 0)
        exact_hits = t1_stats.get("exact_hits", 0)
        direction_hits = t1_stats.get("direction_hits", 0)
        
        monthly_stats["total_samples"] += total
        monthly_stats["t1_exact_hits"] += exact_hits
        monthly_stats["t1_direction_hits"] += direction_hits
        
        # T+2统计
        t2_total = t2_stats.get("total", 0)
        t2_exact_hits = t2_stats.get("exact_hits", 0)
        monthly_stats["t2_total_samples"] += t2_total
        monthly_stats["t2_exact_hits"] += t2_exact_hits
        
        # 周度统计
        weekly_data[week_num]["total_samples"] += total
        weekly_data[week_num]["t1_exact_hits"] += exact_hits
        weekly_data[week_num]["t1_direction_hits"] += direction_hits
        
        # 分析预测模式
        for validation_item in validation.get("validations", []):
            symbol = validation_item.get("symbol", "unknown")
            pred = validation_item.get("t1_prediction", "")
            actual = validation_item.get("t1_actual", "")
            
            # 股票统计
            if symbol not in monthly_stats["symbol_stats"]:
                monthly_stats["symbol_stats"][symbol] = {
                    "total": 0,
                    "exact_hits": 0,
                    "direction_hits": 0,
                }
            
            monthly_stats["symbol_stats"][symbol]["total"] += 1
            
            # 检查命中
            exact_hit = False
            direction_hit = False
            
            for pattern in ["强", "偏强", "分歧", "偏弱", "兑现"]:
                if pattern in pred:
                    if pattern not in monthly_stats["pattern_stats"]:
                        monthly_stats["pattern_stats"][pattern] = {
                            "total": 0, 
                            "hits": 0
                        }
                    monthly_stats["pattern_stats"][pattern]["total"] += 1
                    
                    # 检查是否命中
                    if pattern in actual or (pattern == "强" and "强" in actual):
                        monthly_stats["pattern_stats"][pattern]["hits"] += 1
                        exact_hit = True
            
            # 方向命中检查
            pred_lower = pred.lower()
            actual_lower = actual.lower() if actual else ""
            
            if ("偏多" in pred_lower or "强" in pred_lower) and "强" in actual_lower:
                direction_hit = True
            elif ("偏空" in pred_lower or "弱" in pred_lower or "兑现" in pred_lower) and "弱" in actual_lower:
                direction_hit = True
            elif "分歧" in pred_lower and "分歧" in actual_lower:
                direction_hit = True
            
            if exact_hit:
                monthly_stats["symbol_stats"][symbol]["exact_hits"] += 1
            if direction_hit:
                monthly_stats["symbol_stats"][symbol]["direction_hits"] += 1
    
    # 计算命中率
    if monthly_stats["total_samples"] > 0:
        monthly_stats["t1_exact_hit_rate"] = round(
            monthly_stats["t1_exact_hits"] / monthly_stats["total_samples"] * 100, 1
        )
        monthly_stats["t1_direction_hit_rate"] = round(
            monthly_stats["t1_direction_hits"] / monthly_stats["total_samples"] * 100, 1
        )
    else:
        monthly_stats["t1_exact_hit_rate"] = 0.0
        monthly_stats["t1_direction_hit_rate"] = 0.0
    
    if monthly_stats["t2_total_samples"] > 0:
        monthly_stats["t2_exact_hit_rate"] = round(
            monthly_stats["t2_exact_hits"] / monthly_stats["t2_total_samples"] * 100, 1
        )
    else:
        monthly_stats["t2_exact_hit_rate"] = 0.0
    
    # 计算模式命中率
    for pattern, stats in monthly_stats["pattern_stats"].items():
        if stats["total"] > 0:
            stats["rate"] = round(stats["hits"] / stats["total"] * 100, 1)
        else:
            stats["rate"] = 0.0
    
    # 计算股票命中率
    for symbol, stats in monthly_stats["symbol_stats"].items():
        if stats["total"] > 0:
            stats["exact_hit_rate"] = round(stats["exact_hits"] / stats["total"] * 100, 1)
            stats["direction_hit_rate"] = round(stats["direction_hits"] / stats["total"] * 100, 1)
        else:
            stats["exact_hit_rate"] = 0.0
            stats["direction_hit_rate"] = 0.0
    
    # 生成周度趋势
    for week_num in sorted(weekly_data.keys()):
        week_data = weekly_data[week_num]
        if week_data["total_samples"] > 0:
            week_data["exact_hit_rate"] = round(
                week_data["t1_exact_hits"] / week_data["total_samples"] * 100, 1
            )
            week_data["direction_hit_rate"] = round(
                week_data["t1_direction_hits"] / week_data["total_samples"] * 100, 1
            )
        else:
            week_data["exact_hit_rate"] = 0.0
            week_data["direction_hit_rate"] = 0.0
        
        monthly_stats["weekly_trends"].append(week_data)
    
    return monthly_stats


def generate_monthly_report(monthly_stats: Dict, start_date: str, end_date: str) -> str:
    """生成月度优化报告"""
    lines = [
        f"# 每月策略优化报告",
        f"",
        f"- 月份：{start_date[:7]}",
        f"- 日期范围：{start_date} 至 {end_date}",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 样本总数：{monthly_stats['total_samples']}",
        f"",
        f"## 命中率统计",
        f"",
        f"| 维度 | 数值 |",
        f"|------|------|",
        f"| T+1精确命中率 | {monthly_stats['t1_exact_hit_rate']}% |",
        f"| T+1方向命中率 | {monthly_stats['t1_direction_hit_rate']}% |",
        f"| T+2精确命中率 | {monthly_stats['t2_exact_hit_rate']}% |",
        f"| 总样本数 | {monthly_stats['total_samples']} |",
        f"",
        f"## 周度趋势",
        f"",
        f"| 周次 | 样本 | 精确命中 | 方向命中 | 精确率 | 方向率 |",
        f"|------|------|----------|----------|--------|--------|",
    ]
    
    for trend in monthly_stats["weekly_trends"]:
        lines.append(
            f"| W{trend['week']} | {trend['total_samples']} | {trend['t1_exact_hits']} | "
            f"{trend['t1_direction_hits']} | {trend['exact_hit_rate']}% | {trend['direction_hit_rate']}% |"
        )
    
    lines.extend([
        f"",
        f"## 预测模式分析",
        f"",
        f"| 预测模式 | 样本 | 命中 | 命中率 | 评估 |",
        f"|---------|------|------|--------|------|",
    ])
    
    for pattern, stats in monthly_stats["pattern_stats"].items():
        if stats["total"] > 0:
            rate = stats.get("rate", 0)
            if rate >= 70:
                eval_mark = "✅ 强"
            elif rate >= 50:
                eval_mark = "⚡ 中"
            else:
                eval_mark = "⚠️ 弱"
            lines.append(
                f"| {pattern} | {stats['total']} | {stats['hits']} | {rate}% | {eval_mark} |"
            )
    
    # 股票命中率分析
    lines.extend([
        f"",
        f"## 股票命中率分析",
        f"",
        f"| 股票代码 | 样本 | 精确命中 | 方向命中 | 精确率 | 方向率 |",
        f"|----------|------|----------|----------|--------|--------|",
    ])
    
    # 按精确命中率排序
    sorted_symbols = sorted(
        monthly_stats["symbol_stats"].items(),
        key=lambda x: x[1].get("exact_hit_rate", 0),
        reverse=True
    )
    
    for symbol, stats in sorted_symbols[:10]:  # 显示前10只
        if stats["total"] >= 2:  # 至少2个样本
            lines.append(
                f"| {symbol} | {stats['total']} | {stats['exact_hits']} | "
                f"{stats['direction_hits']} | {stats['exact_hit_rate']}% | {stats['direction_hit_rate']}% |"
            )
    
    # 生成优化建议
    lines.extend([
        f"",
        f"## 月度优化建议",
        f"",
    ])
    
    # 弱项建议
    weak_patterns = []
    strong_patterns = []
    
    for pattern, stats in monthly_stats["pattern_stats"].items():
        if stats["total"] >= 3:  # 月度分析要求更多样本
            if stats.get("rate", 0) < 50:
                weak_patterns.append(f"'{pattern}'（命中率{stats['rate']}%，样本{stats['total']}）")
            elif stats.get("rate", 0) >= 70:
                strong_patterns.append(f"'{pattern}'（命中率{stats['rate']}%，样本{stats['total']}）")
    
    if weak_patterns:
        lines.append(f"⚠️ **弱项模式**：{', '.join(weak_patterns)}")
        lines.append(f"   - 建议：深入分析误判案例，调整技术因子权重，优化判断条件")
    
    if strong_patterns:
        lines.append(f"✅ **强项模式**：{', '.join(strong_patterns)}")
        lines.append(f"   - 建议：保持现有判断逻辑，可作为高置信度参考")
    
    # 趋势分析
    if len(monthly_stats["weekly_trends"]) >= 2:
        first_week = monthly_stats["weekly_trends"][0]
        last_week = monthly_stats["weekly_trends"][-1]
        
        exact_trend = last_week["exact_hit_rate"] - first_week["exact_hit_rate"]
        direction_trend = last_week["direction_hit_rate"] - first_week["direction_hit_rate"]
        
        if exact_trend > 10:
            lines.append(f"📈 **精确命中率趋势**：上升{exact_trend:.1f}%（从{first_week['exact_hit_rate']}%到{last_week['exact_hit_rate']}%）")
        elif exact_trend < -10:
            lines.append(f"📉 **精确命中率趋势**：下降{abs(exact_trend):.1f}%（从{first_week['exact_hit_rate']}%到{last_week['exact_hit_rate']}%）")
    
    lines.extend([
        f"",
        f"## 下月行动计划",
        f"",
        f"- [ ] 分析弱项模式的共同特征",
        f"- [ ] 调整技术因子权重配置",
        f"- [ ] 优化预测模型参数",
        f"- [ ] 验证优化效果（对比下月命中率）",
        f"- [ ] 更新策略因子配置文件",
        f"",
        f"---",
        f"_此报告由 monthly_optimizer.py 自动生成_",
    ])
    
    return "\n".join(lines)


def save_monthly_report(report: str, month_str: str) -> Path:
    """保存月度报告"""
    STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    
    output_file = STRATEGY_DIR / f"monthly-strategy-optimization-{month_str}.md"
    output_file.write_text(report, encoding="utf-8")
    
    return output_file


def main() -> None:
    args = parse_args()
    
    # 获取月范围
    start_date, end_date = get_month_range(args.month)
    month_str = start_date[:7]
    print(f"分析月份：{month_str}（{start_date} 至 {end_date}）")
    
    # 加载验证报告
    validations = load_monthly_validations(start_date, end_date)
    print(f"加载了 {len(validations)} 天的验证报告")
    
    if not validations:
        print("❌ 未找到验证报告，请先运行 validate_pending_reports.py")
        return
    
    # 分析月度趋势
    monthly_stats = analyze_monthly_trends(validations)
    
    # 生成报告
    report = generate_monthly_report(monthly_stats, start_date, end_date)
    
    if args.format == "json":
        print(json.dumps(monthly_stats, ensure_ascii=False, indent=2))
    else:
        print(report)
    
    # 保存报告
    output_file = save_monthly_report(report, month_str)
    print(f"\n月度优化报告已保存至：{output_file}")


if __name__ == "__main__":
    main()