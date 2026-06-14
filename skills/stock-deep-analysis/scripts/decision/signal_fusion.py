"""
信号融合器 - Signal Fusion
汇总8个Agent的信号，生成融合信号供多空辩论Agent使用
"""

import json
import logging
from typing import Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FusedSignals:
    """融合后的信号"""
    composite_score: float  # 综合评分 0-100
    bullish_signals: List[Dict[str, Any]]  # 看涨信号列表
    bearish_signals: List[Dict[str, Any]]  # 看跌信号列表
    signal_strength: str  # strong/medium/weak
    confidence: float  # 0-1
    key_factors: List[str]  # 关键因素
    raw_scores: Dict[str, float]  # 各维度原始评分


class SignalFusion:
    """
    信号融合器
    
    职责：
    1. 汇总8个Agent的信号
    2. 计算综合评分
    3. 提取看涨/看跌信号
    4. 生成融合信号供多空辩论Agent使用
    """
    
    name = "signal_fusion"
    role = "信号融合器"
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def run(self, agent_results: Dict[str, Any]) -> Dict[str, Any]:
        """
        运行信号融合器
        
        Args:
            agent_results: 8个Agent的结果
                - kline_sync: K线同步
                - news: 新闻
                - intraday: 分时
                - sector: 板块
                - stock_dims: 股票维度
                - dragon_tiger: 龙虎榜
                - intraday_linkage: 分时联动
                - fundamental_deep: 基本面
        
        Returns:
            融合信号
        """
        self.logger.info("Running Signal Fusion...")
        
        # 提取各Agent信号
        kline_signals = self._extract_kline_signals(agent_results.get("kline_sync", {}))
        news_signals = self._extract_news_signals(agent_results.get("news", {}))
        intraday_signals = self._extract_intraday_signals(agent_results.get("intraday", {}))
        sector_signals = self._extract_sector_signals(agent_results.get("sector", {}))
        dims_signals = self._extract_dims_signals(agent_results.get("stock_dims", {}))
        dragon_signals = self._extract_dragon_signals(agent_results.get("dragon_tiger", {}))
        linkage_signals = self._extract_linkage_signals(agent_results.get("intraday_linkage", {}))
        fundamental_signals = self._extract_fundamental_signals(agent_results.get("fundamental_deep", {}))
        
        # 合并所有信号
        all_signals = {
            "kline": kline_signals,
            "news": news_signals,
            "intraday": intraday_signals,
            "sector": sector_signals,
            "dims": dims_signals,
            "dragon": dragon_signals,
            "linkage": linkage_signals,
            "fundamental": fundamental_signals,
        }
        
        # 分离看涨/看跌信号
        bullish_signals = []
        bearish_signals = []
        
        for source, signal in all_signals.items():
            if signal.get("bias") == "bullish":
                bullish_signals.append({"source": source, **signal})
            elif signal.get("bias") == "bearish":
                bearish_signals.append({"source": source, **signal})
        
        # 计算综合评分
        composite_score = self._calculate_composite_score(all_signals)
        
        # 判断信号强度
        signal_strength = self._determine_strength(composite_score, len(bullish_signals), len(bearish_signals))
        
        # 计算置信度
        confidence = self._calculate_confidence(all_signals)
        
        # 提取关键因素
        key_factors = self._extract_key_factors(all_signals)
        
        # 原始评分
        raw_scores = {k: v.get("score", 50) for k, v in all_signals.items()}
        
        result = {
            "agent": self.name,
            "role": self.role,
            "composite_score": composite_score,
            "bullish_signals": bullish_signals,
            "bearish_signals": bearish_signals,
            "signal_strength": signal_strength,
            "confidence": confidence,
            "key_factors": key_factors,
            "raw_scores": raw_scores,
            "all_signals": all_signals,
        }
        
        self.logger.info(f"Signal Fusion: score={composite_score:.1f}, bullish={len(bullish_signals)}, bearish={len(bearish_signals)}")
        
        return result
    
    def _extract_kline_signals(self, kline_data: Dict[str, Any]) -> Dict[str, Any]:
        """提取K线信号"""
        if not kline_data.get("daily"):
            return {"score": 50, "bias": "neutral"}
        
        daily = kline_data["daily"]
        if len(daily) < 5:
            return {"score": 50, "bias": "neutral"}
        
        latest = daily[-1]
        score = 50
        bias = "neutral"
        
        # 阳线
        if latest["close"] > latest["open"]:
            body_ratio = (latest["close"] - latest["open"]) / latest["open"]
            if body_ratio > 0.03:
                score += 15
                bias = "bullish"
        # 阴线
        elif latest["close"] < latest["open"]:
            body_ratio = (latest["open"] - latest["close"]) / latest["open"]
            if body_ratio > 0.03:
                score -= 15
                bias = "bearish"
        
        return {"score": max(0, min(100, score)), "bias": bias}
    
    def _extract_news_signals(self, news_data: Dict[str, Any]) -> Dict[str, Any]:
        """提取新闻信号"""
        if not news_data.get("narrative_context"):
            return {"score": 50, "bias": "neutral"}
        
        narrative = news_data["narrative_context"]
        score = 50
        bias = "neutral"
        
        sentiment = narrative.get("sentiment", 0)
        if sentiment > 0.3:
            score += 10
            bias = "bullish"
        elif sentiment < -0.3:
            score -= 10
            bias = "bearish"
        
        return {"score": max(0, min(100, score)), "bias": bias}
    
    def _extract_intraday_signals(self, intraday_data: Dict[str, Any]) -> Dict[str, Any]:
        """提取分时信号"""
        if not intraday_data.get("intraday"):
            return {"score": 50, "bias": "neutral"}
        
        intraday = intraday_data["intraday"]
        score = 50
        bias = "neutral"
        
        # 分时强度
        strength = intraday.get("strength", 0)
        if strength > 0.6:
            score += 10
            bias = "bullish"
        elif strength < 0.4:
            score -= 10
            bias = "bearish"
        
        return {"score": max(0, min(100, score)), "bias": bias}
    
    def _extract_sector_signals(self, sector_data: Dict[str, Any]) -> Dict[str, Any]:
        """提取板块信号"""
        if not sector_data.get("sector_context"):
            return {"score": 50, "bias": "neutral"}
        
        sector = sector_data["sector_context"]
        score = 50
        bias = "neutral"
        
        rank = sector.get("rank", 50)
        if rank <= 10:
            score += 10
            bias = "bullish"
        elif rank >= 40:
            score -= 10
            bias = "bearish"
        
        return {"score": max(0, min(100, score)), "bias": bias}
    
    def _extract_dims_signals(self, dims_data: Dict[str, Any]) -> Dict[str, Any]:
        """提取股票维度信号"""
        if not dims_data:
            return {"score": 50, "bias": "neutral"}
        
        score = 50
        bias = "neutral"
        
        # 趋势结构
        trend = dims_data.get("trend_structure", {})
        if trend.get("trend") == "up":
            score += 5
            bias = "bullish"
        elif trend.get("trend") == "down":
            score -= 5
            bias = "bearish"
        
        # 筹码结构
        chip = dims_data.get("chip_structure", {})
        winner_rate = chip.get("winner_rate", 50)
        if winner_rate > 70:
            score += 5
        elif winner_rate < 30:
            score -= 5
        
        return {"score": max(0, min(100, score)), "bias": bias}
    
    def _extract_dragon_signals(self, dragon_data: Dict[str, Any]) -> Dict[str, Any]:
        """提取龙虎榜信号"""
        if not dragon_data.get("dragon_tiger"):
            return {"score": 50, "bias": "neutral"}
        
        dt = dragon_data["dragon_tiger"]
        score = 50
        bias = "neutral"
        
        # 机构买卖
        inst_buy = dt.get("inst_buy_amount", 0)
        inst_sell = dt.get("inst_sell_amount", 0)
        
        if inst_buy > inst_sell:
            score += 10
            bias = "bullish"
        elif inst_sell > inst_buy:
            score -= 10
            bias = "bearish"
        
        return {"score": max(0, min(100, score)), "bias": bias}
    
    def _extract_linkage_signals(self, linkage_data: Dict[str, Any]) -> Dict[str, Any]:
        """提取联动信号"""
        if not linkage_data:
            return {"score": 50, "bias": "neutral"}
        
        score = 50
        bias = "neutral"
        
        label = linkage_data.get("linkage_label", "")
        if "强" in label or "领涨" in label:
            score += 5
            bias = "bullish"
        elif "弱" in label or "领跌" in label:
            score -= 5
            bias = "bearish"
        
        return {"score": max(0, min(100, score)), "bias": bias}
    
    def _extract_fundamental_signals(self, fund_data: Dict[str, Any]) -> Dict[str, Any]:
        """提取基本面信号"""
        if not fund_data:
            return {"score": 50, "bias": "neutral"}
        
        score = 50
        bias = "neutral"
        
        health = fund_data.get("financial_health", "")
        if "健康" in health or "良好" in health:
            score += 5
            bias = "bullish"
        elif "风险" in health or "不佳" in health:
            score -= 5
            bias = "bearish"
        
        return {"score": max(0, min(100, score)), "bias": bias}
    
    def _calculate_composite_score(self, all_signals: Dict[str, Any]) -> float:
        """计算综合评分"""
        if not all_signals:
            return 50
        
        total = 0
        count = 0
        
        # 权重
        weights = {
            "kline": 1.5,
            "news": 1.2,
            "intraday": 1.0,
            "sector": 1.0,
            "dims": 1.3,
            "dragon": 0.8,
            "linkage": 0.7,
            "fundamental": 1.0,
        }
        
        for source, signal in all_signals.items():
            weight = weights.get(source, 1.0)
            total += signal.get("score", 50) * weight
            count += weight
        
        return total / count if count > 0 else 50
    
    def _determine_strength(self, score: float, bullish_count: int, bearish_count: int) -> str:
        """判断信号强度"""
        if score > 65 or bullish_count >= 5:
            return "strong"
        elif score < 35 or bearish_count >= 5:
            return "strong"
        elif abs(score - 50) > 10:
            return "medium"
        else:
            return "weak"
    
    def _calculate_confidence(self, all_signals: Dict[str, Any]) -> float:
        """计算置信度"""
        if not all_signals:
            return 0.5
        
        # 信号一致性
        biases = [s.get("bias", "neutral") for s in all_signals.values()]
        bullish_count = biases.count("bullish")
        bearish_count = biases.count("bearish")
        total = len(biases)
        
        if total == 0:
            return 0.5
        
        max_count = max(bullish_count, bearish_count)
        return max_count / total
    
    def _extract_key_factors(self, all_signals: Dict[str, Any]) -> List[str]:
        """提取关键因素"""
        factors = []
        
        for source, signal in all_signals.items():
            if signal.get("bias") != "neutral":
                factors.append(f"{source}: {signal['bias']}")
        
        return factors[:5]


def run_signal_fusion(agent_results: Dict[str, Any]) -> Dict[str, Any]:
    """
    运行信号融合器的便捷函数
    
    Args:
        agent_results: 8个Agent的结果
    
    Returns:
        融合信号
    """
    fusion = SignalFusion()
    return fusion.run(agent_results)
