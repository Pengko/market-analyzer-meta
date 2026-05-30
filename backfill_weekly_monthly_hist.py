#!/usr/bin/env python3
"""从本地 parquet 日线聚合生成历史周/月线（2000-2019），合并到现有 parquet。"""

import os, sys, time
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.paths import get_stock_data_dir
import pandas as pd
import numpy as np

BASE_DIR = Path(get_stock_data_dir())
DAILY_DIR = BASE_DIR / 'daily'
WEEKLY_DIR = BASE_DIR / 'weekly'
MONTHLY_DIR = BASE_DIR / 'monthly'
TRADE_CAL_FILE = BASE_DIR / 'trade_cal' / 'trade_cal_all.csv'

def log(msg):
    ts = datetime.now().strftime('%H:%M:%S')
    print(f'[{ts}] {msg}')
    sys.stdout.flush()

def load_trade_cal():
    df = pd.read_csv(TRADE_CAL_FILE)
    df = df[df['is_open'] == 1].copy()
    df['cal_date'] = pd.to_numeric(df['cal_date'], errors='coerce').astype('int64')
    df['dt'] = pd.to_datetime(df['cal_date'].astype(str), format='%Y%m%d')
    df = df.sort_values('cal_date').reset_index(drop=True)

    df['year_week'] = (df['dt'].dt.isocalendar().year.astype(str) + '-W'
                       + df['dt'].dt.isocalendar().week.astype(str).str.zfill(2))
    week_ends = df.groupby('year_week')['cal_date'].max()
    df['week_end'] = df['year_week'].map(week_ends)

    df['year_month'] = df['dt'].dt.strftime('%Y-%m')
    month_ends = df.groupby('year_month')['cal_date'].max()
    df['month_end'] = df['year_month'].map(month_ends)

    return df[['cal_date', 'week_end', 'month_end']]

def get_all_daily_codes():
    codes = []
    for fp in DAILY_DIR.glob('*.parquet'):
        code = fp.stem
        if code and not code.startswith('.'):
            codes.append(code)
    return sorted(codes)

def existing_periods(parquet_dir, prefix):
    existing = set()
    if parquet_dir.exists():
        for fp in parquet_dir.glob(f'{prefix}_*.parquet'):
            code = fp.stem[len(prefix)+1:]
            if code:
                existing.add(code)
    return existing

def process_stock(code, cal_df, existing_weekly, existing_monthly):
    try:
        df = pd.read_parquet(DAILY_DIR / f'{code}.parquet')
    except Exception:
        return code, 'read_fail', 0, 0

    needed_cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg', 'vol', 'amount']
    missing = [c for c in needed_cols if c not in df.columns]
    if missing:
        return code, f'missing_cols:{missing}', 0, 0

    df = df.copy()
    df['trade_date'] = df['trade_date'].astype(str)
    df['trade_date_int'] = pd.to_numeric(df['trade_date'], errors='coerce').astype('int64')

    mini = cal_df[['cal_date', 'week_end', 'month_end']].copy()
    mini.columns = ['trade_date_int', 'week_end', 'month_end']
    df = df.merge(mini, on='trade_date_int', how='inner')
    df = df.dropna(subset=['week_end', 'month_end'])
    if df.empty:
        return code, 'no_overlap', 0, 0

    df['week_end'] = df['week_end'].astype('int64')
    df['month_end'] = df['month_end'].astype('int64')
    df = df.sort_values('trade_date_int')

    w_results = []
    m_results = []

    for (end, grp) in df.groupby('week_end'):
        grp = grp.sort_values('trade_date_int')
        first = grp.iloc[0]
        last = grp.iloc[-1]
        pre_close = first['pre_close']
        close = last['close']
        change_val = close - pre_close
        pct_chg_val = round(change_val / pre_close * 100, 4) if pre_close != 0 else 0.0
        w_results.append({
            'ts_code': first['ts_code'], 'trade_date': str(end),
            'open': first['open'], 'high': grp['high'].max(), 'low': grp['low'].min(), 'close': close,
            'pre_close': pre_close, 'change': change_val, 'pct_chg': pct_chg_val,
            'vol': grp['vol'].sum(), 'amount': grp['amount'].sum(),
        })

    for (end, grp) in df.groupby('month_end'):
        grp = grp.sort_values('trade_date_int')
        first = grp.iloc[0]
        last = grp.iloc[-1]
        pre_close = first['pre_close']
        close = last['close']
        change_val = close - pre_close
        pct_chg_val = round(change_val / pre_close * 100, 4) if pre_close != 0 else 0.0
        m_results.append({
            'ts_code': first['ts_code'], 'trade_date': str(end),
            'open': first['open'], 'high': grp['high'].max(), 'low': grp['low'].min(), 'close': close,
            'pre_close': pre_close, 'change': change_val, 'pct_chg': pct_chg_val,
            'vol': grp['vol'].sum(), 'amount': grp['amount'].sum(),
        })

    w_df = pd.DataFrame(w_results)
    m_df = pd.DataFrame(m_results)

    w_new = len(w_df)
    m_new = len(m_df)

    if code in existing_weekly:
        old_w = pd.read_parquet(WEEKLY_DIR / f'weekly_{code}.parquet')
        old_w['trade_date'] = old_w['trade_date'].astype(str)
        w_df = w_df[~w_df['trade_date'].isin(old_w['trade_date'].unique())]
        w_new = len(w_df)
        if not w_df.empty:
            w_df = pd.concat([old_w, w_df], ignore_index=True)
            w_df = w_df.drop_duplicates(subset=['trade_date'], keep='last')
            w_df = w_df.sort_values('trade_date', ascending=False).reset_index(drop=True)
    else:
        w_new = len(w_df)

    if code in existing_monthly:
        old_m = pd.read_parquet(MONTHLY_DIR / f'monthly_{code}.parquet')
        old_m['trade_date'] = old_m['trade_date'].astype(str)
        m_df = m_df[~m_df['trade_date'].isin(old_m['trade_date'].unique())]
        m_new = len(m_df)
        if not m_df.empty:
            m_df = pd.concat([old_m, m_df], ignore_index=True)
            m_df = m_df.drop_duplicates(subset=['trade_date'], keep='last')
            m_df = m_df.sort_values('trade_date', ascending=False).reset_index(drop=True)
    else:
        m_new = len(m_df)

    if not w_df.empty:
        w_df.to_parquet(WEEKLY_DIR / f'weekly_{code}.parquet', index=False)
    if not m_df.empty:
        m_df.to_parquet(MONTHLY_DIR / f'monthly_{code}.parquet', index=False)

    return code, 'ok', w_new, m_new

def main():
    t0 = time.time()
    log('加载交易日历...')
    cal_df = load_trade_cal()
    log(f'交易日历: {cal_df.cal_date.min()} ~ {cal_df.cal_date.max()} ({len(cal_df)} 天)')

    log('扫描日线文件...')
    all_codes = get_all_daily_codes()
    log(f'日线文件: {len(all_codes)} 只')

    existing_w = existing_periods(WEEKLY_DIR, 'weekly')
    existing_m = existing_periods(MONTHLY_DIR, 'monthly')
    log(f'已有周线: {len(existing_w)} 只  月线: {len(existing_m)} 只')

    codes_to_process = sorted(set(all_codes) | existing_w | existing_m)
    log(f'待处理: {len(codes_to_process)} 只')

    ok = exists = read_fail = no_overlap = 0
    total_w_new = total_m_new = 0

    # Single-process for simplicity; use chunked approach for stability
    chunk_size = 200
    for start in range(0, len(codes_to_process), chunk_size):
        chunk = codes_to_process[start:start+chunk_size]
        chunk_start = time.time()
        for idx, code in enumerate(chunk):
            result = process_stock(code, cal_df, existing_w, existing_m)
            c, status, w_n, m_n = result
            if status == 'ok':
                ok += 1
                total_w_new += w_n
                total_m_new += m_n
                if w_n > 0 or m_n > 0:
                    pass
            elif status.startswith('read_fail'):
                read_fail += 1
            elif status.startswith('missing_cols'):
                exists += 1
            elif status == 'no_overlap':
                no_overlap += 1

            if (idx + 1) % 50 == 0 or (idx + 1) == len(chunk):
                elapsed = time.time() - t0
                log(f'  [{start+idx+1}/{len(codes_to_process)}] ok:{ok} read_fail:{read_fail} '
                    f'w_new:{total_w_new} m_new:{total_m_new} elapsed:{elapsed:.0f}s')

        chunk_elapsed = time.time() - chunk_start
        log(f'  chunk {start//chunk_size + 1} done in {chunk_elapsed:.0f}s, '
            f'total elapsed: {time.time()-t0:.0f}s')

    log(f'\n完成! {time.time()-t0:.0f}s')
    log(f'处理: {len(codes_to_process)} 只 | ok:{ok} read_fail:{read_fail} no_overlap:{no_overlap}')
    log(f'新增周线行数: {total_w_new}  新增月线行数: {total_m_new}')

if __name__ == '__main__':
    main()
