#!/usr/bin/env python3
"""
每周策略因子优化脚本

汇总一周的验证报告，分析命中率趋势，生成周度优化建议。

用法：
    python3 weekly_optimizer.py
    python3 weekly_optimizer.py --week 2026-04-14_to_2026-04-18
    python3 weekly_optimizer.py --format json
"""

import argparse
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent.parent
PENDING_VALIDATIONS_DIR = Path.home() / "quant-data" / "市场分析" / "reports" / "个股分析报告"
VALIDATIONS_DIR = PENDING_VALIDATIONS_DIR / "validations"
STRATEGY_DIR = SKILL_DIR / "references" / "strategy-analysis"
from data.config_loader import cfg

DATA_ROOT = cfg.paths('stock_data_root')
DAILY_DIR = DATA_ROOT / "daily"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="每周策略因子优化")
    parser.add_argument(
        "--week", 
        default=None, 
        help="指定周范围，格式：YYYY-MM-DD_to_YYYY-MM-DD"
    )
    parser.add_argument(
        "--format", 
        choices=("text", "json"), 
        default="text", 
        help="输出格式"
    )
    return parser.parse_args()


def get_week_range(week_str: Optional[str] = None) -> tuple[str, str]:
    """获取周范围（周一到周五）"""
    if week_str:
        start_str, end_str = week_str.split("_to_")
        start_date = datetime.strptime(start_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_str, "%Y-%m-%d")
    else:
        # 默认本周
        today = datetime.now()
        start_date = today - timedelta(days=today.weekday())  # 周一
        end_date = start_date + timedelta(days=4)  # 周五
    
    return start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")


def load_pending_validations_for_date(date_str: str) -> List[Dict]:
    """从 pending-validations 加载某日的 meta.json 报告"""
    validations = []
    # date_str: YYYY-MM-DD -> YYYY/MM/DD
    td = date_str.replace("-", "/")
    date_dir = PENDING_VALIDATIONS_DIR / td
    
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


def load_weekly_validations(start_date: str, end_date: str) -> List[Dict]:
    """加载一周的验证报告（同时检查 validations 和 pending-validations）"""
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


def analyze_weekly_trends(validations: List[Dict]) -> Dict:
    """分析周度趋势"""
    weekly_stats = {
        "total_samples": 0,
        "t1_exact_hits": 0,
        "t1_direction_hits": 0,
        "daily_trends": [],
        "pattern_stats": {},
    }
    
    for validation in validations:
        date = validation.get("date", "unknown")
        t1_stats = validation.get("t1_stats", {})
        
        total = t1_stats.get("total", 0)
        exact_hits = t1_stats.get("exact_hits", 0)
        direction_hits = t1_stats.get("direction_hits", 0)
        
        weekly_stats["total_samples"] += total
        weekly_stats["t1_exact_hits"] += exact_hits
        weekly_stats["t1_direction_hits"] += direction_hits
        
        # 记录每日趋势
        daily_trend = {
            "date": date,
            "total": total,
            "exact_hits": exact_hits,
            "direction_hits": direction_hits,
            "exact_rate": t1_stats.get("exact_hit_rate", 0),
            "direction_rate": t1_stats.get("direction_hit_rate", 0),
        }
        weekly_stats["daily_trends"].append(daily_trend)
        
        # 分析预测模式
        for validation_item in validation.get("validations", []):
            pred = validation_item.get("t1_prediction", "")
            actual = validation_item.get("t1_actual", "")
            
            # 提取预测模式
            for pattern in ["强", "偏强", "分歧", "偏弱", "兑现"]:
                if pattern in pred:
                    if pattern not in weekly_stats["pattern_stats"]:
                        weekly_stats["pattern_stats"][pattern] = {
                            "total": 0, 
                            "hits": 0
                        }
                    weekly_stats["pattern_stats"][pattern]["total"] += 1
                    
                    # 检查是否命中
                    if pattern in actual or (pattern == "强" and "强" in actual):
                        weekly_stats["pattern_stats"][pattern]["hits"] += 1
    
    # 计算命中率
    if weekly_stats["total_samples"] > 0:
        weekly_stats["t1_exact_hit_rate"] = round(
            weekly_stats["t1_exact_hits"] / weekly_stats["total_samples"] * 100, 1
        )
        weekly_stats["t1_direction_hit_rate"] = round(
            weekly_stats["t1_direction_hits"] / weekly_stats["total_samples"] * 100, 1
        )
    else:
        weekly_stats["t1_exact_hit_rate"] = 0.0
        weekly_stats["t1_direction_hit_rate"] = 0.0
    
    # 计算模式命中率
    for pattern, stats in weekly_stats["pattern_stats"].items():
        if stats["total"] > 0:
            stats["rate"] = round(stats["hits"] / stats["total"] * 100, 1)
        else:
            stats["rate"] = 0.0
    
    return weekly_stats


def generate_weekly_report(weekly_stats: Dict, start_date: str, end_date: str) -> str:
    """生成周度优化报告"""
    lines = [
        f"# 每周策略优化报告",
        f"",
        f"- 周范围：{start_date} 至 {end_date}",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 样本总数：{weekly_stats['total_samples']}",
        f"",
        f"## 命中率统计",
        f"",
        f"| 维度 | 数值 |",
        f"|------|------|",
        f"| T+1精确命中率 | {weekly_stats['t1_exact_hit_rate']}% |",
        f"| T+1方向命中率 | {weekly_stats['t1_direction_hit_rate']}% |",
        f"| 总样本数 | {weekly_stats['total_samples']} |",
        f"",
        f"## 每日趋势",
        f"",
        f"| 日期 | 样本 | 精确命中 | 方向命中 | 精确率 | 方向率 |",
        f"|------|------|----------|----------|--------|--------|",
    ]
    
    for trend in weekly_stats["daily_trends"]:
        lines.append(
            f"| {trend['date']} | {trend['total']} | {trend['exact_hits']} | "
            f"{trend['direction_hits']} | {trend['exact_rate']}% | {trend['direction_rate']}% |"
        )
    
    lines.extend([
        f"",
        f"## 预测模式分析",
        f"",
        f"| 预测模式 | 样本 | 命中 | 命中率 | 评估 |",
        f"|---------|------|------|--------|------|",
    ])
    
    for pattern, stats in weekly_stats["pattern_stats"].items():
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
    
    # 生成优化建议
    lines.extend([
        f"",
        f"## 优化建议",
        f"",
    ])
    
    # 弱项建议
    weak_patterns = []
    strong_patterns = []
    
    for pattern, stats in weekly_stats["pattern_stats"].items():
        if stats["total"] >= 2:
            if stats.get("rate", 0) < 50:
                weak_patterns.append(f"'{pattern}'（命中率{stats['rate']}%）")
            elif stats.get("rate", 0) >= 70:
                strong_patterns.append(f"'{pattern}'（命中率{stats['rate']}%）")
    
    if weak_patterns:
        lines.append(f"⚠️ **弱项模式**：{', '.join(weak_patterns)}")
        lines.append(f"   - 建议：加强这些场景的判断条件，调整评分权重")
    
    if strong_patterns:
        lines.append(f"✅ **强项模式**：{', '.join(strong_patterns)}")
        lines.append(f"   - 建议：保持现有判断逻辑，可作为优先参考")
    
    if not weak_patterns and not strong_patterns:
        lines.append(f"📊 本周样本较少，继续积累数据后再分析")
    
    lines.extend([
        f"",
        f"## 行动项",
        f"",
        f"- [ ] 分析弱项模式的误判原因",
        f"- [ ] 调整技术因子权重",
        f"- [ ] 更新预测模型参数",
        f"- [ ] 验证优化效果",
        f"",
        f"---",
        f"_此报告由 weekly_optimizer.py 自动生成_",
    ])
    
    return "\n".join(lines)


def save_weekly_report(report: str, start_date: str, end_date: str) -> Path:
    """保存周度报告"""
    STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    
    # 生成周标识符
    start = datetime.strptime(start_date, "%Y-%m-%d")
    week_num = start.isocalendar()[1]
    year = start.year
    
    output_file = STRATEGY_DIR / f"weekly-strategy-optimization-{year}-W{week_num:02d}.md"
    output_file.write_text(report, encoding="utf-8")
    
    return output_file


def main() -> None:
    args = parse_args()
    
    # 获取周范围
    start_date, end_date = get_week_range(args.week)
    print(f"分析周范围：{start_date} 至 {end_date}")
    
    # 加载验证报告
    validations = load_weekly_validations(start_date, end_date)
    print(f"加载了 {len(validations)} 天的验证报告")
    
    if not validations:
        print("❌ 未找到验证报告，请先运行 validate_pending_reports.py")
        return
    
    # 分析周度趋势
    weekly_stats = analyze_weekly_trends(validations)
    
    # 生成报告
    report = generate_weekly_report(weekly_stats, start_date, end_date)
    
    if args.format == "json":
        print(json.dumps(weekly_stats, ensure_ascii=False, indent=2))
    else:
        print(report)
    
    # 保存报告
    output_file = save_weekly_report(report, start_date, end_date)
    print(f"\n周度优化报告已保存至：{output_file}")


if __name__ == "__main__":
    main()