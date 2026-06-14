"""
决策层 - Decision Layer
信号融合 → 多空辩论 → 裁判 → 决策经理
"""

from .signal_fusion import SignalFusion, run_signal_fusion
from .bull_agent import BullAgent, run_bull_agent
from .bear_agent import BearAgent, run_bear_agent
from .judge import JudgeAgent, run_judge_agent
from .debater_agents import run_debaters, run_risk_debate
from .portfolio_manager import DecisionManagerAgent, run_decision_manager_agent, format_decision_output

__all__ = [
    # 信号融合
    "SignalFusion",
    "run_signal_fusion",
    # 多空辩论
    "BullAgent",
    "run_bull_agent",
    "BearAgent",
    "run_bear_agent",
    # 辩论Agent
    "run_debaters",
    "run_risk_debate",
    # 裁判
    "JudgeAgent",
    "run_judge_agent",
    # 决策经理
    "DecisionManagerAgent",
    "run_decision_manager_agent",
    "format_decision_output",
    # Data classes
    "BullArgument",
    "BearArgument",
    "DebaterOpinion",
]
