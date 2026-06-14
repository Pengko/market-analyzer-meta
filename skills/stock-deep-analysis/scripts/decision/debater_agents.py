"""
辩论Agent - Debater Agents
激进派、保守派、中性派三视角辩论
"""

import json
import logging
from typing import Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DebaterOpinion:
    """辩论观点"""
    agent: str  # aggressive/conservative/neutral
    stance: str  # bull/bear/neutral
    confidence: float  # 0-1
    key_points: List[str]  # 关键论点
    risk_assessment: str  # 风险评估
    suggestion: str  # 建议


def run_debaters(
    bull_report: Dict[str, Any],
    bear_report: Dict[str, Any],
    risk_data: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    运行三个辩论Agent
    
    Args:
        bull_report: 看多Agent报告
        bear_report: 看空Agent报告
        risk_data: 风险数据
        context: 上下文（新闻、板块、基本面等）
    
    Returns:
        {
            "aggressive": DebaterOpinion,
            "conservative": DebaterOpinion,
            "neutral": DebaterOpinion,
            "summary": str
        }
    """
    logger.info("Running Debater Agents...")
    
    # 激进派
    aggressive = _run_aggressive_debater(bull_report, bear_report, risk_data, context)
    
    # 保守派
    conservative = _run_conservative_debater(bull_report, bear_report, risk_data, context)
    
    # 中性派
    neutral = _run_neutral_debater(bull_report, bear_report, risk_data, context)
    
    # 汇总
    summary = _summarize_debate(aggressive, conservative, neutral)
    
    return {
        "aggressive": vars(aggressive),
        "conservative": vars(conservative),
        "neutral": vars(neutral),
        "summary": summary,
    }


def _run_aggressive_debater(
    bull_report: Dict[str, Any],
    bear_report: Dict[str, Any],
    risk_data: Dict[str, Any],
    context: Dict[str, Any],
) -> DebaterOpinion:
    """激进派辩论 - 偏向看多，追求高收益"""
    
    bull_score = bull_report.get("score", 50)
    bear_score = bear_report.get("score", 50)
    
    key_points = []
    
    # 激进派更关注看多信号
    if bull_score > 60:
        key_points.append(f"看多信号强（{bull_score:.0f}分），趋势向上")
    
    # 关注催化剂
    if context.get("news", {}).get("events"):
        for event in context["news"]["events"][:2]:
            if any(kw in event.get("title", "") for kw in ["利好", "增长", "突破"]):
                key_points.append(f"催化剂：{event['title'][:30]}")
    
    # 关注板块热度
    if context.get("sector", {}).get("sector", {}).get("rank", 999) <= 10:
        key_points.append("板块热度高，有板块效应")
    
    # 忽略部分看空信号
    if bear_score > 50 and bull_score > bear_score:
        key_points.append(f"虽然有看空信号（{bear_score:.0f}分），但看多更强")
    
    # 风险评估
    risk_level = _assess_risk_level(risk_data)
    if risk_level == "low":
        risk_assessment = "风险可控，适合激进操作"
    elif risk_level == "medium":
        risk_assessment = "有一定风险，但收益可期"
    else:
        risk_assessment = "风险较高，需控制仓位"
    
    # 建议
    if bull_score > 70 and risk_level != "high":
        stance = "bull"
        confidence = min(bull_score / 100, 0.9)
        suggestion = "建议买入，可适当追高"
    elif bull_score > 55:
        stance = "bull"
        confidence = 0.6
        suggestion = "可轻仓试探，等待确认"
    else:
        stance = "neutral"
        confidence = 0.5
        suggestion = "暂不操作，等待更好机会"
    
    return DebaterOpinion(
        agent="aggressive",
        stance=stance,
        confidence=confidence,
        key_points=key_points[:5],
        risk_assessment=risk_assessment,
        suggestion=suggestion,
    )


def _run_conservative_debater(
    bull_report: Dict[str, Any],
    bear_report: Dict[str, Any],
    risk_data: Dict[str, Any],
    context: Dict[str, Any],
) -> DebaterOpinion:
    """保守派辩论 - 偏向看空，注重风险控制"""
    
    bull_score = bull_report.get("score", 50)
    bear_score = bear_report.get("score", 50)
    
    key_points = []
    
    # 保守派更关注看空信号
    if bear_score > 50:
        key_points.append(f"看空信号存在（{bear_score:.0f}分），需警惕")
    
    # 关注风险点
    if context.get("news", {}).get("events"):
        for event in context["news"]["events"][:2]:
            if any(kw in event.get("title", "") for kw in ["利空", "下跌", "违规", "减持"]):
                key_points.append(f"风险事件：{event['title'][:30]}")
    
    # 关注套牢盘
    if context.get("chips", {}).get("cyq", {}).get("winner_rate", 100) < 40:
        key_points.append("套牢盘重，上方压力大")
    
    # 关注基本面风险
    if context.get("fundamental", {}).get("financial", {}).get("risk"):
        key_points.append(f"基本面风险：{context['fundamental']['financial']['risk'][:30]}")
    
    # 即使看多也要谨慎
    if bull_score > 60:
        key_points.append(f"虽然看多信号强（{bull_score:.0f}分），但需防回调")
    
    # 风险评估
    risk_level = _assess_risk_level(risk_data)
    if risk_level == "high":
        risk_assessment = "风险很高，建议回避"
    elif risk_level == "medium":
        risk_assessment = "风险中等，需严格止损"
    else:
        risk_assessment = "风险较低，可谨慎参与"
    
    # 建议
    if bear_score > 60 or risk_level == "high":
        stance = "bear"
        confidence = min(max(bear_score, risk_data.get("score", 0)) / 100, 0.9)
        suggestion = "建议卖出或观望"
    elif bear_score > 45:
        stance = "bear"
        confidence = 0.6
        suggestion = "不建议追高，已持仓可考虑减仓"
    else:
        stance = "neutral"
        confidence = 0.5
        suggestion = "维持现有仓位，不加仓"
    
    return DebaterOpinion(
        agent="conservative",
        stance=stance,
        confidence=confidence,
        key_points=key_points[:5],
        risk_assessment=risk_assessment,
        suggestion=suggestion,
    )


def _run_neutral_debater(
    bull_report: Dict[str, Any],
    bear_report: Dict[str, Any],
    risk_data: Dict[str, Any],
    context: Dict[str, Any],
) -> DebaterOpinion:
    """中性派辩论 - 平衡多空，客观分析"""
    
    bull_score = bull_report.get("score", 50)
    bear_score = bear_report.get("score", 50)
    
    key_points = []
    
    # 平衡分析多空信号
    key_points.append(f"看多{bull_score:.0f}分 vs 看空{bear_score:.0f}分")
    
    # 分析分歧点
    if abs(bull_score - bear_score) > 20:
        if bull_score > bear_score:
            key_points.append("多空分歧大，但多方占优")
        else:
            key_points.append("多空分歧大，但空方占优")
    else:
        key_points.append("多空力量均衡，方向不明")
    
    # 关注关键因素
    if context.get("sector", {}).get("sector", {}).get("rank", 999) <= 5:
        key_points.append("板块效应强，可能带动个股")
    
    if context.get("chips", {}).get("cyq", {}).get("winner_rate"):
        winner_rate = context["chips"]["cyq"]["winner_rate"]
        key_points.append(f"获利盘{winner_rate:.0f}%，{'抛压小' if winner_rate > 60 else '抛压大'}")
    
    # 风险评估
    risk_level = _assess_risk_level(risk_data)
    risk_assessment = f"风险等级：{risk_level}"
    
    # 建议
    diff = bull_score - bear_score
    if diff > 15:
        stance = "bull"
        confidence = 0.65
        suggestion = "偏向看多，可适量参与"
    elif diff < -15:
        stance = "bear"
        confidence = 0.65
        suggestion = "偏向看空，建议观望"
    else:
        stance = "neutral"
        confidence = 0.5
        suggestion = "方向不明，建议等待信号明确"
    
    return DebaterOpinion(
        agent="neutral",
        stance=stance,
        confidence=confidence,
        key_points=key_points[:5],
        risk_assessment=risk_assessment,
        suggestion=suggestion,
    )


def _assess_risk_level(risk_data: Dict[str, Any]) -> str:
    """评估风险等级"""
    if not risk_data:
        return "medium"
    
    risk_score = risk_data.get("score", 50)
    
    if risk_score >= 70:
        return "high"
    elif risk_score >= 40:
        return "medium"
    else:
        return "low"


def _summarize_debate(
    aggressive: DebaterOpinion,
    conservative: DebaterOpinion,
    neutral: DebaterOpinion,
) -> str:
    """汇总辩论结果"""
    
    opinions = [aggressive, conservative, neutral]
    stances = [o.stance for o in opinions]
    
    bull_count = stances.count("bull")
    bear_count = stances.count("bear")
    neutral_count = stances.count("neutral")
    
    if bull_count >= 2:
        return f"多数看多（{bull_count}/3），建议偏向买入"
    elif bear_count >= 2:
        return f"多数看空（{bear_count}/3），建议偏向卖出或观望"
    else:
        return "多空分歧，建议等待信号明确"


def run_risk_debate(
    risk_report: Dict[str, Any],
    bull_report: Dict[str, Any],
    bear_report: Dict[str, Any],
) -> Dict[str, Any]:
    """
    风险辩论 - 评估风险对多空论据的影响
    
    Args:
        risk_report: 风险分析报告
        bull_report: 看多报告
        bear_report: 看空报告
    
    Returns:
        风险辩论结果
    """
    logger.info("Running Risk Debate...")
    
    risk_score = risk_report.get("score", 50)
    bull_score = bull_report.get("score", 50)
    bear_score = bear_report.get("score", 50)
    
    # 风险对看多的影响
    bull_risk_penalty = 0
    if risk_score > 60:
        bull_risk_penalty = (risk_score - 60) * 0.5
        bull_risk_penalty = min(bull_risk_penalty, 20)
    
    # 风险对看空的影响
    bear_risk_boost = 0
    if risk_score > 60:
        bear_risk_boost = (risk_score - 60) * 0.3
        bear_risk_boost = min(bear_risk_boost, 15)
    
    adjusted_bull = max(bull_score - bull_risk_penalty, 0)
    adjusted_bear = min(bear_score + bear_risk_boost, 100)
    
    return {
        "original": {"bull": bull_score, "bear": bear_score},
        "risk_score": risk_score,
        "adjustments": {"bull_penalty": bull_risk_penalty, "bear_boost": bear_risk_boost},
        "adjusted": {"bull": adjusted_bull, "bear": adjusted_bear},
        "impact": "风险削弱看多信号" if bull_risk_penalty > 5 else "风险影响有限",
    }
