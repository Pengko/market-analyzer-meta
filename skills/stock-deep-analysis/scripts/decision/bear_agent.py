"""
看空Agent - Bear Agent
专注于寻找看跌信号，构建看跌论据
"""

import json
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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
    
    职责：
    1. 接收信号融合器的融合信号
    2. 专注于寻找看跌信号
    3. 构建看跌论据
    4. 输出看空报告
    """
    
    name = "bear"
    role = "看空Agent"
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def run(
        self,
        fused_signals: Dict[str, Any],
        data_bundle: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        运行看空Agent
        
        Args:
            fused_signals: 信号融合器的融合信号
            data_bundle: 原始数据包
        
        Returns:
            看空报告
        """
        self.logger.info("Running Bear Agent...")
        
        # 从融合信号中提取看跌信号
        bearish_from_fusion = fused_signals.get("bearish_signals", [])
        
        # 基于原始数据构建看跌论据
        arguments = []
        
        # 1. K线形态分析
        kline_args = self._analyze_kline_bearish(data_bundle.get("kline", {}))
        arguments.extend(kline_args)
        
        # 2. 量能分析
        volume_args = self._analyze_volume_bearish(data_bundle.get("volume", {}))
        arguments.extend(volume_args)
        
        # 3. 筹码分析
        chips_args = self._analyze_chips_bearish(data_bundle.get("chip_structure", {}))
        arguments.extend(chips_args)
        
        # 4. 板块分析
        sector_args = self._analyze_sector_bearish(data_bundle.get("sector_context", {}))
        arguments.extend(sector_args)
        
        # 5. 新闻/事件分析
        news_args = self._analyze_news_bearish(data_bundle.get("news_sentiment", {}))
        arguments.extend(news_args)
        
        # 6. 基本面分析
        fund_args = self._analyze_fundamental_bearish(data_bundle.get("fundamental", {}))
        arguments.extend(fund_args)
        
        # 7. 分时分析
        intraday_args = self._analyze_intraday_bearish(data_bundle.get("intraday", {}))
        arguments.extend(intraday_args)
        
        # 8. 龙虎榜分析
        dragon_args = self._analyze_dragon_tiger_bearish(data_bundle.get("dragon_tiger", {}))
        arguments.extend(dragon_args)
        
        # 计算总分
        score = self._calculate_bear_score(arguments)
        confidence = self._calculate_confidence(arguments)
        
        # 提取关键价位
        key_levels = self._extract_key_levels(
            data_bundle.get("kline", {}),
            data_bundle.get("chip_structure", {}),
        )
        
        # 识别风险点
        risks = self._identify_risks(
            data_bundle.get("news_sentiment", {}),
            data_bundle.get("sector_context", {}),
            data_bundle.get("fundamental", {}),
        )
        
        result = {
            "agent": self.name,
            "role": self.role,
            "score": score,
            "confidence": confidence,
            "arguments": [vars(a) for a in arguments],
            "argument_count": len(arguments),
            "key_levels": key_levels,
            "risks": risks,
            "bearish_from_fusion": bearish_from_fusion,
        }
        
        self.logger.info(f"Bear Agent: score={score:.1f}, arguments={len(arguments)}")
        
        return result
    
    def _analyze_kline_bearish(self, kline_data: Dict[str, Any]) -> List[BearArgument]:
        """K线形态看跌分析"""
        args = []
        
        if not kline_data.get("daily"):
            return args
        
        daily = kline_data["daily"]
        if len(daily) < 5:
            return args
        
        latest = daily[-1]
        
        # 阴线
        if latest["close"] < latest["open"]:
            body_ratio = (latest["open"] - latest["close"]) / latest["open"]
            if body_ratio > 0.03:
                args.append(BearArgument(
                    signal="K线_大阴线",
                    strength=min(body_ratio * 10, 1.0),
                    description=f"收出大阴线，跌幅{body_ratio*100:.1f}%",
                    evidence=f"开盘{latest['open']:.2f}，收盘{latest['close']:.2f}",
                ))
        
        # 跌破前低
        if len(daily) >= 20:
            low_20 = min(d["low"] for d in daily[-20:])
            if latest["close"] < low_20 * 1.02:
                args.append(BearArgument(
                    signal="K线_跌破前低",
                    strength=0.8,
                    description="跌破近20日低点",
                    evidence=f"当前{latest['close']:.2f} < 前低{low_20:.2f}",
                ))
        
        # 上影线（压力）
        upper_shadow = latest["high"] - max(latest["open"], latest["close"])
        body = abs(latest["close"] - latest["open"])
        if body > 0 and upper_shadow / body > 1.5:
            args.append(BearArgument(
                signal="K线_长上影线",
                strength=0.6,
                description="长上影线显示上方压力大",
                evidence=f"上影线{upper_shadow:.2f}，实体{body:.2f}",
            ))
        
        # 连续阴线
        阴线_count = 0
        for d in daily[-5:]:
            if d["close"] < d["open"]:
                阴线_count += 1
        if 阴线_count >= 3:
            args.append(BearArgument(
                signal="K线_连续阴线",
                strength=阴线_count / 5,
                description=f"近5日{阴线_count}根阴线",
                evidence=f"阴线数={阴线_count}",
            ))
        
        return args
    
    def _analyze_volume_bearish(self, volume_data: Dict[str, Any]) -> List[BearArgument]:
        """量能看跌分析"""
        args = []
        
        if not volume_data.get("moneyflow"):
            return args
        
        mf = volume_data["moneyflow"]
        if len(mf) < 5:
            return args
        
        latest = mf[-1]
        
        # 主力净流出
        if latest.get("net_mf_amount", 0) < 0:
            net_ratio = abs(latest["net_mf_amount"]) / latest.get("sell_lg_amount", 1)
            if net_ratio > 0.3:
                args.append(BearArgument(
                    signal="量能_主力净流出",
                    strength=min(net_ratio * 2, 1.0),
                    description="主力资金大幅净流出",
                    evidence=f"净流出{abs(latest['net_mf_amount'])/10000:.1f}万",
                ))
        
        # 放量下跌
        if latest.get("volume_ratio", 1) > 2:
            daily = volume_data.get("daily", [])
            if daily and daily[-1].get("close", 0) < daily[-1].get("open", 0):
                args.append(BearArgument(
                    signal="量能_放量下跌",
                    strength=0.7,
                    description="放量下跌，抛压沉重",
                    evidence=f"量比{latest['volume_ratio']:.1f}",
                ))
        
        return args
    
    def _analyze_chips_bearish(self, chips_data: Dict[str, Any]) -> List[BearArgument]:
        """筹码看跌分析"""
        args = []
        
        if not chips_data.get("cyq"):
            return args
        
        cyq = chips_data["cyq"]
        
        # 套牢盘比例高
        if cyq.get("winner_rate", 100) < 30:
            args.append(BearArgument(
                signal="筹码_高套牢盘",
                strength=(100 - cyq["winner_rate"]) / 100,
                description=f"套牢盘比例{100-cyq['winner_rate']:.1f}%，抛压大",
                evidence=f"winner_rate={cyq['winner_rate']:.1f}%",
            ))
        
        # 价格跌破成本中位数
        if cyq.get("cost_50pct"):
            price = cyq.get("price", 0)
            if price > 0 and price < cyq["cost_50pct"] * 0.95:
                args.append(BearArgument(
                    signal="筹码_跌破成本",
                    strength=0.7,
                    description="价格跌破筹码密集区",
                    evidence=f"当前{price:.2f} < 成本中位{cyq['cost_50pct']:.2f}",
                ))
        
        return args
    
    def _analyze_sector_bearish(self, sector_data: Dict[str, Any]) -> List[BearArgument]:
        """板块看跌分析"""
        args = []
        
        if not sector_data.get("sector"):
            return args
        
        sector = sector_data["sector"]
        
        # 板块跌幅排名
        if sector.get("rank", 0) >= 40:
            args.append(BearArgument(
                signal="板块_排名靠后",
                strength=(sector["rank"] - 40) / 60,
                description=f"板块涨幅排名第{sector['rank']}位",
                evidence=f"rank={sector['rank']}",
            ))
        
        # 板块资金流出
        if sector.get("net_amount", 0) < 0:
            args.append(BearArgument(
                signal="板块_资金流出",
                strength=min(abs(sector["net_amount"]) / 100000, 1.0),
                description="板块资金净流出",
                evidence=f"净流出{abs(sector['net_amount'])/10000:.1f}万",
            ))
        
        return args
    
    def _analyze_news_bearish(self, news_data: Dict[str, Any]) -> List[BearArgument]:
        """新闻看跌分析"""
        args = []
        
        if not news_data.get("events"):
            return args
        
        for event in news_data["events"][:3]:
            # 利空事件
            if any(kw in event.get("title", "") for kw in ["利空", "下跌", "违规", "处罚", "亏损", "减持"]):
                args.append(BearArgument(
                    signal="新闻_利空事件",
                    strength=0.7,
                    description=event.get("title", "")[:50],
                    evidence=event.get("source", ""),
                ))
        
        return args
    
    def _analyze_fundamental_bearish(self, fund_data: Dict[str, Any]) -> List[BearArgument]:
        """基本面看跌分析"""
        args = []
        
        if not fund_data.get("financial"):
            return args
        
        fin = fund_data["financial"]
        
        # 营收下降
        if fin.get("revenue_yoy", 0) < -10:
            args.append(BearArgument(
                signal="基本面_营收下滑",
                strength=min(abs(fin["revenue_yoy"]) / 30, 1.0),
                description=f"营收同比下降{abs(fin['revenue_yoy']):.1f}%",
                evidence=f"revenue_yoy={fin['revenue_yoy']:.1f}%",
            ))
        
        # 净利润下降
        if fin.get("netprofit_yoy", 0) < -20:
            args.append(BearArgument(
                signal="基本面_净利润下滑",
                strength=min(abs(fin["netprofit_yoy"]) / 40, 1.0),
                description=f"净利润同比下降{abs(fin['netprofit_yoy']):.1f}%",
                evidence=f"netprofit_yoy={fin['netprofit_yoy']:.1f}%",
            ))
        
        return args
    
    def _analyze_intraday_bearish(self, intraday_data: Dict[str, Any]) -> List[BearArgument]:
        """分时看跌分析"""
        args = []
        
        if not intraday_data.get("tick"):
            return args
        
        tick = intraday_data["tick"]
        
        # 大单卖出占比高
        if tick.get("sell_lg_ratio", 0) > 0.3:
            args.append(BearArgument(
                signal="分时_大单卖出",
                strength=tick["sell_lg_ratio"],
                description="大单卖出占比高",
                evidence=f"大单卖出{tick['sell_lg_ratio']*100:.1f}%",
            ))
        
        return args
    
    def _analyze_dragon_tiger_bearish(self, dragon_data: Dict[str, Any]) -> List[BearArgument]:
        """龙虎榜看跌分析"""
        args = []
        
        if not dragon_data.get("dragon_tiger"):
            return args
        
        dt = dragon_data["dragon_tiger"]
        
        # 机构卖出
        if dt.get("inst_sell_amount", 0) > dt.get("inst_buy_amount", 0):
            net = dt["inst_sell_amount"] - dt["inst_buy_amount"]
            args.append(BearArgument(
                signal="龙虎榜_机构净卖",
                strength=min(net / 100000, 1.0),
                description="龙虎榜机构净卖出",
                evidence=f"机构净卖{net/10000:.1f}万",
            ))
        
        return args
    
    def _calculate_bear_score(self, arguments: List[BearArgument]) -> float:
        """计算看空总分 (0-100)"""
        if not arguments:
            return 30  # 基础分
        
        total = sum(a.strength for a in arguments)
        avg = total / len(arguments)
        count_bonus = min(len(arguments) * 3, 20)
        
        return min(avg * 60 + count_bonus, 100)
    
    def _calculate_confidence(self, arguments: List[BearArgument]) -> float:
        """计算置信度 (0-1)"""
        if not arguments:
            return 0.3
        
        # 信号一致性
        strong_signals = sum(1 for a in arguments if a.strength > 0.6)
        return min(strong_signals / len(arguments) + 0.3, 1.0)
    
    def _extract_key_levels(
        self,
        kline_data: Dict[str, Any],
        chips_data: Dict[str, Any],
    ) -> Dict[str, List[float]]:
        """提取关键价位"""
        levels = {"support": [], "resistance": []}
        
        # K线支撑/阻力
        if kline_data.get("daily"):
            daily = kline_data["daily"]
            if len(daily) >= 20:
                levels["support"].append(min(d["low"] for d in daily[-20:]))
                levels["resistance"].append(max(d["high"] for d in daily[-20:]))
        
        # 筹码阻力
        if chips_data.get("cyq"):
            cyq = chips_data["cyq"]
            if cyq.get("cost_85pct"):
                levels["resistance"].append(cyq["cost_85pct"])
            if cyq.get("cost_50pct"):
                levels["support"].append(cyq["cost_50pct"])
        
        return levels
    
    def _identify_risks(
        self,
        news_data: Dict[str, Any],
        sector_data: Dict[str, Any],
        fund_data: Dict[str, Any],
    ) -> List[str]:
        """识别风险点"""
        risks = []
        
        if news_data.get("events"):
            for event in news_data["events"][:2]:
                if any(kw in event.get("title", "") for kw in ["利空", "下跌", "违规", "处罚"]):
                    risks.append(event.get("title", "")[:50])
        
        if fund_data.get("financial", {}).get("risk"):
            risks.append(fund_data["financial"]["risk"])
        
        return risks[:5]


def run_bear_agent(
    fused_signals: Dict[str, Any],
    data_bundle: Dict[str, Any],
) -> Dict[str, Any]:
    """
    运行看空Agent的便捷函数
    
    Args:
        fused_signals: 信号融合器的融合信号
        data_bundle: 原始数据包
    
    Returns:
        看空报告
    """
    agent = BearAgent()
    return agent.run(fused_signals, data_bundle)
