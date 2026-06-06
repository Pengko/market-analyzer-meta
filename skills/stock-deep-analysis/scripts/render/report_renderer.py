#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from data.portfolio_loader import render_position_section

from data.config_loader import cfg
from data.data_access import _read_single_parquet

STOCK_DATA_ROOT = cfg.paths("stock_data_root")

STOCK_BASIC_ALL_PARQUET = STOCK_DATA_ROOT / 'stock_basic' / 'stock_basic_all.parquet'

STATUS_TEXT_MAP = {
    'available': '已生成',
    'rebuilt': '已重建',
    'fetched': '已补抓',
    'fetched_tushare_fallback': 'Tushare补抓成功',
    'fetched_tencent_fallback': '腾讯补抓成功',
    'already_available': '本地已齐',
    'browser_fetch_failed': '浏览器补抓失败',
    'skipped_not_latest_trade_date': '非最新交易日已跳过',
    'partial_available': '阶段可用',
    'fallback_available': '已回退补齐',
    'manual_pending': '待补充',
    'unavailable': '暂不可用',
    'failed': '抓取失败',
    'generated': '已生成',
    'provided': '已提供',
    'pending_started': '后台抓取中',
    'pending_running': '后台抓取中',
    'missing': '缺失',
    'stale': '已过期',
    'invalid': '无效',
    'pending_validation': '待验证',
    'historical_replay': '历史回放',
    'open_day': '交易日',
    'calendar_missing': '交易日历缺失',
    'calendar_empty': '交易日历为空',
    'closed_day_use_pretrade': '休市日已回退到前一交易日',
    'closed_day_use_latest_open': '休市日已回退到最近交易日',
    'requested_before_calendar_range': '请求日期早于交易日历范围',
    'aligned_or_not_latest': '非最新交易日或无需拦截',
    'aligned': '已对齐',
    'mixed_trade_date_context': '混合时点已降级',
}

ACTION_BIAS_TEXT_MAP = {
    'supportive': '偏支持',
    'neutral': '中性',
    'conservative': '偏谨慎',
}

ACQUISITION_METHOD_TEXT_MAP = {
    'browser_required_without_api': '需要浏览器补抓，当前未拿到结构化消息',
}

NEWS_PIPELINE_SOURCE_TEXT_MAP = {
    'existing_quant_data_news': '已有本地结构化结果',
    'hermes_sync_pipeline': '当天 Hermes 同步抓取',
    'local_sync_pipeline': '当天本地同步抓取',
    'local_fallback_pipeline': 'Hermes 失败后回退本地抓取',
    'hermes_fallback_pipeline': '本地失败后回退 Hermes 抓取',
    'latest_valid_cached_news': '最近一次有效结构化结果回退',
}

NEWS_PIPELINE_REASON_TEXT_MAP = {
    'news_capture_returned_no_articles': '未抓到有效文章',
    'news_capture_prepared_but_not_structured': '抓到候选内容但未完成结构化',
    'news_capture_backend_failed': '抓取后端执行失败',
    'news_pipeline_unavailable': '当天消息链未产出有效结果',
}


def render_status_text(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return '当前个股本地数据缺失'
    return STATUS_TEXT_MAP.get(text, text)


def render_action_bias_text(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return '当前个股本地数据缺失'
    return ACTION_BIAS_TEXT_MAP.get(text, text)


def render_acquisition_method_text(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return '当前个股本地数据缺失'
    return ACQUISITION_METHOD_TEXT_MAP.get(text, text)


def _load_stock_name_fallback(symbol: str) -> str | None:
    full_symbol = str(symbol or '').strip().upper()
    if not full_symbol:
        return None
    if not STOCK_BASIC_ALL_PARQUET.exists():
        return None
    try:
        rows = _read_single_parquet("stock_basic", "stock_basic_all.parquet")
        for row in rows:
            row_symbol = str(row.get('ts_code') or '').strip().upper()
            if row_symbol != full_symbol:
                continue
            name = str(row.get('name') or '').strip()
            return name or None
    except Exception:
        return None
    return None


def render_news_pipeline_source(meta: dict[str, Any]) -> str:
    summary = str((meta or {}).get('source_summary') or '').strip()
    if summary:
        return summary
    source = str((meta or {}).get('source') or '').strip()
    if not source:
        return '当前个股本地数据缺失'
    text = NEWS_PIPELINE_SOURCE_TEXT_MAP.get(source, source)
    requested = str((meta or {}).get('requested_executor') or '').strip()
    fallback_from = str((meta or {}).get('fallback_from') or '').strip()
    if source == 'latest_valid_cached_news':
        path = str((meta or {}).get('path') or '').strip()
        if path:
            parts = path.rsplit('_', 1)
            if len(parts) == 2 and parts[1].endswith('.json'):
                suffix = parts[1][:-5]
                if len(suffix) == 10:
                    text += f'（参考日期 {suffix}）'
    elif fallback_from:
        text += f'（原计划 {fallback_from}）'
    elif requested and source not in {'existing_quant_data_news'}:
        text += f'（原计划 {requested}）'
    return text


def _render_attempt_reason(meta: dict[str, Any]) -> str:
    reason = str((meta or {}).get('reason') or '').strip()
    if reason:
        return NEWS_PIPELINE_REASON_TEXT_MAP.get(reason, reason)
    stderr = str((meta or {}).get('stderr') or '').strip()
    if 'TargetClosedError' in stderr:
        return '浏览器启动失败'
    if 'permission denied while trying to connect to the docker API' in stderr:
        return 'Docker 权限不足'
    if stderr:
        return stderr.splitlines()[-1][:80]
    return '原因未明'


def render_news_pipeline_failure(meta: dict[str, Any]) -> str:
    summary = str((meta or {}).get('attempt_summary') or '').strip()
    if summary:
        return summary
    first = (meta or {}).get('first_attempt') or {}
    second = (meta or {}).get('second_attempt') or {}
    parts: list[str] = []
    if first:
        parts.append(
            f"{str(first.get('executor') or '首次尝试')}：{_render_attempt_reason(first)}"
        )
    if second:
        parts.append(
            f"{str(second.get('executor') or '回退尝试')}：{_render_attempt_reason(second)}"
        )
    if not parts:
        reason = str((meta or {}).get('reason') or '').strip()
        if reason:
            return NEWS_PIPELINE_REASON_TEXT_MAP.get(reason, reason)
        return '当前个股本地数据缺失'
    return '；'.join(parts)


def format_news_source(item: dict[str, str]) -> str:
    title = item.get('title') or '未命名来源'
    parts = [title]
    source = item.get('source') or ''
    published_at = item.get('published_at') or ''
    if source:
        parts.append(source)
    if published_at:
        parts.append(published_at)
    return ' | '.join(parts)


def summarize_main_news_sources(news_sentiment: dict[str, Any]) -> str:
    sources = news_sentiment.get('main_sources') or []
    if not sources:
        return '当前个股本地数据缺失'
    return '；'.join(format_news_source(item) for item in sources[:3])


def tidy_sentence(text: str | None) -> str:
    if not text:
        return '当前个股本地数据缺失'
    return str(text).strip().rstrip('。.!！?')


def render_bool_like(value: Any) -> str:
    if value is True:
        return '新催化'
    if value is False:
        return '旧消息重炒'
    if value in (None, ''):
        return '当前个股本地数据缺失'
    return str(value)


def _render_kline_sync_summary(sync_meta: dict[str, Any]) -> str:
    status = str(sync_meta.get('status') or '')
    if not status:
        return '当前个股本地数据缺失'
    if status == 'fetched':
        rebuild = sync_meta.get('rebuild') or {}
        weekly_date = rebuild.get('latest_weekly_trade_date') or '未知'
        monthly_date = rebuild.get('latest_monthly_trade_date') or '未知'
        return f"浏览器已补最新日K，并重建周/月数据（周={weekly_date}，月={monthly_date}）"
    if status == 'fetched_tushare_fallback':
        rebuild = sync_meta.get('rebuild') or {}
        weekly_date = rebuild.get('latest_weekly_trade_date') or '未知'
        monthly_date = rebuild.get('latest_monthly_trade_date') or '未知'
        return f"Tushare API 已补最新日K，并重建周/月数据（周={weekly_date}，月={monthly_date}）"
    if status == 'fetched_tencent_fallback':
        rebuild = sync_meta.get('rebuild') or {}
        weekly_date = rebuild.get('latest_weekly_trade_date') or '未知'
        monthly_date = rebuild.get('latest_monthly_trade_date') or '未知'
        return f"浏览器失败后已用腾讯报价补最新日K，并重建周/月数据（周={weekly_date}，月={monthly_date}）"
    if status == 'already_available':
        rebuild = sync_meta.get('rebuild') or {}
        weekly_date = rebuild.get('latest_weekly_trade_date') or '未知'
        monthly_date = rebuild.get('latest_monthly_trade_date') or '未知'
        return f"本地最新日K已存在，已按日线重建周/月数据（周={weekly_date}，月={monthly_date}）"
    if status == 'skipped_not_latest_trade_date':
        return f"目标日不是当前最新交易日（最新开市日 {sync_meta.get('latest_open_trade_date') or '未知'}），跳过浏览器日K补抓"
    if status == 'browser_fetch_failed':
        return '浏览器补抓最新日K失败，当前仍以本地已落地日K为准'
    return tidy_sentence(sync_meta.get('reason') or status)


def _render_factor_sync_summary(sync_meta: dict[str, Any]) -> str:
    status = str(sync_meta.get('status') or '')
    if not status:
        return '当前个股本地数据缺失'
    if status == 'rebuilt':
        latest_trade_date = sync_meta.get('latest_trade_date') or '未知'
        latest_turnover_trade_date = sync_meta.get('latest_turnover_trade_date') or latest_trade_date
        rows = sync_meta.get('rows')
        if latest_turnover_trade_date != latest_trade_date:
            return (
                f"已根据最新日K重建 stk_factor_pro（最新={latest_trade_date}，"
                f"换手口径止于 {latest_turnover_trade_date}，样本 {rows or '未知'} 条）"
            )
        return f"已根据最新日K重建 stk_factor_pro（最新={latest_trade_date}，样本 {rows or '未知'} 条）"
    return tidy_sentence(sync_meta.get('reason') or status)


def _render_mixed_trade_date_summary(context: dict[str, Any]) -> str:
    status = str((context or {}).get('status') or '')
    if not status:
        return '当前个股本地数据缺失'
    if status in {'aligned_or_not_latest', 'aligned'}:
        return str((context or {}).get('summary') or '无需降级').strip()
    return str((context or {}).get('summary') or '当前存在混合时点上下文，已降级').strip()


def render_pending_validation_markdown(payload: dict) -> str:
    return render_markdown(payload)


def _join_or_default(items: list[str] | None, default: str = '当前个股本地数据缺失', sep: str = '；') -> str:
    values = [str(item).strip() for item in (items or []) if str(item).strip()]
    return sep.join(values) if values else default


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output


def _render_data_notes(payload: dict, freshness: dict[str, Any], mixed_context: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    kline_sync = payload.get('kline_sync') or {}
    factor_sync = payload.get('factor_sync') or {}
    kline_status = str(kline_sync.get('status') or '').strip()
    factor_status = str(factor_sync.get('status') or '').strip()

    if kline_status and kline_status not in {'already_available', 'fetched', 'fetched_tushare_fallback', 'fetched_tencent_fallback'}:
        notes.append(f"最新日K同步：{render_status_text(kline_status)}")
    if factor_status and factor_status != 'rebuilt':
        notes.append(f"技术因子同步：{render_status_text(factor_status)}")

    stale_items = [item for item in (freshness.get('summary', {}).get('stale') or []) if item not in {'close_auction_tushare', 'open_auction_tushare'}]
    missing_items = freshness.get('summary', {}).get('missing') or []
    invalid_items = freshness.get('summary', {}).get('invalid') or []
    if stale_items:
        notes.append(f"存在滞后数据：{_join_or_default(stale_items, sep='、')}")
    if missing_items:
        notes.append(f"存在缺失数据：{_join_or_default(missing_items, sep='、')}")
    if invalid_items:
        notes.append(f"存在无效数据：{_join_or_default(invalid_items, sep='、')}")

    if mixed_context.get('status') == 'mixed_trade_date_context':
        notes.append(_render_mixed_trade_date_summary(mixed_context))
    elif (mixed_context.get('warning_items') or []):
        warning_items = _join_or_default(mixed_context.get('warning_items') or [], sep='、')
        notes.append(f"辅助维度存在滞后（{warning_items}），相关判断降权使用")
    return notes


def _render_session_title(session: str) -> str:
    mapping = {
        'pre_open': '收盘复盘 + 次日及后续交易日推演',
        '盘前': '收盘复盘 + 次日及后续交易日推演',
        'open': '上午盘中分析',
        '上午盘中': '上午盘中分析',
        'noon': '午间休盘分析',
        '午间休盘': '午间休盘分析',
        'afternoon': '下午盘中分析',
        '下午盘中': '下午盘中分析',
        'close': '收盘复盘 + 次日及后续交易日推演',
        '盘后': '收盘复盘 + 次日及后续交易日推演',
        'next_close': '收盘复盘 + 次日及后续交易日推演',
    }
    return mapping.get(str(session or '').strip(), '分析报告')


def _render_intraday_summary(intraday: dict[str, Any], session: str) -> str:
    if intraday.get('status') != 'available':
        return intraday.get('reason') or '当前个股本地数据缺失'
    result = intraday.get('result') or {}
    label = str(result.get('label') or '当前个股本地数据缺失')
    score = result.get('score')
    score_text = f'（{score}分）' if score is not None else ''
    session_text = str(session or '').strip()
    if session_text in {'close', '盘后', 'next_close'}:
        snapshot = result.get('snapshot') or {}
        broke_morning_high = snapshot.get('broke_morning_high')
        pm_close = snapshot.get('pm_close')
        morning_close = snapshot.get('morning_close')
        close_confirm = '午后未突破上午高点，全天更像修复博弈'
        if broke_morning_high is True:
            close_confirm = '午后有效突破上午高点，收盘结构对修复判断形成确认'
        elif (
            isinstance(pm_close, (int, float))
            and isinstance(morning_close, (int, float))
            and pm_close < morning_close
        ):
            close_confirm = '收盘弱于上午收盘，午后承接一般，修复力度有限'
        return f'{label}{score_text}；{close_confirm}'
    return f"{label}{score_text}；{result.get('afternoon_view') or '当前个股本地数据缺失'}"


def _format_float(value: Any, digits: int = 2) -> str:
    if not isinstance(value, (int, float)):
        return '当前个股本地数据缺失'
    return f'{float(value):.{digits}f}'


def _format_freshness_cell(item: dict[str, Any]) -> str:
    if not isinstance(item, dict):
        return 'missing'
    status = str(item.get('status') or '').strip() or 'missing'
    latest_trade_date = str(item.get('latest_trade_date') or '').strip()
    rows = item.get('rows')
    status_text = status
    if latest_trade_date:
        if len(latest_trade_date) == 8:
            latest_trade_date = f'{latest_trade_date[:4]}-{latest_trade_date[4:6]}-{latest_trade_date[6:8]}'
        status_text += f'（止{latest_trade_date}）'
    elif isinstance(rows, int) and status == 'available':
        status_text += f'（{rows} 行）'
    return status_text


def _combined_chip_freshness(items: dict[str, Any]) -> str:
    perf = items.get('cyq_perf') or {}
    chips = items.get('cyq_chips') or {}
    perf_status = str(perf.get('status') or 'missing').strip()
    chips_status = str(chips.get('status') or 'missing').strip()
    if perf_status == 'available' and chips_status == 'available':
        latest = str(chips.get('latest_trade_date') or perf.get('latest_trade_date') or '').strip()
        if latest and len(latest) == 8:
            latest = f'{latest[:4]}-{latest[4:6]}-{latest[6:8]}'
            return f'available（止{latest}）'
        return 'available'
    if perf_status == 'missing' and chips_status == 'missing':
        return 'missing'
    return f'{perf_status}/{chips_status}'


def _render_news_digest(news_sentiment: dict[str, Any]) -> str:
    direction = str(news_sentiment.get('direction') or '').strip()
    summary = tidy_sentence(news_sentiment.get('summary'))
    if summary == '当前个股本地数据缺失':
        return summary
    if direction:
        return f'{direction}：{summary}'
    return summary


def _build_analysis_prompts(
    payload: dict[str, Any],
    freshness: dict[str, Any],
    sector_context: dict[str, Any],
    peer_linkage: dict[str, Any],
    auction_intent: dict[str, Any],
    mixed_context: dict[str, Any],
) -> list[str]:
    prompts: list[str] = []
    freshness_items = freshness.get('items') or {}

    if mixed_context.get('warning_items'):
        warning_items = _join_or_default(mixed_context.get('warning_items') or [], sep='、')
        prompts.append(f'辅助维度存在滞后（{warning_items}），资金流与筹码相关判断已降权使用。')

    minute_item = freshness_items.get('minute') or {}
    if str(minute_item.get('status') or '').strip() != 'available':
        prompts.append('分钟线不可用，盘中结构判断仅基于已有快照和降级逻辑。')

    moneyflow_item = freshness_items.get('moneyflow') or {}
    if str(moneyflow_item.get('status') or '').strip() != 'available':
        prompts.append('主力资金流未同步到当天，资金面结论仅作辅助参考。')

    cyq_perf = freshness_items.get('cyq_perf') or {}
    cyq_chips = freshness_items.get('cyq_chips') or {}
    if any(str(item.get('status') or '').strip() != 'available' for item in (cyq_perf, cyq_chips)):
        prompts.append('筹码分布数据未完全同步到当天，筹码相关判断已降权使用。')

    if auction_intent.get('status') not in {'available', 'generated'}:
        prompts.append('集合竞价数据缺失，竞价主力意图未纳入本次结论。')

    top_theme = str(sector_context.get('top_theme') or '').strip()
    target_theme_reason = str(sector_context.get('target_theme_reason') or '').strip()
    target_theme_role = str(sector_context.get('target_theme_role') or '').strip()
    if top_theme:
        prompt = f'题材归因以本地主链最新题材映射为准，当前归入 {top_theme}'
        if target_theme_reason:
            prompt += f'；命中依据：{tidy_sentence(target_theme_reason)}'
        prompt += '。'
        prompts.append(prompt)
        if not target_theme_role or target_theme_role == '当前个股本地数据缺失':
            prompts.append('题材已识别，但题材位次未识别，无法确认是否属于龙头或前排。')
    else:
        prompts.append('本地主链未识别出稳定题材归因，板块结论仅作弱参考。')

    if peer_linkage.get('status') != 'available':
        prompts.append('对标股样本不足，本次未形成有效联动对照。')

    return prompts


def render_markdown(payload: dict) -> str:
    freshness = payload['freshness']
    intraday = payload['intraday_strength']
    next_day = payload['next_day_bias']
    capital_freshness = payload['capital_freshness']
    market_context = payload.get('market_context', {})
    sector_context = payload.get('sector_context', {})
    financing_context = payload.get('financing_context', {})
    auction_intent = payload.get('auction_intent', {})
    trend_structure = payload.get('trend_structure', {})
    chip_structure = payload.get('chip_structure', {})
    volatility_context = payload.get('volatility_context', {})
    news_sentiment = payload['news_sentiment']
    peer_linkage = payload.get('dimension_results', {}).get('peer_linkage', {})
    mixed_context = payload.get('mixed_trade_date_context', {})
    t_plus_two_bias = payload.get('t_plus_two_bias', {})
    final_decision = payload.get('final_decision', {})
    fundamental = payload.get('fundamental', {})

    payload_stock_name = str(payload.get('stock_name') or '').strip()
    if not payload_stock_name or payload_stock_name == str(payload.get('symbol') or '').strip():
        payload_stock_name = ''
    stock_name = (
        payload_stock_name
        or _load_stock_name_fallback(payload.get('symbol') or '')
        or payload['symbol']
    )
    title = _render_session_title(payload.get('current_session'))
    freshness_items = freshness.get('items') or {}
    intraday_result = intraday.get('result') or {}
    snapshot = intraday_result.get('snapshot') or {}

    intraday_summary = _render_intraday_summary(
        intraday, payload.get('checkpoint') or payload.get('current_session')
    )
    next_day_label = '当前个股本地数据缺失'
    next_day_view = '当前个股本地数据缺失'
    if next_day.get('status') == 'available':
        next_result = next_day.get('result') or {}
        next_day_label = f"{next_result.get('label')}（{next_result.get('score')}分）"
        next_day_view = next_result.get('next_day_view') or '当前个股本地数据缺失'
    elif next_day.get('reason'):
        next_day_view = next_day.get('reason')

    t2_label = t_plus_two_bias.get('label') or '当前个股本地数据缺失'
    t2_score = t_plus_two_bias.get('score')
    if t2_score is not None and t_plus_two_bias.get('label'):
        t2_label = f"{t2_label}（{t2_score}分）"

    sector_line = sector_context.get('summary') or '当前个股本地数据缺失'
    sector_role = str(sector_context.get('target_theme_role') or '').strip()
    if not sector_role or sector_role == '当前个股本地数据缺失':
        sector_role = '题材位次未识别'
    theme_leader = (
        (lambda name, symbol: f'{name}（{symbol}）' if name and symbol else (name or '题材龙头未识别'))(
            sector_context.get('theme_leader_name'),
            sector_context.get('theme_leader_symbol'),
        )
    )
    leader_prediction = ((sector_context.get('leader_prediction') or {}).get('summary')) or '当前个股本地数据缺失'
    peer_summary = peer_linkage.get('summary') or '当前个股本地数据缺失'
    trend_summary = trend_structure.get('summary') or '当前个股本地数据缺失'
    chip_summary = chip_structure.get('summary') or '当前个股本地数据缺失'
    volatility_summary = volatility_context.get('summary') or '当前个股本地数据缺失'
    financing_line = financing_context.get('summary') or '当前个股本地数据缺失'
    auction_line = auction_intent.get('summary') or '当前个股本地数据缺失'
    news_digest = _render_news_digest(news_sentiment)
    capital_label = capital_freshness.get('label') or '当前个股本地数据缺失'
    capital_summary = capital_freshness.get('summary') or '当前个股本地数据缺失'
    capital_signals = capital_freshness.get('signals') or []

    risk_items: list[str] = []
    if mixed_context.get('warning_items'):
        risk_items.append('辅助维度仍有滞后，资金流与筹码相关判断仅作参考')
    if peer_linkage.get('status') != 'available':
        risk_items.append(f'对标股联动降级：{peer_summary}')
    if auction_intent.get('status') not in {'available', 'generated'}:
        risk_items.append(f'集合竞价：{auction_line}')
    if missing := (freshness.get('summary', {}).get('missing') or []):
        risk_items.append(f'缺失数据：{_join_or_default(missing, sep="、")}')

    factor_latest = str((freshness_items.get("stk_factor_pro") or {}).get("latest_trade_date") or '').strip()
    if len(factor_latest) == 8:
        factor_latest = f'{factor_latest[:4]}-{factor_latest[4:6]}-{factor_latest[6:8]}'
    moneyflow_latest = str((freshness_items.get("moneyflow") or {}).get("latest_trade_date") or '').strip()
    if len(moneyflow_latest) == 8:
        moneyflow_latest = f'{moneyflow_latest[:4]}-{moneyflow_latest[4:6]}-{moneyflow_latest[6:8]}'
    analysis_prompts = _build_analysis_prompts(
        payload,
        freshness,
        sector_context,
        peer_linkage,
        auction_intent,
        mixed_context,
    )
    risk_items = _dedupe_preserve_order(analysis_prompts + risk_items)
    intraday_signals = intraday_result.get('signals') or []
    peer_rows = peer_linkage.get('peers', [])[:3] if peer_linkage.get('status') == 'available' else []
    final_decision_text = final_decision.get("decision") or "当前个股本地数据缺失"
    t2_view = t_plus_two_bias.get("view") or "当前个股本地数据缺失"
    key_levels = final_decision.get("key_levels") if isinstance(final_decision.get("key_levels"), dict) else {}
    observe_level = key_levels.get("observe") or "当前个股本地数据缺失"
    confirm_level = key_levels.get("confirm") or "当前个股本地数据缺失"
    invalid_level = key_levels.get("invalid") or "当前个股本地数据缺失"

    lines = [
        f'# {stock_name}({payload["symbol"]}) 深度分析报告',
        '',
        f'> 分析时间：{payload["analysis_time"]}',
        f'> 数据截止：个股日线/分钟线至 {payload["trade_date"]}，技术因子至 {factor_latest or "当前个股本地数据缺失"}，资金流至 {moneyflow_latest or "当前个股本地数据缺失"}',
        f'> 分析类型：{title}',
        '',
        '---',
        '',
        '## 一、大盘与板块环境',
        '',
        '### 1.1 大盘氛围',
        f'- {market_context.get("summary") or "当前个股本地数据缺失"}',
        '',
        '### 1.2 板块表现',
        f'- {sector_line}',
        f'- 题材位次：{sector_role}',
        f'- 题材龙头：{theme_leader}',
        f'- 龙头切换推演：{leader_prediction}',
        '',
        '---',
        '',
        '## 二、对标股联动分析',
        '',
    ]
    # --- 目标股在板块中的角色 ---
    target_position = peer_linkage.get('target_position') or ''
    target_pct_in_linkage = peer_linkage.get('target_pct_chg')
    if sector_role and sector_role != '题材位次未识别':
        _role_line = f'{stock_name}在板块中的角色：{sector_role}'
        if target_position:
            _role_line += f'（联动排名：{target_position}）'
        if target_pct_in_linkage is not None:
            _role_line += f'，当日涨跌 {target_pct_in_linkage:+.2f}%'
        lines.append(f'- **{_role_line}**')
    elif target_position:
        _role_line = f'{stock_name}在板块中的联动排名：{target_position}'
        if target_pct_in_linkage is not None:
            _role_line += f'，当日涨跌 {target_pct_in_linkage:+.2f}%'
        lines.append(f'- **{_role_line}**')
    if peer_rows:
        lines.extend([
            '| 对标股 | 角色 | 最新表现 | 启示 |',
            '|---|---|---|---|',
        ])
        for peer in peer_rows:
            name = peer.get("name") or "当前个股本地数据缺失"
            symbol = peer.get("symbol") or ""
            peer_label = f'{name}({symbol})' if symbol else str(name)
            latest = str(peer.get("latest_performance") or "当前个股本地数据缺失").replace('|', '／')
            inspiration = str(peer.get("inspiration") or "当前个股本地数据缺失").replace('|', '／')
            lines.append(f'| {peer_label} | {peer.get("role") or "当前个股本地数据缺失"} | {latest} | {inspiration} |')
        lines.extend([
            '',
            f'**联动结论：**{peer_summary}',
        ])
    else:
        lines.append(f'- 对标股联动降级：{peer_summary}')

    # 基本面速览
    if fundamental.get('status') == 'available':
        pe = fundamental.get('pe')
        pb = fundamental.get('pb')
        total_mv = fundamental.get('total_mv')
        circ_mv = fundamental.get('circ_mv')
        pe_text = f'{pe:.2f}' if pe is not None else '当前个股本地数据缺失'
        pb_text = f'{pb:.2f}' if pb is not None else '当前个股本地数据缺失'
        total_mv_text = f'{total_mv:.2f}亿' if total_mv is not None else '当前个股本地数据缺失'
        circ_mv_text = f'{circ_mv:.2f}亿' if circ_mv is not None else '当前个股本地数据缺失'
        # 从 final_decision 中提取基本面过滤结果
        fd_bullish = final_decision.get('bullish_dimensions') or []
        fd_bearish = final_decision.get('bearish_dimensions') or []
        fd_conflicts = final_decision.get('conflicts') or []
        fund_bullish = [d for d in fd_bullish if isinstance(d, str) and d.startswith('基本面：')]
        fund_bearish = [d for d in fd_bearish if isinstance(d, str) and d.startswith('基本面：')]
        fund_conflicts = [d for d in fd_conflicts if isinstance(d, str) and '基本面：' in d]
        filter_lines = []
        if fund_bullish:
            filter_lines.append(f"- 过滤提示：{'、'.join(fund_bullish)}")
        if fund_bearish:
            filter_lines.append(f"- 过滤警告：{'、'.join(fund_bearish)}")
        if fund_conflicts:
            filter_lines.append(f"- 过滤冲突：{'、'.join(fund_conflicts)}")
        fundamental_lines = [
            '### 3.0 基本面速览',
            f'- PE：{pe_text}｜PB：{pb_text}｜总市值：{total_mv_text}｜流通市值：{circ_mv_text}',
        ]
        fundamental_lines.extend(filter_lines)
        fundamental_lines.append('')
    else:
        fundamental_lines = [
            '### 3.0 基本面速览',
            f'- {fundamental.get("reason") or "当前个股本地数据缺失"}',
            '',
        ]

    lines.extend([
        '',
        '---',
        '',
        f'## 三、{stock_name}({payload["symbol"]}) 个股深度分析',
        '',
    ])
    lines.extend(fundamental_lines)
    lines.extend([
        '### 3.1 趋势结构（日线级）',
        f'- 结构定性：{intraday_summary}',
        f'- 趋势结构：{trend_summary}',
        f'- 筹码结构：{chip_summary}',
        f'- 波动率：{volatility_summary}',
        '',
        '### 3.2 分时主力分析',
    ])
    if intraday.get('status') == 'available':
        lines.extend([
            f'- 当日上午：开 {_format_float(snapshot.get("morning_open"))} → 收 {_format_float(snapshot.get("morning_close"))}，高 {_format_float(snapshot.get("morning_high"))}，低 {_format_float(snapshot.get("morning_low"))}',
            f'- 上午成交额：{_format_float(snapshot.get("morning_amount_yi"))} 亿',
            f'- 收盘位置：收 {_format_float(snapshot.get("pm_close"))}，午后是否突破上午高点：{"是" if snapshot.get("broke_morning_high") else "否"}',
        ])
    else:
        lines.append(f'- 分时数据：{intraday.get("reason") or "当前个股本地数据缺失"}')
    if capital_signals:
        lines.append(f'- 资金信号：{_join_or_default(capital_signals, sep="；")}')
    if intraday_signals:
        lines.append('- 关键时窗：')
        for signal in intraday_signals[:8]:
            lines.append(f'  - {signal}')

    # --- 筹码详细分析（基于 cyq_perf） ---
    chip_details = chip_structure.get('details') or {}
    chip_lines: list[str] = []
    if chip_structure.get('status') == 'available' and chip_details:
        wr = chip_details.get('winner_rate')
        wa = chip_details.get('weight_avg')
        c5 = chip_details.get('cost_5pct')
        c15 = chip_details.get('cost_15pct')
        c50 = chip_details.get('cost_50pct')
        c85 = chip_details.get('cost_85pct')
        c95 = chip_details.get('cost_95pct')
        if wr is not None:
            chip_lines.append(f'  获利盘比例：{wr:.1f}%（套牢盘{100 - wr:.1f}%）')
        if wa is not None:
            chip_lines.append(f'  加权平均成本：{wa:.2f}元')
        if c5 is not None and c95 is not None:
            chip_lines.append(f'  成本区间：{c5:.2f}（5%分位）→ {c50:.2f}（中位）→ {c95:.2f}（95%分位）')
        conc = chip_details.get('cost_concentration')
        if conc is not None:
            chip_lines.append(f'  成本集中度：{conc:.0%}（中段50%筹码占价差比）')
        dev = chip_details.get('cost_deviation_pct')
        if dev is not None:
            chip_lines.append(f'  现价偏离筹码均价：{dev:+.1f}%')
        # 成本迁移趋势
        avg_trend = chip_details.get('avg_cost_trend')
        wr_trend = chip_details.get('winner_rate_trend')
        if avg_trend is not None or wr_trend is not None:
            trend_parts = []
            if avg_trend is not None:
                trend_parts.append(f'均价趋势{avg_trend:+.1f}%')
            if wr_trend is not None:
                trend_parts.append(f'获利盘趋势{wr_trend:+.1f}%')
            chip_lines.append(f'  近5日趋势：{"，".join(trend_parts)}')
    else:
        chip_lines.append(f'  {chip_summary}')

    lines.extend([
        '',
        '### 3.3 筹码与资金面',
        f'- 融资融券：{financing_line}',
        f'- 集合竞价：{auction_line}',
        f'- 主力资金新鲜度：{capital_label}｜{capital_summary}',
        '',
        '**筹码结构分析**（数据来源：cyq_perf）',
        *chip_lines,
        '',
        '### 3.4 消息面',
        f'- {news_digest}',
        '',
        '---',
        '',
        '## 四、技术因子复核',
        '',
        f'- 趋势结构：{trend_summary}',
        f'- 筹码结构：{chip_summary}',
        f'- 波动率：{volatility_summary}',
        '',
        '---',
        '',
        '## 五、交易结论与推演',
        '',
        f'- 建仓结论：{final_decision_text}',
        f'- T+1：{next_day_label}',
        f'- T+1 预期：{next_day_view}',
        f'- T+2：{t2_label}',
        f'- T+2 预期：{t2_view}',
    ])
    # 插入个人持仓分析（若 portfolio 数据存在）
    portfolio_data = payload.get("portfolio")
    if portfolio_data:
        position_md = render_position_section(
            payload["symbol"], payload.get("current_price")
        )
        if position_md:
            try:
                idx = lines.index('## 一、大盘与板块环境')
                lines.insert(idx, '')
                lines.insert(idx, position_md)
            except ValueError:
                pass
    bullish_dimensions = final_decision.get('bullish_dimensions') or []
    bearish_dimensions = final_decision.get('bearish_dimensions') or []
    conflicts = final_decision.get('conflicts') or []
    if bullish_dimensions:
        lines.append(f'- 偏多依据：{_join_or_default(bullish_dimensions, sep="、")}')
    if bearish_dimensions:
        lines.append(f'- 偏空依据：{_join_or_default(bearish_dimensions, sep="、")}')
    if conflicts:
        lines.append(f'- 冲突项：{_join_or_default(conflicts, sep="、")}')
    lines.extend([
        f'- 前提条件：{"；".join(final_decision.get("preconditions") or ["当前个股本地数据缺失"])}',
        f'- 失效条件：{"；".join(final_decision.get("invalidations") or ["当前个股本地数据缺失"])}',
        f'- 关键价位：观察 {observe_level} / 确认 {confirm_level} / 失效 {invalid_level}',
        '',
        '---',
        '',
        '## 六、风险提示',
        '',
    ])
    if risk_items:
        for item in risk_items:
            lines.append(f'- {item}')
    else:
        lines.append('- 当前未见额外降级风险。')
    
    # 历史对比模块
    history_comparison = payload.get("history_comparison", {})
    if history_comparison.get("status") == "available":
        lines.extend([
            '',
            '---',
            '',
            '## 七、历史对比',
            '',
            f'- 上次分析日期：{history_comparison.get("previous_date")}',
        ])
        changes = history_comparison.get("changes", {})
        if "price" in changes:
            price_info = changes["price"]
            direction = "↑" if price_info["change"] > 0 else "↓" if price_info["change"] < 0 else "→"
            lines.append(f'- 价格变化：{price_info["previous"]:.2f} → {price_info["current"]:.2f} ({direction} {price_info["change"]:+.2f}, {price_info["change_pct"]:+.2f}%)')
        if "decision" in changes:
            decision_info = changes["decision"]
            if decision_info["changed"]:
                lines.append(f'- 决策变化：{decision_info["previous"]} → {decision_info["current"]}')
            else:
                lines.append(f'- 决策一致：{decision_info["current"]}')
        if "winner_rate" in changes:
            wr_info = changes["winner_rate"]
            lines.append(f'- 获利盘变化：{wr_info["previous"]:.1f}% → {wr_info["current"]:.1f}% ({wr_info["change"]:+.2f}%)')
        if "atr14" in changes:
            atr_info = changes["atr14"]
            lines.append(f'- 波动率变化：{atr_info["previous"]:.2f} → {atr_info["current"]:.2f} ({atr_info["change"]:+.2f})')
    elif history_comparison.get("status") == "no_history":
        lines.extend([
            '',
            '---',
            '',
            '## 七、历史对比',
            '',
            '- 首次分析该股票，无历史对比数据',
        ])
    
    return '\n'.join(lines)
