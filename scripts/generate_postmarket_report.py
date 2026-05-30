#!/usr/bin/env python3
"""
generate_postmarket_report.py
基于 quick_analyze.py JSON 输出生成标准盘后深度分析报告。

用法:
    python3 generate_postmarket_report.py --input /tmp/000555_quick.json --output /tmp/report.md
    python3 generate_postmarket_report.py --input /tmp/000555_quick.json --output /tmp/report.md --name "神州信息"

输入: quick_analyze.py 输出的 JSON（含第一行标题）
输出: 符合 stock-deep-analysis 技能规范的 Markdown 报告
"""

import argparse
import json
import os
import sys
from datetime import datetime


def fmt_num(val, dec=2):
    """安全数字格式化。对字符串'N/A'不会报错。"""
    try:
        return f"{float(val):.{dec}f}"
    except (ValueError, TypeError):
        return str(val)


def parse_quick_analyze_json(path):
    """解析 quick_analyze.py 输出（跳过第一行标题）。"""
    with open(path, 'r', encoding='utf-8') as fp:
        lines = fp.readlines()
    # 第一行是标题如 "[快速分析] 000555.SZ @ 2026-05-26"
    if lines and lines[0].startswith('['):
        data = json.loads(''.join(lines[1:]))
    else:
        data = json.loads(''.join(lines))
    return data


def build_report(data, stock_name_override=None):
    """基于 quick_analyze 数据生成标准盘后深度分析报告。"""
    s = data['snapshot']
    symbol = data['meta']['symbol']
    name = stock_name_override or s.get('name', symbol)
    current = s.get('current', 0)
    change_pct = s.get('change_pct', 0)
    prev_close = s.get('prev_close', 0)
    open_price = s.get('open', 0)
    high = s.get('high', 0)
    low = s.get('low', 0)
    volume = s.get('volume', 0)
    amount = s.get('amount', 0)
    turnover = s.get('turnover', 0)
    market_cap = s.get('market_cap', 'N/A')

    klines = data.get('klines', [])
    mi = data.get('minute_intent', [])
    mf = data.get('moneyflow', {})
    mf_rows = mf.get('rows', []) if mf.get('status') == 'available' else []
    fac = data.get('factors', {})
    fac_latest = fac.get('latest', {}) if fac.get('status') == 'available' else {}
    chips = data.get('chips', {})
    chips_rows = chips.get('rows', []) if chips.get('status') == 'available' else []
    chips_date = chips.get('latest_date', 'N/A')
    db = data.get('daily_basic', {})
    db_latest = db.get('latest', {}) if db.get('status') == 'available' else {}

    # ---- 决策引擎 ----
    decision = "观察确认"
    reasons = []
    if change_pct > 9.5:
        decision, reasons = "强势持有", ["涨停"]
    elif change_pct < -9.5:
        decision, reasons = "风险提示", ["跌停"]
    elif change_pct > 5:
        decision, reasons = "积极观察", ["大涨"]
    elif change_pct < -5:
        decision, reasons = "谨慎观察", ["大跌"]

    # KDJ 超买/超卖调整
    try:
        kdj_k = float(fac_latest.get('kdj_k_bfq', 50))
        if kdj_k > 80 and change_pct > 0:
            decision, reasons = "观察确认", ["KDJ超买"]
        elif kdj_k < 20 and change_pct < 0:
            decision, reasons = "观察确认", ["KDJ超卖"]
    except (ValueError, TypeError):
        pass

    # 资金流入调整
    if mf_rows and '流入' in mf_rows[-1].get('net_flow', ''):
        big = str(mf_rows[-1].get('big_order', '0'))
        if '亿' in big or ('+' in big and float(big.replace('+','').replace('万','')) > 5000):
            if decision == "观察确认":
                decision, reasons = "积极观察", ["大资金流入"]

    # 数据完整度
    ds = data.get('data_status', {})
    completeness = int(sum(1 for v in ds.values() if v == 'available') / max(len(ds), 1) * 100)

    # ---- 近10日走势表格 ----
    trend_lines = []
    for k in klines[-10:]:
        date_str = k[0]
        close_p = float(k[2])
        idx = klines.index(k)
        if idx > 0:
            prev = float(klines[idx - 1][2])
            chg = (close_p - prev) / prev * 100
        else:
            chg = 0
        vol = float(k[5]) if len(k) > 5 else 0
        trend_lines.append(f"| {date_str} | {fmt_num(close_p)} | {'+' if chg >= 0 else ''}{fmt_num(chg)}% | - | {fmt_num(vol/10000, 1)}万手 | - |")

    # ---- 分时主力意图表格 ----
    minute_lines = []
    key_windows = ["09:30-09:45", "09:48-09:56", "11:25-11:30", "13:00-13:30", "14:55-15:00"]
    for w in key_windows:
        found = next((m for m in mi if w in m.get('time_window', '')), None)
        if found:
            # volume 是累计值，需特殊标注
            vol_display = f"{found.get('volume', 'N/A')}万手(累计)"
            minute_lines.append(f"| {found.get('time_window', w)} | {found.get('price_range', '-')} | {vol_display} | {found.get('behavior', '-')} | 中性 |")
        else:
            minute_lines.append(f"| {w} | - | - | - | 中性 |")

    # ---- 近5日资金流向表格 ----
    money_lines = []
    for r in mf_rows[:5]:
        money_lines.append(
            f"| {r.get('date', '-')} | {r.get('net_flow', '-')} | "
            f"{r.get('big_order', '-')} | {r.get('mid_order', '-')} | "
            f"{r.get('small_order', '-')} | - |"
        )

    # ---- 技术因子 ----
    kdj_k = fmt_num(fac_latest.get('kdj_k_bfq', 'N/A'))
    kdj_d = fmt_num(fac_latest.get('kdj_d_bfq', 'N/A'))
    kdj_j = fmt_num(fac_latest.get('kdj_j_bfq', 'N/A'))
    macd = fmt_num(fac_latest.get('macd_bfq', 'N/A'))
    rsi6 = fmt_num(fac_latest.get('rsi_bfq_6', 'N/A'))
    ma5 = fmt_num(fac_latest.get('ma5_bfq', 'N/A'))
    ma10 = fmt_num(fac_latest.get('ma10_bfq', 'N/A'))
    ma20 = fmt_num(fac_latest.get('ma20_bfq', 'N/A'))

    # ---- 筹码 ----
    chips_valid = len(chips_rows) > 2
    chips_summary = ""
    if chips_valid:
        chips_summary = f"筹码数据可用（数据日期：{chips_date}，延迟1-3日属正常）"
    else:
        chips_summary = f"筹码数据异常（仅含{len(chips_rows)}行占位数据），筹码分析不可用"

    # ---- 生成报告 ----
    report = f"""# {name}（{symbol}）盘后深度分析

## 场景与数据

- **分析时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
- **数据日期**：{data['meta']['trade_date']}
- **数据完整度**：{completeness}%

## 一、大盘与板块环境

（此部分需根据当日大盘数据手动补充或调用 build_stock_report.py 获取）

## 二、目标股结构

### 2.1 今日行情快照

| 指标 | 数值 |
|------|------|
| 收盘价 | {fmt_num(current)} 元 |
| 涨跌幅 | {'+' if change_pct >= 0 else ''}{fmt_num(change_pct)}% |
| 开盘价 | {fmt_num(open_price)} 元 |
| 最高价 | {fmt_num(high)} 元 |
| 最低价 | {fmt_num(low)} 元 |
| 成交量 | {fmt_num(volume/10000, 1)} 万手 |
| 成交额 | {fmt_num(amount/10000, 1)} 万元 |
| 换手率 | {fmt_num(turnover)}% |
| 总市值 | {market_cap} |

### 2.2 最近10日走势

| 日期 | 收盘价 | 涨跌幅 | 换手率 | 成交量 | 形态/信号 |
|------|--------|--------|--------|--------|-----------|
{chr(10).join(trend_lines)}

### 2.3 分时主力意图分析

**注意**：下表"量能表现"列中的成交量为**累计到该时段的成交量**，非区间成交量。

| 时间窗口 | 价格区间 | 量能表现 | 主力行为 | 信号判断 |
|----------|----------|----------|----------|----------|
{chr(10).join(minute_lines)}

## 三、资金流向

### 3.1 近5日资金流向

| 日期 | 净流入/流出（万元） | 大单（万元） | 中单（万元） | 小单（万元） | 市场含义 |
|------|---------------------|--------------|--------------|--------------|----------|
{chr(10).join(money_lines)}

## 四、筹码分布

{chips_summary}

## 五、技术因子复核

| 指标 | 数值 | 说明 |
|------|------|------|
| KDJ K | {kdj_k} | - |
| KDJ D | {kdj_d} | - |
| KDJ J | {kdj_j} | {'超买区' if kdj_k != 'N/A' and float(kdj_k) > 80 else '超卖区' if kdj_k != 'N/A' and float(kdj_k) < 20 else '中性区'} |
| MACD | {macd} | {'多头' if macd != 'N/A' and float(macd) > 0 else '空头'} |
| RSI(6) | {rsi6} | {'超买' if rsi6 != 'N/A' and float(rsi6) > 80 else '超卖' if rsi6 != 'N/A' and float(rsi6) < 20 else '中性'} |
| MA5 | {ma5} | 动态支撑/压力 |
| MA10 | {ma10} | 波段支撑/压力 |
| MA20 | {ma20} | 趋势支撑/压力 |

## 六、消息面

（此部分需通过浏览器/TrendRadar MCP 补充，或调用 market-news-intelligence 获取）

| 维度 | 判断结果 | 说明 |
|------|----------|------|
| 消息方向 | 待补充 | - |
| 消息级别 | 待补充 | - |
| 消息新鲜度 | 待补充 | - |
| 消息可信度 | 待补充 | - |
| 情绪作用方式 | 待补充 | - |

## 七、交易结论

### 7.1 核心判断

- **决策建议**：{decision}
- **理由**：{', '.join(reasons) if reasons else '技术面与资金面综合判断'}

### 7.2 次日与后续预期

（需结合大盘环境、板块判断、消息面综合推演）

### 7.3 风险与失效条件

- 跌破关键支撑位
- 板块整体退潮
- 消息面出现实质性利空

## 八、置信度评分

- **数据完整度**：{completeness}%
- **建议行动**：{'立即执行' if completeness >= 90 else '观察确认' if completeness >= 70 else '等待数据'}

---
*报告由 generate_postmarket_report.py 自动生成，部分模块需人工补充大盘/板块/消息面分析*
"""
    return report


def main():
    parser = argparse.ArgumentParser(description='从 quick_analyze JSON 生成盘后深度分析报告')
    parser.add_argument('--input', '-i', required=True, help='quick_analyze.py 输出的 JSON 文件路径')
    parser.add_argument('--output', '-o', required=True, help='生成的 Markdown 报告输出路径')
    parser.add_argument('--name', '-n', default=None, help='股票名称（覆盖 JSON 中的 name）')
    args = parser.parse_args()

    data = parse_quick_analyze_json(args.input)
    report = build_report(data, stock_name_override=args.name)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"报告已生成: {args.output}")


if __name__ == '__main__':
    main()
