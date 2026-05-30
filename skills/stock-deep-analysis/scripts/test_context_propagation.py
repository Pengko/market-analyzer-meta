#!/usr/bin/env python3
"""
测试 context_propagation 规则链引擎
验证规则链在各种场景下的表现
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from decision.context_propagation_rules import ContextPropagationRules

def test_scenario(name: str, context: dict):
    """测试单个场景"""
    print(f"\n{'='*60}")
    print(f"场景: {name}")
    print(f"{'='*60}")
    
    engine = ContextPropagationRules()
    chain = engine.evaluate_chain(context)
    
    print(f"输入上下文:")
    for key, value in context.items():
        print(f"  {key}: {value}")
    
    print(f"\n规则链结果:")
    print(f"  总体偏见: {chain.overall_bias}")
    print(f"  行动偏向: {chain.action_bias}")
    print(f"  执行说明: {chain.execution_note}")
    print(f"  支持标志: {chain.support_flags}")
    print(f"  风险标志: {chain.risk_flags}")
    print(f"  约束评分: {chain.constraint_score}")
    
    print(f"\n规则详情:")
    for rule_group in ['market_to_sector', 'sector_to_stock', 'stock_to_intraday', 'news_to_decision']:
        result = getattr(chain, rule_group)
        print(f"  {rule_group}:")
        print(f"    命中: {result.condition_met}")
        print(f"    动作: {result.action}")
        print(f"    偏见增量: {result.bias_delta}")
        print(f"    推理: {result.reasoning}")
        if result.flags:
            print(f"    标志: {result.flags}")
        if result.constraints:
            print(f"    约束: {result.constraints}")
    
    return chain

def main():
    """运行所有测试场景"""
    print("开始测试 context_propagation 规则链引擎")
    
    # 场景1: 强势市场 + 龙头股 + 积极信号
    scenario1 = {
        'market_bias': '偏强',
        'size_style': '小盘成长占优',
        'sector_status': 'available',
        'target_theme_role': '题材龙头',
        'news_status': 'available',
        'news_is_new': True,
        'news_credibility': '公告实锤',
        'intraday_score': 3,
        'next_day_label': '偏强延续',
        'auction_overall': '偏主动抢筹',
        'peer_position': '领先'
    }
    chain1 = test_scenario("强势市场 + 龙头股 + 积极信号", scenario1)
    
    # 场景2: 弱势市场 + 板块降级 + 消息缺失
    scenario2 = {
        'market_bias': '偏弱',
        'size_style': '大盘权重占优',
        'sector_status': 'fallback_available',
        'target_theme_role': '题材跟风',
        'news_status': 'missing',
        'intraday_score': -1,
        'next_day_label': '分歧',
        'auction_overall': '偏谨慎观望',
        'peer_position': '掉队'
    }
    chain2 = test_scenario("弱势市场 + 板块降级 + 消息缺失", scenario2)
    
    # 场景3: 中性市场 + 板块不可用
    scenario3 = {
        'market_bias': '中性',
        'sector_status': 'missing',
        'news_status': 'available',
        'news_is_new': False,
        'news_credibility': '二手转述',
        'intraday_score': 0,
        'next_day_label': '中性'
    }
    chain3 = test_scenario("中性市场 + 板块不可用", scenario3)
    
    # 场景4: 市场偏强 + 旧消息重炒
    scenario4 = {
        'market_bias': '偏强',
        'size_style': '小盘成长占优',
        'sector_status': 'available',
        'target_theme_role': '题材中位',
        'news_status': 'available',
        'news_is_new': False,
        'news_credibility': '主流媒体',
        'intraday_score': 1,
        'next_day_label': '中性'
    }
    chain4 = test_scenario("市场偏强 + 旧消息重炒", scenario4)
    
    # 场景5: 竞价积极但分时偏弱（冲突检测）
    scenario5 = {
        'market_bias': '偏强',
        'sector_status': 'available',
        'target_theme_role': '题材龙头',
        'auction_overall': '偏主动抢筹',
        'intraday_score': -2,
        'next_day_label': '分歧'
    }
    chain5 = test_scenario("竞价积极但分时偏弱（冲突检测）", scenario5)
    
    print(f"\n{'='*60}")
    print("测试总结")
    print(f"{'='*60}")
    print(f"场景1 - 总体偏见: {chain1.overall_bias}, 行动偏向: {chain1.action_bias}")
    print(f"场景2 - 总体偏见: {chain2.overall_bias}, 行动偏向: {chain2.action_bias}")
    print(f"场景3 - 总体偏见: {chain3.overall_bias}, 行动偏向: {chain3.action_bias}")
    print(f"场景4 - 总体偏见: {chain4.overall_bias}, 行动偏向: {chain4.action_bias}")
    print(f"场景5 - 总体偏见: {chain5.overall_bias}, 行动偏向: {chain5.action_bias}")
    
    # 验证冲突检测
    print(f"\n冲突检测验证:")
    print(f"场景5 风险标志: {chain5.risk_flags}")
    conflict_flags = [flag for flag in chain5.risk_flags if '冲突' in flag]
    if conflict_flags:
        print(f"✅ 冲突检测正常工作: {len(conflict_flags)} 个冲突信号")
    else:
        print(f"⚠️  未检测到冲突信号")

if __name__ == "__main__":
    main()