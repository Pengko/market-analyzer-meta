"""
裁判Agent - Judge Agent
审查看多/看空报告，综合风险辩论意见，做出裁决
"""

import json
import logging
from typing import Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class JudgeVerdict:
    """裁判裁决"""
    stance: str  # bull/bear/neutral
    confidence: float  # 0-1
    score: float  # 0-100
    reasoning: str  # 裁决理由
    key_factors: List[str]  # 关键因素
    caveats: List[str]  # 注意事项


class JudgeAgent:
    """
    裁判Agent
    
    职责：
    1. 审查看多/看空Agent的报告
    2. 综合辩论Agent的意见
    3. 考虑风险辩论结果
    4. 做出最终裁决
    """
    
    name = "judge"
    role = "裁判Agent"
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def run(
        self,
        bull_report: Dict[str, Any],
        bear_report: Dict[str, Any],
        debate_result: Dict[str, Any],
        risk_debate: Dict[str, Any],
        signal_scores: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """
        运行裁判Agent
        
        Args:
            bull_report: 看多Agent报告
            bear_report: 看空Agent报告
            debate_result: 辩论结果（三个辩论Agent）
            risk_debate: 风险辩论结果
            signal_scores: 信号融合评分（可选）
        
        Returns:
            裁判裁决结果
        """
        self.logger.info("Running Judge Agent...")
        
        # 提取各方观点
        bull_score = bull_report.get("score", 50)
        bear_score = bear_report.get("score", 50)
        bull_confidence = bull_report.get("confidence", 0.5)
        bear_confidence = bear_report.get("confidence", 0.5)
        
        # 辩论结果
        aggressive = debate_result.get("aggressive", {})
        conservative = debate_result.get("conservative", {})
        neutral = debate_result.get("neutral", {})
        
        # 风险调整
        adjusted = risk_debate.get("adjusted", {})
        adjusted_bull = adjusted.get("bull", bull_score)
        adjusted_bear = adjusted.get("bear", bear_score)
        
        # 综合评估
        bull_total = adjusted_bull * 0.4 + bull_confidence * 30
        bear_total = adjusted_bear * 0.4 + bear_confidence * 30
        
        # 辩论权重
        debater_stances = [
            aggressive.get("stance", "neutral"),
            conservative.get("stance", "neutral"),
            neutral.get("stance", "neutral"),
        ]
        bull_debate_bonus = debater_stances.count("bull") * 5
        bear_debate_bonus = debater_stances.count("bear") * 5
        
        bull_total += bull_debate_bonus
        bear_total += bear_debate_bonus
        
        # 做出裁决
        key_factors = []
        caveats = []
        
        # 关键因素
        if bull_report.get("arguments"):
            top_bull = bull_report["arguments"][0]
            key_factors.append(f"看多：{top_bull.get('signal', '')}")
        
        if bear_report.get("arguments"):
            top_bear = bear_report["arguments"][0]
            key_factors.append(f"看空：{top_bear.get('signal', '')}")
        
        # 催化剂
        if bull_report.get("catalysts"):
            key_factors.append(f"催化剂：{bull_report['catalysts'][0][:30]}")
        
        # 风险
        if bear_report.get("risks"):
            caveats.append(f"风险：{bear_report['risks'][0][:30]}")
        
        # 注意事项
        if risk_debate.get("impact") == "风险削弱看多信号":
            caveats.append("风险较高，削弱看多信号")
        
        if abs(bull_score - bear_score) < 15:
            caveats.append("多空分歧大，方向不明确")
        
        # 最终裁决
        score_diff = bull_total - bear_total
        
        if score_diff > 15:
            stance = "bull"
            score = min(bull_total, 85)
            confidence = min(bull_confidence + 0.1, 0.9)
            reasoning = f"看多信号强于看空（{bull_total:.0f} vs {bear_total:.0f}），多方占优"
        elif score_diff < -15:
            stance = "bear"
            score = min(bear_total, 85)
            confidence = min(bear_confidence + 0.1, 0.9)
            reasoning = f"看空信号强于看多（{bear_total:.0f} vs {bull_total:.0f}），空方占优"
        else:
            stance = "neutral"
            score = 50
            confidence = 0.5
            reasoning = f"多空力量均衡（{bull_total:.0f} vs {bear_total:.0f}），方向不明"
        
        # 补充理由
        if debater_stances.count("bull") >= 2:
            reasoning += "，辩论多数看多"
        elif debater_stances.count("bear") >= 2:
            reasoning += "，辩论多数看空"
        
        # 信号融合对比
        if signal_scores:
            fusion_score = signal_scores.get("composite_score", 50)
            if abs(score - fusion_score) > 20:
                caveats.append(
                    f"裁判与信号融合评分差异大（{score:.0f} vs {fusion_score:.0f}）"
                )
            # 综合评分
            final_score = score * 0.6 + fusion_score * 0.4
        else:
            final_score = score
        
        result = {
            "agent": self.name,
            "role": self.role,
            "stance": stance,
            "confidence": confidence,
            "score": score,
            "final_score": final_score,
            "reasoning": reasoning,
            "key_factors": key_factors[:5],
            "caveats": caveats[:5],
        }
        
        self.logger.info(f"Judge: stance={stance}, score={score:.1f}, confidence={confidence:.2f}")
        
        return result


def run_judge_agent(
    bull_report: Dict[str, Any],
    bear_report: Dict[str, Any],
    debate_result: Dict[str, Any],
    risk_debate: Dict[str, Any],
    signal_scores: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """
    运行裁判Agent的便捷函数
    
    Args:
        bull_report: 看多Agent报告
        bear_report: 看空Agent报告
        debate_result: 辩论结果
        risk_debate: 风险辩论结果
        signal_scores: 信号融合评分（可选）
    
    Returns:
        裁判裁决结果
    """
    agent = JudgeAgent()
    return agent.run(bull_report, bear_report, debate_result, risk_debate, signal_scores)


# 兼容旧接口
run_judge = run_judge_agent
run_full_judgment = run_judge_agent
