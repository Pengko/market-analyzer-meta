#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

from signals.core.score_next_day_bias import analyze as analyze_next_day_bias
from signals.core.score_next_day_bias import build_features
from data.config_loader import cfg


def safe_float(value: str | None) -> float | None:
    if value in (None, ''):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def tidy_sentence(text: str | None) -> str:
    if not text:
        return '当前个股本地数据缺失'
    return str(text).strip().rstrip('。.!！?')


def safe_next_day(full_symbol: str, trade_date_compact: str, narrative_context: dict | None = None) -> dict:
    try:
        features, freshness = build_features(full_symbol, trade_date_compact)
        return {
            'status': 'available',
            'result': analyze_next_day_bias(features, freshness, narrative_context=narrative_context),
        }
    except BaseException as exc:  # noqa: BLE001
        if isinstance(exc, KeyboardInterrupt):
            raise
        return {'status': 'unavailable', 'reason': str(exc)}


def rolling_mean(values: list[float], n: int) -> float | None:
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def analyze_t_plus_two_bias(payload: dict[str, Any]) -> dict[str, Any]:
    next_day = payload.get('next_day_bias') or {}
    sector_context = payload.get('sector_context') or {}
    trend_structure = payload.get('trend_structure') or {}
    volatility_context = payload.get('volatility_context') or {}
    news_sentiment = payload.get('news_sentiment') or {}
    peer_linkage = (payload.get('dimension_results') or {}).get('peer_linkage') or {}
    context_propagation = payload.get('context_propagation') or {}

    if next_day.get('status') != 'available':
        return {
            'status': 'manual_pending',
            'label': '当前个股本地数据缺失',
            'score': None,
            'view': 'T+1 主推演未生成，无法继续外推 T+2',
            'signals': [],
        }

    result = next_day.get('result') or {}
    theme_trend = (sector_context.get('theme_trend') or {}).get('trend')
    theme_progression = sector_context.get('theme_progression') or {}
    next_theme = theme_progression.get('next_theme')
    current_theme = sector_context.get('top_theme')
    action_bias = str(context_propagation.get('action_bias') or '')
    peer_position = str(peer_linkage.get('target_position') or '')

    score = int(result.get('score') or 0)
    signals: list[str] = [f"T+1 基线：{result.get('label') or '当前个股本地数据缺失'}"]

    if theme_trend == '上升':
        score += 1
        signals.append('当前主题材热度上升，T+2 延续窗口仍在')
    elif theme_trend == '平稳':
        signals.append('当前主题材热度相对平稳，T+2 更看个股强弱分化')
    elif theme_trend == '回落':
        score -= 1
        signals.append('当前主题材热度回落，T+2 更容易转入分歧')
    elif theme_trend == '退潮':
        score -= 2
        signals.append('当前主题材进入退潮，T+2 承压更明显')

    if next_theme and current_theme and next_theme != current_theme:
        score -= 1
        signals.append(f'题材轮动指向 {next_theme}，原主题材 T+2 存在被抽血风险')
    elif current_theme and not next_theme:
        signals.append('未识别明显接棒题材，原主题材仍有继续博弈空间')

    trend_score = int(trend_structure.get('score') or 0)
    if trend_structure.get('status') == 'available':
        ts_bullish = cfg.decision('t2_forecast', 'trend_score', 'bullish', default=2)
        ts_bearish = cfg.decision('t2_forecast', 'trend_score', 'bearish', default=-2)
        if trend_score >= ts_bullish:
            score += 1
            signals.append('周/月结构仍偏多，T+2 下沿支撑更稳')
        elif trend_score <= ts_bearish:
            score -= 1
            signals.append('周/月结构偏弱，T+2 更难走成连续趋势')

    vol_score = int(volatility_context.get('score') or 0)
    if volatility_context.get('status') == 'available':
        vs_bearish = cfg.decision('t2_forecast', 'vol_score', 'bearish', default=0)
        vs_bullish = cfg.decision('t2_forecast', 'vol_score', 'bullish', default=0)
        if vol_score < vs_bearish:
            score -= 1
            signals.append('波动率偏高，T+2 需要防止强分歧放大')
        elif vol_score > vs_bullish:
            score += 1
            signals.append('波动率相对可控，T+2 更容易走震荡延续')

    if peer_position == '领先':
        score += 1
        signals.append('对标股联动领先，T+2 更容易争取主动')
    elif peer_position == '掉队':
        score -= 1
        signals.append('对标股联动掉队，T+2 容易继续承压')

    if action_bias == 'supportive':
        score += 1
        signals.append('上下文传导偏顺畅，T+2 可维持偏多预期')
    elif action_bias == 'conservative':
        score -= 1
        signals.append('上下文传导偏保守，T+2 需抬高确认门槛')
    elif action_bias == 'defensive':
        score -= 2
        signals.append('上下文传导偏防守，T+2 不宜按强趋势处理')

    if news_sentiment.get('status') == 'available':
        news_direction = str(news_sentiment.get('direction') or '')
        news_level = str(news_sentiment.get('level') or '')
        news_is_new = news_sentiment.get('is_new_catalyst')
        news_credibility = str(news_sentiment.get('credibility') or '')
        news_summary = tidy_sentence(news_sentiment.get('summary'))

        if news_direction == '偏多':
            score += 1
            signals.append('消息方向偏多，T+2 有望获得额外情绪支撑')
        elif news_direction == '偏空':
            score -= 1
            signals.append('消息方向偏空，T+2 容易受压')

        if news_is_new is True:
            score += 1
            signals.append('存在新催化，T+2 更容易延续关注度')
        elif news_is_new is False:
            signals.append('消息偏旧，T+2 更依赖盘口自己走出来')

        if news_level in ('国家级', '板块级'):
            score += 1
            signals.append(f'消息级别为{news_level}，T+2 影响半径更大')
        elif news_level == '个股级':
            signals.append('消息主要停留在个股级，T+2 扩散性有限')

        if news_credibility == '二手转述':
            score -= 1
            signals.append('消息可信度偏弱，T+2 需防止预期落空')
        elif news_summary and news_summary != '当前个股本地数据缺失':
            signals.append(f'消息摘要参考：{news_summary}')
    else:
        signals.append('消息面尚未稳定回填，T+2 暂按纯结构推演')

    t2_very_bullish = cfg.decision('t2_forecast', 'score_thresholds', 'very_bullish', default=5)
    t2_bullish = cfg.decision('t2_forecast', 'score_thresholds', 'bullish', default=2)
    t2_neutral = cfg.decision('t2_forecast', 'score_thresholds', 'neutral', default=0)
    t2_bearish = cfg.decision('t2_forecast', 'score_thresholds', 'bearish', default=-2)
    if score >= t2_very_bullish:
        label = 'T+2 偏强延续'
        view = '若 T+1 不走坏，T+2 更偏向继续震荡走强或尝试再冲高'
    elif score >= t2_bullish:
        label = 'T+2 震荡偏强'
        view = 'T+2 仍有修复与轮动承接，但更像震荡偏强而非单边主升'
    elif score >= t2_neutral:
        label = 'T+2 分歧轮动'
        view = 'T+2 更像强弱切换与轮动博弈，宜等胜出方向确认'
    elif score >= t2_bearish:
        label = 'T+2 偏弱承压'
        view = 'T+2 更容易冲高受阻或回落承压，主动性不足'
    else:
        label = 'T+2 转弱兑现'
        view = '若无新增催化，T+2 更像兑现或情绪退潮后的弱修复'

    return {
        'status': 'available',
        'label': label,
        'score': score,
        'view': view,
        'signals': signals,
        'theme_trend': theme_trend,
        'theme_progression': theme_progression,
    }


def analyze_trend_structure(full_symbol: str, trade_date_text: str) -> dict[str, Any]:
    from data.data_provider import get_weekly, get_monthly, get_daily
    td = trade_date_text.replace("-", "")
    w_rows = get_weekly(full_symbol, td)
    m_rows = get_monthly(full_symbol, td)
    d_row = get_daily(full_symbol, td)
    if not d_row:
        return {'status': 'manual_pending', 'summary': '日线缺失，无法判定周/月结构'}

    close = d_row.get('close')
    if close is None:
        return {'status': 'manual_pending', 'summary': '日线收盘价缺失，无法判定周/月结构'}

    summary_parts: list[str] = []
    score = 0
    week_state = '当前个股本地数据缺失'
    month_state = '当前个股本地数据缺失'
    if w_rows:
        w_close = [x.get('close') for x in w_rows if x.get('close') is not None]
        if w_close:
            w5 = rolling_mean(w_close, 5)
            w20 = rolling_mean(w_close, 20)
            last_w = w_close[-1]
            if w5 is not None and last_w >= w5:
                score += 1
                week_state = '周线在5周均线之上'
            elif w5 is not None:
                score -= 1
                week_state = '周线在5周均线之下'
            if w5 is not None and w20 is not None and w5 >= w20:
                score += 1
                week_state += '，5周均线>=20周均线'
            elif w5 is not None and w20 is not None:
                score -= 1
                week_state += '，5周均线<20周均线'
    if m_rows:
        m_close = [x.get('close') for x in m_rows if x.get('close') is not None]
        if m_close:
            m3 = rolling_mean(m_close, 3)
            m12 = rolling_mean(m_close, 12)
            last_m = m_close[-1]
            if m3 is not None and last_m >= m3:
                score += 1
                month_state = '月线在3月均线之上'
            elif m3 is not None:
                score -= 1
                month_state = '月线在3月均线之下'
            if m3 is not None and m12 is not None and m3 >= m12:
                score += 1
                month_state += '，3月均线>=12月均线'
            elif m3 is not None and m12 is not None:
                score -= 1
                month_state += '，3月均线<12月均线'

    summary_parts.append(week_state)
    summary_parts.append(month_state)
    return {
        'status': 'available',
        'score': score,
        'week_state': week_state,
        'month_state': month_state,
        'summary': '；'.join(summary_parts),
    }


def analyze_chip_structure(full_symbol: str, trade_date_text: str) -> dict[str, Any]:
    """基于 cyq_perf 的筹码结构分析。

    cyq_chips 上游数据质量普遍无效（price/percent 为占位值），不再依赖。
    cyq_perf 提供：winner_rate（获利盘比例）、weight_avg（加权平均成本）、
    cost_5/15/50/85/95pct（成本分位数），足以支撑完整的筹码分析。
    """
    from data.data_provider import get_chips_perf, get_daily
    td = trade_date_text.replace("-", "")
    d_row = get_daily(full_symbol, td)
    close = d_row.get('close') if d_row else None
    perf_rows = get_chips_perf(full_symbol, td)
    if not perf_rows:
        return {'status': 'manual_pending', 'summary': 'cyq_perf 缺失，筹码分析不可用'}

    latest = perf_rows[-1]
    winner_rate = latest.get('winner_rate')
    weight_avg = latest.get('weight_avg')
    cost_5 = latest.get('cost_5pct')
    cost_15 = latest.get('cost_15pct')
    cost_50 = latest.get('cost_50pct')
    cost_85 = latest.get('cost_85pct')
    cost_95 = latest.get('cost_95pct')

    score = 0
    parts: list[str] = []
    details: dict[str, Any] = {}

    # --- 1. 获利盘比例 ---
    if winner_rate is not None:
        trapped_rate = 100.0 - winner_rate
        details['winner_rate'] = winner_rate
        details['trapped_rate'] = trapped_rate
        chip_high = cfg.decision('chip_structure', 'winner_rate', 'high', default=0.70)
        chip_low = cfg.decision('chip_structure', 'winner_rate', 'low', default=0.30)
        if winner_rate >= chip_high * 100:
            score += 1
            parts.append(f'获利盘占比极高（{winner_rate:.1f}%），套牢盘仅{trapped_rate:.1f}%')
        elif winner_rate <= chip_low * 100:
            score -= 1
            parts.append(f'获利盘占比较低（{winner_rate:.1f}%），套牢盘{trapped_rate:.1f}%')
        else:
            parts.append(f'获利盘{winner_rate:.1f}%，套牢盘{trapped_rate:.1f}%')

    # --- 2. 加权平均成本 vs 收盘价 ---
    if close is not None and weight_avg is not None:
        cost_deviation = (close - weight_avg) / weight_avg * 100
        details['weight_avg'] = weight_avg
        details['cost_deviation_pct'] = cost_deviation
        if close >= weight_avg:
            score += 1
            parts.append(f'收盘价（{close:.2f}）高于筹码均价（{weight_avg:.2f}），偏离+{cost_deviation:.1f}%')
        else:
            score -= 1
            parts.append(f'收盘价（{close:.2f}）低于筹码均价（{weight_avg:.2f}），偏离{cost_deviation:.1f}%')

    # --- 3. 成本分位数与套牢盘区间 ---
    if cost_5 is not None and cost_95 is not None:
        cost_spread = cost_95 - cost_5
        details['cost_5pct'] = cost_5
        details['cost_15pct'] = cost_15
        details['cost_50pct'] = cost_50
        details['cost_85pct'] = cost_85
        details['cost_95pct'] = cost_95
        details['cost_spread'] = cost_spread

        # 成本集中度：中间50%筹码占全部筹码的价差比
        if cost_spread > 0 and cost_15 is not None and cost_85 is not None:
            concentration = (cost_85 - cost_15) / cost_spread
            details['cost_concentration'] = concentration
            if concentration >= 0.60:
                parts.append(f'成本高度集中（中段50%筹码占价差{concentration:.0%}），支撑/压力明确')
            elif concentration <= 0.35:
                parts.append(f'成本分散（中段50%筹码仅占价差{concentration:.0%}），支撑/压力模糊')
            else:
                parts.append(f'成本集中度中等（中段50%筹码占价差{concentration:.0%}）')

        # 套牢盘压力位：cost_85 以上为重度套牢区
        if close is not None and cost_85 is not None:
            if close < cost_85:
                above_cost_pct = (cost_85 - close) / close * 100 if close > 0 else 0
                parts.append(f'上方套牢压力：cost_85={cost_85:.2f}（高于现价{above_cost_pct:.1f}%），突破需放量')
            elif close >= cost_85:
                parts.append(f'已突破cost_85压力位（{cost_85:.2f}），上方仅剩cost_95={cost_95:.2f}套牢盘')

        # 支撑位：cost_15 以下为获利盘集中区
        if close is not None and cost_15 is not None and cost_5 is not None:
            if close > cost_15:
                parts.append(f'下方支撑：cost_15={cost_15:.2f}，cost_5={cost_5:.2f}（获利盘密集区）')

    # --- 4. 成本迁移趋势（近5日） ---
    recent = perf_rows[-5:] if len(perf_rows) >= 5 else perf_rows
    if len(recent) >= 2:
        first = recent[0]
        last = recent[-1]
        first_avg = first.get('weight_avg')
        last_avg = last.get('weight_avg')
        first_wr = first.get('winner_rate')
        last_wr = last.get('winner_rate')
        if first_avg is not None and last_avg is not None and first_avg > 0:
            avg_shift = (last_avg - first_avg) / first_avg * 100
            details['avg_cost_trend'] = avg_shift
            if avg_shift > 1:
                parts.append(f'近{len(recent)}日筹码均价上移（{first_avg:.2f}→{last_avg:.2f}，+{avg_shift:.1f}%），新进场资金成本抬升')
            elif avg_shift < -1:
                parts.append(f'近{len(recent)}日筹码均价下移（{first_avg:.2f}→{last_avg:.2f}，{avg_shift:.1f}%），低成本筹码涌入')
        if first_wr is not None and last_wr is not None:
            wr_shift = last_wr - first_wr
            details['winner_rate_trend'] = wr_shift
            if abs(wr_shift) > 5:
                trend = '上升' if wr_shift > 0 else '下降'
                parts.append(f'获利盘比例{trend}（{first_wr:.1f}%→{last_wr:.1f}%），{'获利回吐' if wr_shift < 0 else '新进资金推高成本'}')

    # --- 5. 综合评分 ---
    details['score'] = score
    details['data_date'] = latest.get('trade_date', '')

    return {
        'status': 'available',
        'score': score,
        'winner_rate': winner_rate,
        'weight_avg': weight_avg,
        'details': details,
        'summary': '；'.join(parts) if parts else '筹码特征中性',
    }


def analyze_volatility_context(full_symbol: str, trade_date_text: str) -> dict[str, Any]:
    from data.data_provider import _STOCK_ROOT
    import pandas as pd
    td = trade_date_text.replace("-", "")
    path = _STOCK_ROOT / "stk_factor_pro" / f"{full_symbol}.parquet"
    try:
        df = pd.read_parquet(path)
    except Exception:
        return {'status': 'manual_pending', 'summary': 'stk_factor_pro parquet 缺失'}
    df = df[df["trade_date"] <= td]
    if df.empty:
        return {'status': 'manual_pending', 'summary': 'stk_factor_pro 无有效数据'}
    df = df.sort_values("trade_date")
    min_samples = cfg.decision('volatility', 'min_samples', default=20)
    if len(df) < min_samples:
        return {'status': 'manual_pending', 'summary': 'stk_factor_pro 历史样本不足'}

    trs: list[float] = []
    for _, row in df.iterrows():
        high = row.get('high')
        low = row.get('low')
        pre_close = row.get('pre_close')
        if high is None or low is None:
            continue
        tr = high - low
        if pre_close not in (None, 0):
            tr = max(tr, abs(high - pre_close), abs(low - pre_close))
        trs.append(tr)

    if len(trs) < 20:
        return {'status': 'manual_pending', 'summary': '波动率样本不足'}

    atr_period = cfg.decision('volatility', 'atr_period', default=14)
    sample_days = cfg.decision('volatility', 'sample_days', default=120)
    atr14 = sum(trs[-atr_period:]) / atr_period
    recent = trs[-sample_days:] if len(trs) >= sample_days else trs[:]
    sorted_recent = sorted(recent)
    le = sum(1 for x in sorted_recent if x <= atr14)
    pct_rank = le / len(sorted_recent)
    latest_close = float(df.iloc[-1].get('close', 0) or 0)
    atr_pct = (atr14 / latest_close * 100.0) if latest_close else None

    score = 0
    level = '中波动'
    vol_high = cfg.decision('volatility', 'rank', 'high', default=0.80)
    vol_low = cfg.decision('volatility', 'rank', 'low', default=0.20)
    if pct_rank >= vol_high:
        score -= 1
        level = '高波动'
    elif pct_rank <= vol_low:
        score += 1
        level = '低波动'

    summary = f'ATR14={atr14:.4f}，近120样本分位 {pct_rank:.2%}，波动等级 {level}'
    if atr_pct is not None:
        summary += f'，约占收盘价 {atr_pct:.2f}%'

    return {
        'status': 'available',
        'score': score,
        'atr14': atr14,
        'atr_pct_of_close': atr_pct,
        'pct_rank_120': pct_rank,
        'level': level,
        'summary': summary,
    }
