---
name: tushare_pro
description: Tushare Pro 量化数据接口封装，支持 A 股全市场数据获取、本地 parquet 存储（兼容 CSV 作为备份）
---

# Tushare Pro 数据服务

Tushare Pro 量化数据接口封装，支持 A 股全市场数据获取与本地存储。

## 快速开始

### 1. 环境配置

```bash
# 设置代理环境变量
export TUSHARE_API_URL="http://lianghua.nanyangqiankun.top"
export TUSHARE_TOKEN="your_token"
```

### 2. 主更新入口

```bash
# 按最近交易日执行日常更新
python3 auto_fill_data.py --mode latest --latest-trade-days 1

# 自动模式：轻量更新或升级到全量闭环
python3 auto_fill_data.py

# 强制全量闭环
python3 auto_fill_data.py --mode full

# 指定接口执行最近窗口补齐 + 体检
python3 auto_fill_data.py --mode latest --interfaces daily,dc_index

# 指定接口执行全量闭环补缺 + 体检
python3 auto_fill_data.py --mode full --interfaces ths_index,ths_member,ths_daily,dc_daily,dc_index,dc_member
```

补充说明：

- `ths_index`、`ths_member`、`dc_index`、`dc_member` 在主脚本 `auto` / `latest` 模式下只追最新 1 个交易日覆盖
- `ths_member`、`dc_member` 在主脚本 `full` 模式下也只追最新 1 个交易日覆盖
- 如需补历史缺口，请使用 `--mode full`

### 3. 概念数据索引

开盘啦概念成分股数据（kpl_concept_cons）支持多维索引和自动去重。

**数据特点**:
- 按日期获取原始数据（`kpl_concept_cons_YYYYMMDD.csv`）
- `by_concept` 目录：**自动去重**，每个股票只保留最新日期记录
- `by_stock` 目录：保留个股历史概念记录

```bash
# 更新概念数据索引
python3 kpl_concept_indexer.py

# 批量获取近一个月历史数据
python3 fetch_kpl_concept_history.py
```

```python
from kpl_concept_indexer import KplConceptIndexer

indexer = KplConceptIndexer()

# 查询概念的成分股（自动返回最新日期，已去重）
stocks = indexer.get_concept_stocks(concept_name='光纤')
# 返回: [{'stock_code': '601869.SH', 'stock_name': '长飞光纤', 'hot_num': 9543, 'desc': '...'}, ...]

# 查询个股所属概念
concepts = indexer.get_stock_concepts('601869.SH')
# 返回: [{'concept_code': '000257.KP', 'concept_name': '光纤', 'hot_num': 9543}, ...]

# 获取热门概念
hot = indexer.get_hot_concepts('20260408', top_n=10)
```

## 目录结构

```
tushare_pro/
├─── auto_fill_data.py            # 日常更新/体检/补齐主入口
├─── kpl_concept_indexer.py       # 概念数据索引管理器
├─── fetch_kpl_concept_history.py # 批量获取概念历史数据
├─── fetch_month_data.py          # 批量获取近一个月数据
├─── batch_update_today.sh        # 批量更新脚本
├─── SKILL.md                     # 本文件
├─── core/                        # 核心模块
│   ├─── autofill_runtime.py     # 自动填充运行时
│   ├─── autofill_workflow.py    # 自动填充工作流
│   ├─── registry.py             # 接口注册表
│   ├─── tencent_min_fetcher.py  # 腾讯 API 实时分钟数据获取
│   └─── ...
├─── utils/                       # 工具模块
│   ├─── config.py               # 配置文件
│   ├─── tushare_client.py       # Tushare 客户端封装
│   └─── tushare_tools.py        # 工具函数
├─── tests/                       # 测试脚本
├─── archives/                    # 归档脚本（旧版）
├─── logs/                        # 日志文件
└─── deprecated/                  # 废弃脚本
```

## 数据存储结构

> 实际数据目录：`~/quant-data/tushare/股票数据/` 和 `~/quant-data/tushare/指数数据/`

**读取规则：以 root 目录下的 parquet 为主，CSV 按年份/日期分目录仅作为备份/历史遗留。**

### 股票数据

| 接口 | 目录 | 主文件（parquet） | 备份文件（CSV） | 存储模式 |
|------|------|------------------|----------------|----------|
| `daily` | `daily/` | `{ts_code}.parquet` | `YYYY/daily_{ts_code}.csv` | 按股票扁平 |
| `daily_basic` | `daily_basic/` | `{ts_code}.parquet` | `YYYY/daily_basic_{ts_code}.csv` | 按股票扁平 |
| `weekly` | `weekly/` | `weekly_{ts_code}.parquet` | 无（几乎全部已迁移） | 按股票扁平+前缀 |
| `monthly` | `monthly/` | `monthly_{ts_code}.parquet` | 无 | 按股票扁平+前缀 |
| `cyq_chips` | `cyq_chips/` | `{ts_code}.parquet` | `YYYY/cyq_chips_{ts_code}.csv` | 按股票扁平 |
| `cyq_perf` | `cyq_perf/` | `{ts_code}.parquet` | `cyq_perf_{ts_code}.csv`, `YYYY/*.csv` | 按股票扁平 |
| `stk_factor_pro` | `stk_factor_pro/` | `{ts_code}.parquet` | `YYYY/stk_factor_pro_{ts_code}.csv` | 按股票扁平 |
| `stk_auction_o` | `stk_auction_o/` | `{ts_code}.parquet` | `YYYY/*.csv` | 按股票扁平 |
| `stk_auction_c` | `stk_auction_c/` | `{ts_code}.parquet` | `YYYY/*.csv` | 按股票扁平 |
| `margin` | `margin/` | `{ts_code}.parquet` | `YYYY/margin_{ts_code}.csv` | 按股票扁平 |
| `margin_detail` | `margin_detail/` | `{ts_code}.parquet` | `YYYY/*.csv` | 按股票扁平 |
| `pledge_detail` | `pledge_detail/` | `{ts_code}.parquet` | `YYYY/*.csv` | 按股票扁平 |
| `pledge_stat` | `pledge_stat/` | `{ts_code}.parquet` | `YYYY/*.csv` | 按股票扁平 |
| `share_float` | `share_float/` | `share_float_{ts_code}.parquet` | `share_float_{ts_code}.csv` | 按股票扁平+前缀 |
| `stk_nineturn` | `stk_nineturn/` | `{ts_code}.parquet` | `YYYY/*.csv` | 按股票扁平 |
| `top_list` | `top_list/` | `{year}.parquet` | `YYYY/top_list_{date}.csv` | 按年份全市场表 |
| `top_inst` | `top_inst/` | `{year}.parquet` | `YYYY/top_inst_{date}.csv` | 按年份全市场表 |
| `block_trade` | `block_trade/` | `{year}.parquet` | `YYYY/*.csv` | 按年份全市场表 |
| `repurchase` | `repurchase/` | `{year}.parquet` | `{year}.csv` | 按年份全市场表 |
| `top10_holders` | `top10_holders/` | `{year}.parquet` | `YYYY/top10_holders_{ts_code}.csv` | 按年份全市场表 |
| `top10_floatholders` | `top10_floatholders/` | `{year}.parquet` | `YYYY/*.csv` | 按年份全市场表 |
| `limit_list_d` | `limit_list_d/` | `limit_list_d.parquet` | `YYYY/*.csv` | 单一文件+日备份 |
| `limit_list_ths` | `limit_list_ths/` | `limit_list_ths.parquet` | `YYYY/*.csv` | 单一文件+日备份 |
| `limit_step` | `limit_step/` | `limit_step.parquet` | `YYYY/*.csv` | 单一文件+日备份 |
| `limit_cpt_list` | `limit_cpt_list/` | `limit_cpt_list.parquet` | `YYYY/*.csv` | 单一文件+日备份 |
| `hm_detail` | `hm_detail/` | `hm_detail.parquet` | `YYYY/*.csv` | 单一文件+日备份 |
| `hm_list` | `hm_list/` | `hm_list.parquet` | `hm_list.csv` | 单一文件 |
| `stk_shock` | `stk_shock/` | `stk_shock.parquet` | `YYYY/*.csv` | 单一文件+日备份 |
| `stock_basic` | `stock_basic/` | `*.parquet` | `*.csv` | 杂项（缺失股票补丁） |
| `trade_cal` | `trade_cal/` | `trade_cal_all.parquet`, `trade_days.parquet` | `*.csv` | 单一文件 |
| `stk_weekly_monthly` | `stk_weekly_monthly/` | `weekly_{ts_code}.parquet`, `monthly_{ts_code}.parquet` | `weekly_{ts_code}.csv`, `monthly_{ts_code}.csv` | 按股票扁平 |

### 指数数据

| 接口 | 目录 | 主文件（parquet） | 备份文件（CSV） | 存储模式 |
|------|------|------------------|----------------|----------|
| `index_basic` | `指数数据/index_basic/` | `index_basic_all.parquet` | `*.csv` | 单一文件 |
| `index_classify` | `指数数据/index_classify/` | `index_classify_all.parquet` | `*.csv` | 单一文件 |
| `index_global` | `指数数据/index_global/` | `index_global_all.parquet` | `*.csv` | 单一文件 |
| `index_member` | `指数数据/index_member/` | `index_member.parquet` | `*.csv` | 单一文件 |
| `index_weight` | `指数数据/index_weight/` | `index_weight.parquet` | `*.csv` | 单一文件 |
| `index_daily` | `指数数据/index_daily/` | `{index_code}.parquet` | `YYYY/*.csv` | 按指数扁平 |
| `index_weekly` | `指数数据/index_weekly/` | `{index_code}.parquet` | `YYYY/*.csv` | 按指数扁平 |
| `index_monthly` | `指数数据/index_monthly/` | `{index_code}.parquet` | `YYYY/*.csv` | 按指数扁平 |
| `sw_daily` | `指数数据/sw_daily/` | `{code}.SI.parquet` | `*.csv` | 按行业扁平 |

### 资金流向

| 接口 | 目录 | 主文件（parquet） | 备份文件（CSV） | 存储模式 |
|------|------|------------------|----------------|----------|
| `moneyflow` (Tushare) | `moneyflow_data/individual/tushare/` | `{ts_code}.parquet` | `YYYY/*.csv` | 按股票扁平 |
| `moneyflow_ths` | `moneyflow_data/individual/ths/` | `{ts_code}.parquet` | `YYYY/*.csv` | 按股票扁平 |
| `moneyflow_mkt_dc` | `moneyflow_data/market/dc_market/` | 无 | `moneyflow_dc_market_{date}.csv` | 按日期 |
| `moneyflow_hsgt` | `moneyflow_data/market/hsgt/` | 无 | `moneyflow_hsgt_{date}.csv` | 按日期 |
| `moneyflow_ind_dc` | `moneyflow_data/sector/dc_sector/` | 无 | `moneyflow_ind_dc_{date}.csv` | 按日期 |
| `moneyflow_ind_ths` | `moneyflow_data/sector/ths_industry/` | `{code}.TI.parquet` | `moneyflow_ind_ths_{date}.csv` | 按行业+日备份 |
| `moneyflow_cnt_ths` | `moneyflow_data/sector/ths_concept/` | `{code}.TI.parquet` | `moneyflow_cnt_ths_{date}.csv` | 按概念+日备份 |

### 主题/概念数据

| 接口 | 目录 | 主文件（parquet/CSV） | 存储模式 |
|------|------|----------------------|----------|
| `ths_index` | `theme_data/ths_index/` | `ths_index_all.parquet` + `ths_index_all.csv` | 单一全量表 |
| `dc_index` | `theme_data/dc_index/` | `dc_index_all.parquet` + `dc_index_all.csv` | 单一全量表 |
| `ths_daily` | `theme_data/ths_daily/` | `{code}.TI.parquet` + `{code}.TI.csv` | 按概念扁平 |
| `dc_daily` | `theme_data/dc_daily/` | `{code}.DC.parquet` + `{code}.DC.csv` | 按概念扁平 |
| `ths_member` | `theme_data/ths_member/` | `{code}.TI_{name}.parquet` + `{code}.TI_{name}.csv` | 按概念成分股 |
| `dc_member` | `theme_data/dc_member/` | `{code}.DC.parquet` + `{code}.DC.csv` | 按概念成分股 |
| `kpl_list` | `theme_data/kpl_list/` | `YYYY/{date}.csv` | 按日期 |
| `kpl_concept_cons` | `theme_data/kpl_concept_cons/` | `YYYY/{month}.csv` | 按月份 |
| `dc_concept` | `theme_data/dc_concept/` | `{year}.parquet` + `YYYY/{month}.csv` | 按年份+月份 |
| `dc_concept_cons` | `theme_data/dc_concept_cons/` | `{year}.parquet` + `YYYY/{month}.csv` | 按年份+月份 |

### 分钟线数据

| 接口 | 目录 | 文件名 | 存储模式 |
|------|------|--------|----------|
| 分钟线 | `分钟数据/YYYY/MM/DD/` | `{ts_code}/1m.csv` | 按日期分股票（CSV） |
| 腾讯实时分钟 | `分钟数据/YYYY/MM/DD/` | `{ts_code}/1m.csv` | 按日期分股票（CSV） |

**数据来源说明**：
- **本地历史分钟线**：通过 Tushare `stk_mins` 接口获取（每日限额 2 次）
- **实时分钟线**：通过腾讯 API `minute/query` 获取当日实时分时数据（免费、无限额）
- 两者保存格式完全一致，均为 `datetime,open,close,high,low,volume,amount,avg`
- 技术详情参见 [`references/tencent-minute-api.md`](references/tencent-minute-api.md)
- 实现代码与集成细节参见 [`references/tencent-minute-implementation.md`](references/tencent-minute-implementation.md)

### 读取示例

```python
from pathlib import Path
import pandas as pd

base = Path('/Users/penghongming/quant-data/tushare/股票数据')

# 方式一：读取单股日线（parquet 主格式，无需合并年份）
df = pd.read_parquet(base / 'daily' / '000001.SZ.parquet')

# 方式二：读取龙虎榜（按年份全市场表）
df = pd.read_parquet(base / 'top_list' / '2026.parquet')

# 方式三：读取同花顺资金流向（按股票）
df = pd.read_parquet(base / 'moneyflow_data' / 'individual' / 'ths' / '000001.SZ.parquet')

# 方式四：读取指数行情
base_idx = Path('/Users/penghongming/quant-data/tushare/指数数据')
df = pd.read_parquet(base_idx / 'index_daily' / '000001.SH.parquet')
```

### 关于 CSV 兼容

- 写入时同时生成 CSV 作为备份（部分接口）
- 部分接口 CSV 数量远超 parquet（如 `daily` 有 7.1万 CSV vs 0.57万 parquet），说明 parquet 迁移仍在进行中
- **读取方应以 root 目录下的 parquet 为主**，CSV 仅用于人工检查/调试
- `stock-deep-analysis` 已切换为 parquet-only 读取，本 skill 的 parquet 输出是其维一数据源

## MCP 底层接口

当本地 parquet 数据不存在或不完整时，可通过 MCP 服务器直接请求 Tushare 原始数据。

### 配置

```json
{
  "mcpServers": {
    "tushare": {
      "url": "http://124.220.22.110:8020/mcp?token=6be0552842c69a4c84636359df4028459ce14d13d092cdce491ce77d361ab5a6"
    }
  }
}
```

- 协议：SSE (Server-Sent Events)
- 服务名：`tushare-mcp-static`
- 工具总数：258 个（覆盖 Tushare 全量数据接口）

### 底层调用顺序

```
本地 parquet → MCP tushare API → 腾讯分钟 API → 浏览器补抓
```

**当前规则：**
1. 先检查本地 `~/quant-data/tushare/股票数据/` 和 `~/quant-data/tushare/指数数据/` 下的 parquet 文件
2. 本地无数据时，通过 MCP `tushare` 服务器请求对应接口
3. 分钟数据场景下，Tushare `stk_mins`/`rt_min` 限额用完时，通过腾讯 API `minute/query` 获取当日实时分钟（免费无限额）
4. 以上均无数据/超限时，启动浏览器补抓作为最终底层

### 常用 MCP Tools（按分类）

**行情数据**
- `daily` / `weekly` / `monthly` — 日/周/月线
- `daily_basic` — 每日指标
- `stk_mins` / `rt_min` — 历史/实时分钟线
- `adj_factor` — 复权因子
- `suspend` / `suspend_d` — 停复牌
- `limit_list_d` / `limit_list_ths` / `limit_step` / `limit_cpt_list` — 涨跌停

**财务数据**
- `income` / `balancesheet` / `cashflow` — 三大报表
- `fina_indicator` — 财务指标
- `forecast` / `express` — 业绩预告/快报
- `fina_mainbz` — 主营业务构成
- `dividend` — 分红送股

**资金流向**
- `moneyflow` / `moneyflow_ths` / `moneyflow_dc` — 个股资金流向
- `moneyflow_hsgt` / `moneyflow_mkt_dc` — 港股通/大盘
- `moneyflow_ind_ths` / `moneyflow_cnt_ths` / `moneyflow_ind_dc` — 行业/概念流向
- `margin` / `margin_detail` — 融资融券

**龙虎榜**
- `top_list` / `top_inst` — 龙虎榜明细/机构
- `hm_list` / `hm_detail` — 游资名录/交易明细

**特色数据**
- `cyq_chips` / `cyq_perf` — 筹码分布/胜率
- `stk_factor_pro` / `stk_factor` — 技术面因子
- `stk_auction_o` / `stk_auction_c` / `stk_auction` — 集合竞价
- `stk_nineturn` — 神奇九转
- `stk_holdernumber` — 股东户数
- `stk_shock` / `stk_high_shock` — 异常波动

**概念/板块**
- `ths_index` / `ths_daily` / `ths_member` / `ths_hot` — 同花顺
- `dc_index` / `dc_daily` / `dc_member` / `dc_hot` — 东方财富
- `kpl_list` / `kpl_concept` / `kpl_concept_cons` — 开盘啦
- `dc_concept` / `dc_concept_cons` — 东财题材
- `tdx_index` / `tdx_daily` / `tdx_member` — 通达信
- `cls_index` / `cls_member` / `cls_stock_shock` / `cls_market_shock` — 财联社

**指数数据**
- `index_basic` / `index_daily` / `index_weekly` / `index_monthly` — 指数行情
- `index_weight` / `index_member` / `index_classify` — 指数成分
- `index_global` — 国际主要指数
- `sw_daily` / `sw_industry` — 申万行业

**其他**
- `news` / `major_news` / `cctv_news` — 新闻
- `research_report` — 研报
- `stock_basic` / `trade_cal` / `stock_company` — 基础信息
- `repurchase` / `block_trade` / `share_float` — 市场参考

### 调用示例

```python
# MCP 服务器通过 Hermes native-mcp 或 mcporter 提供工具
# 在 skill 中直接使用对应 tool 名称即可
# 例如：获取某日所有股票日线数据
# tool: tushare.daily(trade_date="20260523")
```

## 支持接口

### 核心行情（Core）
| 接口 | 说明 | 存储方式 |
|------|------|----------|
| `daily` | 日线行情 | 按股票 |
| `daily_basic` | 每日指标 | 按股票 |
| `moneyflow` | 个股资金流向 | 按股票 |

### 涨跌停数据（Limit）
| 接口 | 说明 | 存储方式 |
|------|------|----------|
| `limit_list_d` | 涨跌停列表 | 按日期 |
| `limit_step` | 涨停阶梯 | 按日期 |
| `top_list` | 龙虎榜 | 按日期 |
| `kpl_list` | 开盘啦涨跌停 | 按日期 |
| `kpl_concept_cons` | 概念成分股 | 多维索引 |

### 资金流向（Moneyflow）
| 接口 | 说明 | 存储方式 |
|------|------|----------|
| `moneyflow_dc` | 东方财富资金流向 | 按日期 |
| `moneyflow_ths` | 同花顺资金流向 | 按日期 |
| `moneyflow_hsgt` | 沪深港通资金流向 | 按日期 |
| `moneyflow_ind_ths` | 同花顺行业资金流向 | 按日期 |
| `moneyflow_cnt_ths` | 同花顺概念资金流向 | 按日期 |

### 特色数据
| 接口 | 说明 | 存储方式 | 限额 |
|------|------|----------|------|
| `stk_auction_o` | 开盘集合竞价 | 按股票 | - |
| `stk_auction_c` | 收盘集合竞价 | 按股票 | - |
| `stk_factor_pro` | 专业因子数据 | 按股票 | - |
| `stk_mins` | 历史分钟数据 | 按股票 | 2次/天 |
| `rt_min` | 实时分钟数据 | 按股票 | 10次/天 |
| **腾讯实时分钟** | 当日实时分时数据 | 按股票 | **无限额** |
| `margin_detail` | 融资融券明细 | 按股票 | - |
| `cyq_chips` | 筹码分布 | 按股票 | - |
| `cyq_perf` | 筹码绩效 | 按股票 | - |

### 股东数据（Shareholder）
| 接口 | 说明 | 存储方式 | 备注 |
|------|------|----------|------|
| `stk_holdernumber` | 股东户数 | **仅API** | 未纳入本地数仓同步；数据为**定期披露**（季报/年报/月度），非实时 |
| `top10_holders` | 前十大股东 | 按年份 | 本地已存储 |
| `top10_floatholders` | 前十大流通股东 | 按年份 | 本地已存储 |

#### 股东户数变化分析流程

**数据源特性**：
- `stk_holdernumber` 为**定期披露数据**，不存在逐日实时股东户数
- 披露频率：季报/年报（最权威）+ 部分公司月度区间披露
- 字段含义：`end_date` 为期末日期，`ann_date` 为公告日期，`holder_num` 为股东总户数

**数据质量检查**（必须先做）：
```python
# IPO早期记录可能出现年份错误（如2027→应为2017），分析前必须检查
df['end_year'] = df['end_date'].astype(str).str[:4].astype(int)
df['ann_year'] = df['ann_date'].astype(str).str[:4].astype(int)
# 过滤明显异常的年份（如未来年份或早于上市年份）
from datetime import datetime
current_year = datetime.now().year
# 标记异常
df.loc[df['end_year'] > current_year + 1, 'year_error'] = True
```

**核心分析维度**：
1. **股东户数变化率**：`Δ = (本期户数 - 上期户数) / 上期户数`
2. **户均持股 = 总股本 / 股东户数**（需配合 `daily_basic` 的 `total_share`）
3. **筹码集中度趋势**：户数持续减少 → 筹码集中（利好）；户数大幅增加 → 筹码分散（利空）
4. **关键阈值判断**：单季度变化 > ±15% 为显著异动，需结合股价走势分析

**完整分析代码模板**：
```python
import pandas as pd
from utils.tushare_client import TushareClient

client = TushareClient()
pro = client.pro

# 1. 获取股东户数（最多5000条，对单只股票足够）
df = pro.stk_holdernumber(ts_code='603305.SH', limit=5000)
df = df.sort_values('end_date').reset_index(drop=True)

# 2. 数据清洗：过滤年份异常
current_year = pd.Timestamp.now().year
df['end_year'] = df['end_date'].astype(str).str[:4].astype(int)
df = df[df['end_year'] <= current_year + 1].copy()

# 3. 计算变化率
df['holder_change_pct'] = df['holder_num'].pct_change() * 100

# 4. 计算户均持股（万股）——需要总股本数据
df_basic = pro.daily_basic(ts_code='603305.SH',
                           trade_date=df['end_date'].iloc[-1],
                           fields='ts_code,trade_date,total_share')
total_share = df_basic['total_share'].iloc[0]  # 万股
df['avg_shares_per_holder'] = total_share / df['holder_num']

# 5. 输出分析
print(f"最新股东户数: {df['holder_num'].iloc[-1]:,.0f}")
print(f"最近一期变化: {df['holder_change_pct'].iloc[-1]:+.2f}%")
print(f"户均持股: {df['avg_shares_per_holder'].iloc[-1]:.2f}万股")

# 6. 趋势判断
recent = df.tail(4)  # 最近4期
if (recent['holder_change_pct'] < 0).all():
    print("筹码持续集中（利好）")
elif (recent['holder_change_pct'] > 0).all():
    print("筹码持续分散（利空）")
```

**分析结论模板**：
- **户数↓ + 户均持股↑** → 大户吸筹，筹码集中，偏利好
- **户数↑ + 户均持股↓** → 散户涌入，筹码分散，偏利空
- **户数剧增（>+30%）但股价横盘** → 主力出货，高度警惕
- **户数锐减（>-20%）且股价企稳** → 机构建仓信号

### 指数数据（Index）
| 接口 | 说明 | 存储方式 |
|------|------|----------|
| `index_basic` | 指数基本信息 | 全量文件 |
| `index_daily` | 指数日线行情 | 按日期 |
| `index_weight` | 指数成分和权重 | 按日期 |
| `sw_daily` | 申万行业指数日行情 | 按日期 |

## 常用命令

```bash
# 按最近交易日执行主链
python3 auto_fill_data.py --mode latest --latest-trade-days 1

# 分析单只股票的股东户数变化
python3 scripts/analyze_stk_holdernumber.py 603305.SH

# 仅对指定接口补最近窗口并体检
python3 auto_fill_data.py --mode latest --interfaces daily,dc_index

# 批量更新今日数据
./batch_update_today.sh

# 批量获取近一个月数据
python3 fetch_month_data.py

# 批量获取概念历史数据（近一个月）
python3 fetch_kpl_concept_history.py

# 检查数据完整性
python3 utils/check_data_integrity.py

# 强制全量 SQLite 同步（修复增量遗漏）
python3 scripts/data/sync_to_sqlite.py --full --tables cyq_chips,stk_auction_c

# 跳过 SQLite 同步（仅下载CSV）
python3 auto_fill_data.py --mode latest --latest-trade-days 1 --skip-sqlite-sync

# ─── 腾讯实时分钟数据 ───

# 获取单只股票当日实时分钟数据
python3 core/tencent_min_fetcher.py --symbol 600519.SH

# 获取多只股票当日实时分钟数据
python3 core/tencent_min_fetcher.py --symbols "600519.SH,000001.SZ,002594.SZ" --workers 4

# 通过 auto_fill_data.py 批量获取当日实时分钟（全部非 ST 股票池）
python3 auto_fill_data.py --tencent-min --tencent-min-workers 8

# 批量获取指定股票池（前 100 只）
python3 auto_fill_data.py --tencent-min --tencent-min-batch-size 100 --tencent-min-workers 8

# 指定股票列表批量获取
python3 auto_fill_data.py --tencent-min --tencent-min-symbols "600519.SH,000001.SZ" --tencent-min-workers 4

# 对指定接口做全量补缺并体检
python3 auto_fill_data.py --mode full --interfaces ths_index,ths_member,ths_daily,dc_daily,dc_index,dc_member
```

补充：

- `ths_index`、`ths_member`、`dc_index`、`dc_member` 在默认近期窗口模式下不会追最近 10 个交易日，而是只追最新覆盖

## 注意事项

1. **代理限制**: 代理服务器 `lianghua.nanyangqiankun.top` 有每日限额，超限后会返回 `"当前接口达到请求上限，请稍后重试"`

2. **限额接口**:
   - `stk_mins`: 每天 2 次
   - `rt_min`: 每天 10 次

3. **腾讯实时分钟 API**（新增）:
   - 接口: `https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}`
   - 限制: **无限额**，免费使用
   - 数据范围: **仅当日实时分时数据**，无法获取历史分钟 K 线
   - 返回格式: 每分钟一个价格点 + 累计成交量/成交额，转换为 OHLCV 时 open/high/low/close 均等于该分钟价格
   - 保存路径: `~/quant-data/tushare/股票数据/分钟数据/YYYY/MM/DD/{ts_code}/1m.csv`
   - 适用场景: 盘中实时分析、Tushare 分钟限额用完时的 fallback

4. **数据更新规则**:
   - 日线数据：收盘后 15:00-17:00 更新
   - 集合竞价：09:25 开盘前、15:00 收盘后
   - 概念数据：盘中实时更新
   - `by_concept` 索引：**自动去重**，每个股票只保留最新日期记录

5. **数据同步**: 建议每日收盘后 16:30 运行 `auto_fill_data.py --mode latest --latest-trade-days 1`

6. **批量获取历史数据**:
   - 使用 `fetch_month_data.py` 获取近一个月的日线、资金流向、涨跌停等数据
   - 使用 `fetch_kpl_concept_history.py` 获取近一个月的概念成分股数据
   - 这些脚本会自动处理日期范围和交易日判断

7. **SQLite 同步（历史兼容）**:
   - `stock-deep-analysis` 已切换为 parquet-only 读取，SQLite 仅作为历史兼容
   - `auto_fill_data.py` 默认只同步 7 个表到 SQLite，其他表不会自动同步
   - 若需修复，手动执行：`python3 scripts/data/sync_to_sqlite.py --full --tables 缺失的表名`
   - 可使用 `--skip-sqlite-sync` 跳过 SQLite 同步，只下载 parquet/CSV

8. **股东户数数据（stk_holdernumber）特殊处理**:
   - 本地数仓**未同步** stk_holdernumber，分析时需直接调用 Tushare API
   - 数据为**定期披露**（季报/年报/月度），不存在逐日实时股东户数
   - **数据异常检查**：部分IPO早期记录可能出现年份错误（如 2027 → 应为 2017），分析前建议检查 end_date 和 ann_date 的年份合理性

9. **Python 标准库冲突（core/calendar.py）**:
   - `core/calendar.py` 与 Python 标准库 `calendar` 同名。当从 `core/` 目录直接运行脚本时（如 `python3 core/some_script.py`），Python 会将 `core/` 加入 `sys.path` 最前面，导致 `import calendar` 优先命中 `core/calendar.py` 而非标准库。
   - 连锁反应：`import urllib.request` → `import http.client` → `import email.parser` → `import email.utils` → `import calendar` → **循环导入错误**
   - **修复**：在 `core/` 下的可执行脚本开头，显式将 `core/` 从 `sys.path` 移除后再导入标准库模块。已在 `core/tencent_min_fetcher.py` 中应用。

## 实时大盘分析参考

进行实时/准实时市场全景分析时，参见：[`references/realtime-market-analysis.md`](references/realtime-market-analysis.md)

包含内容：
- 数据获取 fallback chain（本地 → Tushare → 免费 API → 浏览器）
- Tushare object dtype 导致的 format 失败及解法
- 北向资金字段单位弄清
- 市场广度计算模式
- 阶段化报告输出模式
- 时段分析规则

## K线图表可视化参考

生成专业级A股K线图表（含均线、成交量、关键点位标注）时，参见：[`references/chart-visualization.md`](references/chart-visualization.md)

包含内容：
- matplotlib 中文字体配置（macOS/Linux）
- 完整K线+均线+成交量组合图代码模板
- 斐波那契回撤位绘制
- 历史量化类比方法（筛选相似K线组合预测后续走势）
- execute_code vs terminal 环境差异说明
- 常见陷阱与解池

## 数据分析执行模式参考

进行A股数据获取与分析时，工具选型与常见陷阱参见：[`references/execution-patterns.md`](references/execution-patterns.md)

包含内容：
- execute_code / terminal / write_file 选型决策树
- execute_code 隔离环境缺 tushare 陷阱
- terminal heredoc 被误判为背景进程及解法
- 按年份分区数据文件的 rglob 合并模式
- 多步骤分析最佳实践（write_file → terminal → execute_code）
- 常用分析脚本模板（单股票全量合并、API缓存、多接口并行）

## 下游 Skill 对接

其他 skill（如 `stock-deep-analysis`）需要在 Python 代码层直接复用 `tushare_pro` 的客户端时，参见详细说明与代码模板：[`references/downstream-integration.md`](references/downstream-integration.md)

要点：
- 使用 `sys.path` 动态注入导入 `utils.tushare_client`
- Tushare API 作为本地 parquet 缺失时的第一 fallback
- 获取到的数据应立即缓存回本地 parquet

## 配置

```python
TOKEN = "your_token"
PROXY_URL = "http://lianghua.nanyangqiankun.top"
DATA_DIR = "~/quant-data/tushare/investor" (可通过 QUANT_DATA_DIR 环境变量覆盖)
```
