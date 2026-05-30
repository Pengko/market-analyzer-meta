#!/usr/bin/env python3
"""
Context Propagation Rules Engine
将市场→板块→个股→分时的上下文传递从文本摘要升级为规则链
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# 配置日志
logger = logging.getLogger(__name__)


@dataclass
class RuleResult:
    """规则执行结果"""
    condition_met: bool
    action: str  # 'support', 'risk', 'neutral', 'downgrade'
    bias_delta: int  # -2, -1, 0, +1, +2
    flags: List[str]
    constraints: List[str]
    reasoning: str


@dataclass
class PropagationChain:
    """传播链结果"""
    market_to_sector: RuleResult
    sector_to_stock: RuleResult
    stock_to_intraday: RuleResult
    news_to_decision: RuleResult  # 新增：消息面规则结果
    overall_bias: int
    action_bias: str  # 'supportive', 'neutral', 'conservative', 'defensive'
    execution_note: str
    support_flags: List[str]
    risk_flags: List[str]
    constraint_score: int


class ContextPropagationRules:
    """上下文传播规则引擎"""
    
    def __init__(self):
        self.rules = self._build_rules()
    
    def _build_rules(self) -> Dict[str, List[dict]]:
        """构建规则库"""
        return {
            'market_to_sector': [
                {
                    'name': '市场偏强+小盘成长',
                    'condition': lambda ctx: (
                        '偏强' in ctx.get('market_bias', '') and
                        ctx.get('size_style', '') == '小盘成长占优'
                    ),
                    'action': 'support',
                    'bias_delta': 2,
                    'flags': ['市场风险偏好对题材扩散友好', '风格偏向小盘成长'],
                    'constraints': [],
                    'reasoning': '市场偏强且小盘成长占优，题材股容错较高'
                },
                {
                    'name': '市场偏强+大盘权重',
                    'condition': lambda ctx: (
                        '偏强' in ctx.get('market_bias', '') and
                        ctx.get('size_style', '') == '大盘权重占优'
                    ),
                    'action': 'neutral',
                    'bias_delta': 1,
                    'flags': ['市场风险偏好对题材扩散友好'],
                    'constraints': ['风格偏向权重，题材弹性票容错下降'],
                    'reasoning': '市场偏强但风格偏权重，题材股需更谨慎'
                },
                {
                    'name': '市场偏弱+小盘成长',
                    'condition': lambda ctx: (
                        '偏弱' in ctx.get('market_bias', '') and
                        ctx.get('size_style', '') == '小盘成长占优'
                    ),
                    'action': 'neutral',
                    'bias_delta': 0,
                    'flags': ['风格偏向小盘成长'],
                    'constraints': ['市场整体偏弱，个股更依赖独立强度'],
                    'reasoning': '市场偏弱但小盘成长占优，需精选个股'
                },
                {
                    'name': '市场偏弱+大盘权重',
                    'condition': lambda ctx: (
                        '偏弱' in ctx.get('market_bias', '') and
                        ctx.get('size_style', '') == '大盘权重占优'
                    ),
                    'action': 'risk',
                    'bias_delta': -2,
                    'flags': [],
                    'constraints': ['市场整体偏弱，风格偏向权重，题材股容错低'],
                    'reasoning': '市场偏弱且风格偏权重，题材股风险较高'
                },
                {
                    'name': '市场中性',
                    'condition': lambda ctx: (
                        not ctx.get('market_bias') or
                        ctx.get('market_bias', '') in ('中性', '')
                    ),
                    'action': 'neutral',
                    'bias_delta': 0,
                    'flags': [],
                    'constraints': [],
                    'reasoning': '市场环境偏中性'
                }
            ],
            
            'sector_to_stock': [
                {
                    'name': '板块可用+龙头/前排',
                    'condition': lambda ctx: (
                        ctx.get('sector_status') == 'available' and
                        ctx.get('target_theme_role', '') in ('题材龙头', '题材前排')
                    ),
                    'action': 'support',
                    'bias_delta': 2,
                    'flags': ['目标股处于龙头/前排位置'],
                    'constraints': [],
                    'reasoning': '板块归因稳定且目标股处于龙头/前排位置'
                },
                {
                    'name': '板块可用+中位/跟风',
                    'condition': lambda ctx: (
                        ctx.get('sector_status') == 'available' and
                        ctx.get('target_theme_role', '') in ('题材中位', '题材跟风')
                    ),
                    'action': 'neutral',
                    'bias_delta': 0,
                    'flags': [],
                    'constraints': ['目标股处于中位/跟风，需要更依赖板块延续'],
                    'reasoning': '板块归因稳定但目标股处于中位/跟风，需观察板块延续性'
                },
                {
                    'name': '板块降级可用',
                    'condition': lambda ctx: (
                        ctx.get('sector_status') in ('fallback_available', 'browser_preferred')
                    ),
                    'action': 'risk',
                    'bias_delta': -1,
                    'flags': [],
                    'constraints': ['板块归因存在回退或纠偏，题材判断需保守'],
                    'reasoning': '板块归因不稳定，需要保守处理'
                },
                {
                    'name': '板块不可用',
                    'condition': lambda ctx: (
                        ctx.get('sector_status') not in ('available', 'fallback_available', 'browser_preferred')
                    ),
                    'action': 'downgrade',
                    'bias_delta': -2,
                    'flags': [],
                    'constraints': ['板块层未稳定命中，暂不宜给激进动作'],
                    'reasoning': '板块归因缺失，无法确认题材逻辑'
                }
            ],
            
            'stock_to_intraday': [
                {
                    'name': '竞价偏积极+分时偏强',
                    'condition': lambda ctx: (
                        ctx.get('auction_overall', '') in ('偏主动抢筹', '偏积极试盘') and
                        ctx.get('intraday_score', 0) >= 2
                    ),
                    'action': 'support',
                    'bias_delta': 2,
                    'flags': ['竞价偏积极', '分时承接偏强'],
                    'constraints': [],
                    'reasoning': '竞价和分时均显示主力积极'
                },
                {
                    'name': '竞价偏弱+分时偏弱',
                    'condition': lambda ctx: (
                        ctx.get('auction_overall', '') in ('偏兑现离场', '偏谨慎观望') and
                        ctx.get('intraday_score', 0) < 0
                    ),
                    'action': 'risk',
                    'bias_delta': -2,
                    'flags': [],
                    'constraints': ['竞价偏弱', '分时承接偏弱'],
                    'reasoning': '竞价和分时均显示主力谨慎'
                },
                {
                    'name': '次日预期分歧',
                    'condition': lambda ctx: (
                        '分歧' in ctx.get('next_day_label', '')
                    ),
                    'action': 'risk',
                    'bias_delta': -1,
                    'flags': [],
                    'constraints': ['次日预期为分歧，动作应更保守'],
                    'reasoning': '次日预期存在分歧，需提高确认门槛'
                },
                {
                    'name': '次日预期偏强',
                    'condition': lambda ctx: (
                        any(keyword in ctx.get('next_day_label', '') for keyword in ('延续', '强', '修复'))
                    ),
                    'action': 'support',
                    'bias_delta': 1,
                    'flags': ['次日预期存在延续窗口'],
                    'constraints': [],
                    'reasoning': '次日预期偏强，可适度积极'
                },
                {
                    'name': '对标领先',
                    'condition': lambda ctx: (
                        ctx.get('peer_position', '') == '领先'
                    ),
                    'action': 'support',
                    'bias_delta': 1,
                    'flags': ['对标联动相对领先'],
                    'constraints': [],
                    'reasoning': '目标股在板块中处于领先位置'
                },
                {
                    'name': '对标掉队',
                    'condition': lambda ctx: (
                        ctx.get('peer_position', '') == '掉队'
                    ),
                    'action': 'risk',
                    'bias_delta': -1,
                    'flags': [],
                    'constraints': ['对标联动掉队，追价风险升高'],
                    'reasoning': '目标股在板块中处于掉队位置'
                }
            ],
            
            'news_to_decision': [
                {
                    'name': '新催化+高可信度',
                    'condition': lambda ctx: (
                        ctx.get('news_status') == 'available' and
                        ctx.get('news_is_new') is True and
                        ctx.get('news_credibility', '') in ('公告实锤', '主流媒体')
                    ),
                    'action': 'support',
                    'bias_delta': 1,
                    'flags': ['消息面存在新催化'],
                    'constraints': [],
                    'reasoning': '消息面有新催化且可信度高，可适度积极'
                },
                {
                    'name': '旧消息重炒',
                    'condition': lambda ctx: (
                        ctx.get('news_status') == 'available' and
                        ctx.get('news_is_new') is False
                    ),
                    'action': 'neutral',
                    'bias_delta': 0,
                    'flags': [],
                    'constraints': ['消息面为旧消息重炒，需警惕预期兑现'],
                    'reasoning': '消息面为旧消息重炒，持续性存疑'
                },
                {
                    'name': '消息面缺失',
                    'condition': lambda ctx: (
                        ctx.get('news_status') not in ('available', 'fallback_available')
                    ),
                    'action': 'neutral',
                    'bias_delta': 0,
                    'flags': [],
                    'constraints': ['消息面未确认，需更依赖盘面信号'],
                    'reasoning': '消息面缺失或不可用'
                }
            ]
        }
    
    def evaluate_chain(self, context: Dict[str, Any]) -> PropagationChain:
        """评估完整的传播链"""
        logger.info("开始评估上下文传播链")
        
        # 1. 市场→板块
        market_to_sector = self._evaluate_rules('market_to_sector', context)
        
        # 2. 板块→个股
        sector_to_stock = self._evaluate_rules('sector_to_stock', context)
        
        # 3. 个股→分时
        stock_to_intraday = self._evaluate_rules('stock_to_intraday', context)
        
        # 4. 消息面→决策 (新增)
        news_to_decision = self._evaluate_rules('news_to_decision', context)
        
        # 5. 计算总体偏见 (包含消息面)
        overall_bias = (
            market_to_sector.bias_delta +
            sector_to_stock.bias_delta +
            stock_to_intraday.bias_delta +
            news_to_decision.bias_delta
        )
        
        # 6. 确定行动偏向
        action_bias, execution_note = self._determine_action_bias(overall_bias, context)
        
        # 7. 收集支持/风险标志
        support_flags = []
        risk_flags = []
        
        for result in [market_to_sector, sector_to_stock, stock_to_intraday, news_to_decision]:
            if result.action in ('support',):
                support_flags.extend(result.flags)
            elif result.action in ('risk', 'downgrade'):
                risk_flags.extend(result.flags)
            risk_flags.extend(result.constraints)
        
        # 8. 检测冲突信号
        conflict_flags = self._detect_conflicts(market_to_sector, sector_to_stock, stock_to_intraday, news_to_decision)
        risk_flags.extend(conflict_flags)
        
        # 9. 去重
        support_flags = list(dict.fromkeys(support_flags))
        risk_flags = list(dict.fromkeys(risk_flags))
        
        logger.info(f"传播链评估完成: overall_bias={overall_bias}, action_bias={action_bias}")
        
        return PropagationChain(
            market_to_sector=market_to_sector,
            sector_to_stock=sector_to_stock,
            stock_to_intraday=stock_to_intraday,
            news_to_decision=news_to_decision,
            overall_bias=overall_bias,
            action_bias=action_bias,
            execution_note=execution_note,
            support_flags=support_flags,
            risk_flags=risk_flags,
            constraint_score=overall_bias
        )
    
    def _evaluate_rules(self, rule_group: str, context: Dict[str, Any]) -> RuleResult:
        """评估规则组"""
        rules = self.rules.get(rule_group, [])
        logger.debug(f"评估规则组 '{rule_group}'，共 {len(rules)} 条规则")
        
        for rule in rules:
            try:
                if rule['condition'](context):
                    logger.info(f"规则命中: {rule_group}/{rule['name']} -> {rule['action']} (bias_delta={rule['bias_delta']})")
                    return RuleResult(
                        condition_met=True,
                        action=rule['action'],
                        bias_delta=rule['bias_delta'],
                        flags=rule['flags'],
                        constraints=rule['constraints'],
                        reasoning=rule['reasoning']
                    )
            except Exception as e:
                logger.warning(f"规则执行异常: {rule_group}/{rule['name']} - {e}")
                continue
        
        # 默认结果
        logger.debug(f"规则组 '{rule_group}' 未命中任何规则，返回默认中性结果")
        return RuleResult(
            condition_met=False,
            action='neutral',
            bias_delta=0,
            flags=[],
            constraints=[],
            reasoning='未命中任何规则'
        )
    
    def _determine_action_bias(self, overall_bias: int, context: Dict[str, Any]) -> Tuple[str, str]:
        """确定行动偏向"""
        # 检查是否有严重降级
        has_downgrade = any(
            context.get(key) not in ('available', 'fallback_available', 'browser_preferred')
            for key in ('sector_status', 'news_status')
        )
        
        if has_downgrade and overall_bias <= 0:
            return 'defensive', '上下文传导偏防守，优先等待确认，不宜给激进动作'
        
        if overall_bias >= 3:
            return 'supportive', '上下文传导偏顺畅，可在确认条件满足后考虑更主动的轻仓试错'
        elif overall_bias <= -3:
            return 'defensive', '上下文传导偏防守，优先等待确认，不宜给激进动作'
        elif overall_bias <= -1:
            return 'conservative', '上下文传导存在约束，执行上应抬高确认门槛'
        else:
            return 'neutral', '上下文传导中性，重点看盘中确认和对标股延续'
    
    def _detect_conflicts(self, market_result: RuleResult, sector_result: RuleResult, 
                         stock_result: RuleResult, news_result: RuleResult) -> List[str]:
        """检测规则之间的冲突信号"""
        conflict_flags = []
        
        # 冲突1: 市场偏强但板块不可用
        if market_result.action == 'support' and sector_result.action in ('risk', 'downgrade'):
            conflict_flags.append('冲突: 市场偏强但板块信号偏弱，需谨慎')
        
        # 冲突2: 板块支持但个股分时偏弱
        if sector_result.action == 'support' and stock_result.action == 'risk':
            conflict_flags.append('冲突: 板块支持但个股分时偏弱，注意个股独立风险')
        
        # 冲突3: 消息面支持但市场/板块偏弱
        if news_result.action == 'support' and (market_result.action == 'risk' or sector_result.action in ('risk', 'downgrade')):
            conflict_flags.append('冲突: 消息面支持但市场/板块环境偏弱，需观察持续性')
        
        # 冲突4: 竞价积极但分时偏弱
        if stock_result.action == 'support' and '竞价偏积极' in stock_result.flags and '分时承接偏弱' in stock_result.constraints:
            conflict_flags.append('冲突: 竞价积极但分时承接偏弱，注意主力意图变化')
        
        if conflict_flags:
            logger.warning(f"检测到规则冲突: {len(conflict_flags)} 个冲突信号")
        
        return conflict_flags


def build_context_from_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """从payload构建规则引擎需要的上下文"""
    market_context = payload.get('market_context') or {}
    sector_context = payload.get('sector_context') or {}
    news_sentiment = payload.get('news_sentiment') or {}
    intraday = payload.get('intraday_strength') or {}
    next_day = payload.get('next_day_bias') or {}
    auction_intent = payload.get('auction_intent') or {}
    peer_linkage = (payload.get('dimension_results') or {}).get('peer_linkage') or {}
    
    intraday_result = intraday.get('result') or {}
    next_day_result = next_day.get('result') or {}
    
    return {
        'market_bias': str(market_context.get('market_bias') or ''),
        'size_style': str(market_context.get('size_style') or ''),
        'sector_status': str(sector_context.get('status') or ''),
        'sector_summary': str(sector_context.get('summary') or '').strip(),
        'target_theme_role': str(sector_context.get('target_theme_role') or ''),
        'news_status': str(news_sentiment.get('status') or ''),
        'news_direction': str(news_sentiment.get('direction') or ''),
        'news_level': str(news_sentiment.get('level') or ''),
        'news_is_new': news_sentiment.get('is_new_catalyst'),
        'news_credibility': str(news_sentiment.get('credibility') or ''),
        'news_summary': str(news_sentiment.get('summary') or ''),
        'intraday_label': str(intraday_result.get('label') or ''),
        'intraday_score': intraday_result.get('score') or 0,
        'next_day_label': str(next_day_result.get('label') or ''),
        'next_day_view': str(next_day_result.get('next_day_view') or ''),
        'auction_overall': str(auction_intent.get('overall_intent') or ''),
        'peer_position': str(peer_linkage.get('target_position') or ''),
    }


def format_propagation_chain(chain: PropagationChain) -> Dict[str, Any]:
    """将传播链结果格式化为输出字典"""
    return {
        'market_to_sector': chain.market_to_sector.reasoning,
        'market_sector_news_to_stock': chain.sector_to_stock.reasoning,
        'market_sector_stock_to_intraday': chain.stock_to_intraday.reasoning,
        'news_to_decision': chain.news_to_decision.reasoning,  # 新增
        'action_bias': chain.action_bias,
        'execution_note': chain.execution_note,
        'support_flags': chain.support_flags,
        'risk_flags': chain.risk_flags,
        'constraint_score': chain.constraint_score,
        'rule_details': {
            'market_to_sector': {
                'action': chain.market_to_sector.action,
                'bias_delta': chain.market_to_sector.bias_delta,
                'reasoning': chain.market_to_sector.reasoning
            },
            'sector_to_stock': {
                'action': chain.sector_to_stock.action,
                'bias_delta': chain.sector_to_stock.bias_delta,
                'reasoning': chain.sector_to_stock.reasoning
            },
            'stock_to_intraday': {
                'action': chain.stock_to_intraday.action,
                'bias_delta': chain.stock_to_intraday.bias_delta,
                'reasoning': chain.stock_to_intraday.reasoning
            },
            'news_to_decision': {  # 新增
                'action': chain.news_to_decision.action,
                'bias_delta': chain.news_to_decision.bias_delta,
                'reasoning': chain.news_to_decision.reasoning
            }
        }
    }