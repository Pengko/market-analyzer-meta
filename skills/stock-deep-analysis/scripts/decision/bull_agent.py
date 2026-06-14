"""
看多Agent - Bull Agent
专注于寻找看涨信号，构建看涨论据
"""

import json
import logging
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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
    
    职责：
    1. 接收信号融合器的融合信号
    2. 专注于寻找看涨信号
    3. 构建看涨论据
    4. 输出看多报告
    """
    
    name = "bull"
    role = "看多Agent"
    
    def __init__(self):
        self.logger = logging.getLogger(__name__)
    
    def run(
        self,
        fused_signals: Dict[str, Any],
        data_bundle: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        运行看多Agent
        
        Args:
            fused_signals: 信号融合器的融合信号
            data_bundle: 原始数据包
        
        Returns:
            看多报告
        """
        self.logger.info("Running Bull Agent...")
        
        # 从融合信号中提取看涨信号
        bullish_from_fusion = fused_signals.get("bullish_signals", [])
        
        # 基于原始数据构建看涨论据
        arguments = []
        
        # 1. K线形态分析
        kline_args = self._analyze_kline_bullish(data_bundle.get("kline", {}))
        arguments.extend(kline_args)
        
        # 2. 量能分析
        volume_args = self._analyze_volume_bullish(data_bundle.get("volume", {}))
        arguments.extend(volume_args)
        
        # 3. 筹码分析
        chips_args = self._analyze_chips_bullish(data_bundle.get("chip_structure", {}))
        arguments.extend(chips_args)
        
        # 4. 板块分析
        sector_args = self._analyze_sector_bullish(data_bundle.get("sector_context", {}))
        arguments.extend(sector_args)
        
        # 5. 新闻/事件分析
        news_args = self._analyze_news_bullish(data_bundle.get("news_sentiment", {}))
        arguments.extend(news_args)
        
        # 6. 基本面分析
        fund_args = self._analyze_fundamental_bullish(data_bundle.get("fundamental", {}))
        arguments.extend(fund_args)
        
        # 7. 分时分析
        intraday_args = self._analyze_intraday_bullish(data_bundle.get("intraday", {}))
        arguments.extend(intraday_args)
        
        # 8. 龙虎榜分析
        dragon_args = self._analyze_dragon_tiger_bullish(data_bundle.get("dragon_tiger", {}))
        arguments.extend(dragon_args)
        
        # 计算总分
        score = self._calculate_bull_score(arguments)
        confidence = self._calculate_confidence(arguments)
        
        # 提取关键价位
        key_levels = self._extract_key_levels(
            data_bundle.get("kline", {}),
            data_bundle.get("chip_structure", {}),
        )
        
        # 识别催化剂
        catalysts = self._identify_catalysts(
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
            "catalysts": catalysts,
            "bullish_from_fusion": bullish_from_fusion,
        }
        
        self.logger.info(f"Bull Agent: score={score:.1f}, arguments={len(arguments)}")
        
        return result
    
    def _analyze_kline_bullish(self, kline_data: Dict[str, Any]) -> List[BullArgument]:
        """K线形态看涨分析"""
        args = []
        
        if not kline_data.get("daily"):
            return args
        
        daily = kline_data["daily"]
        if len(daily) < 5:
            return args
        
        latest = daily[-1]
        
        # 阳线
        if latest["close"] > latest["open"]:
            body_ratio = (latest["close"] - latest["open"]) / latest["open"]
            if body_ratio > 0.03:
                args.append(BullArgument(
                    signal="K线_大阳线",
                    strength=min(body_ratio * 10, 1.0),
                    description=f"收出大阳线，涨幅{body_ratio*100:.1f}%",
                    evidence=f"开盘{latest['open']:.2f}，收盘{latest['close']:.2f}",
                ))
        
        # 突破前高
        if len(daily) >= 20:
            high_20 = max(d["high"] for d in daily[-20:])
            if latest["close"] > high_20 * 0.98:
                args.append(BullArgument(
                    signal="K线_突破前高",
                    strength=0.8,
                    description="突破近20日高点",
                    evidence=f"当前{latest['close']:.2f} > 前高{high_20:.2f}",
                ))
        
        # 下影线（支撑）
        lower_shadow = min(latest["open"], latest["close"]) - latest["low"]
        body = abs(latest["close"] - latest["open"])
        if body > 0 and lower_shadow / body > 1.5:
            args.append(BullArgument(
                signal="K线_长下影线",
                strength=0.6,
                description="长下影线显示下方支撑强劲",
                evidence=f"下影线{lower_shadow:.2f}，实体{body:.2f}",
            ))
        
        return args
    
    def _analyze_volume_bullish(self, volume_data: Dict[str, Any]) -> List[BullArgument]:
        """量能看涨分析"""
        args = []
        
        if not volume_data.get("moneyflow"):
            return args
        
        mf = volume_data["moneyflow"]
        if len(mf) < 5:
            return args
        
        latest = mf[-1]
        
        # 主力净流入
        if latest.get("net_mf_amount", 0) > 0:
            net_ratio = latest["net_mf_amount"] / latest.get("buy_lg_amount", 1)
            if net_ratio > 0.3:
                args.append(BullArgument(
                    signal="量能_主力净流入",
                    strength=min(net_ratio * 2, 1.0),
                    description="主力资金大幅净流入",
                    evidence=f"净流入{latest['net_mf_amount']/10000:.1f}万",
                ))
        
        # 量比放大
        if latest.get("volume_ratio", 1) > 2:
            args.append(BullArgument(
                signal="量能_量比放大",
                strength=min(latest["volume_ratio"] / 5, 1.0),
                description=f"量比{latest['volume_ratio']:.1f}，成交活跃",
                evidence=f"量比>{2}",
            ))
        
        return args
    
    def _analyze_chips_bullish(self, chips_data: Dict[str, Any]) -> List[BullArgument]:
        """筹码看涨分析"""
        args = []
        
        if not chips_data.get("cyq"):
            return args
        
        cyq = chips_data["cyq"]
        
        # 获利盘比例高
        if cyq.get("winner_rate", 0) > 70:
            args.append(BullArgument(
                signal="筹码_高获利盘",
                strength=cyq["winner_rate"] / 100,
                description=f"获利盘比例{cyq['winner_rate']:.1f}%，持仓信心强",
                evidence=f"winner_rate={cyq['winner_rate']:.1f}%",
            ))
        
        # 价格接近成本中位数
        if cyq.get("cost_50pct"):
            price = cyq.get("price", 0)
            if price > 0:
                deviation = abs(price - cyq["cost_50pct"]) / cyq["cost_50pct"]
                if deviation < 0.05:
                    args.append(BullArgument(
                        signal="筹码_成本支撑",
                        strength=0.7,
                        description="价格接近筹码密集区，成本支撑强",
                        evidence=f"当前{price:.2f}，成本中位{cyq['cost_50pct']:.2f}",
                    ))
        
        return args
    
    def _analyze_sector_bullish(self, sector_data: Dict[str, Any]) -> List[BullArgument]:
        """板块看涨分析"""
        args = []
        
        if not sector_data.get("sector"):
            return args
        
        sector = sector_data["sector"]
        
        # 板块涨幅排名
        if sector.get("rank", 999) <= 5:
            args.append(BullArgument(
                signal="板块_排名靠前",
                strength=(5 - sector["rank"]) / 5,
                description=f"板块涨幅排名第{sector['rank']}位",
                evidence=f"rank={sector['rank']}",
            ))
        
        # 板块资金流入
        if sector.get("net_amount", 0) > 0:
            args.append(BullArgument(
                signal="板块_资金流入",
                strength=min(sector["net_amount"] / 100000, 1.0),
                description="板块资金净流入",
                evidence=f"净流入{sector['net_amount']/10000:.1f}万",
            ))
        
        return args
    
    def _analyze_news_bullish(self, news_data: Dict[str, Any]) -> List[BullArgument]:
        """新闻看涨分析"""
        args = []
        
        if not news_data.get("events"):
            return args
        
        for event in news_data["events"][:3]:
            # 利好事件
            if any(kw in event.get("title", "") for kw in ["利好", "增长", "突破", "合作", "中标"]):
                args.append(BullArgument(
                    signal="新闻_利好事件",
                    strength=0.7,
                    description=event.get("title", "")[:50],
                    evidence=event.get("source", ""),
                ))
        
        return args
    
    def _analyze_fundamental_bullish(self, fund_data: Dict[str, Any]) -> List[BullArgument]:
        """基本面看涨分析"""
        args = []
        
        if not fund_data.get("financial"):
            return args
        
        fin = fund_data["financial"]
        
        # 营收增长
        if fin.get("revenue_yoy", 0) > 20:
            args.append(BullArgument(
                signal="基本面_营收高增长",
                strength=min(fin["revenue_yoy"] / 50, 1.0),
                description=f"营收同比增长{fin['revenue_yoy']:.1f}%",
                evidence=f"revenue_yoy={fin['revenue_yoy']:.1f}%",
            ))
        
        # 净利润增长
        if fin.get("netprofit_yoy", 0) > 30:
            args.append(BullArgument(
                signal="基本面_净利润高增长",
                strength=min(fin["netprofit_yoy"] / 60, 1.0),
                description=f"净利润同比增长{fin['netprofit_yoy']:.1f}%",
                evidence=f"netprofit_yoy={fin['netprofit_yoy']:.1f}%",
            ))
        
        return args
    
    def _analyze_intraday_bullish(self, intraday_data: Dict[str, Any]) -> List[BullArgument]:
        """分时看涨分析"""
        args = []
        
        if not intraday_data.get("tick"):
            return args
        
        tick = intraday_data["tick"]
        
        # 大单买入占比高
        if tick.get("buy_lg_ratio", 0) > 0.3:
            args.append(BullArgument(
                signal="分时_大单买入",
                strength=tick["buy_lg_ratio"],
                description="大单买入占比高",
                evidence=f"大单买入{tick['buy_lg_ratio']*100:.1f}%",
            ))
        
        return args
    
    def _analyze_dragon_tiger_bullish(self, dragon_data: Dict[str, Any]) -> List[BullArgument]:
        """龙虎榜看涨分析"""
        args = []
        
        if not dragon_data.get("dragon_tiger"):
            return args
        
        dt = dragon_data["dragon_tiger"]
        
        # 机构买入
        if dt.get("inst_buy_amount", 0) > dt.get("inst_sell_amount", 0):
            net = dt["inst_buy_amount"] - dt["inst_sell_amount"]
            args.append(BullArgument(
                signal="龙虎榜_机构净买",
                strength=min(net / 100000, 1.0),
                description="龙虎榜机构净买入",
                evidence=f"机构净买{net/10000:.1f}万",
            ))
        
        return args
    
    def _calculate_bull_score(self, arguments: List[BullArgument]) -> float:
        """计算看多总分 (0-100)"""
        if not arguments:
            return 30  # 基础分
        
        total = sum(a.strength for a in arguments)
        avg = total / len(arguments)
        count_bonus = min(len(arguments) * 3, 20)
        
        return min(avg * 60 + count_bonus, 100)
    
    def _calculate_confidence(self, arguments: List[BullArgument]) -> float:
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
        
        # 筹码支撑
        if chips_data.get("cyq"):
            cyq = chips_data["cyq"]
            if cyq.get("cost_50pct"):
                levels["support"].append(cyq["cost_50pct"])
            if cyq.get("cost_85pct"):
                levels["resistance"].append(cyq["cost_85pct"])
        
        return levels
    
    def _identify_catalysts(
        self,
        news_data: Dict[str, Any],
        sector_data: Dict[str, Any],
        fund_data: Dict[str, Any],
    ) -> List[str]:
        """识别催化剂"""
        catalysts = []
        
        if news_data.get("events"):
            for event in news_data["events"][:2]:
                catalysts.append(event.get("title", "")[:50])
        
        if sector_data.get("sector", {}).get("catalyst"):
            catalysts.append(sector_data["sector"]["catalyst"])
        
        return catalysts[:5]


def run_bull_agent(
    fused_signals: Dict[str, Any],
    data_bundle: Dict[str, Any],
) -> Dict[str, Any]:
    """
    运行看多Agent的便捷函数
    
    Args:
        fused_signals: 信号融合器的融合信号
        data_bundle: 原始数据包
    
    Returns:
        看多报告
    """
    agent = BullAgent()
    return agent.run(fused_signals, data_bundle)
