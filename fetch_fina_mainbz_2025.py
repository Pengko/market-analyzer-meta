#!/usr/bin/env python3
"""拉取 fina_mainbz 2025年数据，批量请求，按年格式保存."""

import time
import pandas as pd
from pathlib import Path
from utils.tushare_client import create_pro_api

pro = create_pro_api()
OUTPUT_DIR = Path("/Users/penghongming/quant-data/tushare/财务数据/fina_mainbz")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 获取非ST股票列表
print("获取股票列表...")
df_basic = pro.stock_basic(exchange='', list_status='L', fields='ts_code,name')
df_basic = df_basic[~df_basic['name'].str.contains('ST', na=False)]
codes = df_basic['ts_code'].tolist()
print(f"股票数量: {len(codes)}")

# 批量请求，每批1500只
batch_size = 1500
all_frames = []
total = len(codes)
total_batches = (total + batch_size - 1) // batch_size

for batch_idx in range(total_batches):
    start_idx = batch_idx * batch_size
    end_idx = min(start_idx + batch_size, total)
    batch_codes = codes[start_idx:end_idx]
    code_str = ','.join(batch_codes)
    
    print(f"  请求第 {batch_idx + 1}/{total_batches} 批 ({len(batch_codes)} 只)...")
    try:
        df = pro.fina_mainbz(ts_code=code_str, start_date='20250101', end_date='20251231')
        if df is not None and not df.empty:
            # 过滤2025年数据
            df['end_date'] = df['end_date'].astype(str)
            df_2025 = df[df['end_date'].str.startswith('2025')]
            if not df_2025.empty:
                all_frames.append(df_2025)
                print(f"    ✅ 返回 {len(df)} 条, 2025年 {len(df_2025)} 条, 涉及 {df_2025['ts_code'].nunique()} 只股票")
            else:
                print(f"    ⚪ 返回 {len(df)} 条, 无2025年数据")
        else:
            print(f"    ⚪ 无数据")
    except Exception as e:
        print(f"    ❌ 失败: {e}")
    
    time.sleep(0.5)

# 合并写入
if all_frames:
    merged = pd.concat(all_frames, ignore_index=True)
    merged = merged.drop_duplicates(keep='last')
    merged = merged.sort_values(['end_date', 'ts_code', 'bz_item'])

    csv_path = OUTPUT_DIR / "2025.csv"
    parquet_path = OUTPUT_DIR / "2025.parquet"
    merged.to_csv(csv_path, index=False)
    merged.to_parquet(parquet_path, index=False)
    print(f"\n✅ 写入完成: {csv_path}")
    print(f"   总记录数: {len(merged)}")
    print(f"   股票数: {merged['ts_code'].nunique()}")
    print(f"   报告期: {sorted(merged['end_date'].unique().tolist())}")
else:
    print("\n⚪ 未获取到2025年数据")
