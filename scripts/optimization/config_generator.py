#!/usr/bin/env python3
"""
策略因子配置生成模块

根据优化结果生成可直接使用的配置文件，供其他agent使用。

用法：
    python3 config_generator.py --input strategy-optimization-2026-04-17.md
    python3 config_generator.py --month 2026-04
    python3 config_generator.py --format json
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent.parent
STRATEGY_DIR = SKILL_DIR / "references" / "strategy-analysis"
CONFIG_DIR = STRATEGY_DIR / "configs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="策略因子配置生成")
    parser.add_argument(
        "--input", 
        default=None, 
        help="输入优化报告路径"
    )
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


def load_optimization_report(input_path: str = None) -> Optional[Dict]:
    """加载优化报告"""
    if input_path:
        path = Path(input_path)
        if path.exists():
            content = path.read_text(encoding="utf-8")
            return parse_optimization_content(content)
    
    # 查找最新报告
    reports = sorted(STRATEGY_DIR.glob("strategy-optimization-*.md"), reverse=True)
    if reports:
        content = reports[0].read_text(encoding="utf-8")
        return parse_optimization_content(content)
    
    return None


def parse_optimization_content(content: str) -> Dict:
    """解析优化报告内容"""
    result = {
        "hit_rates": {},
        "pattern_stats": {},
        "weak_patterns": [],
        "strong_patterns": [],
        "recommendations": [],
    }
    
    # 提取命中率
    exact_match = re.search(r"T\+1精确命中率.*?(\d+\.?\d*)%", content)
    if exact_match:
        result["hit_rates"]["t1_exact"] = float(exact_match.group(1))
    
    direction_match = re.search(r"T\+1方向命中率.*?(\d+\.?\d*)%", content)
    if direction_match:
        result["hit_rates"]["t1_direction"] = float(direction_match.group(1))
    
    # 提取预测模式统计
    pattern_section = re.search(r"## 预测模式分析(.*?)(?=##|$)", content, re.DOTALL)
    if pattern_section:
        pattern_lines = pattern_section.group(1).strip().split("\n")
        for line in pattern_lines:
            if "|" in line and "预测模式" not in line and "---" not in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 4:
                    pattern = parts[0]
                    total = int(parts[1])
                    hits = int(parts[2])
                    rate = float(parts[3].replace("%", ""))
                    
                    result["pattern_stats"][pattern] = {
                        "total": total,
                        "hits": hits,
                        "rate": rate,
                    }
                    
                    if total >= 2:
                        if rate < 50:
                            result["weak_patterns"].append(pattern)
                        elif rate >= 70:
                            result["strong_patterns"].append(pattern)
    
    # 提取优化建议
    suggestion_section = re.search(r"## 优化建议(.*?)(?=##|$)", content, re.DOTALL)
    if suggestion_section:
        suggestions = suggestion_section.group(1).strip().split("\n")
        for suggestion in suggestions:
            if suggestion.strip() and not suggestion.startswith("#"):
                result["recommendations"].append(suggestion.strip())
    
    return result


def generate_factor_weights(optimization_data: Dict) -> Dict:
    """生成因子权重配置"""
    weights = {
        "version": datetime.now().strftime("%Y-%m-%d"),
        "description": "基于策略因子优化结果生成的技术因子权重",
        "factors": {
            "trend": {
                "weight": 0.25,
                "description": "趋势类指标权重",
                "subfactors": ["ma_bfq_5", "ma_bfq_10", "ma_bfq_20", "ma_bfq_30"]
            },
            "momentum": {
                "weight": 0.20,
                "description": "动量类指标权重",
                "subfactors": ["rsi_bfq_6", "rsi_bfq_12", "volume_ratio"]
            },
            "volatility": {
                "weight": 0.15,
                "description": "波动率类指标权重",
                "subfactors": ["volatility_20d", "atr_14"]
            },
            "volume": {
                "weight": 0.25,
                "description": "成交量类指标权重",
                "subfactors": ["volume_ratio", "turnover_rate"]
            },
            "pattern": {
                "weight": 0.15,
                "description": "形态类指标权重",
                "subfactors": ["divergence_signal", "support_resistance"]
            }
        },
        "adjustments": []
    }
    
    # 根据优化结果调整权重
    weak_patterns = optimization_data.get("weak_patterns", [])
    strong_patterns = optimization_data.get("strong_patterns", [])
    
    # 弱项模式调整
    if "偏弱" in weak_patterns:
        weights["adjustments"].append({
            "factor": "momentum",
            "adjustment": -0.05,
            "reason": "偏弱预测模式命中率低，降低动量类指标权重"
        })
    
    if "分歧" in weak_patterns:
        weights["adjustments"].append({
            "factor": "pattern",
            "adjustment": +0.05,
            "reason": "分歧预测模式需要更多形态类指标确认"
        })
    
    # 强项模式调整
    if "偏强" in strong_patterns:
        weights["adjustments"].append({
            "factor": "trend",
            "adjustment": +0.05,
            "reason": "偏强预测模式命中率高，增加趋势类指标权重"
        })
    
    # 应用调整
    for adjustment in weights["adjustments"]:
        factor = adjustment["factor"]
        if factor in weights["factors"]:
            weights["factors"][factor]["weight"] += adjustment["adjustment"]
    
    # 归一化权重
    total_weight = sum(f["weight"] for f in weights["factors"].values())
    if total_weight > 0:
        for factor in weights["factors"].values():
            factor["weight"] = round(factor["weight"] / total_weight, 3)
    
    return weights


def generate_prediction_model(optimization_data: Dict) -> Dict:
    """生成预测模型配置"""
    model = {
        "version": datetime.now().strftime("%Y-%m-%d"),
        "description": "基于策略因子优化结果生成的预测模型参数",
        "thresholds": {
            "strong_continuation": 7.0,  # 次日强延续阈值
            "bullish": 2.0,              # 次日偏强阈值
            "neutral_upper": -2.0,       # 次日分歧上界
            "neutral_lower": 2.0,        # 次日分歧下界
            "bearish": -7.0,             # 次日偏弱阈值
        },
        "confidence_levels": {
            "high": 0.7,      # 高置信度阈值
            "medium": 0.5,    # 中置信度阈值
            "low": 0.3,       # 低置信度阈值
        },
        "pattern_weights": {},
        "adjustment_rules": []
    }
    
    # 根据命中率设置模式权重
    pattern_stats = optimization_data.get("pattern_stats", {})
    for pattern, stats in pattern_stats.items():
        rate = stats.get("rate", 0)
        if rate >= 70:
            weight = 1.2  # 高命中率模式权重增加
        elif rate >= 50:
            weight = 1.0  # 中等命中率模式权重不变
        else:
            weight = 0.8  # 低命中率模式权重降低
        
        model["pattern_weights"][pattern] = {
            "weight": weight,
            "hit_rate": rate,
            "samples": stats.get("total", 0)
        }
    
    # 生成调整规则
    weak_patterns = optimization_data.get("weak_patterns", [])
    for pattern in weak_patterns:
        model["adjustment_rules"].append({
            "condition": f"prediction == '{pattern}'",
            "action": "increase_confidence_threshold",
            "adjustment": 0.1,
            "reason": f"{pattern}模式命中率低，提高置信度阈值"
        })
    
    return model


def save_configs(weights: Dict, model: Dict) -> tuple[Path, Path]:
    """保存配置文件"""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    date_str = datetime.now().strftime("%Y-%m-%d")
    
    weights_file = CONFIG_DIR / f"factor-weights-{date_str}.json"
    model_file = CONFIG_DIR / f"prediction-model-{date_str}.json"
    
    weights_file.write_text(json.dumps(weights, ensure_ascii=False, indent=2), encoding="utf-8")
    model_file.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
    
    return weights_file, model_file


def generate_config_report(weights: Dict, model: Dict, optimization_data: Dict) -> str:
    """生成配置报告"""
    lines = [
        f"# 策略因子配置报告",
        f"",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 配置版本：{weights['version']}",
        f"",
        f"## 因子权重配置",
        f"",
        f"| 因子类型 | 权重 | 描述 |",
        f"|----------|------|------|",
    ]
    
    for factor_name, factor_info in weights["factors"].items():
        lines.append(
            f"| {factor_name} | {factor_info['weight']} | {factor_info['description']} |"
        )
    
    lines.extend([
        f"",
        f"## 预测模型参数",
        f"",
        f"### 阈值设置",
        f"",
        f"| 场景 | 阈值 |",
        f"|------|------|",
        f"| 次日强延续 | ≥{model['thresholds']['strong_continuation']}% |",
        f"| 次日偏强 | ≥{model['thresholds']['bullish']}% |",
        f"| 次日分歧 | {model['thresholds']['neutral_lower']}% ~ {model['thresholds']['neutral_upper']}% |",
        f"| 次日偏弱 | ≤{model['thresholds']['bearish']}% |",
        f"",
        f"### 模式权重",
        f"",
        f"| 预测模式 | 权重 | 命中率 | 样本数 |",
        f"|----------|------|--------|--------|",
    ])
    
    for pattern, info in model["pattern_weights"].items():
        lines.append(
            f"| {pattern} | {info['weight']} | {info['hit_rate']}% | {info['samples']} |"
        )
    
    lines.extend([
        f"",
        f"## 优化调整",
        f"",
        f"### 因子权重调整",
        f"",
    ])
    
    for adjustment in weights.get("adjustments", []):
        lines.append(f"- **{adjustment['factor']}**：{adjustment['adjustment']:+.2f}（{adjustment['reason']}）")
    
    lines.extend([
        f"",
        f"### 预测模型调整规则",
        f"",
    ])
    
    for rule in model.get("adjustment_rules", []):
        lines.append(f"- **{rule['condition']}**：{rule['action']}（{rule['reason']}）")
    
    lines.extend([
        f"",
        f"## 使用说明",
        f"",
        f"1. **因子权重**：用于调整技术因子分析时的权重分配",
        f"2. **预测模型**：用于调整T+1/T+2预测的阈值和置信度",
        f"3. **调整规则**：用于自动调整特定场景的预测参数",
        f"",
        f"## 配置文件",
        f"",
        f"- 因子权重：`references/strategy-analysis/configs/factor-weights-{weights['version']}.json`",
        f"- 预测模型：`references/strategy-analysis/configs/prediction-model-{weights['version']}.json`",
        f"",
        f"---",
        f"_此报告由 config_generator.py 自动生成_",
    ])
    
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    
    # 加载优化报告
    optimization_data = load_optimization_report(args.input)
    
    if not optimization_data:
        print("❌ 未找到优化报告，请先运行 optimize_strategy.py")
        return
    
    print(f"加载优化报告成功")
    print(f"命中率：T+1精确={optimization_data['hit_rates'].get('t1_exact', 0)}%，方向={optimization_data['hit_rates'].get('t1_direction', 0)}%")
    
    # 生成配置
    weights = generate_factor_weights(optimization_data)
    model = generate_prediction_model(optimization_data)
    
    # 生成报告
    report = generate_config_report(weights, model, optimization_data)
    
    if args.format == "json":
        config = {
            "factor_weights": weights,
            "prediction_model": model,
            "optimization_data": optimization_data,
        }
        print(json.dumps(config, ensure_ascii=False, indent=2))
    else:
        print(report)
    
    # 保存配置
    weights_file, model_file = save_configs(weights, model)
    print(f"\n配置文件已保存：")
    print(f"- 因子权重：{weights_file}")
    print(f"- 预测模型：{model_file}")


if __name__ == "__main__":
    main()