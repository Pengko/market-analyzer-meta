#!/usr/bin/env python3
"""
主要作用:
- 从本地 `daily` 数据聚合生成 `weekly` / `monthly`
- 作为 `update_weekly_monthly.py` 的底层聚合模块

适用场景:
- 需要先用本地日线快速生成周线和月线
- 再由上层模块决定是否用 API 做校验或覆盖
"""

import os
import sys
import pandas as pd
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils.paths import get_stock_data_dir

BASE_DIR = Path(get_stock_data_dir())
DAILY_DIR = BASE_DIR / 'daily'
WEEKLY_DIR = BASE_DIR / 'weekly'
MONTHLY_DIR = BASE_DIR / 'monthly'
TRADE_CAL_FILE = BASE_DIR / 'trade_cal' / 'trade_cal_all.csv'
DAILY_FILE_INDEX = None

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def build_daily_file_index():
    """按 ts_code 建立 daily 文件索引，兼容年分目录与根目录。"""
    global DAILY_FILE_INDEX
    if DAILY_FILE_INDEX is not None:
        return DAILY_FILE_INDEX
    index = {}
    for fp in DAILY_DIR.rglob('daily_*.csv'):
        code = fp.stem[6:]
        if not code:
            continue
        index.setdefault(code, []).append(fp)
    DAILY_FILE_INDEX = index
    return DAILY_FILE_INDEX

def load_trade_cal():
    """加载交易日历，返回周结束日和月结束日映射"""
    df = pd.read_csv(TRADE_CAL_FILE)
    df = df[df['is_open'] == 1].copy()
    df['cal_date'] = pd.to_numeric(df['cal_date'], errors='coerce').astype('int64')
    df['dt'] = pd.to_datetime(df['cal_date'].astype(str), format='%Y%m%d')
    df = df.sort_values('cal_date').reset_index(drop=True)
    
    # 周结束日: 按 (年份, 周数) 分组取最后一个交易日
    df['year_week'] = df['dt'].dt.isocalendar().year.astype(str) + '-W' + df['dt'].dt.isocalendar().week.astype(str).str.zfill(2)
    week_ends = df.groupby('year_week')['cal_date'].max().to_dict()
    week_map = dict(zip(df['cal_date'], df['year_week'].map(week_ends)))
    
    # 月结束日: 按 (年份, 月份) 分组取最后一个交易日
    df['year_month'] = df['dt'].dt.strftime('%Y-%m')
    month_ends = df.groupby('year_month')['cal_date'].max().to_dict()
    month_map = dict(zip(df['cal_date'], df['year_month'].map(month_ends)))
    
    return week_map, month_map

def read_daily(ts_code):
    index = build_daily_file_index()
    file_paths = index.get(ts_code, [])
    if not file_paths:
        return None
    try:
        frames = []
        for file_path in file_paths:
            part = pd.read_csv(file_path)
            if part is None or part.empty:
                continue
            frames.append(part)
        if not frames:
            return None
        df = pd.concat(frames, ignore_index=True)
        df['trade_date'] = pd.to_numeric(df['trade_date'], errors='coerce').astype('int64')
        df = df.dropna(subset=['trade_date'])
        df['trade_date'] = df['trade_date'].astype('int64')
        df = df.drop_duplicates(subset=['trade_date'], keep='last')
        df = df.sort_values('trade_date').reset_index(drop=True)
        return df if not df.empty else None
    except Exception:
        return None

def aggregate_period(df_daily, end_date_map):
    """通用周期聚合"""
    if df_daily is None or df_daily.empty:
        return None
    
    df = df_daily.copy()
    df['period_end'] = df['trade_date'].map(end_date_map)
    df = df.dropna(subset=['period_end'])
    if df.empty:
        return None
    df['period_end'] = df['period_end'].astype('int64')
    
    result = []
    for end_date, group in df.groupby('period_end'):
        group = group.sort_values('trade_date')
        first = group.iloc[0]
        last = group.iloc[-1]
        
        result.append({
            'ts_code': first['ts_code'],
            'trade_date': end_date,
            'close': last['close'],
            'open': first['open'],
            'high': group['high'].max(),
            'low': group['low'].min(),
            'pre_close': first['pre_close'],
            'change': last['close'] - first['pre_close'],
            'pct_chg': round((last['close'] - first['pre_close']) / first['pre_close'] * 100, 4) if first['pre_close'] != 0 else 0.0,
            'vol': group['vol'].sum(),
            'amount': group['amount'].sum(),
        })
    
    return pd.DataFrame(result)

def save_merged(ts_code, df_new, data_dir, prefix):
    if df_new is None or df_new.empty:
        return 0
    df_new['trade_date'] = df_new['trade_date'].astype(str)
    file_path = data_dir / f'{prefix}_{ts_code}.csv'
    
    if file_path.exists():
        existing = pd.read_csv(file_path)
        existing['trade_date'] = existing['trade_date'].astype(str)
        existing = existing[~existing['trade_date'].isin(df_new['trade_date'].unique())]
        combined = pd.concat([existing, df_new], ignore_index=True)
        combined = combined.sort_values('trade_date', ascending=False).reset_index(drop=True)
    else:
        combined = df_new.sort_values('trade_date', ascending=False).reset_index(drop=True)
    
    combined.to_csv(file_path, index=False)
    return len(df_new)

def update_weekly_from_daily(stock_list, week_map, min_date=20260401):
    log(f"\n📊 从 daily 聚合 weekly (补全 >= {min_date})...")
    updated = no_data = 0
    for i, ts_code in enumerate(stock_list):
        df_daily = read_daily(ts_code)
        if df_daily is None:
            no_data += 1
            continue
        df_weekly = aggregate_period(df_daily, week_map)
        if df_weekly is not None and not df_weekly.empty:
            df_weekly = df_weekly[df_weekly['trade_date'] >= min_date]
            if not df_weekly.empty:
                save_merged(ts_code, df_weekly, WEEKLY_DIR, 'weekly')
                updated += 1
        if (i + 1) % 500 == 0 or (i + 1) == len(stock_list):
            log(f"  weekly 进度: {i+1}/{len(stock_list)} | 已更新:{updated} 无daily:{no_data}")
    log(f"weekly 聚合完成: 更新 {updated}, 无 daily 数据 {no_data}")
    return updated

def update_monthly_from_daily(stock_list, month_map, min_date=20260301):
    log(f"\n📊 从 daily 聚合 monthly (补全 >= {min_date})...")
    updated = no_data = 0
    for i, ts_code in enumerate(stock_list):
        df_daily = read_daily(ts_code)
        if df_daily is None:
            no_data += 1
            continue
        df_monthly = aggregate_period(df_daily, month_map)
        if df_monthly is not None and not df_monthly.empty:
            df_monthly = df_monthly[df_monthly['trade_date'] >= min_date]
            if not df_monthly.empty:
                save_merged(ts_code, df_monthly, MONTHLY_DIR, 'monthly')
                updated += 1
        if (i + 1) % 500 == 0 or (i + 1) == len(stock_list):
            log(f"  monthly 进度: {i+1}/{len(stock_list)} | 已更新:{updated} 无daily:{no_data}")
    log(f"monthly 聚合完成: 更新 {updated}, 无 daily 数据 {no_data}")
    return updated

def get_stock_list():
    try:
        df = pd.read_csv(BASE_DIR / 'stock_basic' / 'stock_basic_non_st.csv')
        return df['ts_code'].tolist()
    except Exception as e:
        log(f"无法加载股票列表: {e}")
        return []

def main():
    log("=" * 70)
    log("🚀 从 daily 聚合生成 weekly / monthly")
    log("=" * 70)
    
    week_map, month_map = load_trade_cal()
    build_daily_file_index()
    stock_list = get_stock_list()
    if not stock_list:
        log("无法获取股票列表")
        return
    
    log(f"股票总数: {len(stock_list)}")
    log(f"交易日历覆盖: {len(week_map)} 天")
    
    # 只更新可能缺失的最新数据
    update_weekly_from_daily(stock_list, week_map, min_date=20260401)
    update_monthly_from_daily(stock_list, month_map, min_date=20260301)
    
    log("\n" + "=" * 70)
    log("✅ 全部完成")
    log("=" * 70)

if __name__ == '__main__':
    main()
