# Tushare Pro Parquet 路径规范

> 基于实际目录扫描结果，确保 SKILL.md 中的路径描述与硬盘一致。未来接口增加时，应先扫描确认再更新本文件。

## 两大根目录

```
~/quant-data/tushare/股票数据/
~/quant-data/tushare/指数数据/
```

## 读取规则

- **主格式：parquet**，位于各接口目录的 root
- **CSV 仅为备份**，按年份/日期分子目录存储
- `stock-deep-analysis` 已切换为 parquet-only 读取

## 股票数据 — 按股票扁平

| 接口 | 读取示例 | 说明 |
|------|---------|------|
| `daily` | `pd.read_parquet(base / 'daily' / '000001.SZ.parquet')` | ~5,700 个 parquet，同时存在 YYYY/*.csv 备份 |
| `daily_basic` | `pd.read_parquet(base / 'daily_basic' / '000001.SZ.parquet')` | ~5,700 parquet |
| `weekly` | `pd.read_parquet(base / 'weekly' / 'weekly_000001.SZ.parquet')` | ~6,200 parquet，已全量迁移 |
| `monthly` | `pd.read_parquet(base / 'monthly' / 'monthly_000001.SZ.parquet')` | ~6,200 parquet，已全量迁移 |
| `cyq_chips` | `pd.read_parquet(base / 'cyq_chips' / '000001.SZ.parquet')` | ~5,500 parquet |
| `cyq_perf` | `pd.read_parquet(base / 'cyq_perf' / '000001.SZ.parquet')` | ~5,500 parquet + 同名 CSV |
| `stk_factor_pro` | `pd.read_parquet(base / 'stk_factor_pro' / '000001.SZ.parquet')` | ~5,700 parquet |
| `stk_auction_o` | `pd.read_parquet(base / 'stk_auction_o' / '000001.SZ.parquet')` | ~6,700 parquet |
| `stk_auction_c` | `pd.read_parquet(base / 'stk_auction_c' / '000001.SZ.parquet')` | ~7,900 parquet |
| `margin` | `pd.read_parquet(base / 'margin' / '000001.SZ.parquet')` | ~5,800 parquet |
| `margin_detail` | `pd.read_parquet(base / 'margin_detail' / '000001.SZ.parquet')` | ~4,800 parquet |
| `pledge_detail` | `pd.read_parquet(base / 'pledge_detail' / '000001.SZ.parquet')` | ~2,200 parquet |
| `pledge_stat` | `pd.read_parquet(base / 'pledge_stat' / '000001.SZ.parquet')` | ~4,000 parquet |
| `share_float` | `pd.read_parquet(base / 'share_float' / 'share_float_000001.SZ.parquet')` | ~1,200 parquet + 同名 CSV |
| `stk_nineturn` | `pd.read_parquet(base / 'stk_nineturn' / '000001.SZ.parquet')` | ~5,700 parquet |

## 股票数据 — 按年份全市场表

| 接口 | 读取示例 | 说明 |
|------|---------|------|
| `top_list` | `pd.read_parquet(base / 'top_list' / '2026.parquet')` | 每年一个文件 |
| `top_inst` | `pd.read_parquet(base / 'top_inst' / '2026.parquet')` | 每年一个文件 |
| `block_trade` | `pd.read_parquet(base / 'block_trade' / '2026.parquet')` | 每年一个文件 |
| `repurchase` | `pd.read_parquet(base / 'repurchase' / '2026.parquet')` | 每年一个文件 |
| `top10_holders` | `pd.read_parquet(base / 'top10_holders' / '2026.parquet')` | ~21 个年份文件，CSV 仍为按年份分股票 |
| `top10_floatholders` | `pd.read_parquet(base / 'top10_floatholders' / '2026.parquet')` | 同上 |

## 股票数据 — 单一文件

| 接口 | 读取示例 | 说明 |
|------|---------|------|
| `limit_list_d` | `pd.read_parquet(base / 'limit_list_d' / 'limit_list_d.parquet')` | 全量涨跌停表 |
| `limit_list_ths` | `pd.read_parquet(base / 'limit_list_ths' / 'limit_list_ths.parquet')` | THS 涨跌停 |
| `limit_step` | `pd.read_parquet(base / 'limit_step' / 'limit_step.parquet')` | 连板阶梯 |
| `limit_cpt_list` | `pd.read_parquet(base / 'limit_cpt_list' / 'limit_cpt_list.parquet')` | 涨跌停概念板块 |
| `hm_detail` | `pd.read_parquet(base / 'hm_detail' / 'hm_detail.parquet')` | 高管增减明细 |
| `hm_list` | `pd.read_parquet(base / 'hm_list' / 'hm_list.parquet')` | 高管列表 |
| `stk_shock` | `pd.read_parquet(base / 'stk_shock' / 'stk_shock.parquet')` | 急速拉升放量 |
| `trade_cal` | `pd.read_parquet(base / 'trade_cal' / 'trade_days.parquet')` | 交易日历 |

## 指数数据

根目录：`~/quant-data/tushare/指数数据/`

| 接口 | 读取示例 | 说明 |
|------|---------|------|
| `index_daily` | `pd.read_parquet(idx / 'index_daily' / '000001.SH.parquet')` | 按指数代码扁平，~2,000 个文件 |
| `index_weekly` | `pd.read_parquet(idx / 'index_weekly' / '000001.SH.parquet')` | 按指数代码扁平 |
| `index_monthly` | `pd.read_parquet(idx / 'index_monthly' / '000001.SH.parquet')` | 按指数代码扁平 |
| `sw_daily` | `pd.read_parquet(idx / 'sw_daily' / '801083.SI.parquet')` | 申万行业日线，~400 个文件 |
| `index_basic` | `pd.read_parquet(idx / 'index_basic' / 'index_basic_all.parquet')` | 单一全量表 |
| `index_classify` | `pd.read_parquet(idx / 'index_classify' / 'index_classify_all.parquet')` | 单一全量表 |
| `index_global` | `pd.read_parquet(idx / 'index_global' / 'index_global_all.parquet')` | 单一全量表 |
| `index_member` | `pd.read_parquet(idx / 'index_member' / 'index_member.parquet')` | 单一全量表 |
| `index_weight` | `pd.read_parquet(idx / 'index_weight' / 'index_weight.parquet')` | 单一全量表 |

## 资金流向

| 接口 | 读取示例 | 说明 |
|------|---------|------|
| `moneyflow` (Tushare) | `pd.read_parquet(base / 'moneyflow_data/individual/tushare' / '000001.SZ.parquet')` | 按股票，~5,500 个文件 |
| `moneyflow_ths` | `pd.read_parquet(base / 'moneyflow_data/individual/ths' / '000001.SZ.parquet')` | 按股票，~5,400 个文件 |
| `moneyflow_mkt_dc` | `pd.read_csv(base / 'moneyflow_data/market/dc_market' / f'moneyflow_dc_market_{date}.csv')` | 无 parquet，仅日期 CSV |
| `moneyflow_hsgt` | `pd.read_csv(base / 'moneyflow_data/market/hsgt' / f'moneyflow_hsgt_{date}.csv')` | 无 parquet，仅日期 CSV |
| `moneyflow_ind_dc` | `pd.read_csv(base / 'moneyflow_data/sector/dc_sector' / f'moneyflow_ind_dc_{date}.csv')` | 无 parquet，仅日期 CSV |
| `moneyflow_ind_ths` | `pd.read_parquet(base / 'moneyflow_data/sector/ths_industry' / '881162.TI.parquet')` | 按行业，同时有日备份 CSV |
| `moneyflow_cnt_ths` | `pd.read_parquet(base / 'moneyflow_data/sector/ths_concept' / '885999.TI.parquet')` | 按概念，同时有日备份 CSV |

## 主题/概念数据

| 接口 | 读取示例 | 说明 |
|------|---------|------|
| `ths_index` | `pd.read_parquet(base / 'theme_data/ths_index' / 'ths_index_all.parquet')` | 同花顺概念列表全量表 |
| `dc_index` | `pd.read_parquet(base / 'theme_data/dc_index' / 'dc_index_all.parquet')` | 东财概念列表全量表 |
| `ths_daily` | `pd.read_parquet(base / 'theme_data/ths_daily' / '871032.TI.parquet')` | 按概念代码扁平，~2,100 个 |
| `dc_daily` | `pd.read_parquet(base / 'theme_data/dc_daily' / 'BK1339.DC.parquet')` | 按概念代码扁平，~1,000 个 |
| `ths_member` | `pd.read_parquet(base / 'theme_data/ths_member' / '885972.TI_金属回收.parquet')` | 按概念名存储成分股 |
| `dc_member` | `pd.read_parquet(base / 'theme_data/dc_member' / 'BK1339.DC.parquet')` | 按概念名存储成分股 |
| `kpl_list` | `pd.read_csv(base / 'theme_data/kpl_list/2026' / '20260325.csv')` | 按日期 CSV，无 parquet |
| `kpl_concept_cons` | `pd.read_csv(base / 'theme_data/kpl_concept_cons/2025' / '202508.csv')` | 按月份 CSV，无 parquet |
| `dc_concept` | `pd.read_parquet(base / 'theme_data/dc_concept' / '2026.parquet')` | 按年份 parquet + 月份 CSV |
| `dc_concept_cons` | `pd.read_parquet(base / 'theme_data/dc_concept_cons' / '2026.parquet')` | 按年份 parquet + 月份 CSV |

## 分钟线数据

```python
# 分钟线仍为 CSV，按日期分粒度存储
min_file = base / '分钟数据' / '2026' / '05' / '20' / '000001.SZ' / '1m.csv'
```

## 扫描方法（用于未来验证）

```python
from pathlib import Path

def scan_interface(path, max_depth=3):
    parquet = list(path.rglob('*.parquet'))
    csv = list(path.rglob('*.csv'))
    # 筛选在 max_depth 以内的文件
    parquet = [f for f in parquet if len(f.relative_to(path).parts) <= max_depth]
    csv = [f for f in csv if len(f.relative_to(path).parts) <= max_depth]
    return {
        'parquet': len(parquet),
        'csv': len(csv),
        'samples': [str(f.relative_to(path)) for f in (parquet + csv)[:5]]
    }
```
