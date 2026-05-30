#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
股东户数变化分析脚本
使用 Tushare Pro stk_holdernumber 接口获取历史股东户数数据，
输出筹码集中度趋势、环比变化和关键节点分析。

用法:
    python3 analyze_stk_holdernumber.py <ts_code>
示例:
    python3 analyze_stk_holdernumber.py 603305.SH
    python3 analyze_stk_holdernumber.py 000519.SZ
"""

import sys
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.tushare_client import create_pro_api


def analyze_holder_number(ts_code: str, output_dir: str = None):
    """
    分析单只股票的历史股东户数变化。

    Args:
        ts_code: Tushare 股票代码，如 '603305.SH'
        output_dir: 图表输出目录，默认当前目录
    """
    if output_dir is None:
        output_dir = str(Path.home() / 'quant-data')
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    pro = create_pro_api()
    df = pro.query('stk_holdernumber', ts_code=ts_code)

    if df is None or df.empty:
        print(f"[!] 未获取到 {ts_code} 的股东户数数据")
        return

    # 清洗数据
    df['end_date'] = pd.to_datetime(df['end_date'], format='%Y%m%d')
    df['ann_date'] = pd.to_datetime(df['ann_date'], format='%Y%m%d')

    # 修正异常年份（如 2027 → 2017）
    for col in ['end_date', 'ann_date']:
        mask = df[col].dt.year > 2030
        if mask.any():
            df.loc[mask, col] = df.loc[mask, col].apply(lambda x: x.replace(year=x.year - 10))

    # 去重：同一end_date保留ann_date最新的记录
    df = df.sort_values('ann_date').drop_duplicates(subset=['end_date'], keep='last')
    df = df.sort_values('end_date').reset_index(drop=True)

    # 计算环比变化
    df['change_num'] = df['holder_num'].diff()
    df['change_pct'] = df['holder_num'].pct_change() * 100

    # 输出文本分析
    print("=" * 60)
    print(f"股东户数变化分析 - {ts_code}")
    print("=" * 60)
    print(f"\n数据范围: {df['end_date'].min().strftime('%Y-%m-%d')} ~ {df['end_date'].max().strftime('%Y-%m-%d')}")
    print(f"总期数: {len(df)} 期")
    print(f"最新股东户数: {int(df['holder_num'].iloc[-1]):,} 户")
    print(f"历史最低: {int(df['holder_num'].min()):,} 户")
    print(f"历史最高: {int(df['holder_num'].max()):,} 户")

    # 近10期明细
    print("\n" + "-" * 60)
    print("近10期变化明细")
    print("-" * 60)
    recent = df.tail(10)
    for _, row in recent.iterrows():
        change_str = f"{row['change_num']:+.0f} ({row['change_pct']:+.1f}%)" if pd.notna(row['change_num']) else "—"
        print(f"{row['end_date'].strftime('%Y-%m-%d')} | {int(row['holder_num']):>7,}户 | {change_str}")

    # 关键变化节点
    print(f"\n关键变化节点:")
    max_inc_idx = df['change_pct'].idxmax()
    max_inc = df.loc[max_inc_idx]
    print(f"  最大增幅: {max_inc['change_pct']:+.1f}% ({max_inc['end_date'].strftime('%Y-%m-%d')})")
    max_dec_idx = df['change_pct'].idxmin()
    max_dec = df.loc[max_dec_idx]
    print(f"  最大降幅: {max_dec['change_pct']:+.1f}% ({max_dec['end_date'].strftime('%Y-%m-%d')})")

    # 绘制图表
    fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True)

    ax1 = axes[0]
    ax1.fill_between(df['end_date'], df['holder_num'], alpha=0.3, color='#4A90D9')
    ax1.plot(df['end_date'], df['holder_num'], color='#2E5AAC', linewidth=1.5, marker='o', markersize=3)
    ax1.set_ylabel('股东户数')
    ax1.set_title(f'{ts_code} 股东户数变化趋势')
    ax1.grid(True, alpha=0.3)

    min_idx = df['holder_num'].idxmin()
    max_idx = df['holder_num'].idxmax()
    ax1.annotate(f'最低: {int(df.loc[min_idx, "holder_num"]):,}',
                 xy=(df.loc[min_idx, 'end_date'], df.loc[min_idx, 'holder_num']),
                 xytext=(10, -25), textcoords='offset points', fontsize=9,
                 arrowprops=dict(arrowstyle='->', color='green'), color='green')
    ax1.annotate(f'最高: {int(df.loc[max_idx, "holder_num"]):,}',
                 xy=(df.loc[max_idx, 'end_date'], df.loc[max_idx, 'holder_num']),
                 xytext=(10, 20), textcoords='offset points', fontsize=9,
                 arrowprops=dict(arrowstyle='->', color='red'), color='red')

    ax2 = axes[1]
    colors = ['green' if x < 0 else 'red' for x in df['change_pct'].fillna(0)]
    ax2.bar(df['end_date'], df['change_pct'].fillna(0), color=colors, alpha=0.7, width=30)
    ax2.axhline(y=0, color='black', linewidth=0.8)
    ax2.set_ylabel('环比变化 (%)')
    ax2.set_xlabel('报告期末日期')
    ax2.set_title('股东户数环比变化率')
    ax2.grid(True, alpha=0.3)

    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor='red', alpha=0.7, label='户数增加（散户流入）'),
        Patch(facecolor='green', alpha=0.7, label='户数减少（筹码集中）')
    ]
    ax2.legend(handles=legend_elements, loc='upper left', fontsize=9)

    plt.tight_layout()
    chart_path = Path(output_dir) / f'{ts_code.replace(".", "_")}_holder_trend.png'
    plt.savefig(chart_path, dpi=150, bbox_inches='tight')
    plt.close()

    print(f"\n图表已保存: {chart_path}")
    return df


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python3 analyze_stk_holdernumber.py <ts_code>")
        print("示例: python3 analyze_stk_holdernumber.py 603305.SH")
        sys.exit(1)

    ts_code = sys.argv[1]
    analyze_holder_number(ts_code)
