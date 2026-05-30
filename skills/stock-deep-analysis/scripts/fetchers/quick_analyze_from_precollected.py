#!/usr/bin/env python3
"""
从预收集数据生成精简分析报告

用法：
  python3 quick_analyze_from_precollected.py ~/quant-data/tushare/股票数据/pre_collected/600103.SH/2026-04-24.json
"""

import json
import sys
from datetime import datetime
from pathlib import Path


def fmt(v):
    if v is None or v == "N/A":
        return "N/A"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def pct_color(v):
    if v is None:
        return "N/A"
    try:
        fv = float(v)
        return f"+{fv:.2f}%" if fv > 0 else f"{fv:.2f}%"
    except (ValueError, TypeError):
        return str(v)


def generate_report(data: dict) -> str:
    meta = data["meta"]
    symbol = meta["symbol"]
    date = meta["trade_date"]
    status = data["data_status"]

    snapshot = data.get("snapshot", {})
    klines = data.get("klines", [])
    minute = data.get("minute_data", {})
    index_data = data.get("index_snapshots", {})
    daily = data.get("daily_local", {})
    factors = data.get("factors", {})
    chips = data.get("chips", {})
    basic = data.get("daily_basic", {})
    moneyflow = data.get("moneyflow", {})
    top_list = data.get("top_list", {})
    top_inst = data.get("top_inst", {})
    ind_con = data.get("industry_concept", {})

    name = snapshot.get("name", symbol)
    current = snapshot.get("current")
    prev_close = snapshot.get("prev_close")
    change_pct = snapshot.get("computed_change_pct")
    open_p = snapshot.get("open")
    high = snapshot.get("high")
    low = snapshot.get("low")
    volume = snapshot.get("volume")
    turnover_rate = snapshot.get("turnover_rate")

    lines = []
    lines.append(f"# {name} ({symbol}) 精简分析报告")
    lines.append(f"日期: {date}  |  收集时间: {meta.get('collected_at', 'N/A')[:19]}")
    lines.append("")

    # 数据状态概览
    lines.append("## 数据状态概览")
    avail = [k for k, v in status.items() if v == "available"]
    miss = [k for k, v in status.items() if v == "missing"]
    lines.append(f"- 可用 ({len(avail)}): {', '.join(avail)}")
    if miss:
        lines.append(f"- 缺失 ({len(miss)}): {', '.join(miss)}")
    lines.append("")

    # 实时行情
    lines.append("## 实时行情")
    lines.append(f"- 名称: {name}")
    lines.append(f"- 当前价: {fmt(current)} (涨跌幅: {pct_color(change_pct)})")
    lines.append(f"- 昨收: {fmt(prev_close)} | 今开: {fmt(open_p)} | 最高: {fmt(high)} | 最低: {fmt(low)}")
    lines.append(f"- 成交量: {fmt(volume)} | 换手率: {fmt(turnover_rate)}%")
    lines.append("")

    # 大盘环境
    lines.append("## 大盘环境")
    if "error" not in index_data:
        for code, info in index_data.items():
            lines.append(f"- {info['name']}: {fmt(info['current'])} ({pct_color(info['change_pct'])})")
    else:
        lines.append("获取失败")
    lines.append("")

    # 近10日走势
    lines.append("## 最近10日走势")
    if klines:
        lines.append("| 日期 | 开盘 | 收盘 | 最高 | 最低 | 涨跌幅 |")
        lines.append("|------|------|------|------|------|----------|")
        for k in klines[-10:]:
            try:
                o, c, h, l = float(k[1]), float(k[2]), float(k[3]), float(k[4])
                pct = (c - o) / o * 100
                lines.append(f"| {k[0]} | {o:.2f} | {c:.2f} | {h:.2f} | {l:.2f} | {pct:+.2f}% |")
            except (ValueError, IndexError):
                continue
    else:
        lines.append("无数据")
    lines.append("")

    # 技术指标
    lines.append("## 技术指标 (T-1)")
    fl = factors.get("latest", {})
    if fl:
        lines.append(f"- KDJ: K={fmt(fl.get('kdj_k_bfq'))}  D={fmt(fl.get('kdj_d_bfq'))}")
        lines.append(f"- MACD: MACD={fmt(fl.get('macd_bfq'))}  DEA={fmt(fl.get('macd_dea_bfq'))}  DIF={fmt(fl.get('macd_dif_bfq'))}")
        lines.append(f"- RSI: 6日={fmt(fl.get('rsi_bfq_6'))}  12日={fmt(fl.get('rsi_bfq_12'))}  24日={fmt(fl.get('rsi_bfq_24'))}")
        lines.append(f"- BOLL: 上轨={fmt(fl.get('boll_upper_bfq'))}  中轨={fmt(fl.get('boll_mid_bfq'))}  下轨={fmt(fl.get('boll_lower_bfq'))}")
        lines.append(f"- MA: 5日={fmt(fl.get('ma_bfq_5'))}  10日={fmt(fl.get('ma_bfq_10'))}  20日={fmt(fl.get('ma_bfq_20'))}  60日={fmt(fl.get('ma_bfq_60'))}")
    else:
        lines.append("无数据")
    lines.append("")

    # 基本面
    lines.append("## 基本面 (T-1)")
    bl = basic.get("latest", {})
    if bl:
        lines.append(f"- 市盈率 PE: {fmt(bl.get('pe'))}")
        lines.append(f"- 市净率 PB: {fmt(bl.get('pb'))}")
        lines.append(f"- 总市值: {fmt(bl.get('total_mv'))}万")
        lines.append(f"- 流通市值: {fmt(bl.get('circ_mv'))}万")
        lines.append(f"- 换手率: {fmt(bl.get('turnover_rate'))}% (自由流通股): {fmt(bl.get('turnover_rate_f'))}%")
    else:
        lines.append("无数据")
    lines.append("")

    # 筹码
    lines.append("## 筹码分布")
    chip_rows = chips.get("rows", [])
    if chip_rows:
        lines.append("| 日期 | 指数 |")
        lines.append("|------|------|")
        for r in chip_rows[:5]:
            td = r.get("trade_date", "N/A")
            # 筹码数据字段可能不同，输出原始值
            price = r.get("price", "N/A")
            percent = r.get("percent", "N/A")
            lines.append(f"| {td} | 价格区间={price}, 占比={percent} |")
    else:
        lines.append("无数据")
    lines.append("")

    # 资金流向
    lines.append("## 资金流向")
    mf_rows = moneyflow.get("rows", [])
    if mf_rows:
        lines.append("| 日期 | 净流向 | 大单 | 中单 | 小单 |")
        lines.append("|------|----------|------|------|------|")
        for r in mf_rows:
            lines.append(f"| {r['date']} | {r['net_flow']} | {r['big_order']} | {r['mid_order']} | {r['small_order']} |")
    else:
        lines.append("无数据")
    lines.append("")

    # 行业概念
    lines.append("## 行业与概念")
    if ind_con.get("industry"):
        lines.append(f"- 行业: {', '.join(ind_con['industry'])}")
    if ind_con.get("concept"):
        lines.append(f"- 概念: {', '.join(ind_con['concept'])}")
    if not ind_con.get("industry") and not ind_con.get("concept"):
        lines.append("无数据")
    lines.append("")

    # 龙虎榜
    lines.append("## 龙虎榜")
    if top_list.get("record"):
        rec = top_list["record"]
        lines.append(f"- 收盘价: {fmt(rec.get('close'))}  涨跌幅: {pct_color(rec.get('pct_change'))}")
        lines.append(f"- 换手率: {fmt(rec.get('turnover_rate'))}%  成交额: {fmt(rec.get('amount'))}")
        lines.append(f"- 买卖比: {fmt(rec.get('l_buy'))}/{fmt(rec.get('l_sell'))}  净流入: {fmt(rec.get('net_amount'))}")
        lines.append(f"- 登榜原因: {rec.get('reason', 'N/A')}")
        if top_inst.get("records"):
            lines.append("")
            lines.append("龙虎榜机构:")
            for r in top_inst["records"][:5]:
                side = "买入" if r.get("side") == "buy" else "卖出"
                lines.append(f"- {r.get('exalter', 'N/A')}: {side} {fmt(r.get('buy'))} (网买: {fmt(r.get('net_buy'))})")
    else:
        lines.append("无数据")
    lines.append("")

    # 分时
    lines.append("## 分时走势")
    mbars = minute.get("bars", [])
    if mbars:
        lines.append(f"- 数据点: {minute.get('count')} 个")
        lines.append(f"- 时间范围: {minute.get('first_time')} ~ {minute.get('last_time')}")
        lines.append(f"- 价格区间: {fmt(minute.get('low'))} ~ {fmt(minute.get('high'))}")
    else:
        lines.append("无数据")
    lines.append("")

    # 综合判断
    lines.append("## 综合判断")
    # 简单的自动判断逻辑
    signals = []
    if change_pct is not None:
        if change_pct > 5:
            signals.append("当日涨幅较大")
        elif change_pct < -5:
            signals.append("当日跌幅较大")
    if turnover_rate and float(turnover_rate) > 10:
        signals.append("换手率偏高")
    if fl:
        try:
            k = float(fl.get("kdj_k_bfq", 50))
            d = float(fl.get("kdj_d_bfq", 50))
            if k > 80 and d > 80:
                signals.append("技术指标超买(KDJ>80)")
            elif k < 20 and d < 20:
                signals.append("技术指标超卖(KDJ<20)")
        except (ValueError, TypeError):
            pass

    if signals:
        lines.append("自动检测信号: " + "; ".join(signals))
    else:
        lines.append("暂无明显信号")

    lines.append("")
    lines.append("---")
    lines.append("生成时间: " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("免责声明: 本报告仅供参考，不构成投资建议。")

    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 2:
        print(f"用法: {sys.argv[0]} <path/to/precollected.json>", file=sys.stderr)
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"错误: 文件不存在 {path}", file=sys.stderr)
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    report = generate_report(data)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
