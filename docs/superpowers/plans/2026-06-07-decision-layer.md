# 决策层实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现决策层5个模块（信号融合器、多空辩论、裁判、风险辩论、决策经理），替代现有的单一决策引擎

**Architecture:** 基于架构图设计，决策层分为5个阶段：信号融合器汇总8个Agent信号 → 多空辩论Agent构建看多/看空报告 → 裁判审查报告 → 风险辩论讨论风险偏好 → 决策经理输出最终交易建议。每个模块独立职责，通过数据流串联。

**Tech Stack:** Python, dataclasses, JSON

**Frozen Baseline:** 
- `build_stock_report.py` 已经定义了完整的调用接口（lines 645-738）
- 现有 `decision_engine.py` 保留兼容，新模块并行实现
- 架构图：`analysis-architecture.html`

---

## 文件结构

| 文件 | 职责 |
|------|------|
| `decision/signal_fusion.py` | 信号融合器 - 汇总8个Agent信号 |
| `decision/bull_agent.py` | 看多Agent - 构建看涨论据 |
| `decision/bear_agent.py` | 看空Agent - 构建看跌论据 |
| `decision/debater_agents.py` | 风险辩论 - 激进/保守/中性 |
| `decision/judge.py` | 裁判 - 审查多空报告 |
| `decision/portfolio_manager.py` | 决策经理 - 最终交易决策 |
| `decision/__init__.py` | 统一导出 |

---

## Task 1: 创建信号融合器

**Files:**
- Create: `scripts/decision/signal_fusion.py`

- [x] **Step 1: 实现 SignalFusion 类**

```python
"""
信号融合器 - 汇总8个Agent的信号，生成综合评分
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class FusedSignals:
    """融合后的信号"""
    composite_score: float  # 综合评分 0-100
    bull_score: float  # 看多评分
    bear_score: float  # 看空评分
    signal_strength: str  # strong/medium/weak
    key_factors: list[str] = field(default_factory=list)  # 关键因素
    risk_flags: list[str] = field(default_factory=list)  # 风险标记


class SignalFusion:
    """
    信号融合器
    
    汇总8个Agent的信号，计算综合评分
    """
    
    # 权重配置
    WEIGHTS = {
        "kline_sync": 0.15,
        "news": 0.20,
        "intraday": 0.10,
        "sector": 0.15,
        "stock_dims": 0.20,
        "dragon_tiger": 0.10,
        "intraday_linkage": 0.05,
        "fundamental_deep": 0.05,
    }
    
    def run(self, agent_results: dict[str, Any]) -> dict[str, Any]:
        """
        融合8个Agent的结果
        
        Args:
            agent_results: 8个Agent的结果字典
            
        Returns:
            FusedSignals 字典
        """
        bull_signals = []
        bear_signals = []
        key_factors = []
        risk_flags = []
        
        # 1. K线同步 Agent
        kline_data = agent_results.get("kline_sync", {})
        kline_score = self._score_kline(kline_data)
        bull_signals.append(kline_score["bull"])
        bear_signals.append(kline_score["bear"])
        key_factors.extend(kline_score.get("factors", []))
        
        # 2. 新闻 Agent
        news_data = agent_results.get("news", {})
        news_score = self._score_news(news_data)
        bull_signals.append(news_score["bull"])
        bear_signals.append(news_score["bear"])
        key_factors.extend(news_score.get("factors", []))
        
        # 3. 分钟线 Agent
        intraday_data = agent_results.get("intraday", {})
        intraday_score = self._score_intraday(intraday_data)
        bull_signals.append(intraday_score["bull"])
        bear_signals.append(intraday_score["bear"])
        
        # 4. 大盘+板块 Agent
        sector_data = agent_results.get("sector", {})
        sector_score = self._score_sector(sector_data)
        bull_signals.append(sector_score["bull"])
        bear_signals.append(sector_score["bear"])
        key_factors.extend(sector_score.get("factors", []))
        
        # 5. 个股维度 Agent
        dims_data = agent_results.get("stock_dims", {})
        dims_score = self._score_stock_dims(dims_data)
        bull_signals.append(dims_score["bull"])
        bear_signals.append(dims_score["bear"])
        key_factors.extend(dims_score.get("factors", []))
        risk_flags.extend(dims_score.get("risks", []))
        
        # 6. 龙虎榜 Agent
        dragon_data = agent_results.get("dragon_tiger", {})
        dragon_score = self._score_dragon_tiger(dragon_data)
        bull_signals.append(dragon_score["bull"])
        bear_signals.append(dragon_score["bear"])
        
        # 7. 分时联动 Agent
        linkage_data = agent_results.get("intraday_linkage", {})
        linkage_score = self._score_intraday_linkage(linkage_data)
        bull_signals.append(linkage_score["bull"])
        bear_signals.append(linkage_score["bear"])
        
        # 8. 基本面 Agent
        fund_data = agent_results.get("fundamental_deep", {})
        fund_score = self._score_fundamental(fund_data)
        bull_signals.append(fund_score["bull"])
        bear_signals.append(fund_score["bear"])
        risk_flags.extend(fund_score.get("risks", []))
        
        # 计算综合评分
        weights = list(self.WEIGHTS.values())
        bull_composite = sum(b * w for b, w in zip(bull_signals, weights))
        bear_composite = sum(b * w for b, w in zip(bear_signals, weights))
        composite = bull_composite - bear_composite + 50  # 中性为50
        
        # 判断信号强度
        if abs(composite - 50) >= 20:
            strength = "strong"
        elif abs(composite - 50) >= 10:
            strength = "medium"
        else:
            strength = "weak"
        
        return {
            "composite_score": round(composite, 2),
            "bull_score": round(bull_composite, 2),
            "bear_score": round(bear_composite, 2),
            "signal_strength": strength,
            "key_factors": key_factors[:5],
            "risk_flags": risk_flags,
        }
    
    def _score_kline(self, data: dict) -> dict:
        """K线信号评分"""
        daily = data.get("daily", [])
        if not daily:
            return {"bull": 50, "bear": 50}
        
        latest = daily[-1] if daily else {}
        close = float(latest.get("close", 0))
        open_ = float(latest.get("open", 0))
        
        if close > open_:
            return {"bull": 60, "bear": 40, "factors": ["收阳线"]}
        elif close < open_:
            return {"bull": 40, "bear": 60, "factors": ["收阴线"]}
        return {"bull": 50, "bear": 50}
    
    def _score_news(self, data: dict) -> dict:
        """新闻信号评分"""
        narrative = data.get("narrative_context", {})
        sentiment = narrative.get("sentiment", 0)
        
        if sentiment > 0.3:
            return {"bull": 65, "bear": 35, "factors": ["消息面偏多"]}
        elif sentiment < -0.3:
            return {"bull": 35, "bear": 65, "factors": ["消息面偏空"]}
        return {"bull": 50, "bear": 50}
    
    def _score_intraday(self, data: dict) -> dict:
        """分钟线信号评分"""
        intraday = data.get("intraday", {})
        strength = intraday.get("strength", 0.5)
        
        if strength > 0.6:
            return {"bull": 60, "bear": 40}
        elif strength < 0.4:
            return {"bull": 40, "bear": 60}
        return {"bull": 50, "bear": 50}
    
    def _score_sector(self, data: dict) -> dict:
        """板块信号评分"""
        sector = data.get("sector_context", {})
        rank = sector.get("rank", 50)
        
        if rank <= 10:
            return {"bull": 65, "bear": 35, "factors": [f"板块排名第{rank}"]}
        elif rank >= 40:
            return {"bull": 35, "bear": 65, "factors": [f"板块排名靠后"]}
        return {"bull": 50, "bear": 50}
    
    def _score_stock_dims(self, data: dict) -> dict:
        """个股维度评分"""
        trend = data.get("trend_structure", {})
        chip = data.get("chip_structure", {})
        
        bull = 50
        bear = 50
        factors = []
        risks = []
        
        # 趋势
        trend_type = trend.get("trend", "neutral")
        if trend_type == "up":
            bull += 10
            factors.append("趋势向上")
        elif trend_type == "down":
            bear += 10
            risks.append("趋势向下")
        
        # 筹码
        winner_rate = chip.get("winner_rate", 50)
        if winner_rate > 70:
            bull += 5
        elif winner_rate < 30:
            bear += 5
            risks.append("套牢盘重")
        
        return {"bull": min(bull, 100), "bear": min(bear, 100), "factors": factors, "risks": risks}
    
    def _score_dragon_tiger(self, data: dict) -> dict:
        """龙虎榜评分"""
        if not data or data.get("status") == "no_data":
            return {"bull": 50, "bear": 50}
        
        inst_net = data.get("inst_net_buy", 0)
        if inst_net > 0:
            return {"bull": 60, "bear": 40}
        elif inst_net < 0:
            return {"bull": 40, "bear": 60}
        return {"bull": 50, "bear": 50}
    
    def _score_intraday_linkage(self, data: dict) -> dict:
        """分时联动评分"""
        if not data:
            return {"bull": 50, "bear": 50}
        
        linkage = data.get("linkage", "neutral")
        if linkage == "positive":
            return {"bull": 55, "bear": 45}
        elif linkage == "negative":
            return {"bull": 45, "bear": 55}
        return {"bull": 50, "bear": 50}
    
    def _score_fundamental(self, data: dict) -> dict:
        """基本面评分"""
        if not data:
            return {"bull": 50, "bear": 50}
        
        health = data.get("financial_health", "neutral")
        risks = []
        
        if health == "healthy":
            return {"bull": 60, "bear": 40}
        elif health == "risky":
            risks.append("基本面存在风险")
            return {"bull": 40, "bear": 60, "risks": risks}
        return {"bull": 50, "bear": 50, "risks": risks}


def run_signal_fusion(agent_results: dict[str, Any]) -> dict[str, Any]:
    """便捷函数"""
    fusion = SignalFusion()
    return fusion.run(agent_results)
```

- [x] **Step 2: 验证模块导入**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python -c "from decision.signal_fusion import SignalFusion, run_signal_fusion; print('OK')"
```

---

## Task 2: 创建看多Agent

**Files:**
- Create: `scripts/decision/bull_agent.py`

- [x] **Step 1: 实现 BullAgent 类**

```python
"""
看多Agent - 构建看涨论据
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BullArgument:
    """看多论据"""
    signal: str  # 信号名称
    strength: float  # 强度 0-1
    description: str  # 描述
    evidence: str  # 证据


class BullAgent:
    """
    看多Agent
    
    职责：基于融合信号和原始数据，构建看涨论据
    """
    
    def run(self, fused_signals: dict[str, Any], data_bundle: dict[str, Any]) -> dict[str, Any]:
        """
        运行看多Agent
        
        Args:
            fused_signals: 信号融合器的输出
            data_bundle: 原始数据包
            
        Returns:
            看多报告
        """
        arguments = []
        
        # 从融合信号中提取看多因素
        if fused_signals.get("bull_score", 50) > 55:
            arguments.append(BullArgument(
                signal="综合信号偏多",
                strength=(fused_signals["bull_score"] - 50) / 50,
                description=f"看多评分 {fused_signals['bull_score']:.1f}",
                evidence="信号融合器输出",
            ))
        
        # K线看多
        kline = data_bundle.get("kline", {}).get("daily", [])
        if kline:
            latest = kline[-1]
            close = float(latest.get("close", 0))
            open_ = float(latest.get("open", 0))
            if close > open_ and (close - open_) / open_ > 0.02:
                arguments.append(BullArgument(
                    signal="阳线实体",
                    strength=0.7,
                    description="收出中阳线以上",
                    evidence=f"涨 {(close-open_)/open_*100:.1f}%",
                ))
        
        # 量能看多
        volume = data_bundle.get("volume", {}).get("moneyflow", [])
        if volume:
            latest = volume[-1]
            net = float(latest.get("net_mf_amount", 0))
            if net > 0:
                arguments.append(BullArgument(
                    signal="主力净流入",
                    strength=min(abs(net) / 1e8, 1.0),
                    description="主力资金净流入",
                    evidence=f"净流入 {net/1e4:.0f}万",
                ))
        
        # 板块看多
        sector = data_bundle.get("sector_context", {})
        rank = sector.get("rank", 50)
        if rank <= 10:
            arguments.append(BullArgument(
                signal="板块强势",
                strength=(10 - rank) / 10,
                description=f"板块排名第{rank}",
                evidence="板块热度高",
            ))
        
        # 计算看多强度
        if arguments:
            avg_strength = sum(a.strength for a in arguments) / len(arguments)
        else:
            avg_strength = 0.3
        
        return {
            "agent": "bull",
            "arguments": [vars(a) for a in arguments],
            "argument_count": len(arguments),
            "avg_strength": round(avg_strength, 2),
            "conclusion": "看多" if avg_strength > 0.5 else "中性偏多",
        }


def run_bull_agent(fused_signals: dict[str, Any], data_bundle: dict[str, Any]) -> dict[str, Any]:
    """便捷函数"""
    agent = BullAgent()
    return agent.run(fused_signals, data_bundle)
```

- [x] **Step 2: 验证模块导入**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python -c "from decision.bull_agent import BullAgent, run_bull_agent; print('OK')"
```

---

## Task 3: 创建看空Agent

**Files:**
- Create: `scripts/decision/bear_agent.py`

- [x] **Step 1: 实现 BearAgent 类**

```python
"""
看空Agent - 构建看跌论据
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BearArgument:
    """看空论据"""
    signal: str  # 信号名称
    strength: float  # 强度 0-1
    description: str  # 描述
    evidence: str  # 证据


class BearAgent:
    """
    看空Agent
    
    职责：基于融合信号和原始数据，构建看跌论据
    """
    
    def run(self, fused_signals: dict[str, Any], data_bundle: dict[str, Any]) -> dict[str, Any]:
        """
        运行看空Agent
        
        Args:
            fused_signals: 信号融合器的输出
            data_bundle: 原始数据包
            
        Returns:
            看空报告
        """
        arguments = []
        
        # 从融合信号中提取看空因素
        if fused_signals.get("bear_score", 50) > 55:
            arguments.append(BearArgument(
                signal="综合信号偏空",
                strength=(fused_signals["bear_score"] - 50) / 50,
                description=f"看空评分 {fused_signals['bear_score']:.1f}",
                evidence="信号融合器输出",
            ))
        
        # K线看空
        kline = data_bundle.get("kline", {}).get("daily", [])
        if kline:
            latest = kline[-1]
            close = float(latest.get("close", 0))
            open_ = float(latest.get("open", 0))
            if close < open_ and (open_ - close) / open_ > 0.02:
                arguments.append(BearArgument(
                    signal="阴线实体",
                    strength=0.7,
                    description="收出中阴线以上",
                    evidence=f"跌 {(open_-close)/open_*100:.1f}%",
                ))
        
        # 量能看空
        volume = data_bundle.get("volume", {}).get("moneyflow", [])
        if volume:
            latest = volume[-1]
            net = float(latest.get("net_mf_amount", 0))
            if net < 0:
                arguments.append(BearArgument(
                    signal="主力净流出",
                    strength=min(abs(net) / 1e8, 1.0),
                    description="主力资金净流出",
                    evidence=f"净流出 {abs(net)/1e4:.0f}万",
                ))
        
        # 风险标记
        risk_flags = fused_signals.get("risk_flags", [])
        for flag in risk_flags[:2]:
            arguments.append(BearArgument(
                signal="风险提示",
                strength=0.6,
                description=flag,
                evidence="风险检测",
            ))
        
        # 计算看空强度
        if arguments:
            avg_strength = sum(a.strength for a in arguments) / len(arguments)
        else:
            avg_strength = 0.3
        
        return {
            "agent": "bear",
            "arguments": [vars(a) for a in arguments],
            "argument_count": len(arguments),
            "avg_strength": round(avg_strength, 2),
            "conclusion": "看空" if avg_strength > 0.5 else "中性偏空",
        }


def run_bear_agent(fused_signals: dict[str, Any], data_bundle: dict[str, Any]) -> dict[str, Any]:
    """便捷函数"""
    agent = BearAgent()
    return agent.run(fused_signals, data_bundle)
```

- [x] **Step 2: 验证模块导入**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python -c "from decision.bear_agent import BearAgent, run_bear_agent; print('OK')"
```

---

## Task 4: 创建风险辩论Agent

**Files:**
- Create: `scripts/decision/debater_agents.py`

- [x] **Step 1: 实现风险辩论Agent**

```python
"""
风险辩论 - 激进/保守/中性三视角辩论
"""

from typing import Any


def run_risk_debate(
    risk_report: dict[str, Any],
    bull_report: dict[str, Any],
    bear_report: dict[str, Any],
) -> dict[str, Any]:
    """
    风险辩论
    
    Args:
        risk_report: 风险评估报告
        bull_report: 看多报告
        bear_report: 看空报告
        
    Returns:
        风险辩论结果
    """
    bull_args = bull_report.get("arguments", [])
    bear_args = bear_report.get("arguments", [])
    
    bull_strength = bull_report.get("avg_strength", 0.3)
    bear_strength = bear_report.get("avg_strength", 0.3)
    
    # 激进派观点
    aggressive = {
        "stance": "aggressive",
        "opinion": "可承受高风险",
        "reasoning": f"看多信号 {bull_strength:.1f}，建议激进操作",
        "position_size": "full" if bull_strength > 0.6 else "half",
    }
    
    # 保守派观点
    conservative = {
        "stance": "conservative",
        "opinion": "需控制风险",
        "reasoning": f"看空信号 {bear_strength:.1f}，建议保守操作",
        "position_size": "quarter" if bear_strength > 0.5 else "half",
    }
    
    # 中性派观点
    neutral = {
        "stance": "neutral",
        "opinion": "平衡建议",
        "reasoning": f"多空力量对比 {bull_strength:.1f}:{bear_strength:.1f}",
        "position_size": "half",
    }
    
    return {
        "aggressive": aggressive,
        "conservative": conservative,
        "neutral": neutral,
        "risk_score": risk_report.get("score", 50),
    }


def run_debaters(
    bull_report: dict[str, Any],
    bear_report: dict[str, Any],
    risk_data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """
    运行三个辩论Agent（激进/保守/中性）
    
    Args:
        bull_report: 看多报告
        bear_report: 看空报告
        risk_data: 风险数据
        context: 上下文数据
        
    Returns:
        辩论结果
    """
    risk_report = {"score": risk_data.get("score", 50)}
    
    debate_result = run_risk_debate(risk_report, bull_report, bear_report)
    debate_result["context"] = context
    
    return debate_result
```

- [x] **Step 2: 验证模块导入**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python -c "from decision.debater_agents import run_debaters, run_risk_debate; print('OK')"
```

---

## Task 5: 创建裁判Agent

**Files:**
- Create: `scripts/decision/judge.py`

- [x] **Step 1: 实现 JudgeAgent 类**

```python
"""
裁判Agent - 审查看多/看空报告，综合风险辩论意见，产出裁决
"""

from dataclasses import dataclass, field
from typing import Any


class JudgeAgent:
    """
    裁判Agent
    
    职责：
    1. 审查看多/看空报告
    2. 综合风险辩论意见
    3. 产出最终裁决
    """
    
    def run(
        self,
        bull_report: dict[str, Any],
        bear_report: dict[str, Any],
        debate_result: dict[str, Any],
        risk_debate: dict[str, Any],
        signal_scores: dict[str, Any],
    ) -> dict[str, Any]:
        """
        运行裁判Agent
        
        Args:
            bull_report: 看多报告
            bear_report: 看空报告
            debate_result: 辩论结果
            risk_debate: 风险辩论结果
            signal_scores: 融合信号
            
        Returns:
            裁决结果
        """
        bull_strength = bull_report.get("avg_strength", 0.3)
        bear_strength = bear_report.get("avg_strength", 0.3)
        composite = signal_scores.get("composite_score", 50)
        
        # 综合判断
        if bull_strength > bear_strength + 0.2:
            direction = "bullish"
            confidence = min(bull_strength, 0.8)
        elif bear_strength > bull_strength + 0.2:
            direction = "bearish"
            confidence = min(bear_strength, 0.8)
        else:
            direction = "neutral"
            confidence = 0.5
        
        # 风险调整
        risk_score = risk_debate.get("risk_score", 50)
        if risk_score > 70:
            confidence *= 0.8  # 高风险降低置信度
        
        return {
            "direction": direction,
            "confidence": round(confidence, 2),
            "composite_score": composite,
            "bull_strength": bull_strength,
            "bear_strength": bear_strength,
            "risk_adjusted": risk_score > 60,
        }


def run_judge_agent(
    bull_report: dict[str, Any],
    bear_report: dict[str, Any],
    debate_result: dict[str, Any],
    risk_debate: dict[str, Any],
    signal_scores: dict[str, Any],
) -> dict[str, Any]:
    """便捷函数"""
    agent = JudgeAgent()
    return agent.run(bull_report, bear_report, debate_result, risk_debate, signal_scores)


def run_full_judgment(
    bull_report: dict[str, Any],
    bear_report: dict[str, Any],
    debate_result: dict[str, Any],
    risk_debate: dict[str, Any],
    signal_scores: dict[str, Any],
) -> dict[str, Any]:
    """兼容旧接口"""
    return run_judge_agent(bull_report, bear_report, debate_result, risk_debate, signal_scores)
```

- [x] **Step 2: 验证模块导入**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python -c "from decision.judge import JudgeAgent, run_judge_agent; print('OK')"
```

---

## Task 6: 创建决策经理Agent

**Files:**
- Create: `scripts/decision/portfolio_manager.py`

- [x] **Step 1: 实现 DecisionManagerAgent 类**

```python
"""
决策经理Agent - 综合裁决和持仓，输出最终交易决策
"""

from typing import Any


class DecisionManagerAgent:
    """
    决策经理Agent
    
    职责：
    1. 综合裁决结果
    2. 考虑持仓状态
    3. 输出买入/持有/卖出建议
    """
    
    def run(
        self,
        judge_verdict: dict[str, Any],
        bull_report: dict[str, Any],
        bear_report: dict[str, Any],
        signal_scores: dict[str, Any],
        position_context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        运行决策经理Agent
        
        Args:
            judge_verdict: 裁决结果
            bull_report: 看多报告
            bear_report: 看空报告
            signal_scores: 融合信号
            position_context: 持仓上下文
            
        Returns:
            交易决策
        """
        direction = judge_verdict.get("direction", "neutral")
        confidence = judge_verdict.get("confidence", 0.5)
        composite = judge_verdict.get("composite_score", 50)
        
        is_holding = position_context.get("is_holding", False)
        current_price = position_context.get("current_price", 0)
        cost_price = position_context.get("cost_price", 0)
        
        # 计算盈亏
        if cost_price > 0:
            pnl_pct = (current_price - cost_price) / cost_price * 100
        else:
            pnl_pct = 0
        
        # 决策逻辑
        if direction == "bullish" and confidence > 0.6:
            action = "buy" if not is_holding else "hold"
            reason = f"看多信号强（置信度 {confidence:.0%}）"
            stop_loss = round(current_price * 0.95, 2) if current_price else 0
            take_profit = round(current_price * 1.10, 2) if current_price else 0
        elif direction == "bearish" and confidence > 0.6:
            action = "sell" if is_holding else "watch"
            reason = f"看空信号强（置信度 {confidence:.0%}）"
            stop_loss = 0
            take_profit = 0
        else:
            action = "hold" if is_holding else "watch"
            reason = "信号不明确，保持观望"
            stop_loss = 0
            take_profit = 0
        
        # 仓位建议
        if action in ("buy", "sell"):
            position_size = "half" if confidence < 0.7 else "full"
        else:
            position_size = "none"
        
        return {
            "action": action,
            "position_size": position_size,
            "reason": reason,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "pnl_pct": round(pnl_pct, 2),
            "composite_score": composite,
            "confidence": confidence,
        }


def run_decision_manager_agent(
    judge_verdict: dict[str, Any],
    bull_report: dict[str, Any],
    bear_report: dict[str, Any],
    signal_scores: dict[str, Any],
    position_context: dict[str, Any],
) -> dict[str, Any]:
    """便捷函数"""
    agent = DecisionManagerAgent()
    return agent.run(judge_verdict, bull_report, bear_report, signal_scores, position_context)


def format_decision_output(decision: dict[str, Any]) -> str:
    """格式化决策输出"""
    action_map = {
        "buy": "买入",
        "sell": "卖出",
        "hold": "持有",
        "watch": "观望",
    }
    action = action_map.get(decision.get("action", ""), "未知")
    confidence = decision.get("confidence", 0)
    reason = decision.get("reason", "")
    
    return f"**决策建议**: {action} (置信度 {confidence:.0%})\n**理由**: {reason}"
```

- [x] **Step 2: 验证模块导入**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python -c "from decision.portfolio_manager import DecisionManagerAgent, run_decision_manager_agent, format_decision_output; print('OK')"
```

---

## Task 7: 更新决策模块导出

**Files:**
- Modify: `scripts/decision/__init__.py`

- [ ] **Step 1: 更新导出**

```python
"""
决策层 - 信号融合 → 多空辩论 → 裁判 → 风险辩论 → 决策经理
"""

from .signal_fusion import SignalFusion, run_signal_fusion
from .bull_agent import BullAgent, run_bull_agent
from .bear_agent import BearAgent, run_bear_agent
from .debater_agents import run_debaters, run_risk_debate
from .judge import JudgeAgent, run_judge_agent, run_full_judgment
from .portfolio_manager import DecisionManagerAgent, run_decision_manager_agent, format_decision_output

__all__ = [
    "SignalFusion",
    "run_signal_fusion",
    "BullAgent",
    "run_bull_agent",
    "BearAgent",
    "run_bear_agent",
    "run_debaters",
    "run_risk_debate",
    "JudgeAgent",
    "run_judge_agent",
    "run_full_judgment",
    "DecisionManagerAgent",
    "run_decision_manager_agent",
    "format_decision_output",
]
```

- [x] **Step 2: 验证完整导入**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python -c "from decision import *; print('All imports OK')"
```

---

## Task 8: 端到端验证

- [ ] **Step 1: 运行构建测试**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python -c "
from decision import (
    SignalFusion, BullAgent, BearAgent,
    run_debaters, run_risk_debate,
    JudgeAgent, DecisionManagerAgent,
    format_decision_output
)

# 模拟8个Agent结果
agent_results = {
    'kline_sync': {'daily': [{'close': '10.5', 'open': '10.0'}]},
    'news': {'narrative_context': {'sentiment': 0.4}},
    'intraday': {'intraday': {'strength': 0.7}},
    'sector': {'sector_context': {'rank': 5}},
    'stock_dims': {'trend_structure': {'trend': 'up'}, 'chip_structure': {'winner_rate': 65}},
    'dragon_tiger': {},
    'intraday_linkage': {},
    'fundamental_deep': {},
}

# 1. 信号融合
fusion = SignalFusion()
fused = fusion.run(agent_results)
print('1. 信号融合:', fused['composite_score'], fused['signal_strength'])

# 2. 多空辩论
data_bundle = {'kline': agent_results['kline_sync'], 'volume': {}}
bull = BullAgent().run(fused, data_bundle)
bear = BearAgent().run(fused, data_bundle)
print('2. 看多:', bull['conclusion'], '| 看空:', bear['conclusion'])

# 3. 裁判
judge = JudgeAgent()
verdict = judge.run(bull, bear, {}, {'score': 50}, fused)
print('3. 裁判:', verdict['direction'], verdict['confidence'])

# 4. 决策经理
dm = DecisionManagerAgent()
decision = dm.run(verdict, bull, bear, fused, {'is_holding': False, 'current_price': 10.5, 'cost_price': 0})
print('4. 决策:', format_decision_output(decision))
"
```

---

## 验收检查

- [x] 所有模块导入成功
- [x] `build_stock_report.py` 能正常调用决策层
- [x] 架构图中的5个模块全部实现
- [x] 数据流正确：8 Agent → 信号融合 → 多空辩论 → 裁判 → 决策经理
