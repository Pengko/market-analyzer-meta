"""
决策经理Agent - Decision Manager Agent
最终决策模块，综合所有分析结果做出交易决策
"""

import json
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class TradingDecision:
    """交易决策"""
    action: str  # buy/sell/hold/watch/avoid
    confidence: float  # 0-1
    position_size: str  # full/half/quarter/none
    entry_price: Optional[float]  # 建议入场价
    stop_loss: Optional[float]  # 止损价
    take_profit: Optional[float]  # 止盈价
    time_horizon: str  # short/medium/long
    reasoning: str  # 决策理由
    risk_warnings: List[str]  # 风险提示


class DecisionManagerAgent:
    """
    决策经理Agent
    
    职责：
    1. 综合裁判裁决、看多/看空报告、信号评分
    2. 考虑持仓状态和风险
    3. 做出最终交易决策
    4. 生成风险提示
    """
    
    name = "decision_manager"
    role = "决策经理Agent"
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def run(
        self,
        judge_verdict: Dict[str, Any],
        bull_report: Dict[str, Any],
        bear_report: Dict[str, Any],
        signal_scores: Dict[str, Any],
        position_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        运行组合经理Agent
        
        Args:
            judge_verdict: 裁判裁决
            bull_report: 看多Agent报告
            bear_report: 看空Agent报告
            signal_scores: 信号评分
            position_context: 持仓上下文
        
        Returns:
            交易决策
        """
        self.logger.info("Running Portfolio Manager Agent...")
        
        # 提取关键信息
        stance = judge_verdict.get("stance", "neutral")
        confidence = judge_verdict.get("confidence", 0.5)
        score = judge_verdict.get("final_score", judge_verdict.get("score", 50))
        
        # 持仓状态
        is_holding = position_context.get("is_holding", False)
        current_price = position_context.get("current_price", 0)
        cost_price = position_context.get("cost_price", 0)
        
        # 关键价位
        bull_levels = bull_report.get("key_levels", {})
        bear_levels = bear_report.get("key_levels", {})
        
        support_levels = bull_levels.get("support", []) + bear_levels.get("support", [])
        resistance_levels = bull_levels.get("resistance", []) + bear_levels.get("resistance", [])
        
        # 做出决策
        if is_holding:
            decision = self._make_holding_decision(
                stance, confidence, score,
                current_price, cost_price,
                support_levels, resistance_levels,
                judge_verdict,
            )
        else:
            decision = self._make_no_position_decision(
                stance, confidence, score,
                current_price,
                support_levels, resistance_levels,
                judge_verdict,
            )
        
        # 添加风险提示
        decision.risk_warnings = self._generate_risk_warnings(
            stance, confidence, score, judge_verdict,
        )
        
        result = {
            "agent": self.name,
            "role": self.role,
            **vars(decision),
        }
        
        self.logger.info(f"Portfolio Manager: action={decision.action}, confidence={decision.confidence:.2f}")
        
        return result
    
    def _make_holding_decision(
        self,
        stance: str,
        confidence: float,
        score: float,
        current_price: float,
        cost_price: float,
        support_levels: List[float],
        resistance_levels: List[float],
        judge_verdict: Dict[str, Any],
    ) -> TradingDecision:
        """持仓状态决策"""
        
        # 计算盈亏
        if cost_price > 0:
            pnl_pct = (current_price - cost_price) / cost_price * 100
        else:
            pnl_pct = 0
        
        caveats = judge_verdict.get("caveats", [])
        
        # 看多且信心高
        if stance == "bull" and confidence > 0.65:
            # 继续持有，可能加仓
            stop_loss = min(support_levels) * 0.97 if support_levels else current_price * 0.95
            take_profit = max(resistance_levels) * 1.02 if resistance_levels else current_price * 1.10
            
            return TradingDecision(
                action="hold",
                confidence=confidence,
                position_size="half" if confidence > 0.75 else "quarter",
                entry_price=None,
                stop_loss=stop_loss,
                take_profit=take_profit,
                time_horizon="medium",
                reasoning=f"看多信号强（{score:.0f}分），继续持有",
                risk_warnings=[],
            )
        
        # 看空或信心低
        elif stance == "bear" or confidence < 0.45:
            # 考虑卖出
            if pnl_pct < -5:
                action = "sell"
                reasoning = f"看空信号强且已亏损{pnl_pct:.1f}%，建议止损"
            elif pnl_pct > 10 and stance == "bear":
                action = "sell"
                reasoning = f"虽盈利{pnl_pct:.1f}%但看空信号强，建议止盈"
            else:
                action = "hold"
                reasoning = f"方向不明，维持现有仓位"
            
            return TradingDecision(
                action=action,
                confidence=confidence,
                position_size="none" if action == "sell" else "half",
                entry_price=None,
                stop_loss=min(support_levels) * 0.97 if support_levels else current_price * 0.95,
                take_profit=None,
                time_horizon="short",
                reasoning=reasoning,
                risk_warnings=[],
            )
        
        # 中性
        else:
            return TradingDecision(
                action="hold",
                confidence=confidence,
                position_size="half",
                entry_price=None,
                stop_loss=min(support_levels) * 0.97 if support_levels else current_price * 0.95,
                take_profit=max(resistance_levels) * 1.02 if resistance_levels else current_price * 1.08,
                time_horizon="medium",
                reasoning=f"多空平衡（{score:.0f}分），维持现有仓位",
                risk_warnings=[],
            )
    
    def _make_no_position_decision(
        self,
        stance: str,
        confidence: float,
        score: float,
        current_price: float,
        support_levels: List[float],
        resistance_levels: List[float],
        judge_verdict: Dict[str, Any],
    ) -> TradingDecision:
        """无持仓状态决策"""
        
        # 看多且信心高
        if stance == "bull" and confidence > 0.65:
            # 建议买入
            if support_levels:
                entry_price = max(support_levels) * 1.01  # 在支撑位上方一点
                stop_loss = min(support_levels) * 0.97
            else:
                entry_price = current_price * 0.98
                stop_loss = current_price * 0.95
            
            if resistance_levels:
                take_profit = max(resistance_levels) * 1.02
            else:
                take_profit = current_price * 1.10
            
            return TradingDecision(
                action="buy",
                confidence=confidence,
                position_size="half" if confidence > 0.75 else "quarter",
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                time_horizon="medium",
                reasoning=f"看多信号强（{score:.0f}分），建议买入",
                risk_warnings=[],
            )
        
        # 看空或信心低
        elif stance == "bear" or confidence < 0.45:
            return TradingDecision(
                action="watch",
                confidence=confidence,
                position_size="none",
                entry_price=None,
                stop_loss=None,
                take_profit=None,
                time_horizon="short",
                reasoning=f"看空信号（{score:.0f}分），建议观望",
                risk_warnings=[],
            )
        
        # 中性
        else:
            # 等待信号明确
            if support_levels:
                watch_price = min(support_levels) * 0.98
            else:
                watch_price = current_price * 0.95
            
            return TradingDecision(
                action="watch",
                confidence=confidence,
                position_size="none",
                entry_price=watch_price,
                stop_loss=None,
                take_profit=None,
                time_horizon="short",
                reasoning=f"方向不明（{score:.0f}分），等待信号明确",
                risk_warnings=[],
            )
    
    def _generate_risk_warnings(
        self,
        stance: str,
        confidence: float,
        score: float,
        judge_verdict: Dict[str, Any],
    ) -> List[str]:
        """生成风险提示"""
        warnings = []
        
        if confidence < 0.5:
            warnings.append("置信度较低，建议轻仓操作")
        
        if abs(score - 50) < 15:
            warnings.append("多空力量均衡，方向不明确")
        
        caveats = judge_verdict.get("caveats", [])
        for caveat in caveats[:2]:
            warnings.append(caveat)
        
        return warnings[:5]


def run_decision_manager_agent(
    judge_verdict: Dict[str, Any],
    bull_report: Dict[str, Any],
    bear_report: Dict[str, Any],
    signal_scores: Dict[str, Any],
    position_context: Dict[str, Any],
) -> Dict[str, Any]:
    """
    运行决策经理Agent的便捷函数
    
    Args:
        judge_verdict: 裁判裁决
        bull_report: 看多Agent报告
        bear_report: 看空Agent报告
        signal_scores: 信号评分
        position_context: 持仓上下文
    
    Returns:
        交易决策
    """
    agent = DecisionManagerAgent()
    return agent.run(judge_verdict, bull_report, bear_report, signal_scores, position_context)


def format_decision_output(decision: Dict[str, Any]) -> str:
    """格式化决策输出"""
    lines = []
    
    # 操作建议
    action_map = {
        "buy": "买入",
        "sell": "卖出",
        "hold": "持有",
        "watch": "观望",
        "avoid": "回避",
    }
    lines.append(f"**操作建议**: {action_map.get(decision['action'], decision['action'])}")
    lines.append(f"**置信度**: {decision['confidence']*100:.0f}%")
    lines.append(f"**仓位建议**: {decision['position_size']}")
    
    if decision.get("entry_price"):
        lines.append(f"**入场价**: {decision['entry_price']:.2f}")
    if decision.get("stop_loss"):
        lines.append(f"**止损价**: {decision['stop_loss']:.2f}")
    if decision.get("take_profit"):
        lines.append(f"**止盈价**: {decision['take_profit']:.2f}")
    
    lines.append(f"**持有周期**: {decision['time_horizon']}")
    lines.append(f"**理由**: {decision['reasoning']}")
    
    if decision.get("risk_warnings"):
        lines.append("\n**风险提示**:")
        for w in decision["risk_warnings"]:
            lines.append(f"- {w}")
    
    return "\n".join(lines)


# 兼容旧接口
run_portfolio_manager = run_decision_manager_agent
