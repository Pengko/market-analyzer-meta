#!/usr/bin/env python3
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from common import STOCK_DATA_ROOT

from data.config_loader import cfg


def safe_float(value: str | None) -> float | None:
    if value in (None, ''):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def load_latest_market_moneyflow(trade_date_text: str) -> dict | None:
    base = STOCK_DATA_ROOT / 'moneyflow_data' / 'market'
    candidates = [
        base / 'dc' / f"moneyflow_dc_market_{trade_date_text.replace('-', '')}.csv",
        base / 'dc_market' / f"moneyflow_mkt_dc_{trade_date_text.replace('-', '')}.csv",
    ]
    for path in candidates:
        if not path.exists():
            continue
        with path.open('r', encoding='utf-8', newline='') as f:
            rows = list(csv.DictReader(f))
        if rows:
            return rows[0]
    return None


def load_index_row(index_code: str, trade_date_text: str) -> dict[str, Any] | None:
    from data.data_provider import get_index_daily
    compact = trade_date_text.replace("-", "")
    return get_index_daily(index_code, compact)


def load_index_rows(index_code: str, trade_date_text: str, limit: int = 30) -> list[dict[str, Any]]:
    from data.data_provider import get_index_daily_rows
    compact = trade_date_text.replace("-", "")
    return get_index_daily_rows(index_code, compact, limit)


def infer_related_indexes(full_symbol: str) -> list[dict[str, str]]:
    code = full_symbol.split('.')[0]
    suffix = full_symbol.split('.')[-1]
    indexes: list[dict[str, str]] = []
    if suffix == 'SH':
        indexes.append({'code': '000001.SH', 'name': '上证指数', 'reason': '按沪市股票归属推断'})
        indexes.append({'code': '000300.SH', 'name': '沪深300', 'reason': '作为跨市场核心宽基参考'})
    elif suffix == 'SZ':
        indexes.append({'code': '399001.SZ', 'name': '深证成指', 'reason': '按深市股票归属推断'})
        if code.startswith('300'):
            indexes.append({'code': '399006.SZ', 'name': '创业板指', 'reason': '按创业板股票归属推断'})
        else:
            indexes.append({'code': '000300.SH', 'name': '沪深300', 'reason': '作为跨市场核心宽基参考'})
    return indexes


def analyze_market_context(full_symbol: str, trade_date_text: str) -> dict:
    related_indexes = infer_related_indexes(full_symbol)
    index_map = {
        '000001.SH': '上证指数',
        '399001.SZ': '深证成指',
        '399006.SZ': '创业板指',
        '000300.SH': '沪深300',
    }
    index_snapshot: dict[str, dict[str, Any]] = {}
    breadth_score = 0
    summary_parts: list[str] = []

    for code, name in index_map.items():
        row = load_index_row(code, trade_date_text)
        if not row:
            continue
        pct = safe_float(row.get('pct_chg'))
        close = safe_float(row.get('close'))
        amount = safe_float(row.get('amount'))
        index_snapshot[code] = {
            'name': name,
            'trade_date': row.get('trade_date'),
            'pct_chg': pct,
            'close': close,
            'amount': amount,
        }
        if pct is not None:
            if pct > 0:
                breadth_score += 1
            elif pct < 0:
                breadth_score -= 1

    row = load_latest_market_moneyflow(trade_date_text)
    net_amount = safe_float(row.get('net_amount')) if row else None
    amount = safe_float(row.get('amount')) if row else None

    sse = index_snapshot.get('000001.SH')
    sz = index_snapshot.get('399001.SZ')
    gem = index_snapshot.get('399006.SZ')
    hs300 = index_snapshot.get('000300.SH')

    if sse and sse.get('pct_chg') is not None:
        summary_parts.append(f"上证 {sse['pct_chg']:+.2f}%")
    if sz and sz.get('pct_chg') is not None:
        summary_parts.append(f"深成 {sz['pct_chg']:+.2f}%")
    if gem and gem.get('pct_chg') is not None:
        summary_parts.append(f"创业板 {gem['pct_chg']:+.2f}%")
    if hs300 and hs300.get('pct_chg') is not None:
        summary_parts.append(f"沪深300 {hs300['pct_chg']:+.2f}%")

    bs_cfg_strong = cfg.decision('market_sentiment', 'breadth_score', 'strong_bullish', default=3)
    bs_cfg_mild = cfg.decision('market_sentiment', 'breadth_score', 'mild_bullish', default=1)
    bs_cfg_mild_bear = cfg.decision('market_sentiment', 'breadth_score', 'mild_bearish', default=-1)
    bs_cfg_strong_bear = cfg.decision('market_sentiment', 'breadth_score', 'strong_bearish', default=-3)
    if breadth_score >= bs_cfg_strong:
        market_bias = '指数共振偏强'
    elif breadth_score >= bs_cfg_mild:
        market_bias = '指数多数偏强'
    elif breadth_score <= bs_cfg_strong_bear:
        market_bias = '指数共振偏弱'
    elif breadth_score <= bs_cfg_mild_bear:
        market_bias = '指数多数偏弱'
    else:
        market_bias = '指数分化'

    size_style = '风格当前个股本地数据缺失'
    if hs300 and gem and hs300.get('pct_chg') is not None and gem.get('pct_chg') is not None:
        diff = float(gem['pct_chg']) - float(hs300['pct_chg'])
        sd_cfg_small = cfg.decision('market_sentiment', 'style_diff', 'small_cap_threshold', default=0.7)
        sd_cfg_large = cfg.decision('market_sentiment', 'style_diff', 'large_cap_threshold', default=-0.7)
        if diff >= sd_cfg_small:
            size_style = '小盘成长占优'
        elif diff <= sd_cfg_large:
            size_style = '大盘权重占优'
        else:
            size_style = '大小盘相对均衡'

    flow_text = None
    if net_amount is not None:
        if net_amount > 0:
            flow_text = f'市场资金面偏正，净流入约 {net_amount:.2f}'
        elif net_amount < 0:
            flow_text = f'市场资金面偏弱，净流出约 {abs(net_amount):.2f}'
        else:
            flow_text = '市场资金面中性'
    if amount is not None:
        flow_text = f'{flow_text}；市场成交额约 {amount:.2f}' if flow_text else f'市场成交额约 {amount:.2f}'
    if flow_text:
        summary_parts.append(flow_text)
    summary_parts.append(market_bias)
    summary_parts.append(size_style)

    related_index_snapshots: list[dict[str, Any]] = []
    for item in related_indexes:
        row = index_snapshot.get(item['code'])
        if not row:
            row = load_index_row(item['code'], trade_date_text)
        if not row:
            continue
        related_index_snapshots.append({
            'code': item['code'],
            'name': item['name'],
            'reason': item['reason'],
            'pct_chg': row.get('pct_chg') if isinstance(row, dict) else None,
            'close': row.get('close') if isinstance(row, dict) else None,
            'trade_date': row.get('trade_date') if isinstance(row, dict) else None,
        })

    return {
        'status': 'available',
        'summary': '；'.join(summary_parts) if summary_parts else '市场层已接入，但有效字段有限',
        'source': '指数数据/index_daily + moneyflow_data/market',
        'net_amount': net_amount,
        'amount': amount,
        'index_snapshot': index_snapshot,
        'related_indexes': related_index_snapshots,
        'market_bias': market_bias,
        'size_style': size_style,
        'browser_confirmation': 'pending',
    }
