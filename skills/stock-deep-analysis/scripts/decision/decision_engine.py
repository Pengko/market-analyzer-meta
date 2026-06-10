"""
决策引擎 —— 综合所有数据做最终交易决策。

职责：
1. 综合行情、资金、板块、技术指标、消息面打分
2. 生成最终交易建议：买/卖/观望
3. 记录分析结果，生成待验证报告

谁用它：
- build_stock_report.py 和 quick_analyze.py 调它
"""

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from common import STOCK_DATA_ROOT
from data.data_access import load_daily_row, load_daily_rows_for_symbols, next_trade_dates_compact
from data.parquet_io import save_analysis_parquet
from render.report_renderer import (
    render_pending_validation_markdown,
    render_status_text,
    tidy_sentence,
    render_action_bias_text,
)
from analysis.sector_analyzer import load_stock_basic_index

from data.config_loader import cfg

MINUTE_ROOT = cfg.paths("minute")
PENDING_VALIDATIONS_ROOT = Path.home() / "quant-data" / "市场分析" / "reports" / "个股分析报告"


CHECKPOINT_FILE_LABELS = {
    'pre_open': '收盘',
    'open': '上午盘中',
    'noon': '午间休盘',
    'afternoon': '下午盘中',
    'close': '收盘',
    'next_close': '收盘',
}


def safe_float(value: str | None) -> float | None:
    if value in (None, ''):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def percentile_rank(value: float, values: list[float], higher_better: bool = True) -> float:
    if not values:
        return 0.0
    less = sum(1 for item in values if item < value)
    equal = sum(1 for item in values if item == value)
    rank = (less + 0.5 * equal) / len(values)
    return rank if higher_better else (1.0 - rank)


def safe_pct_amplitude(row: dict[str, Any]) -> float:
    high = safe_float(row.get('high'))
    low = safe_float(row.get('low'))
    pre_close = safe_float(row.get('pre_close'))
    if high is None or low is None or pre_close in (None, 0):
        return 0.0
    return (high - low) / pre_close * 100.0


def amount_to_yi(amount: float | None) -> float:
    if amount is None:
        return 0.0
    return amount / 100000.0


def _build_dc_index() -> None:
    """构建 DC 概念→股票双向索引, 缓存到 /tmp"""
    import pandas as _pd
    import json as _json
    from pathlib import Path as _Path

    theme_root = STOCK_DATA_ROOT / "theme_data"

    # BK code → concept name (仅概念板块)
    c2n: dict[str, str] = {}
    try:
        df_idx = _pd.read_parquet(theme_root / "dc_index" / "dc_index_all.parquet")
        for _, r in df_idx.iterrows():
            c = str(r.get("ts_code", ""))
            n = str(r.get("name", ""))
            t = str(r.get("idx_type", ""))
            if c and n and t == "概念板块":
                c2n[c] = n
    except Exception:
        pass

    # stock → BK codes (通过 dc_member)
    s2c: dict[str, set[str]] = {}
    c2s: dict[str, set[str]] = {}
    member_dir = theme_root / "dc_member"
    for mf in sorted(member_dir.glob("BK*.DC.parquet")):
        try:
            df = _pd.read_parquet(mf)
        except Exception:
            continue
        bk = mf.stem
        if "con_code" not in df.columns:
            continue
        for code in df["con_code"].dropna().unique():
            code = str(code).strip()
            if not code:
                continue
            s2c.setdefault(code, set()).add(bk)
            c2s.setdefault(bk, set()).add(code)

    # 缓存
    _Path("/tmp/stock_deep_dc_index.json").write_text(_json.dumps({
        "s2c": {k: sorted(v) for k, v in s2c.items()},
        "c2n": c2n,
        "c2s": {k: sorted(v) for k, v in c2s.items()},
    }, ensure_ascii=False), encoding="utf-8")


def build_peer_linkage(full_symbol: str, trade_date_text: str) -> dict[str, Any]:
    """兼容旧接口: 先用 DataSlicer 拉数据, 再调纯分析函数"""
    import sys
    from pathlib import Path
    # 确保 scripts 目录在 Python 路径中
    scripts_dir = str(Path(__file__).parent.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from data.dataslicer import slice_all
    slices = slice_all(full_symbol, trade_date_text.replace("-", ""))
    return analyze_peer(slices["market"], slices["concept"])


def analyze_peer(market: Any, concept: Any) -> dict[str, Any]:
    """纯分析函数: 从 MarketSlice + ConceptSlice 产出对标股结果。
    
    不拉取任何数据, 所有数据由上游 DataSlicer 提供。
    """
    import sys
    from pathlib import Path
    # 确保 scripts 目录在 Python 路径中
    scripts_dir = str(Path(__file__).parent.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from data.dataslicer import MarketSlice, ConceptSlice as CSlice
    import math
    import statistics as _stat
    
    trade_date_text = market.trade_date
    trade_date_compact = trade_date_text
    if len(trade_date_text) == 8:
        trade_date_text = f"{trade_date_text[:4]}-{trade_date_text[4:6]}-{trade_date_text[6:]}"
    
    target_pcts = market.daily_pcts
    if len(target_pcts) < 5:
        return {"status": "manual_pending", "summary": "目标股日线数据不足5天"}
    
    target_close = market.daily_latest.get("close")
    target_amount = market.daily_latest.get("amount")
    target_pct = market.daily_latest.get("pct_chg") or 0.0
    target_amp = safe_pct_amplitude({"high": target_close, "low": target_close, "close": target_close, "pre_close": target_close})

    # 概念名 + BK代码映射
    concepts = concept.names
    name_to_bk = {v: k for k, v in concept.bk_to_name.items()}
    c2s = concept.bk_to_stocks
    sector_daily = concept.sector_daily

    if not concepts:
        return {"status": "manual_pending", "summary": "未找到个股有效概念标签"}

    # ── 4. 用预取的 sector_daily 计算个股 vs 概念日K相关性 ──
    def _pearson(x: list[float], y: list[float]) -> float:
        n = min(len(x), len(y))
        if n < 3: return 0.0
        sx = sum(x[:n]); sy = sum(y[:n])
        sxx = sum(v * v for v in x[:n]); syy = sum(v * v for v in y[:n])
        sxy = sum(x[i] * y[i] for i in range(n))
        num = n * sxy - sx * sy
        den = math.sqrt((n * sxx - sx * sx) * (n * syy - sy * sy))
        return num / den if den != 0 else 0.0

    concept_scores: list[dict] = []
    for cn in concepts[:10]:
        sector_pcts = sector_daily.get(cn, [])
        if len(sector_pcts) < 5:
            continue
        bk = name_to_bk.get(cn, "")
        daily_corr = round(_pearson(target_pcts, sector_pcts), 4)
        peer_count = len(c2s.get(bk, []))
        concept_scores.append({"name": cn, "bk_code": bk, "daily_corr": daily_corr, "peer_count": peer_count})

    if not concept_scores:
        return {"status": "manual_pending", "summary": "所有概念板块成分股数据不足"}

    concept_scores.sort(key=lambda x: x["daily_corr"], reverse=True)

    # primary_sector = 日K最相关
    primary = concept_scores[0]
    primary_sector = primary["name"]

    # ── 5. 在 primary_sector 成分股中, 按个股相关性选出对标股 ──
    daily_root = STOCK_DATA_ROOT / "daily"
    peer_codes = c2s.get(primary.get("bk_code", ""), [])
    peer_candidates: list[dict] = []
    target_pct_arr = target_pcts
    for c in peer_codes[:50]:
        if c == market.symbol:
            continue
        rows = _read_daily_parquet(daily_root / f"{c}.parquet")
        if not rows or len(rows) < 5:
            continue
        peer_pcts = [safe_float(r.get("pct_chg")) or 0.0 for r in rows[-20:]]
        peer_latest = rows[-1]
        peer_close = safe_float(peer_latest.get("close"))
        peer_amount = safe_float(peer_latest.get("amount"))
        peer_pct = safe_float(peer_latest.get("pct_chg")) or 0.0
        peer_amp = safe_pct_amplitude(peer_latest)
        if peer_pcts[-1] == 0:
            continue

        daily_corr = round(_pearson(target_pct_arr, peer_pcts), 4)
        if daily_corr < 0.3:
            continue

        # 获取股票名称
        peer_name = c
        try:
            from scripts.data.data_provider import get_stock_basic
            sb = get_stock_basic(c) or {}
            peer_name = sb.get("name", c)
        except Exception:
            pass

        peer_candidates.append({
            "symbol": c,
            "name": peer_name,
            "daily_corr": daily_corr,
            "pct_chg": peer_pct,
            "amount": peer_amount or 0,
            "amplitude": peer_amp,
            "close": peer_close,
        })

    if len(peer_candidates) < 1:
        return {"status": "manual_pending", "summary": f"板块 {primary_sector} 内未找到有效对标股"}

    # 按日K相关度排序
    peer_candidates.sort(key=lambda x: x["daily_corr"], reverse=True)
    top_n = min(5, len(peer_candidates))

    # ── 6. 角色分类 ──
    remaining = list(peer_candidates)
    peers_out: list[dict] = []

    # 龙头: 相关度最高 + 涨幅领先
    leader = remaining[0]
    peers_out.append(_format_peer(leader, "龙头", trade_date_text, f"日K相关 {leader['daily_corr']:.2f}，板块核心参考"))
    remaining = [p for p in remaining if p["symbol"] != leader["symbol"]]

    # 中军: 相关度 ≥0.5 + 成交额最大
    if remaining:
        anchor_pool = [p for p in remaining if p["daily_corr"] >= 0.3]
        if anchor_pool:
            anchor = max(anchor_pool, key=lambda p: p["amount"])
        else:
            anchor = remaining[0]
        peers_out.append(_format_peer(anchor, "中军", trade_date_text, f"容量股，观察板块资金承接"))
        remaining = [p for p in remaining if p["symbol"] != anchor["symbol"]]

    # 高弹性: 相关 + 振幅最大
    if remaining:
        elastic_pool = [p for p in remaining if p["daily_corr"] >= 0.3]
        if elastic_pool:
            elastic = max(elastic_pool, key=lambda p: p["amplitude"])
        else:
            elastic = remaining[0]
        peers_out.append(_format_peer(elastic, "高弹性", trade_date_text, f"振幅突出，反映板块情绪温度"))
        remaining = [p for p in remaining if p["symbol"] != elastic["symbol"]]

    # 纯相关: 剩余按日K相关度补满 top 5
    for p in remaining[:top_n - len(peers_out)]:
        peers_out.append(_format_peer(p, "纯相关", trade_date_text, f"日K高度相关 {p['daily_corr']:.2f}"))

    # ── 7. alignment 判断 ──
    # 检查是否有第二个概念也有高相关度
    if len(concept_scores) >= 2:
        second = concept_scores[1]
        if second["daily_corr"] >= 0.5 and second["name"] != primary_sector:
            alignment = "多板块关联" if primary["daily_corr"] >= 0.5 else "次要板块"
        else:
            alignment = "单一板块主导" if primary["daily_corr"] >= 0.5 else "无明确对标"
    else:
        alignment = "单一板块主导" if primary["daily_corr"] >= 0.5 else "无明确对标"

    summary = (f"走势最相关板块为 {primary_sector} (日K相关 {primary['daily_corr']:.2f})，"
               f"目标股当日涨跌 {target_pct:+.2f}%，"
               f"成分股池 {primary['peer_count']} 只")

    # 兼容旧 target_position: 与 peer_candidates 中的 pct_chg 对比
    peer_all_pcts = [p["pct_chg"] for p in peer_candidates]
    if peer_all_pcts:
        if target_pct > max(peer_all_pcts):
            target_position = "领先"
        elif target_pct < min(peer_all_pcts):
            target_position = "掉队"
        else:
            target_position = "中位"
    else:
        target_position = "中位"

    return {
        "status": "available",
        "primary_sector": primary_sector,
        "alignment": alignment,
        "target_position": target_position,
        "target_pct_chg": target_pct,
        "concept_count": len(concept_scores),
        "peer_count": len(peer_candidates),
        "peers": peers_out,
        "summary": summary,
        "source": "dc_member+dc_index+daily_correlation",
        "confidence": "中",
    }


def _read_daily_parquet(path: Path) -> list[dict]:
    import pandas as _pd
    if not path.exists():
        return []
    try:
        df = _pd.read_parquet(path)
        df = df.sort_values("trade_date")
        return df.to_dict("records")
    except Exception:
        return []


def _format_peer(item: dict, role: str, trade_date: str, inspiration: str) -> dict:
    pct = item["pct_chg"]
    amt_yi = amount_to_yi(item["amount"] or 0)
    return {
        "symbol": item["symbol"],
        "name": item["name"],
        "role": role,
        "daily_corr": item["daily_corr"],
        "latest_performance": f"{trade_date} 涨跌 {pct:+.2f}%；成交额 {amt_yi:.2f} 亿",
        "pct_chg": pct,
        "amount_yi": round(amt_yi, 2),
        "inspiration": inspiration,
    }


DECISION_TASK = """你是一个A股交易决策引擎。基于以下各维度分析摘要,给出综合裁决。
返回 JSON:
{
  "decision": "适合轻仓试仓"|"仅适合观察"|"观察确认"|"暂不适合建仓",
  "bullish_dimensions": ["偏多方面1", ...],
  "bearish_dimensions": ["偏空方面1", ...],
  "conflicts": ["矛盾项1", ...],
  "preconditions": ["放量站稳XX", ...],
  "invalidations": ["跌破XX且回抽无力", ...],
  "key_levels": {"observe": 数值, "confirm": 数值, "invalid": 数值},
  "reasoning": "综合推理过程"
}"""


def extract_decision_context(payload: dict[str, Any]) -> dict[str, Any]:
    ctx = {}

    intraday = payload.get('intraday_strength') or {}
    if intraday.get('status') == 'available':
        r = intraday.get('result') or {}
        ctx['intraday'] = {'score': r.get('score'), 'label': r.get('label'), 'view': r.get('afternoon_view')}

    next_day = payload.get('next_day_bias') or {}
    if next_day.get('status') == 'available':
        r = next_day.get('result') or {}
        ctx['next_day'] = {'score': r.get('score'), 'label': r.get('label'), 'view': r.get('next_day_view')}

    capital = payload.get('capital_freshness') or {}
    ctx['capital'] = {'label': capital.get('label')}

    news = payload.get('news_sentiment') or {}
    if news.get('status') == 'available':
        ctx['news'] = {'direction': news.get('direction'), 'level': news.get('level'), 'is_new': news.get('is_new_catalyst')}

    peer = (payload.get('dimension_results') or {}).get('peer_linkage') or {}
    if peer.get('status') == 'available':
        ctx['peer'] = {
            'primary_sector': peer.get('primary_sector'),
            'alignment': peer.get('alignment'),
            'peer_count': peer.get('peer_count'),
        }

    auction = (payload.get('dimension_results') or {}).get('auction_intent') or {}
    ctx['auction'] = {'intent': auction.get('overall_intent'), 'score': auction.get('score')}

    trend = (payload.get('dimension_results') or {}).get('trend_structure') or {}
    if trend.get('status') == 'available':
        ctx['trend_structure'] = {'score': trend.get('score'), 'summary': trend.get('summary')}

    chip = (payload.get('dimension_results') or {}).get('chip_structure') or {}
    if chip.get('status') == 'available':
        ctx['chip'] = {'score': chip.get('score'), 'summary': chip.get('summary')}

    dt = (payload.get('dimension_results') or {}).get('dragon_tiger') or {}
    ctx['dragon_tiger'] = {'signal': dt.get('signal'), 'score': dt.get('overall_score')}

    market = payload.get('market_context') or {}
    ctx['market'] = {'bias': market.get('market_bias'), 'style': market.get('size_style')}

    sector = payload.get('sector_context') or {}
    ctx['sector'] = {'summary': sector.get('summary'), 'cycle': (sector.get('theme_cycle') or {}).get('cycle')}

    ctx['context_propagation'] = payload.get('context_propagation', {}).get('action_bias')

    return ctx


def compute_data_score(payload: dict[str, Any]) -> int:
    freshness = payload.get('freshness', {}).get('summary', {})
    missing = freshness.get("missing", [])
    stale = freshness.get("stale", [])
    _dq = cfg.decision("data_quality", default={})
    score = 100 - len(missing) * _dq.get("missing_penalty", 8) - len(stale) * _dq.get("stale_penalty", 5)

    intraday = payload.get('intraday_strength') or {}
    next_day = payload.get('next_day_bias') or {}
    peer = (payload.get('dimension_results') or {}).get('peer_linkage') or {}
    news = payload.get('news_sentiment') or {}

    if intraday.get('status') != 'available': score -= _dq.get("intraday_unavailable", 15)
    if next_day.get('status') != 'available': score -= _dq.get("next_day_unavailable", 15)
    if peer.get('status') != 'available': score -= _dq.get("peer_linkage_unavailable", 12)
    if news.get('status') != 'available': score -= _dq.get("news_unavailable", 10)

    return max(_dq.get("min_score", 20), min(_dq.get("max_score", 98), score))


def build_final_decision(payload: dict[str, Any]) -> dict[str, Any]:
    data_score = compute_data_score(payload)
    context = extract_decision_context(payload)

    from llm.llm_client import llm_judge
    llm_result = llm_judge(DECISION_TASK, context)

    return {
        **llm_result,
        'data_completeness': data_score,
        'source': 'llm+script',
        'status': 'ready',
    }


def analyze_context_propagation(payload: dict[str, Any]) -> dict[str, Any]:
    """升级版：使用规则链引擎进行上下文传播分析"""
    from decision.context_propagation_rules import (
        ContextPropagationRules,
        build_context_from_payload,
        format_propagation_chain,
    )
    
    # 构建规则引擎需要的上下文
    context = build_context_from_payload(payload)
    
    # 执行规则链
    engine = ContextPropagationRules()
    chain = engine.evaluate_chain(context)
    
    # 格式化输出
    result = format_propagation_chain(chain)
    
    # 兼容旧格式：添加原有的文本字段
    market_context = payload.get('market_context') or {}
    sector_context = payload.get('sector_context') or {}
    news_sentiment = payload.get('news_sentiment') or {}
    
    market_bias = str(market_context.get('market_bias') or '')
    size_style = str(market_context.get('size_style') or '')
    sector_summary = str(sector_context.get('summary') or '').strip()
    sector_status = str(sector_context.get('status') or '')
    
    # 生成兼容的文本描述
    market_to_sector_parts: list[str] = []
    if market_bias:
        market_to_sector_parts.append(market_bias)
    if size_style and '当前个股本地数据缺失' not in size_style:
        market_to_sector_parts.append(size_style)
    if sector_status == 'available':
        market_to_sector_parts.append(f'板块层已确认：{sector_summary}')
    elif sector_status == 'fallback_available':
        market_to_sector_parts.append(f'板块层先按降级口径观察：{sector_summary}')
    else:
        market_to_sector_parts.append('板块层仍待补强')
    
    market_to_sector_parts.append(f'传导偏向：{render_action_bias_text(chain.action_bias)}')
    
    market_sector_news_parts: list[str] = []
    if sector_summary:
        market_sector_news_parts.append(sector_summary)
    
    news_status = str(news_sentiment.get('status') or '')
    if news_status == 'available':
        news_level = str(news_sentiment.get('level') or '')
        news_is_new = news_sentiment.get('is_new_catalyst')
        news_credibility = str(news_sentiment.get('credibility') or '')
        news_summary = tidy_sentence(news_sentiment.get('summary'))
        
        news_bits = []
        if news_level:
            news_bits.append(news_level)
        if news_is_new is True:
            news_bits.append('新催化')
        elif news_is_new is False:
            news_bits.append('旧消息')
        if news_credibility:
            news_bits.append(news_credibility)
        
        detail = ' / '.join(news_bits) if news_bits else '消息面已接入'
        market_sector_news_parts.append(f'消息面{detail}：{news_summary}')
        
        peer_position = context.get('peer_position', '')
        if peer_position:
            market_sector_news_parts.append(f'对标联动位置：{peer_position}')
    else:
        market_sector_news_parts.append('消息面当前个股本地数据缺失或联动失败')
    
    stock_to_intraday_parts: list[str] = []
    auction_overall = context.get('auction_overall', '')
    if auction_overall:
        stock_to_intraday_parts.append(f'竞价汇总意图：{auction_overall}')
    
    intraday_label = context.get('intraday_label', '')
    intraday_score = context.get('intraday_score', 0)
    if intraday_label:
        intraday_text = f'分时标签 {intraday_label}'
        if intraday_score is not None:
            intraday_text += f'（{intraday_score}分）'
        stock_to_intraday_parts.append(intraday_text)
    
    next_day_label = context.get('next_day_label', '')
    next_day_view = context.get('next_day_view', '')
    if next_day_label or next_day_view:
        nd_text = next_day_label or '次日预期'
        if next_day_view:
            nd_text += f'：{next_day_view}'
        stock_to_intraday_parts.append(nd_text)
    
    if not stock_to_intraday_parts:
        stock_to_intraday_parts.append('个股到分时的自动传导仍待补强')
    
    stock_to_intraday_parts.append(f'执行提示：{chain.execution_note}')
    
    # 更新结果
    result['market_to_sector'] = '；'.join(market_to_sector_parts)
    result['market_sector_news_to_stock'] = '；'.join(market_sector_news_parts)
    result['market_sector_stock_to_intraday'] = '；'.join(stock_to_intraday_parts)
    
    return result


def parse_date_candidates(values: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if not value:
            continue
        candidate = value[:10]
        try:
            normalized = datetime.strptime(candidate, '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            continue
        result.append(normalized)
    return result


def latest_observed_trade_date(freshness: dict) -> str | None:
    items = freshness.get('items', {})
    candidates: list[str] = []
    for item in items.values():
        if not isinstance(item, dict):
            continue
        candidates.extend(parse_date_candidates([item.get('latest_trade_date'), item.get('first_dt'), item.get('last_dt')]))
    return max(candidates) if candidates else None


def global_latest_trade_date() -> str | None:
    candidates: list[str] = []
    if MINUTE_ROOT.exists():
        for symbol_dir in MINUTE_ROOT.iterdir():
            if not symbol_dir.is_dir():
                continue
            for trade_date_dir in symbol_dir.iterdir():
                if not trade_date_dir.is_dir():
                    continue
                candidates.extend(parse_date_candidates([trade_date_dir.name]))
    return max(candidates) if candidates else None


def validation_time_guard(now: datetime, trade_date_text: str) -> dict:
    trade_date_obj = datetime.strptime(trade_date_text, '%Y-%m-%d').date()
    next_days = next_trade_dates_compact(trade_date_text, count=2)
    t_plus_1_date = datetime.strptime(next_days[0], '%Y%m%d').date() if next_days else trade_date_obj + timedelta(days=1)
    t_plus_2_date = datetime.strptime(next_days[1], '%Y%m%d').date() if len(next_days) > 1 else t_plus_1_date + timedelta(days=1)
    now_date = now.date(); now_time = now.time()
    open_time = datetime.strptime('09:30', '%H:%M').time(); close_time = datetime.strptime('15:00', '%H:%M').time()
    force_pending = False; reason = None
    if now_date <= trade_date_obj: force_pending = True; reason = 't_day_or_earlier'
    elif now_date == t_plus_1_date:
        if now_time < open_time: force_pending = True; reason = 't_plus_1_preopen'
        elif now_time <= close_time: force_pending = True; reason = 't_plus_1_intraday'
    elif now_date == t_plus_2_date and now_time < close_time:
        force_pending = True; reason = 't_plus_2_before_close'
    return {'force_pending': force_pending, 'reason': reason, 't_plus_1_trade_date': t_plus_1_date.strftime('%Y-%m-%d'), 't_plus_2_trade_date': t_plus_2_date.strftime('%Y-%m-%d'), 'now_trade_date': now_date.strftime('%Y-%m-%d')}


def build_validation_tracking(payload: dict, now: datetime) -> dict:
    trade_date = payload['trade_date']
    time_guard = validation_time_guard(now, trade_date)
    symbol_latest_date = latest_observed_trade_date(payload['freshness'])
    latest_date = global_latest_trade_date() or symbol_latest_date
    is_latest = latest_date == trade_date if latest_date else False
    should_pending = is_latest or time_guard['force_pending']
    browser_confirmed = False
    minute_item = payload['freshness'].get('items', {}).get('minute', {})
    if isinstance(minute_item, dict):
        browser_confirmed = parse_date_candidates([minute_item.get('first_dt'), minute_item.get('last_dt')]).count(trade_date) > 0
    local_synced = symbol_latest_date == trade_date if symbol_latest_date else False
    intraday = payload['intraday_strength']; next_day = payload['next_day_bias']
    checks: list[dict] = []
    checks.append({'name': '开盘集合竞价主力意图', 'prediction': '当前个股本地数据缺失', 'validation_target': '验证竞价更像抢筹、平衡还是诱多/兑现', 'status': 'pending' if should_pending else 'not_applicable'})
    if intraday['status'] == 'available':
        result = intraday['result']
        checks.append({'name': '早盘推演下午走势', 'prediction': result['afternoon_view'], 'validation_target': '验证下午真实走势是否与午间推演一致', 'status': 'pending' if should_pending else 'historical_replay'})
        checks.append({'name': '午间强度标签', 'prediction': f"{result['label']}（{result['score']}分）", 'validation_target': '验证收盘结构是否支持该午间标签', 'status': 'pending' if should_pending else 'historical_replay'})
    if next_day['status'] == 'available':
        result = next_day['result']
        checks.append({'name': '隔夜次日预期', 'prediction': f"{result['label']}｜{result['next_day_view']}", 'validation_target': '待下一交易日验证是否命中', 'status': 'pending' if should_pending else 'historical_replay'})
    t_plus_two = payload.get('t_plus_two_bias') or {}
    if t_plus_two.get('status') == 'available':
        checks.append({'name': '隔夜T+2预期', 'prediction': f"{t_plus_two.get('label')}｜{t_plus_two.get('view')}", 'validation_target': '待T+2收盘验证是否命中', 'status': 'pending' if should_pending else 'historical_replay'})
    return {'is_latest_trade_date': is_latest, 'should_force_pending_by_time': time_guard['force_pending'], 'pending_guard_reason': time_guard['reason'], 't_plus_1_trade_date': time_guard['t_plus_1_trade_date'], 't_plus_2_trade_date': time_guard['t_plus_2_trade_date'], 'now_trade_date': time_guard['now_trade_date'], 'latest_observed_trade_date': latest_date, 'symbol_latest_trade_date': symbol_latest_date, 'browser_trade_date_confirmed': browser_confirmed, 'local_data_synced_to_trade_date': local_synced, 'record_status': 'pending_validation' if should_pending else 'historical_replay', 'checks': checks}


def _next_trade_date_compact(trade_date_text: str) -> str:
    dates = next_trade_dates_compact(trade_date_text, count=1)
    return dates[0] if dates else (datetime.strptime(trade_date_text, '%Y-%m-%d').date() + timedelta(days=1)).strftime('%Y%m%d')


def build_checkpoint_entry(payload: dict, checkpoint: str) -> dict[str, Any]:
    intraday = payload.get('intraday_strength') or {}
    next_day = payload.get('next_day_bias') or {}
    entry: dict[str, Any] = {'checkpoint': checkpoint, 'logged_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'), 'symbol': payload['symbol'], 'trade_date': payload['trade_date'], 'items': []}
    if checkpoint in ('pre_open', 'open', 'noon'):
        if intraday.get('status') == 'available':
            r = intraday['result']
            entry['items'].append({'name': '早盘推演下午走势', 'kind': 'prediction', 'prediction': r.get('afternoon_view'), 'status': 'pending_close', 'note': f"午间强度标签：{r.get('label')}（{r.get('score')}分）"})
            entry['items'].append({'name': '午间强度标签', 'kind': 'prediction', 'prediction': f"{r.get('label')}（{r.get('score')}分）", 'status': 'pending_close', 'note': '等待收盘验证'})
        if next_day.get('status') == 'available':
            r = next_day['result']
            entry['items'].append({'name': '隔夜次日预期', 'kind': 'prediction', 'prediction': f"{r.get('label')}｜{r.get('next_day_view')}", 'status': 'pending_next_close', 'note': '等待次日收盘验证'})
    if checkpoint == 'afternoon':
        if intraday.get('status') == 'available':
            r = intraday['result']
            entry['items'].append({'name': '下午盘中结构', 'kind': 'observation', 'prediction': f"{r.get('label')}（{r.get('score')}分）", 'status': 'pending_close', 'note': f"盘中结论：{r.get('afternoon_view')}"})
        if next_day.get('status') == 'available':
            r = next_day['result']
            entry['items'].append({'name': '隔夜次日预期', 'kind': 'prediction', 'prediction': f"{r.get('label')}｜{r.get('next_day_view')}", 'status': 'pending_next_close', 'note': '盘中已固化次日预期，等待次日收盘验证'})
    if checkpoint == 'close':
        if intraday.get('status') == 'available':
            r = intraday['result']; s = r.get('snapshot') or {}
            entry['items'].append({'name': '早盘推演下午走势', 'kind': 'verification', 'actual': f"收盘标签 {r.get('label')}（{r.get('score')}分），收盘 {s.get('pm_close')}，午后是否突破上午高点：{'是' if s.get('broke_morning_high') else '否'}", 'status': 'verified_close', 'hit': '当前个股本地数据缺失', 'note': '已留存收盘验证素材'})
            entry['items'].append({'name': '午间强度标签', 'kind': 'verification', 'actual': f"收盘对应标签 {r.get('label')}（{r.get('score')}分）", 'status': 'verified_close', 'hit': '当前个股本地数据缺失', 'note': '可与午间预测标签做人工/脚本对照'})
        if next_day.get('status') == 'available':
            r = next_day['result']
            entry['items'].append({'name': '隔夜次日预期', 'kind': 'prediction', 'prediction': f"{r.get('label')}｜{r.get('next_day_view')}", 'status': 'pending_next_close', 'note': '已在收盘后固化次日预期'})
    if checkpoint == 'next_close':
        full_symbol = payload['symbol']; td_compact = payload['trade_date'].replace('-', '')
        next_dates = next_trade_dates_compact(payload['trade_date'], count=2)
        t1_compact = next_dates[0] if next_dates else _next_trade_date_compact(payload['trade_date'])
        t2_compact = next_dates[1] if len(next_dates) > 1 else None
        t_row = load_daily_row(full_symbol, td_compact); t1_row = load_daily_row(full_symbol, t1_compact)
        if t_row and t1_row:
            t_close = safe_float(t_row.get('close')); t1_close = safe_float(t1_row.get('close'))
            pct = ((t1_close - t_close) / t_close * 100.0) if (t_close and t1_close) else None
            entry['items'].append({'name': '隔夜次日预期', 'kind': 'verification', 'actual': f"T+1 收盘 {t1_close}，相对 T 日收盘 {t_close} 变化 {pct:.2f}%" if pct is not None else 'T+1 收盘结果已获取', 'status': 'verified_next_close', 'hit': '当前个股本地数据缺失', 'note': '已写入客观涨跌结果，可对照‘次日偏强/偏弱/分歧’判定命中'})
        else:
            entry['items'].append({'name': '隔夜次日预期', 'kind': 'verification', 'actual': '当前个股本地数据缺失', 'status': 'pending', 'hit': '当前个股本地数据缺失', 'note': '本地日线未同步到 T+1 收盘'})
        t_plus_two = payload.get('t_plus_two_bias') or {}
        if t_plus_two.get('status') == 'available':
            t2_row = load_daily_row(full_symbol, t2_compact) if t2_compact else None
            if t_row and t2_row:
                t_close = safe_float(t_row.get('close')); t2_close = safe_float(t2_row.get('close'))
                pct = ((t2_close - t_close) / t_close * 100.0) if (t_close and t2_close) else None
                entry['items'].append({'name': '隔夜T+2预期', 'kind': 'verification', 'actual': f"T+2 收盘 {t2_close}，相对 T 日收盘 {t_close} 变化 {pct:.2f}%" if pct is not None else 'T+2 收盘结果已获取', 'status': 'verified_next_close', 'hit': '当前个股本地数据缺失', 'note': '已写入客观涨跌结果，可对照 T+2 推演判定命中'})
            else:
                entry['items'].append({'name': '隔夜T+2预期', 'kind': 'verification', 'actual': '当前个股本地数据缺失', 'status': 'pending', 'hit': '当前个股本地数据缺失', 'note': '本地日线未同步到 T+2 收盘'})
    return entry


def append_checkpoint_markdown(target: Path, entry: dict[str, Any]) -> None:
    lines = ['', f"## Checkpoint {entry['checkpoint']} | {entry['logged_at']}"]
    for item in entry.get('items', []):
        lines.append(f"- {item.get('name')}")
        if item.get('kind') == 'prediction':
            lines.append(f"  - 预测：{item.get('prediction') or '当前个股本地数据缺失'}")
        else:
            lines.append(f"  - 实际结果：{item.get('actual') or '当前个股本地数据缺失'}")
        lines.append(f"  - 状态：{render_status_text(item.get('status') or 'pending')}")
        if 'hit' in item:
            lines.append(f"  - 是否命中：{item.get('hit') or '当前个股本地数据缺失'}")
        lines.append(f"  - 备注：{item.get('note') or ''}")
    with target.open('a', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def sanitize_report_name(name: str | None) -> str:
    text = str(name or '').strip()
    if not text:
        return ''
    text = text.replace('/', '_').replace('\\', '_')
    return text


def persist_pending_validation(payload: dict, checkpoint: str) -> str | None:
    tracking = payload['validation_tracking']
    if tracking['record_status'] != 'pending_validation':
        return None
    trade_date = payload['trade_date']
    td = f"{trade_date[:4]}/{trade_date[4:6]}/{trade_date[6:]}"
    target_dir = PENDING_VALIDATIONS_ROOT / td
    target_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_label = CHECKPOINT_FILE_LABELS.get(checkpoint, checkpoint or '未分类')
    stock_name = sanitize_report_name(payload.get('stock_name'))
    name_suffix = f"-{stock_name}" if stock_name else ''
    # 清理旧文件
    for old_path in target_dir.glob(f"待验证-{payload['symbol']}*-{checkpoint_label}.*"):
        if old_path.is_file():
            old_path.unlink()
    # 保存 md 报告
    target = target_dir / f"待验证-{payload['symbol']}{name_suffix}-{checkpoint_label}.md"
    target.write_text(render_pending_validation_markdown(payload), encoding='utf-8')
    # 保存 parquet 结构化数据
    parquet_target = target_dir / f"待验证-{payload['symbol']}{name_suffix}-{checkpoint_label}"
    save_analysis_parquet(parquet_target, payload, mode="overwrite")
    return str(target)
