#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any

from common import STOCK_DATA_ROOT
from data.data_access import load_dc_concepts_local, load_dc_concept_constituents_local

THEME_DATA_ROOT = STOCK_DATA_ROOT / 'theme_data'
SCRIPT_ROOT = Path(__file__).resolve().parent.parent
MOBILE_SUBTHEME_DISCOVERY_SCRIPT = SCRIPT_ROOT / 'mobile' / 'discover_ths_mobile_subthemes.py'
MOBILE_STOCK_CONCEPTS_SCRIPT = SCRIPT_ROOT / 'mobile' / 'discover_ths_mobile_stock_concepts.py'
MOBILE_THEME_LEADERS_SCRIPT = SCRIPT_ROOT / 'mobile' / 'discover_ths_mobile_theme_leaders.py'
from data.config_loader import cfg

_tmp_dir = cfg.paths('temp_dir')
MOBILE_SUBTHEME_CACHE_DIR = _tmp_dir / 'ths_mobile_subthemes_cache'
MOBILE_STOCK_CONCEPTS_CACHE_DIR = _tmp_dir / 'ths_mobile_stock_concepts_cache'
MOBILE_THEME_LEADERS_CACHE_DIR = _tmp_dir / 'ths_mobile_theme_leaders_cache'
MOBILE_SUBTHEME_TRIGGER_THEMES = {
    '固态电池', '人工智能', '消费电子概念', '商业航天', '无人机', '数据中心', '新能源汽车', '储能', '车联网(车路协同)'
}
EVENT_THEMES = {
    '回购增持再贷款', '并购重组', '股权转让(并购重组)', '融资融券', '回购', '增持'
}
SUBTHEME_SYNONYM_GROUPS = {
    '固态铜箔': ['铜箔', '通孔箔', '集流体', '复合集流体', '电化学集流体', '碳涂层'],
    '富锂锰基': ['富锂锰基'],
    '铝塑膜': ['铝塑膜'],
    '硫化物': ['硫化物'],
    '高镍': ['高镍'],
    '硅基负极': ['硅基负极'],
    '电池设备': ['设备', '产线', 'PACK', '模组', '焊接', '检测'],
    '电池厂商': ['电池', '电芯', '储能电芯'],
    '无人机': ['无人机', '低空'],
    '卫星导航': ['导航', '北斗', '卫星导航'],
    '毫米波雷达': ['毫米波', '雷达'],
    '消费电子概念': ['消费电子', '电子', '主板'],
}

SECTOR_CYCLE_TASK = """判断题材当前所处的阶段。
返回 JSON: {"cycle": "加强"|"分化"|"轮动"|"退潮", "confidence": 0-1, "reasoning": "简要推理依据"}"""

SECTOR_TREND_TASK = """判断题材热度趋势。
返回 JSON: {"trend": "上升"|"平稳"|"回落"|"退潮", "confidence": 0-1, "signals": ["信号1", "信号2"]}"""

SECTOR_PROGRESSION_TASK = """判断题材轮动方向，是否有接棒题材。
返回 JSON: {"next_theme": "题材名"|null, "confidence": 0-1, "reasoning": "简要推理"}"""



def safe_float(value: str | None) -> float | None:
    if value in (None, ''):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def is_event_theme(name: str) -> bool:
    text = str(name or '').strip()
    return text in EVENT_THEMES


def load_stock_name(symbol: str) -> str | None:
    path = STOCK_DATA_ROOT / 'stock_basic' / 'stock_basic_all.csv'
    if not path.exists():
        return None
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            row_symbol = (row.get('ts_code') or row.get('\ufeffts_code') or '').strip()
            if row_symbol == symbol:
                return (row.get('name') or '').strip() or None
    return None


@lru_cache(maxsize=1)
def load_stock_basic_index() -> dict[str, dict[str, str]]:
    path = STOCK_DATA_ROOT / 'stock_basic' / 'stock_basic_all.csv'
    if not path.exists():
        return {}
    index: dict[str, dict[str, str]] = {}
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            ts_code = (row.get('ts_code') or row.get('\ufeffts_code') or '').strip()
            if not ts_code:
                continue
            index[ts_code] = {
                'ts_code': ts_code,
                'name': (row.get('name') or '').strip(),
                'area': (row.get('area') or '').strip(),
                'industry': (row.get('industry') or '').strip(),
                'market': (row.get('market') or '').strip(),
            }
    return index


def infer_sector_cycle_status(concept_name: str | None, trade_date: str | None,
                               kpl_concepts: list[dict],
                               theme_leader_name: str | None = None) -> dict[str, Any]:
    if not concept_name or not trade_date:
        return {'status': 'insufficient_data', 'cycle': None, 'confidence': 0, 'signals': []}

    total = len(kpl_concepts)
    hots = sorted([int(r.get('hot_num') or 0) for r in kpl_concepts], reverse=True)
    top3_concentration = sum(hots[:3]) / sum(hots) if hots and sum(hots) > 0 else 0

    context = {
        "concept_name": concept_name,
        "total_constituents": total,
        "top3_concentration": round(top3_concentration, 3),
        "leader_name": theme_leader_name,
        "top_hot_values": hots[:5],
    }

    from llm.llm_client import llm_judge
    result = llm_judge(SECTOR_CYCLE_TASK, context)
    status = 'analyzed' if result.get('cycle') else 'insufficient_data'
    return {'status': status, 'cycle': result.get('cycle'),
            'confidence': result.get('confidence', 0),
            'signals': [result.get('reasoning', '')] if result.get('reasoning') else []}


def analyze_theme_trend(concept_name: str | None, trade_date: str | None) -> dict[str, Any]:
    if not concept_name or not trade_date:
        return {'status': 'insufficient_data', 'trend': None, 'confidence': 0, 'signals': []}

    kpl_root = THEME_DATA_ROOT / 'kpl_concept_cons'
    kpl_files = sorted(kpl_root.glob('kpl_concept_cons_*.csv'), reverse=True)[:2]
    current_hot = past_hot = None
    for idx, kpl_file in enumerate(kpl_files):
        file_date = kpl_file.stem.rsplit('_', 1)[-1]
        if file_date > trade_date.replace('-', ''):
            continue
        for row in csv.DictReader(kpl_file.open('r', encoding='utf-8-sig', newline='')):
            if str(row.get('name') or '').strip() == concept_name:
                hot = int(row.get('hot_num') or 0)
                if idx == 0:
                    current_hot = hot
                elif idx == 1:
                    past_hot = hot
                break

    context = {
        "concept_name": concept_name,
        "current_hot": current_hot,
        "past_hot": past_hot,
        "hot_change_pct": round((current_hot - past_hot) / past_hot * 100, 1) if current_hot and past_hot and past_hot > 0 else None,
    }

    from llm.llm_client import llm_judge
    result = llm_judge(SECTOR_TREND_TASK, context)
    status = 'analyzed' if result.get('trend') else 'insufficient_data'
    return {'status': status, 'trend': result.get('trend'),
            'current_hot': current_hot, 'past_hot': past_hot,
            'confidence': result.get('confidence', 0),
            'signals': result.get('signals', [])}


def infer_theme_progression(current_theme: str | None, trade_date: str | None) -> dict[str, Any]:
    if not current_theme or not trade_date:
        return {'status': 'insufficient_data', 'next_theme': None, 'confidence': 0, 'reasoning': []}

    kpl_root = THEME_DATA_ROOT / 'kpl_concept_cons'
    kpl_files = sorted(kpl_root.glob('kpl_concept_cons_*.csv'), reverse=True)[:10]
    theme_hots: dict[str, list[tuple[str, int]]] = {}
    for kpl_file in kpl_files:
        file_date = kpl_file.stem.rsplit('_', 1)[-1]
        if not file_date.isdigit() or file_date > trade_date.replace('-', ''):
            continue
        try:
            with kpl_file.open('r', encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    theme = str(row.get('name') or '').strip()
                    if not theme: continue
                    hot = int(row.get('hot_num') or 0)
                    theme_hots.setdefault(theme, []).append((file_date, hot))
        except Exception:
            continue

    current_hot = 0
    if current_theme in theme_hots:
        sorted_hots = sorted(theme_hots[current_theme], key=lambda x: x[0], reverse=True)
        if sorted_hots:
            current_hot = sorted_hots[0][1]

    candidates = []
    for theme, hots in theme_hots.items():
        if theme == current_theme: continue
        sorted_hots = sorted(hots, key=lambda x: x[0], reverse=True)
        if not sorted_hots: continue
        recent_hot = sorted_hots[0][1]
        past_hot = sorted_hots[1][1] if len(sorted_hots) > 1 else 0
        if past_hot > 0:
            change = (recent_hot - past_hot) / past_hot * 100
            if change > 10 and recent_hot > current_hot * 0.5:
                candidates.append((theme, recent_hot, change))
    candidates.sort(key=lambda x: x[1], reverse=True)

    context = {
        "current_theme": current_theme,
        "current_hot": current_hot,
        "candidates": [{"theme": c[0], "hot": c[1], "change_pct": round(c[2], 1)} for c in candidates[:5]],
    }

    from llm.llm_client import llm_judge
    result = llm_judge(SECTOR_PROGRESSION_TASK, context)
    return {'status': 'analyzed' if result.get('next_theme') else 'insufficient_data',
            'current_theme': current_theme,
            'next_theme': result.get('next_theme'),
            'confidence': result.get('confidence', 0),
            'candidates': candidates[:3],
            'reasoning': [result.get('reasoning', '')] if result.get('reasoning') else []}


@lru_cache(maxsize=64)
def fetch_browser_concepts(symbol: str) -> list[str]:
    secid = symbol.split('.')[0]
    url = f'https://stockpage.10jqka.com.cn/{secid}/'
    try:
        result = subprocess.run(['curl', '-L', '-sS', '--max-time', '8', url], capture_output=True, text=True, check=True, env={**os.environ, 'HTTP_PROXY': '', 'HTTPS_PROXY': '', 'ALL_PROXY': '', 'http_proxy': '', 'https_proxy': '', 'all_proxy': ''})
    except Exception:
        return []
    text = result.stdout or ''
    match = re.search(r'<dt>涉及概念：</dt>\s*<dd title="([^"]+)"', text)
    if not match:
        return []
    return [item.strip() for item in match.group(1).split('，') if item.strip()]


def fetch_mobile_stock_concepts(symbol: str, stock_name: str | None = None) -> dict[str, Any] | None:
    if not MOBILE_STOCK_CONCEPTS_SCRIPT.exists():
        return None
    MOBILE_STOCK_CONCEPTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.md5(symbol.encode('utf-8')).hexdigest()
    cache_path = MOBILE_STOCK_CONCEPTS_CACHE_DIR / f'{cache_key}.json'
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
            cached['cache_hit'] = True
            return cached
        except Exception:
            pass
    command = ['python3', str(MOBILE_STOCK_CONCEPTS_SCRIPT), '--symbol', symbol, '--format', 'json']
    if stock_name:
        command.extend(['--expect-name', stock_name])
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=cfg.network('subprocess_timeout', 'short', default=20))
        payload = json.loads(result.stdout)
    except Exception as exc:
        return {'status': 'failed', 'summary': f'移动端个股概念获取失败：{exc}', 'concepts': [], 'source': '移动端同花顺', 'cache_hit': False}
    resolved = {
        'status': 'available',
        'summary': f"移动端同花顺概念：{'、'.join(payload.get('concepts') or []) or '无'}",
        'concepts': list(payload.get('concepts') or []),
        'source': payload.get('source') or '移动端同花顺',
        'stock_page': payload.get('stock_page') or {},
        'search_result': payload.get('search_result') or {},
        'cache_hit': False,
    }
    try:
        cache_path.write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass
    return resolved


def should_trigger_mobile_subtheme_discovery(sector_context: dict[str, Any]) -> bool:
    top_theme = str(sector_context.get('top_theme') or '').strip()
    browser_concepts = [str(item).strip() for item in sector_context.get('browser_concepts') or [] if str(item).strip()]
    if top_theme in MOBILE_SUBTHEME_TRIGGER_THEMES:
        return True
    return any(concept in MOBILE_SUBTHEME_TRIGGER_THEMES for concept in browser_concepts[:5])


def discover_mobile_subthemes_if_needed(sector_context: dict[str, Any]) -> dict[str, Any] | None:
    if not should_trigger_mobile_subtheme_discovery(sector_context):
        return None
    top_theme = str(sector_context.get('top_theme') or '').strip()
    if not top_theme or not MOBILE_SUBTHEME_DISCOVERY_SCRIPT.exists():
        return None
    MOBILE_SUBTHEME_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.md5(top_theme.encode('utf-8')).hexdigest()
    cache_path = MOBILE_SUBTHEME_CACHE_DIR / f'{cache_key}.json'
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
            cached['status'] = cached.get('status') or 'available'
            cached['summary'] = cached.get('summary') or f"手机端同花顺发现小题材：{'、'.join(cached.get('subthemes') or []) or '无'}"
            cached['cache_hit'] = True
            return cached
        except Exception:
            pass
    command = ['python3', str(MOBILE_SUBTHEME_DISCOVERY_SCRIPT), '--query', top_theme, '--expect-name', top_theme, '--format', 'json']
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=cfg.network('subprocess_timeout', 'long', default=120))
        payload = json.loads(result.stdout)
    except Exception as exc:
        return {'status': 'failed', 'summary': f'手机端小题材发现失败：{exc}', 'subthemes': [], 'source': '移动端同花顺'}
    resolved = {
        'status': 'available',
        'summary': f"手机端同花顺发现小题材：{'、'.join(payload.get('subthemes') or []) or '无'}",
        'subthemes': list(payload.get('subthemes') or []),
        'source': payload.get('source') or '移动端同花顺',
        'concept_page': payload.get('concept_page') or {},
        'query': payload.get('query') or top_theme,
        'cache_hit': False,
    }
    try:
        cache_path.write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass
    return resolved


def discover_mobile_theme_leaders_if_needed(sector_context: dict[str, Any]) -> dict[str, Any] | None:
    top_theme = str(sector_context.get('top_theme') or '').strip()
    if top_theme not in MOBILE_SUBTHEME_TRIGGER_THEMES:
        return None
    if not top_theme or not MOBILE_THEME_LEADERS_SCRIPT.exists():
        return None
    MOBILE_THEME_LEADERS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.md5(top_theme.encode('utf-8')).hexdigest()
    cache_path = MOBILE_THEME_LEADERS_CACHE_DIR / f'{cache_key}.json'
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding='utf-8'))
            cached['cache_hit'] = True
            return cached
        except Exception:
            pass
    command = ['python3', str(MOBILE_THEME_LEADERS_SCRIPT), '--query', top_theme, '--expect-name', top_theme, '--format', 'json']
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True, timeout=cfg.network('subprocess_timeout', 'medium', default=40))
        payload = json.loads(result.stdout)
    except Exception as exc:
        return {'status': 'failed', 'summary': f'移动端龙头参考获取失败：{exc}', 'leaders': [], 'source': '移动端同花顺', 'cache_hit': False}
    leaders = list(payload.get('leaders') or [])
    leader_names = [f"{row.get('name')}({row.get('symbol')})" for row in leaders if row.get('name') and row.get('symbol')]
    resolved = {
        'status': 'available',
        'summary': f"移动端龙头参考：{'、'.join(leader_names[:5]) or '无'}",
        'leaders': leaders,
        'source': payload.get('source') or '移动端同花顺',
        'concept_page': payload.get('concept_page') or {},
        'query': payload.get('query') or top_theme,
        'cache_hit': False,
    }
    try:
        cache_path.write_text(json.dumps(resolved, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass
    return resolved


def match_mobile_subthemes(sector_context: dict[str, Any]) -> dict[str, Any] | None:
    mobile = sector_context.get('mobile_subtheme_discovery') or {}
    if str(mobile.get('status') or '') != 'available':
        return None
    subthemes = [str(item).strip() for item in mobile.get('subthemes') or [] if str(item).strip()]
    if not subthemes:
        return None
    reason = str(sector_context.get('target_theme_reason') or '').strip()
    if not reason:
        return {'status': 'manual_pending', 'summary': '缺少个股题材归因文本，暂无法做小题材候选匹配', 'best_match': None, 'candidates': []}
    candidates: list[dict[str, Any]] = []
    normalized_reason = reason.upper()
    for subtheme in subthemes:
        synonyms = SUBTHEME_SYNONYM_GROUPS.get(subtheme, [subtheme])
        hits = [word for word in synonyms if (word.upper() if word.isascii() else word) in normalized_reason]
        if not hits:
            continue
        score = min(0.95, 0.45 + 0.18 * len(hits))
        candidates.append({'name': subtheme, 'score': round(score, 2), 'evidence': hits})
    candidates.sort(key=lambda item: item['score'], reverse=True)
    best = candidates[0] if candidates else None
    if best:
        summary = f"最可能匹配小题材：{best['name']}（置信度 {best['score']:.2f}；依据：{'、'.join(best['evidence'])}）"
    else:
        summary = '未匹配到高置信度小题材，仅保留手机端小题材列表供参考'
    return {'status': 'available' if best else 'no_match', 'summary': summary, 'best_match': best, 'candidates': candidates[:5]}


def build_leader_prediction(sector_context: dict[str, Any]) -> dict[str, Any] | None:
    top_theme = str(sector_context.get('top_theme') or '').strip()
    if not top_theme:
        return None
    leader_name = str(sector_context.get('theme_leader_name') or '').strip()
    leader_symbol = str(sector_context.get('theme_leader_symbol') or '').strip()
    front_runners = [str(item).strip() for item in sector_context.get('theme_front_runners') or [] if str(item).strip()]
    target_role = str(sector_context.get('target_theme_role') or '').strip()
    target_rank = sector_context.get('target_theme_rank')
    event_bonus_items = [str(item).strip() for item in sector_context.get('event_bonus_items') or [] if str(item).strip()]
    strength = safe_float(sector_context.get('top_theme_strength'))
    current_leader = f'{leader_name}({leader_symbol})' if leader_name and leader_symbol else leader_name or None
    leader_candidates: list[dict[str, Any]] = []
    confidence = 0.45
    shift_risk = '中'
    rationale: list[str] = []
    if current_leader:
        confidence += 0.15; rationale.append('本地题材明细存在明确龙头参考')
    if front_runners:
        confidence += 0.1; rationale.append('题材前排名单可用')
    if strength is not None and strength >= 1500:
        confidence += 0.1; rationale.append('题材强度较高')
    if event_bonus_items:
        shift_risk = '中高'; rationale.append(f"存在事件加分项：{'、'.join(event_bonus_items[:2])}")
    if target_role in {'题材龙头', '题材前排'}:
        confidence += 0.05; rationale.append(f'目标股当前位于{target_role}')
    if isinstance(target_rank, int) and target_rank > 5:
        shift_risk = '中高'; rationale.append('目标股暂不在前排，龙头切换概率更高')
    if current_leader:
        leader_candidates.append({'name': current_leader, 'score': round(min(confidence + 0.1, 0.9), 2), 'tag': '当前龙头参考'})
    for idx, item in enumerate(front_runners[:3], start=1):
        score = max(0.35, round(confidence - 0.05 * idx, 2))
        tag = '前排候选' if idx > 1 or item != current_leader else '当前龙头参考'
        if not any(candidate['name'] == item for candidate in leader_candidates):
            leader_candidates.append({'name': item, 'score': score, 'tag': tag})
    if not leader_candidates:
        return {'status': 'manual_pending', 'summary': '缺少足够的题材前排数据，暂无法生成龙头预测', 'current_leader': None, 'leader_candidates': [], 'leader_shift_risk': None, 'confidence': None, 'rationale': []}
    confidence = round(min(confidence, 0.88), 2)
    summary = f"当前龙头参考：{leader_candidates[0]['name']}；候选：{'、'.join(item['name'] for item in leader_candidates[1:3]) or '无'}；切换风险 {shift_risk}；置信度 {confidence:.2f}"
    return {'status': 'available', 'summary': summary, 'current_leader': leader_candidates[0]['name'], 'leader_candidates': leader_candidates[:3], 'leader_shift_risk': shift_risk, 'confidence': confidence, 'rationale': rationale[:4]}


def analyze_sector_context(symbol: str, trade_date_text: str) -> dict:
    trade_date_compact = trade_date_text.replace('-', '')
    stock_name = load_stock_name(symbol)
    basics = load_stock_basic_index()
    basic = basics.get(symbol) or {}
    stock_area = str(basic.get('area') or '').strip()
    stock_industry = str(basic.get('industry') or '').strip()
    fallback_parts: list[str] = []
    if stock_industry:
        fallback_parts.append(f'最相关板块参考：{stock_industry}')
    if stock_area:
        fallback_parts.append(f'地域板块参考：{stock_area}')
    fallback_summary = '；'.join(fallback_parts) if fallback_parts else '板块层当前个股本地数据缺失'
    browser_concepts = fetch_browser_concepts(symbol)
    mobile_stock_concepts = fetch_mobile_stock_concepts(symbol, stock_name)
    mobile_concepts = [str(item).strip() for item in (mobile_stock_concepts or {}).get('concepts') or [] if str(item).strip()]

    kpl_root = THEME_DATA_ROOT / 'kpl_concept_cons'
    kpl_concepts: list[dict[str, Any]] = []
    kpl_date: str | None = None
    kpl_file_path = kpl_root / f'kpl_concept_cons_{trade_date_compact}.csv'
    if kpl_file_path.exists():
        with kpl_file_path.open('r', encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                row_symbol = str(row.get('con_code') or '').strip()
                if row_symbol == symbol or row_symbol == symbol.replace('.SH', '.SZ').replace('.SZ', '.SH'):
                    kpl_concepts.append({'concept_name': str(row.get('name') or '').strip(), 'stock_name': str(row.get('con_name') or '').strip(), 'symbol': str(row.get('con_code') or '').strip(), 'desc': str(row.get('desc') or '').strip(), 'hot_num': int(row.get('hot_num') or 0)})
        if kpl_concepts:
            kpl_date = trade_date_compact
    if not kpl_concepts:
        dated_kpl_files = sorted(kpl_root.glob('kpl_concept_cons_*.csv'), reverse=True)
        for kpl_path in dated_kpl_files:
            kpl_file_date = kpl_path.stem.rsplit('_', 1)[-1]
            if not (len(kpl_file_date) == 8 and kpl_file_date.isdigit()):
                continue
            with kpl_path.open('r', encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    row_symbol = str(row.get('con_code') or '').strip()
                    if row_symbol == symbol or row_symbol == symbol.replace('.SH', '.SZ').replace('.SZ', '.SH'):
                        kpl_concepts.append({'concept_name': str(row.get('name') or '').strip(), 'stock_name': str(row.get('con_name') or '').strip(), 'symbol': str(row.get('con_code') or '').strip(), 'desc': str(row.get('desc') or '').strip(), 'hot_num': int(row.get('hot_num') or 0)})
                        kpl_date = kpl_file_date
                        break
            if kpl_concepts:
                break
    if not kpl_concepts:
        kpl_by_stock_path = kpl_root / 'by_stock' / f'{symbol}.csv'
        if kpl_by_stock_path.exists():
            with kpl_by_stock_path.open('r', encoding='utf-8-sig', newline='') as f:
                rows = list(csv.DictReader(f))
            candidate_dates = sorted({str(row.get('trade_date') or '').strip() for row in rows if len(str(row.get('trade_date') or '').strip()) == 8 and str(row.get('trade_date') or '').strip().isdigit()}, reverse=True)
            target_kpl_date = candidate_dates[0] if candidate_dates else None
            if target_kpl_date:
                kpl_concepts = [{'concept_name': str(row.get('name') or '').strip(), 'stock_name': str(row.get('con_name') or '').strip(), 'symbol': str(row.get('con_code') or '').strip(), 'desc': str(row.get('desc') or '').strip(), 'hot_num': int(row.get('hot_num') or 0)} for row in rows if str(row.get('trade_date') or '').strip() == target_kpl_date]
                if kpl_concepts:
                    kpl_date = target_kpl_date

    if not stock_name:
        return {'status': 'fallback_available' if fallback_parts else 'manual_pending', 'summary': fallback_summary if fallback_parts else '未找到股票名称，暂无法自动匹配板块', 'source': 'stock_basic(industry/area)' if fallback_parts else None, 'top_theme': stock_industry or None, 'top_theme_strength': None, 'top_theme_pct_change': None, 'theme_count': 0, 'related_area': stock_area or None}

    theme_cons_root = THEME_DATA_ROOT / 'dc_concept_cons'
    theme_list_root = THEME_DATA_ROOT / 'dc_concept'
    theme_rows: list[dict[str, str]] = load_dc_concept_constituents_local(stock_name, trade_date_text.replace('-', ''))
    target_date: str | None = None
    if theme_rows:
        target_date = str(theme_rows[0].get('trade_date') or '').split('.')[0]
    if not theme_rows:
        dated_files = sorted(theme_cons_root.glob('dc_concept_cons_*.csv'), reverse=True)
        for path in dated_files:
            file_date = path.stem.rsplit('_', 1)[-1]
            if not (len(file_date) == 8 and file_date.isdigit()):
                continue
            with path.open('r', encoding='utf-8-sig', newline='') as f:
                rows = [row for row in csv.DictReader(f) if str(row.get('ts_code') or '').strip() == symbol]
            if rows:
                target_date = file_date
                theme_rows = rows
                break
    if not theme_rows or not target_date:
        return {'status': 'fallback_available' if fallback_parts else 'manual_pending', 'summary': fallback_summary if fallback_parts else '题材成分当前个股本地数据缺失', 'source': 'stock_basic(industry/area)' if fallback_parts else None, 'top_theme': stock_industry or None, 'top_theme_strength': None, 'top_theme_pct_change': None, 'theme_count': 0, 'related_area': stock_area or None}
    theme_codes = {str(row.get('theme_code') or '').strip() for row in theme_rows if str(row.get('theme_code') or '').strip()}
    matched: list[dict[str, str]] = []
    for row in load_dc_concepts_local(target_date):
        if str(row.get('theme_code') or '').strip() in theme_codes:
            matched.append(row)
    if not matched:
        for path in theme_list_root.glob(f'*/*_{target_date}.csv'):
            with path.open('r', encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    if str(row.get('ts_code') or '').strip() != symbol:
                        continue
                    matched.append({'theme_code': str(row.get('theme_code') or '').strip(), 'name': str(row.get('concept_name') or path.parent.name or '').strip(), 'strength': str(row.get('concept_strength') or '').strip(), 'pct_change': str(row.get('concept_pct_change') or '').strip(), 'hot': str(row.get('concept_hot') or '').strip(), 'lead_stock': str(row.get('lead_stock') or '').strip(), 'detail_path': str(path)})
                    break
    if not matched:
        fallback_detail_date: str | None = None
        for path in sorted(theme_list_root.glob('*/*.csv'), reverse=True):
            stem = path.stem.rsplit('_', 1)
            if len(stem) != 2:
                continue
            file_date = stem[-1]
            if not (len(file_date) == 8 and file_date.isdigit()):
                continue
            with path.open('r', encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    if str(row.get('ts_code') or '').strip() != symbol:
                        continue
                    matched.append({'theme_code': str(row.get('theme_code') or '').strip(), 'name': str(row.get('concept_name') or path.parent.name or '').strip(), 'strength': str(row.get('concept_strength') or '').strip(), 'pct_change': str(row.get('concept_pct_change') or '').strip(), 'hot': str(row.get('concept_hot') or '').strip(), 'lead_stock': str(row.get('lead_stock') or '').strip(), 'detail_path': str(path)})
                    fallback_detail_date = file_date
                    break
            if matched:
                target_date = fallback_detail_date or target_date
                break
    if not matched:
        fallback_theme = next((row for row in sorted(theme_rows, key=lambda item: safe_float(item.get('hot_num')) or 0.0, reverse=True)), None)
        return {'status': 'fallback_available' if fallback_parts else 'manual_pending', 'summary': (f"按本地最新题材线索自推断：{fallback_theme.get('industry') or stock_industry or '未知'}；最近题材日期 {target_date}" if fallback_theme and fallback_theme.get('reason') else (f'本地题材未命中，按行业/地域自推断：{fallback_summary}' if fallback_parts else '本地题材与行业板块当前个股本地数据缺失')), 'source': 'theme_data/dc_concept_cons(latest)+stock_basic' if fallback_theme else ('stock_basic(industry/area)' if fallback_parts else None), 'top_theme': (fallback_theme.get('industry') or stock_industry or None) if fallback_theme else (stock_industry or None), 'top_theme_strength': None, 'top_theme_pct_change': None, 'theme_count': len(theme_codes), 'related_area': stock_area or None, 'theme_trade_date': target_date, 'kpl_concepts': kpl_concepts, 'kpl_date': kpl_date}
    deduped: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for row in matched:
        theme_code = str(row.get('theme_code') or '').strip()
        if not theme_code or theme_code in seen_codes:
            continue
        seen_codes.add(theme_code)
        deduped.append(row)
    concept_priority = {concept: idx for idx, concept in enumerate(mobile_concepts or browser_concepts)}
    deduped.sort(key=lambda row: (1 if is_event_theme(str(row.get('name') or '').strip()) else 0, 0 if str(row.get('name') or '').strip() in concept_priority else 1, concept_priority.get(str(row.get('name') or '').strip(), 999), -(safe_float(row.get('strength')) or 0.0), -(safe_float(row.get('pct_change')) or 0.0)))
    matched = deduped
    event_bonus_items = []
    seen_event_themes: set[str] = set()
    for row in matched:
        name = str(row.get('name') or '').strip()
        if not name or not is_event_theme(name) or name in seen_event_themes:
            continue
        seen_event_themes.add(name)
        event_bonus_items.append(name)
    kpl_primary = None
    if kpl_concepts:
        kpl_sorted = sorted(
            [row for row in kpl_concepts if not is_event_theme(str(row.get('concept_name') or '').strip())],
            key=lambda item: int(item.get('hot_num') or 0),
            reverse=True,
        )
        if kpl_sorted:
            kpl_primary = kpl_sorted[0]

    top_row = next((row for row in matched if not is_event_theme(str(row.get('name') or '').strip())), matched[0] if matched else None)
    if not top_row:
        return {'status': 'fallback_available', 'summary': fallback_summary, 'source': 'stock_basic(industry/area)', 'top_theme': stock_industry or None, 'top_theme_strength': None, 'top_theme_pct_change': None, 'theme_count': len(theme_codes), 'related_area': stock_area or None}
    top_theme = str(top_row.get('name') or '').strip() or stock_industry or None
    top_strength = safe_float(top_row.get('strength'))
    top_pct_change = safe_float(top_row.get('pct_change'))
    if kpl_primary:
        top_theme = str(kpl_primary.get('concept_name') or '').strip() or top_theme
        top_strength = float(kpl_primary.get('hot_num') or 0) or top_strength
        top_pct_change = top_pct_change
    theme_detail_path = top_row.get('detail_path')
    detail_rows: list[dict[str, Any]] = []
    if theme_detail_path and Path(theme_detail_path).exists():
        with Path(theme_detail_path).open('r', encoding='utf-8-sig', newline='') as f:
            detail_rows = list(csv.DictReader(f))
    theme_leader_name = str(top_row.get('lead_stock') or '').strip() or None
    theme_leader_symbol = None
    theme_front_runners: list[str] = []
    target_theme_rank = None
    target_theme_role = '当前个股本地数据缺失'
    target_theme_reason = str(kpl_primary.get('desc') or '').strip() if kpl_primary else None
    if kpl_primary:
        concept_files = list((THEME_DATA_ROOT / 'kpl_concept_cons' / 'by_concept').glob(f'*{top_theme}.csv'))
        if concept_files:
            concept_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
            with concept_files[0].open('r', encoding='utf-8-sig', newline='') as f:
                all_concept_rows = list(csv.DictReader(f))
            concept_rows = [
                row for row in all_concept_rows
                if str(row.get('trade_date') or '').strip() == (kpl_date or '')
            ]
            if not concept_rows:
                available_dates = sorted(
                    {
                        str(row.get('trade_date') or '').strip()
                        for row in all_concept_rows
                        if len(str(row.get('trade_date') or '').strip()) == 8
                    },
                    reverse=True,
                )
                fallback_kpl_date = available_dates[0] if available_dates else None
                if fallback_kpl_date:
                    concept_rows = [
                        row for row in all_concept_rows
                        if str(row.get('trade_date') or '').strip() == fallback_kpl_date
                    ]
            concept_rows.sort(key=lambda row: int(row.get('hot_num') or 0), reverse=True)
            if concept_rows:
                leader_row = concept_rows[0]
                theme_leader_name = str(leader_row.get('con_name') or '').strip() or theme_leader_name
                theme_leader_symbol = str(leader_row.get('con_code') or '').strip() or theme_leader_symbol
                theme_front_runners = []
                for idx, row in enumerate(concept_rows, start=1):
                    row_name = str(row.get('con_name') or '').strip()
                    row_symbol = str(row.get('con_code') or '').strip()
                    if idx <= 3 and row_name:
                        theme_front_runners.append(f'{row_name}({row_symbol})' if row_symbol else row_name)
                    if row_symbol == symbol:
                        target_theme_rank = idx
                if target_theme_rank == 1:
                    target_theme_role = '题材龙头'
                elif isinstance(target_theme_rank, int) and target_theme_rank <= 3:
                    target_theme_role = '题材前排'
                elif isinstance(target_theme_rank, int) and target_theme_rank <= 10:
                    target_theme_role = '题材中位'
                elif isinstance(target_theme_rank, int):
                    target_theme_role = '题材跟风'
        if not concept_files or not theme_leader_name or str(top_row.get('name') or '').strip() != top_theme:
            theme_leader_name = None
            theme_leader_symbol = None
            theme_front_runners = []
    if detail_rows and not kpl_primary:
        detail_rows.sort(key=lambda row: safe_float(row.get('hot_num')) or 0.0, reverse=True)
        for idx, row in enumerate(detail_rows, start=1):
            row_symbol = str(row.get('ts_code') or '').strip()
            row_name = str(row.get('name') or '').strip()
            if idx <= 3 and row_name:
                display_symbol = row_symbol or ''
                theme_front_runners.append(f'{row_name}({display_symbol})' if display_symbol else row_name)
            if idx == 1 and row_name:
                theme_leader_name = row_name
                theme_leader_symbol = row_symbol or None
            if row_symbol == symbol:
                target_theme_rank = idx
                target_theme_reason = (
                    str(row.get('reason') or '').strip()
                    or target_theme_reason
                    or None
                )
        if target_theme_rank == 1:
            target_theme_role = '题材龙头'
        elif isinstance(target_theme_rank, int) and target_theme_rank <= 3:
            target_theme_role = '题材前排'
        elif isinstance(target_theme_rank, int) and target_theme_rank <= 10:
            target_theme_role = '题材中位'
        elif isinstance(target_theme_rank, int):
            target_theme_role = '题材跟风'
    summary_parts = [f'最相关题材为 {top_theme}']
    if top_strength is not None:
        summary_parts.append(f'热度 {top_strength:.0f}')
    if top_pct_change is not None:
        summary_parts.append(f'当日涨幅 {top_pct_change:+.2f}%')
    if target_theme_role != '当前个股本地数据缺失':
        summary_parts.append(f'目标股处于{target_theme_role}')
    if theme_leader_name:
        summary_parts.append(f'题材龙头参考：{theme_leader_name}{f"({theme_leader_symbol})" if theme_leader_symbol else ""}')
    if kpl_date:
        summary_parts.append(f'KPL题材日期 {kpl_date}')
    if stock_area:
        summary_parts.append(f'地域板块参考：{stock_area}')
    payload = {
        'status': 'available',
        'summary': '；'.join(summary_parts),
        'source': 'theme_data/kpl+dc_concept',
        'top_theme': top_theme,
        'top_theme_strength': top_strength,
        'top_theme_pct_change': top_pct_change,
        'theme_count': len(matched),
        'related_area': stock_area or None,
        'theme_trade_date': target_date,
        'kpl_concepts': kpl_concepts,
        'kpl_date': kpl_date,
        'browser_concepts': browser_concepts,
        'mobile_stock_concepts': mobile_stock_concepts,
        'theme_leader_name': theme_leader_name,
        'theme_leader_symbol': theme_leader_symbol,
        'theme_front_runners': theme_front_runners,
        'target_theme_rank': target_theme_rank,
        'target_theme_role': target_theme_role,
        'target_theme_reason': target_theme_reason,
        'event_bonus_items': event_bonus_items,
    }
    payload['theme_cycle'] = infer_sector_cycle_status(top_theme, kpl_date or target_date, kpl_concepts, theme_leader_name)
    payload['theme_trend'] = analyze_theme_trend(top_theme, kpl_date or target_date)
    payload['theme_progression'] = infer_theme_progression(top_theme, kpl_date or target_date)
    payload['mobile_subtheme_discovery'] = discover_mobile_subthemes_if_needed(payload)
    payload['mobile_subtheme_match'] = match_mobile_subthemes(payload)
    payload['mobile_theme_leaders'] = discover_mobile_theme_leaders_if_needed(payload)
    payload['leader_prediction'] = build_leader_prediction(payload)
    return payload
