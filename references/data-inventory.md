# 本地数据资产清单 (Data Inventory)

> 最后更新：2026-04-25
> 更新方法：不可依赖记忆判断数据存在性，必须通过脚本扫描目录和文件确认。
> 扫描基准：`~/quant-data/tushare/股票数据/`
> 自动扫描脚本：`scripts/scan_data_inventory.py`

---

## 一、数据可用状态总览

| 数据类型 | 本地路径 (实际) | 文件格式 | 更新频率 | 当前状态 | 降级方式 |
|----------|-------------------|----------|----------|----------|----------|
|| 日线行情 (daily) | `daily/{YYYY}/daily_{code}.{prefix}.csv` + `.parquet` | CSV + Parquet | T+1 | **可用但结构不匹配** (142,936 文件) | 见下文 2.5 节 |
|| 日线基础 (daily_basic) | `daily_basic/` | CSV + Parquet | T+1 | **目录存在但 0 文件** | 无 |
|| 技术指标因子 (stk_factor_pro) | `stk_factor_pro/{YYYY}/stk_factor_pro_{code}.{prefix}.csv` + `.parquet` 与平铺并存 | CSV + Parquet | T+1 | **可用但双结构并存** (152,694 文件) | 见下文 2.6 节 |
|| 复权因子 (adj_factor) | `缺失` | — | T+1 | **完全缺失** | 无 |
|| 股东增减持 (stk_holdertrade) | `缺失` | — | 低频 | **完全缺失** | 跳过 |
|| 筹码性能 (cyq_perf) | `cyq_perf/{YYYY}/cyq_perf_{code}.{prefix}.csv` + `.parquet` | CSV + Parquet | 日终 | **可用** (10,978 CSV + 42,022 Parquet) | 缺失时跳过 |
|| 大盘资金流 | `moneyflow_data/market/{dc,hsgt}/**/*.{csv,parquet}` | CSV + Parquet | T+1 | **可用** | 无 |
|| 板块资金流 | `moneyflow_data/sector/{ths_industry,ths_concept,dc_sector}/**/*.{csv,parquet}` | CSV + Parquet | T+1 | **可用** | 无 |
|| 个股资金流 (tushare) | `moneyflow_data/individual/tushare/{YYYY}/{MM}/{DD}/moneyflow_{YYYYMMDD}.csv` + `.parquet` | CSV + Parquet | T+1 | **可用但结构不匹配** | 适配按日期全市场表结构 / SQLite `moneyflow` 表 |
|| 筹码分布 (cyq_chips) | `cyq_chips/{YYYY}/cyq_chips_{code}.{prefix}.csv` + `.parquet` | CSV + Parquet | 日终 | **可用但常滞后** (9,069 CSV + 9,111 Parquet；2026-04-24 复核时全局最新为 2026-04-22) | 缺失时跳过 |
|| 开盘竞价 (stk_auction_o) | `stk_auction_o/{YYYY}/stk_auction_o_{code}.{prefix}.csv` + `.parquet` | CSV + Parquet | 日终 | **可用** (12,237 CSV) | 缺失时跳过 |
|| 收盘竞价 (stk_auction_c) | `stk_auction_c/{YYYY}/stk_auction_c_{code}.{prefix}.csv` + `.parquet` | CSV + Parquet | 日终 | **可用** (13,393 CSV) | 缺失时跳过 |
|| 东财概念成分 | `theme_data/dc_concept_cons/` | CSV/JSON | 低频 | **可用** | 缺失时浏览器抓取 |
|| 开盘啦概念成分 | `theme_data/kpl_concept_cons/` | CSV/JSON | 低频 | **可用** | 缺失时浏览器抓取 |
|| 龙虎榜 (top_list) | `top_list/{YYYY}/top_list_{YYYYMMDD}.csv` + `.parquet` | CSV + Parquet | T+1 | **可用** (490文件 = 245 CSV + 245 Parquet) | 东方财富网页抓取 |
|| 龙虎榜机构 (top_inst) | `top_inst/{YYYY}/top_inst_{YYYYMMDD}.csv` + `.parquet` | CSV + Parquet | T+1 | **可用** (490文件 = 245 CSV + 245 Parquet) | 东方财富网页抓取 |
|| 涨停列表 (limit_list_d) | `limit_list_d/{YYYY}/limit_list_d_{YYYYMMDD}.csv` | CSV | T+1 | **可用但偏少** (82文件) | 跳过 |
|| 大宗交易 (block_trade) | `block_trade/{YYYY}/block_trade_{YYYYMMDD}.csv` | CSV | T+1 | **可用** (528文件) | 跳过 |
|| 游资详情 (hm_detail) | `hm_detail/{YYYY}/hm_detail_{YYYYMMDD}.csv` | CSV | T+1 | **极少** (28文件) | 跳过 |
|| 分钟线 | `分钟数据/{YYYY}/{MM}/{DD}/{code}/{period}m.csv` | CSV | 盘中实时/T+1 | **严重不足** (99文件，仅82只股票) |时段分策：盘中浏览器/实时API直拿，午间/盘后本地优先，盘前用T-1历史分钟线 |
|| 交易日历 | `trade_cal/trade_cal_all.csv` | CSV | 低频 | **可用** | 无 |
|| 融资融券明细 (margin_detail) | `margin_detail/margin_detail_{code}.{prefix}.csv` + `.parquet` | CSV + Parquet | T+1 | **部分可用** (13,061文件，覆盖不全) | 跳过 |
|| 融资融券 (margin) | `margin/{YYYY}/margin_{code}.{prefix}.csv` + `.parquet` | CSV + Parquet | T+1 | **可用** (8,880 CSV + 8,287 Parquet) | 无 |
|| 回购 (repurchase) | `repurchase/repurchase_{code}.{prefix}.csv` + `.parquet` | CSV + Parquet | 低频 | **闲置** (24,905文件，代码未引用) | 无需降级 |
|| 股份质押 (pledge_detail) | `pledge_detail/pledge_detail_{code}.{prefix}.csv` + `.parquet` | CSV + Parquet | 低频 | **闲置** (43,508文件，代码未引用) | 无需降级 |
|| 大盘指数日线 (index_daily) | `指数数据/index_daily/YYYY/index_daily_{code}.csv` + `.parquet` | CSV + Parquet | T+1 | **可用** (28 CSV) | 无 |
|| 行业指数日线 (industry_daily) | `缺失` | — | — | **完全缺失** | 腾讯API/浏览器抓取 |
|| 概念指数日线 (concept_daily) | `缺失` | — | — | **完全缺失** | 浏览器抓取 |
|| 行业概念映射 (industry_concept) | `缺失` | — | — | **完全缺失** | 浏览器抓取 |
|| 公司公告 (announcement) | `缺失` | — | — | **完全缺失** | 浏览器抓取 |
|| 新闻舆情 (news) | `消息面数据/raw/{browser_news,news_pipeline}/` | CSV/JSON | 低频 | **严重不足** (~54文件，仅近期) | market-news-intelligence skill |
||| 汇总JSON | `股票数据根目录` | JSON | — | **已移除缓存机制** | 代码不再生成汇总JSON，改用脚本直接扫描本地源 |
|| SQLite 数仓 | `references/data/stock_analytics.db` (7.5GB, 15表, 4,325万行) | SQLite | T+1 | **可用** | 回退 Parquet/CSV 直读(备用) |

---

## 二、结构不匹配详情

### 2.1 个股资金流 (moneyflow)

SKILL.md 旧期期望路径：`moneyflow/{YYYY}/moneyflow_{code}.{prefix}.csv` (按股票分文件)，已彻底废弃。
**重要变化：原先的 `_by_stock/` 目录已完全不存在，数据全部迁移为按日期分区结构。**

实际存储结构：
```
moneyflow_data/individual/
  tushare/{YYYY}/{MM}/{DD}/moneyflow_{YYYYMMDD}.csv + .parquet   # 按日期全市场表
  dc/     # 东财来源，244 CSV + 244 Parquet，按日期命名
  ths/    # 同花顺来源，212 CSV + 212 Parquet，按日期命名
  integrated/  # 整合版，仅两个文件
```

**重要发现：按日期全市场表中也可能缺失个股记录**
- 实测案例：600103.SH 在近5个交易日（2026-04-20 至 04-24）的 `moneyflow_{YYYYMMDD}.csv` 中均无该股记录。
- 原因：可能与个股流动性、盘中是否达到资金流入门槛有关（如小市值、ST、成交量不足等）。
- **分析时必须扫描并确认**，不能假设"全市场表=所有股票都有"。

**SQLite 同步问题：**
- SQLite `moneyflow` 表仅 314 行，与数百个日度 CSV 文件极不匹配，同步极不完整。
- 个股级资金流分析应优先从日度全市场表中筛选 `ts_code`，而非依赖 SQLite。

**影响**：所有依赖 `moneyflow/{code}.csv` 或 `_by_stock/` 路径的脚本 (`build_stock_report.py` 等`) 需要适配按日期全市场表结构，通过日期定位文件后再按 `ts_code` 筛选目标股票。若筛选结果为空，必须在报告中明确标注"本地资金流向表无该股记录"。

### 2.2 筹码分布 (cyq_chips)

SKILL.md 期望路径：`cyq_chips/cyq_chips_{code}.{prefix}.csv` (扁平)

实际存储结构：`cyq_chips/{YYYY}/cyq_chips_{code}.{prefix}.csv` (年份子目录)

**当前同步状态（2026-04-24 复核）**：
- 目录与路径接口已对齐，数据**可用**
- 全库最新 `trade_date` 为 **2026-04-22**
- 相对 `daily/stk_factor_pro/cyq_perf` 的 `2026-04-24`，`cyq_chips` 当前**滞后 2 个交易日**

**影响**：
- `glob` 扫描时必须使用 `recursive=True` 或带通配符的模式，否则会误判为缺失
- 盘点文档必须区分“本地可用”与“是否已同步到目标交易日”，不能把这两件事混成一个状态

### 2.3 融资融券 (margin)

SKILL.md 期望路径：`margin/{YYYY}/margin_{code}.{prefix}.csv` (双结构兼容)

实际存储结构：`margin_detail/margin_detail_{code}.{prefix}.csv` (扁平结构)

**影响**：`load_margin_rows()` 已部分兼容扁平结构，但覆盖不全 (13,061 文件 vs 全市场 5,000+ 只，且部分股票无数据，如 600103.SH)。

### 2.4 SQLite 数仓

**注意：之前误以为数仓缺失/为空，实际情况如下：**

- 错误路径：`~/quant-data/tushare/股票数据/full_stock_warehouse.db` 确实是 0 bytes 空文件（遗留物）
- **正确路径：`~/agent-skills/custom/stock-deep-analysis/references/data/stock_analytics.db`**
- 文件大小：**7.8GB**
- 表数量：15 张（包括 daily_ohlcv、daily_basic、moneyflow、margin、cyq_chips、top_list、stk_auction_c/o、minute_kline 等）
- 最新日期：2026-04-22（比 CSV 原始数据滞后 1 个交易日）

**影响**：`data_access.py` 中 `load_daily_row()` / `load_daily_basic_row()` 等核心接口仍然**优先查询 SQLite**，未命中才回退 CSV。数仓是当前主链的活数据源，不是遗留文件。

### 2.5 `daily` 实际为 `year_stock` 结构，但 `registry.py` 未配置（P0 级）

实际存储路径：`daily/2026/daily_600103.SH.csv`

核心问题：`registry.py` 中 `daily` 接口未配置 `save_granularity: year_stock` 和 `file_prefix: daily_`，导致 `get_local_latest_date()` 按默认 `date` 结构查找 `daily/daily_20260425.csv`，**永远找不到任何文件**。这是一个根本性的基础设施 bug，会导致所有依赖 `daily` 的分析无法读取本地数据。

影响范围：所有分析模块（主体趋势、技术面、资金面等）中调用 `get_local_latest_date('daily')` 的逻辑均会失败。

修复建议：
1. 短期：在 `registry.py` 中为 `daily` 补充 `save_granularity: year_stock` 和 `file_prefix: daily_`。
2. 长期：统一 `update_daily.py` 中的写入逻辑，确保所有 `year_stock` 结构的数据接口在 `registry.py` 中都有正确的 `save_granularity` 和 `file_prefix` 配置。

### 2.6 `stk_factor_pro` 双结构并存

实际存储路径：
- `stk_factor_pro/2026/stk_factor_pro_600103.SH.csv`（year_stock 结构，共 152,694 文件）
- `stk_factor_pro/stk_factor_pro_600103.SH.csv`（平铺文件，数量未统计）

核心问题：`year_stock` 文件是旧版/迁移脚本遗留，当前 `update_daily.py` 中的 `update_stk_factor_pro` 只写平铺文件。这导致 `get_local_latest_date()` 可能找到 year_stock 旧数据而忽略了更新的平铺数据，或者两种数据之间产生穿越不一致。

影响范围：技术面分析可能使用滚动均线、RSI、MACD 时引用错误期的数据。

修复建议：
1. 清理旧的 `year_stock` 文件，或在 `registry.py` 中明确优先级。
2. 确保 `update_daily.py` 与 `data_access.py` 的读取逻辑一致。

### 2.7 `moneyflow_data/` 下个股级别 flat 文件数为 0

实际存储路径：`moneyflow_data/sector/ths_industry/`、`moneyflow_data/sector/ths_concept/`、`moneyflow_data/sector/dc_sector/`

核心问题：`moneyflow_data/` 当前已统一为分层目录结构，不再写 `market/*.csv`、`sector/*.csv` 这类扁平文件。旧扫描规则如果仍按扁平结构检查，会把实际存在的 `market/dc/`、`market/hsgt/`、`sector/ths_industry/`、`sector/ths_concept/`、`sector/dc_sector/` 误报成缺失。

影响范围：如果 SKILL.md 中有依赖按股票平铺文件获取资金流的逻辑，则会失败。需要确认 `update_daily.py` 中是否应该增加按股票平铺的写入逻辑，或者 SKILL.md 中应该仅使用全市场汇总表。

修复建议：
1. 确认 `update_daily.py` 中 `update_moneyflow` 的逻辑，看是否只写全市场汇总表，还是也写按股票平铺文件。
2. 如果只写全市场汇总表，则在 SKILL.md 中移除对于按股票平铺文件的依赖。
3. 如果需要按股票平铺文件，则在 `update_daily.py` 中增加写入逻辑。

### 2.8 `index_daily` 实际存储路径与脚本预期不一致（2026-05-29 新增）

**问题**：`index_daily` 数据实际存储在 `~/quant-data/tushare/指数数据/` 目录下，而不是 `~/quant-data/tushare/股票数据/` 下。

**实际路径**：
```
指数数据/index_daily/YYYY/index_daily_{code}.csv + .parquet
```

**影响**：
- 若脚本统一使用 `STOCK_DATA_ROOT` / `股票数据/` 作为基准路径，会始终无法找到 `index_daily` 数据，被误判为缺失
- 本会话中 `find ~/quant-data/tushare/股票数据 -name "*sh000001*"` 返回空，即因为搜索范围错误

**处理**：
- 读取大盘指数数据时，必须使用独立的 `指数数据/` 根目录
- 在 `data_access.py` 或分析脚本中，`index_daily` 的基准路径应设为 `~/quant-data/tushare/指数数据/`，而非 `~/quant-data/tushare/股票数据/`
- 检查命令：`ls ~/quant-data/tushare/指数数据/index_daily/2026/`

### 2.9 `top_list` / `top_inst` 年份汇总 parquet 结构（2026-05-29 新增）

**问题**：龙虎榜数据不是按日期分文件（`top_list_20260529.csv`），而是按年份汇总为单个 parquet 文件。

**实际路径**：
```
top_list/2026.parquet   # 含全年度所有龙虎榜记录
top_inst/2026.parquet   # 含全年度所有机构席位记录
```

**影响**：
- 传统按日期搜索方式（`top_list_20260529.csv`）在 parquet-only 环境中失效
- 必须先加载年份文件，再用 `df[df['trade_date'] == target_date]` 筛选
- `top_inst` 可能存在只有深股通/沪股通席位、无机构席位的情况（本地无记录 = 当日无机构参与，非数据缺失）

**正确读取方式**：
```python
import pyarrow.parquet as pq

# 加载年份汇总文件
top_list_df = pq.read_table(f'{root}/top_list/2026.parquet').to_pandas()
# 筛选特定日期和股票
date_str = '2026-05-29'
records = top_list_df[(top_list_df['trade_date'] == date_str) & (top_list_df['ts_code'] == '000725.SZ')]
```

**问题**：`index_daily` 数据实际存储在 `~/quant-data/tushare/指数数据/` 目录下，而不是 `~/quant-data/tushare/股票数据/` 下。

**实际路径**：
```
指数数据/index_daily/YYYY/index_daily_{code}.csv + .parquet
```

**影响**：
- 若脚本统一使用 `STOCK_DATA_ROOT` / `股票数据/` 作为基准路径，会始终无法找到 `index_daily` 数据，被误判为缺失
- 本会话中 `find ~/quant-data/tushare/股票数据 -name "*sh000001*"` 返回空，即因为搜索范围错误

**处理**：
- 读取大盘指数数据时，必须使用独立的 `指数数据/` 根目录
- 在 `data_access.py` 或分析脚本中，`index_daily` 的基准路径应设为 `~/quant-data/tushare/指数数据/`，而非 `~/quant-data/tushare/股票数据/`
- 检查命令：`ls ~/quant-data/tushare/指数数据/index_daily/2026/`

### 2.9 `top_list` / `top_inst` 年份汇总 parquet 结构（2026-05-29 新增）

**问题**：龙虎榜数据不是按日期分文件（`top_list_20260529.csv`），而是按年份汇总为单个 parquet 文件。

**实际路径**：
```
top_list/2026.parquet   # 含全年度所有龙虎榜记录
top_inst/2026.parquet   # 含全年度所有机构席位记录
```

**影响**：
- 传统按日期搜索方式（`top_list_20260529.csv`）在 parquet-only 环境中失效
- 必须先加载年份文件，再用 `df[df['trade_date'] == target_date]` 筛选
- `top_inst` 可能存在只有深股通/沪股通席位、无机构席位的情况（本地无记录 = 当日无机构参与，非数据缺失）

**正确读取方式**：
```python
import pyarrow.parquet as pq

# 加载年份汇总文件
top_list_df = pq.read_table(f'{root}/top_list/2026.parquet').to_pandas()
# 筛选特定日期和股票
date_str = '2026-05-29'
records = top_list_df[(top_list_df['trade_date'] == date_str) & (top_list_df['ts_code'] == '000725.SZ')]
```

实际存储路径：`moneyflow_data/sector/ths_industry/`、`moneyflow_data/sector/ths_concept/`、`moneyflow_data/sector/dc_sector/`

核心问题：`moneyflow_data/` 当前已经统一为分层目录结构，不再写 `market/*.csv`、`sector/*.csv` 这类扁平文件。旧扫描规则如果仍按扁平结构检查，会把实际存在的 `market/dc/`、`market/hsgt/`、`sector/ths_industry/`、`sector/ths_concept/`、`sector/dc_sector/` 误报成缺失。

影响范围：如果 SKILL.md 中有依赖按股票平铺文件获取资金流的逻辑，则会失败。需要确认 `update_daily.py` 中是否应该增加按股票平铺的写入逻辑，或者 `SKILL.md` 中应该仅使用全市场汇总表。

修复建议：
1. 确认 `update_daily.py` 中 `update_moneyflow` 的逻辑，看是否只写全市场汇总表，还是也写按股票平铺文件。
2. 如果只写全市场汇总表，则在 SKILL.md 中移除对于按股票平铺文件的依赖。
3. 如果需要按股票平铺文件，则在 `update_daily.py` 中增加写入逻辑。

---

## 三、关键发现

### 3.1 存在但可能过期的数据
- **筹码分布 (cyq_chips)**：本地文件存在，当前全局最新为 `2026-04-22`；相对 `2026-04-24` 的日线主数据滞后 2 个交易日。分析时必须标注数据延迟天数，不能因为“可用”就当成“已同步到目标日”。
- **分钟线**：目录结构统一为 `年/月/日/个股/周期`，但仅 99 个 CSV 文件覆盖 82 只股票（全市场 5000+ 只，覆盖率 < 2%）。分时分析模块在大部分股票上不可用。
- **融资融券 (margin)**：SQLite 中 `margin` 表最新日期 2026-04-21，比 daily 滞后 2 个交易日。

### 3.2 闲置数据（本地存在但代码未真正引用）
- `repurchase`：24,905 个文件，代码中仅在路径速查表被列出，未作为独立分析维度使用。
- `pledge_detail`：43,508 个文件，同上，仅被列出未被读取。

### 3.3 之前误判为缺失、实际存在的数据
以下数据类型在旧版清单中被误标为"缺失"，实际上已有本地数据：
- `top_list` / `top_inst` (龙虎榜)
- `limit_list_d` (涨停列表)
- `block_trade` (大宗交易)
- `stk_factor_pro` / `cyq_perf` (指标因子、筹码性能)
- `repurchase` / `pledge_detail` (回购、质押，但闲置)

### 3.4 真正缺失且影响分析的数据

**误判修正**：之前误以为以下两项完全缺失，实际上均已可用：
- **`index_daily`** → 实际存储在 `~/quant-data/tushare/指数数据/index_daily/YYYY/`，与股票数据分开存放。上证/深证/创业板指数均齐全。
- **SQLite 数仓** → 实际路径为 `~/agent-skills/custom/stock-deep-analysis/references/data/stock_analytics.db` (7.8GB, 15表)，`data_access.py` 主链仍然优先查询 SQLite。

**真正缺失的数据**（无降级方案或只能浏览器补抓）：
- **`industry_daily` / `concept_daily`** — 行业/概念指数日线
- **`moneyflow` (按股票结构)** — 实际为按日期全市场表，需适配
- **`adj_factor`** — 复权因子，完全缺失
- **`stk_holdertrade`** — 股东增减持，完全缺失
- **`daily_basic`** — 目录存在但 0 文件，实质等于缺失
- **`announcement`** — 公司公告
- **`news`** — 新闻舆情
- **`margin` (按股票结构)** — 降级使用 `margin_detail` (覆盖不全)
- **分时数据 (当日盘中)** — 需实时拉取

### 3.6 盘点方法论：不可依赖记忆，必须脚本扫描 + 实际读取验证

本次盘点暴露了一个关键认知偏差：之前多次依赖记忆判断数据是否存在，结果发现了大量结构不匹配和缺失。必须建立可复现的检查流程：

1. **解析 skill 数据引用**：读取 SKILL.md 中所有数据类型的描述，确定每种数据的预期路径、文件命名模式、存储结构。
2. **检查 registry 配置**：打开 `registry.py`，确认每个数据接口的 `save_granularity`、`file_prefix`、`path_template` 是否与预期一致。
3. **脚本扫描实际文件结构**：使用 `find` / `ls` / `wc -l` 等命令扫描实际目录结构，记录文件数、目录层级、命名规律。
4. **对比匹配性**：将实际结构与 registry 配置、SKILL.md 预期进行对比，标注不一致处。
5. **实际读取验证**：随机抽取几只股票，用 `data_access.py` 中的对应接口试读，确认能否正常返回行数、列名、日期范围。

**关键教训**：
- `目录存在 ≠ 数据可用`：如 `daily_basic` 目录存在但 0 文件。
- `文件数量多 ≠ 结构正确`：如 `daily` 有 142,936 文件但结构不匹配。
- `记忆不可信`：每次盘点必须从头扫描，不能因为"上次看过"就跳过验证。

### 3.5 实战分析中发现的额外问题（2026-04-25）

以下问题仅在**实际执行个股分析**时暴露，目录扫描无法发现：

1. **`stk_factor_pro` 技术指标列空值**
   - 发现时间：2026-04-25
   - 症状：600103.SH 的 `stk_factor_pro_600103.SH.csv` 中，从 2026-04-20 起 `kdj_k`, `macd`, `boll`, `cci`, `rsi_12`, `rsi_24`, `ma60` 等关键指标列为空值
   - 影响：`fetch_local_factors()` 返回的 `latest` 中大量指标显示为 "N/A"
   - 处理：分析时必须检查空值比例，必要时向前回退取最新非空行，或在报告中明确标注"数据源缺失"

2. **数据根目录不一致**
   - `index_daily` 实际存储在 `~/quant-data/tushare/指数数据/` 下，而非 `~/quant-data/tushare/股票数据/`
   - 若脚本统一使用 `股票数据/` 作为 base 路径，会导致 `index_daily` 被误判为缺失

3. **文件命名模式差异**
   - 按股票分文件：`daily_{code}.{prefix}.csv` (如 daily_600103.SH.csv) — daily, daily_basic, stk_factor_pro, cyq_perf, cyq_chips 等
   - 按日期分文件：`{type}_{YYYYMMDD}.csv` (如 top_list_20260425.csv) — top_list, top_inst, limit_list_d, block_trade 等
   - 脚本扫描时必须区分这两种模式，不能混用通配符

4. **目录存在 ≠ 数据可用**
   - 扫描发现 `top_list/2026/`、`moneyflow/2026/`、`margin_detail/2026/` 等目录存在，但读取 600103.SH 相关文件时返回 0 行或文件不存在
   - 原因：按日期命名的文件不包含所有股票；部分 2026 子目录实际为空或尚未同步
   - 教训：**必须实际读取文件验证行数，不能仅凭目录存在判断数据可用**

5. **`read_csv_robust` 实际位置**
   - 不在 `data/data_access.py` 中，而在 `quick_analyze.py` 内定义
   - 若其他脚本想复用该函数，需从 `quick_analyze.py` import 或自行实现

5. **浏览器超时时的可靠 fallback**
   - 当 `browser_navigate` 因网络问题超时时，`curl -s "https://qt.gtimg.cn/q={codes}" | iconv -f gb2312 -t utf-8` 可稳定获取实时行情
   - 返回格式为纯文本，字段用 `~` 分隔，需按 `~` split 解析
   - 适用场景：大盘指数实时、个股五档报价、涨跌幅、成交额等基础字段

6. **`data_access.py` `load_yearly_or_flat_rows()` 参数类型陷阱（2026-05-25）**
   - 症状：传入字符串路径时抛出 `AttributeError: 'str' object has no attribute 'name'`
   - 根因：函数内部调用 `root_dir.name`，要求参数为 `pathlib.Path` 对象
   - 修复：调用时必须用 `Path('/path/to/dir')` 包装，不能传字符串
   - 示例：
     ```python
     from pathlib import Path
     from data_access import load_yearly_or_flat_rows
     df = load_yearly_or_flat_rows(Path('/quant-data/daily'), 'daily_000725.SZ.csv')  # ✅
     df = load_yearly_or_flat_rows('/quant-data/daily', 'daily_000725.SZ.csv')       # ❌ AttributeError
     ```

7. **`cyq_chips` 数据内容异常识别（2026-05-25）**
   - 症状：京东方A的 `cyq_chips` 文件中 `price=11.3`，而当日股价仅5.27，数据严重偏离
   - 根因：数据源格式错误或混入了历史/其他股票数据
   - 检测方法：读取筹码数据后，将 `price` 范围与当日收盘价交叉验证；若偏差超过 ±20%，标记为 `invalid`
   - 处理：在报告中明确写出 `筹码数据内容异常，price字段与当前股价严重偏离，已弃用该维度`
   - 注意：`check_data_freshness.py` 只检查文件存在性和最新日期，**不验证数据内容合理性**。分析脚本必须自行做内容校验

8. **pandas `to_datetime` 整数日期解析陷阱（2026-05-25）**
   - 症状：`pd.to_datetime(20260512)` 被解析为 `1970-01-01 00:00:00.020260512`
   - 根因：pandas 将整数视为自1970-01-01起的纳秒数
   - 修复：读取 CSV 后必须先 `astype(str)`，再做 `to_datetime`
   - 示例：
     ```python
     df['trade_date'] = df['trade_date'].astype(str)          # 必须先转字符串
     df['trade_date'] = pd.to_datetime(df['trade_date'])      # ✅ 正确解析为 2026-05-12
     # df['trade_date'] = pd.to_datetime(df['trade_date'])    # ❌ 不转字符串则解析为1970年
     ```
   - 影响范围：所有按年/月/日数字命名的 `trade_date` 字段（`daily`、`stk_factor_pro`、`cyq_perf` 等）

9. **`daily` CSV 不含 `turnover_rate` 字段（2026-05-25）**
   - 症状：从 `daily/YYYY/daily_{code}.csv` 中选取 `turnover_rate` 列时抛出 `KeyError`
   - 事实：`turnover_rate` 存在于 `daily_basic` 中，不在 `daily` 中
   - 修复：换手率必须从 `daily_basic` 或腾讯API快照中获取，不能从 `daily` 表读取
   - 注意：`daily` 表含 `vol`、`amount`、`pct_chg`；`daily_basic` 表含 `turnover_rate`、`pe`、`pb`、`total_mv` 等

10. **`top_inst` parquet 汇总表 vs 按日期 CSV 的覆盖差异（2026-05-25）**
    - 症状：京东方A在 `top_list/2026.parquet` 中有记录，但在 `top_inst/2026.parquet` 中无记录；按日期的 `top_inst_20260521.csv` 中也无记录
    - 根因：该股票当日虽上龙虎榜，但无机构专用席位上榜（或数据未被采集）
    - 处理：分析时必须同时检查 parquet 汇总表和按日期 CSV；若两者均无记录，结论为"当日该股未出现机构席位上榜"，而非"数据缺失"
    - 与 `top_list` 的区分：`top_list` 有记录 = 当日上榜；`top_inst` 无记录 = 当日无机构席位参与，这是正常情况

11. **东方财富财务数据 API 字段内容异常（2026-05-25）**
    - 症状：`datacenter-web.eastmoney.com/api/data/v1/get` 返回的季度报告数据中，`同比`（YoY）字段实际包含的是上年同期的绝对金额，而非同比增长率百分比
    - 根因：API 字段命名误导，`同比` 字面意思为增长率，但实际返回的是去年同期基数
    - 检测方法：读取字段后立即做合理性校验——若"同比"数值与"本期金额"处于同一数量级（而非百分比量级），则判定为绝对值而非增长率
    - 处理：
      1. 不直接使用 API 返回的"同比"字段作为增长率
      2. 手动计算同比增长率：`(本期金额 - 上期金额) / abs(上期金额) * 100%`
      3. 在报告中明确标注 `同比增长率由本期与上期数据手工计算，非API直接返回`
    - 交叉验证：必须与东方财富新闻/公告页面或巨潮资讯网的官方披露数据交叉核对，确认计算结果

12. **`check_data_freshness.py` 日期格式解析错误（2026-05-29 新增）**
    - 症状：执行 `python3 scripts/check_data_freshness.py --symbol 000725.SZ --trade-date 20260529` 时抛出 `ValueError: unconverted data remains: :00`
    - 根因：脚本内部将命令行传入的 `20260529` 与某个带时分秒的日期字符串混合解析，导致 `strptime` 失败
    - 处理：
      1. 当前回退方案：不依赖该脚本，改用直接 `ls` 扫描目录 + `curl` 调用腾讯 API 确认数据时效性
      2. 长期修复：修改 `check_data_freshness.py` 中的日期解析逻辑，统一使用 `pd.to_datetime(..., format='%Y%m%d')` 或 `datetime.strptime(..., '%Y%m%d')`
      3. 临时绕过命令：`--trade-date 2026-05-29`（带横杠格式）可能可行，未验证
    - 影响：该脚本失败后整个分析流程无法自动检测数据时效，必须手工检查或使用降级方案

13. **`stk_factor_pro` 均线/指标列名格式（2026-05-29 新增）**
    - 症状：代码尝试读取 `ma5`、`ma10`、`ema5` 等时在 parquet 中不存在，抛出 `KeyError`
    - 实际列名规范：所有均线/指数移动平均均带 `ma_bfq_` 前缀，所有指数移动平均均带 `ema_bfq_` 前缀
    - 完整列名清单：
      - `ma_bfq_5`, `ma_bfq_10`, `ma_bfq_20`, `ma_bfq_30`, `ma_bfq_60`, `ma_bfq_250` — 移动平均线
      - `ema_bfq_5`, `ema_bfq_10`, `ema_bfq_20`, `ema_bfq_30`, `ema_bfq_60`, `ema_bfq_250` — 指数移动平均
      - `rsi_bfq_6`, `rsi_bfq_12`, `rsi_bfq_24` — RSI
      - `kdj_k_bfq`, `kdj_d_bfq`, `kdj_bfq` — KDJ（注意 J 值是 `kdj_bfq` 而非 `kdj_j_bfq`）
      - `macd_bfq`, `macd_signal_bfq`, `macd_hist_bfq` — MACD
    - 错误示例：
      ```python
      # ❌ 错误
      df[['ma5', 'ma10', 'ma20']]
      # ✅ 正确
      df[['ma_bfq_5', 'ma_bfq_10', 'ma_bfq_20']]
      ```

14. **`top_list` / `top_inst` 年份汇总 parquet 查询模式（2026-05-29 新增）**
    - 存储结构：不是按日期分文件（`top_list_20260529.csv`），而是按年份汇总为单个 parquet：`top_list/2026.parquet`、`top_inst/2026.parquet`
    - 查询方法：先加载年份文件，再用 `df[(df['trade_date'] == target_date) & (df['ts_code'] == code)]` 筛选
    - `top_inst` 特殊性：某些上榜日可能只有深股通/沪股通专用席位，无机构席位记录。本地无机构席位记录 ≠ 数据缺失，而是"当日无机构席位参与龙虎榜"
    - 示例：
      ```python
      import pyarrow.parquet as pq
      top_list = pq.read_table(f'{root}/top_list/2026.parquet').to_pandas()
      # 筛选特定日期+股票的龙虎榜记录
      records = top_list[(top_list['trade_date'] == '2026-05-29') & (top_list['ts_code'] == '000725.SZ')]
      ```

15. **融资融券 API 排序列名与文档不一致（2026-05-25 新增）**
    - 症状：调用东方财富融资融券 API 时，使用 `TRADE_DATE` 作为排序列返回 `排序列不存在` 错误
    - 根因：实际有效排序列为 `HOLD_DATE`，而非文档或直觉上的 `TRADE_DATE`
    - 处理：调用时必须使用 `sortColumns=HOLD_DATE&sortTypes=-1`，不能用 `TRADE_DATE`
    - 注意：该 API 返回的数据也可能存在字段内容异常，读取后需做基本合理性校验（如余额不应为负数、日期格式是否正确）

13. **季度报告 API 行数据缺失日期标识（2026-05-25）**
    - 症状：`datacenter-web.eastmoney.com/api/data/v1/get` 返回的季度报告明细中，部分行缺少 `REPORT_DATE` 或类似日期字段，无法直接判断该行对应哪个报告期
    - 根因：API 返回结构依赖前端渲染时的隐式顺序（按报告期倒序排列），纯 JSON 数据缺乏显式日期标注
    - 处理：
      1. API 数据仅作为辅助参考，不宜作为季度报告的主要权威来源
      2. 优先使用浏览器访问东方财富个股页面的"财务分析"或"公告"板块获取已明确标注报告期的数据
      3. 若必须使用 API 数据，需结合 `end_date` 参数和返回顺序做推断，并在报告中标注 `报告期由返回顺序推断，非显式字段标注`
    - 推荐降级路径：`浏览器抓取（东方财富个股页面） > 巨潮资讯网官方公告 > API 数据`

14. **浏览器新闻搜索作为财务数据交叉验证渠道（2026-05-25）**
    - 场景：当财务 API 返回异常或 ambiguous 数据时，东方财富的新闻搜索（`so.eastmoney.com/web/s?keyword={股票名}+业绩/季报/年报`）往往能提供更清晰、已标注报告期的财务摘要
    - 验证方法：新闻标题通常明确写出"2026年一季度净利润同比增长XX%"，可直接提取增长率数据与 API 计算结果比对
    - 可信度分层：
      - 高：财联社、证券时报等主流媒体引用的公司公告摘要
      - 中：东方财富网站自身整理的报道
      - 低：股吧、论坛转述
    - 使用约束：新闻数据只能作为交叉验证和辅助参考，不能替代官方公告作为最终财务判断依据

---
    - 症状：`quick_analyze.py` 输出的 `minute_intent` 是按15分钟分段的（16个时段），而 `SKILL.md` 要求的关键时窗是5个：`09:30-09:35`、`09:48-09:56`、`11:25-11:30`、`13:00-13:30`、`14:55-15:00`
    - 影响：不能直接拷贝 `minute_intent` 作为分析报告的分时意图章节，必须手动重新划分关键时窗
    - 处理：从 `minute_intent` 中提取对应时段的数据，或直接读取原始分钟线数据自己分析
    - 关键时窗分析要点：
      - `09:30-09:35`：开盘定价强弱
      - `09:48-09:56`：首次强冲与承接
      - `11:25-11:30`：上午收盘前5分钟
      - `13:00-13:30`：午后开盘前半小时
      - `14:55-15:00`：尾盘最后5分钟
    - `高位对倒` 标签使用注意：`quick_analyze` 的该标签是基于量价算法自动生成的，定义较粗放。分析时应结合价格位置、量能比例、均价线关系做人工判断，不能纯粹依赖算法标签

---

## 四、降级策略速查表

| 缺失数据 | 首选替代方案 | 次选方案 | 是否可跳过 |
|----------|-------------|----------|-----------|
| index_daily | 本地库存在，滞后 1 日时腾讯API补抓 | 同花顺/东方财富网页 | 可跳过 (本地备份可用) |
| moneyflow (按股票) | 适配 `moneyflow_data/individual/` 按日期全市场表 / SQLite `moneyflow` 表 | 跳过 | 可跳过 (降级说明) |
| industry_daily | 腾讯 API 行业指数 | 概念成分表 + 龙头股联动 | 可跳过 (降级说明) |
| concept_daily | 同花顺概念指数网页 | 手动标注概念热点 | 可跳过 (降级说明) |
| margin | margin_detail 降级 | 跳过 | 可跳过 |
| top_list | 本地无记录=当日未上榜，禁止浏览器补抓 | 跳过 | 可跳过 |
| block_trade | 跳过 | — | 可跳过 |
| announcement | 东方财富公告页面 | 巨潮资讯网 | 可跳过 (降级说明) |
| news | `market-news-intelligence` skill | 浏览器搜索 | 可跳过 (降级说明) |
| 1m 分钟线 | 跳过当日分时分析 | — | 可跳过 |
| cyq_chips | 跳过筹码分析 | — | 可跳过 |
| stk_auction | 跳过竞价分析 | — | 可跳过 |
| SQLite数仓 | 本地库 7.8GB 可用，无需回退 | 回退 Parquet/CSV 直读 | 可跳过 (SQLite已可用) |

---

## 五、浏览器抓取有效渠道（经验验证）

| 目标数据 | 有效 URL 模式 | 关键技巧 |
|----------|--------------|----------|
| 龙虎榜 | `emweb.securities.eastmoney.com/PC_HSF10/BusinessAnalysis/Index?type=web&code={code}` | 搜索关键词后点击第一个结果 |
| 基本面/公告 | `emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/Index?type=web&code={code}` | 直接 navigate |
| 资金流向 | `quote.eastmoney.com/concept/{code}.html` | 概念资金流向页面 |
|| 行业/概念指数 | `qt.gtimg.cn/q=sh000001,sz399001,sz399006` | 腾讯 API 返回纯文本，需 `iconv -f gb2312 -t utf-8` 转码 |
|| 大盘指数实时 | `curl -s "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006" \| iconv -f gb2312 -t utf-8` | 返回当日实时行情，含收盘价、涨跌幅、成交额等字段 |
| 季度报告/财务摘要 | `so.eastmoney.com/web/s?keyword={股票名}+一季报/年报/业绩` | 用于交叉验证 API 返回的季度数据；新闻标题通常含明确的同比增长率，可直接对比 |
| 筹码分布 | `emweb.securities.eastmoney.com/PC_HSF10/CapitalStockStructure/Index?type=web&code={code}` | 股东人数页面 |

> **注意**：当 `OpenClaw(决策) + Hermes(执行)` 时，浏览器抓取默认走 Hermes 执行层。

---

## 六、更新记录

| 日期 | 更新内容 | 更新人 |
|------|----------|--------|
| 2026-04-24 | 创建本清单，确认本地数据实际可用状态；发现 index_daily 实际缺失，必须 API 补抓；确认分钟线、cyq_chips、stk_auction 均存在 | Hermes |
| 2026-04-24 | 重大修正：(1) 发现 top_list/top_inst/limit_list_d/block_trade 实际已存在，之前误标为缺失；(2) 发现 moneyflow 实际为按日期全市场表结构，与 SKILL.md 期望的按股票结构不匹配；(3) 发现 cyq_chips 实际为年份子目录结构；(4) 发现 SQLite 数仓 full_stock_warehouse.db 为 0 bytes 空文件；(5) 发现 margin_detail 存在但覆盖不全 (600103.SH 无数据) | Hermes |
| 2026-04-24 | 数据格式三层分工落地：(1) 所有主要数据类型已双写 CSV + Parquet；(2) `data_access.py` 新增 `load_daily_rows_bulk` / `load_daily_basic_rows_bulk` / `load_moneyflow_rows_bulk` 批量查询接口；(3) 数据源章节更新为三层存储架构文档；(4) 修复 `load_yearly_or_flat_rows` 重复定义 bug | Hermes |
| 2026-04-24 | 修正文档中 `cyq_chips` 口径：确认全局最新日期为 `2026-04-22`，应标注为"本地可用但未同步到 2026-04-24 目标日"，避免将"可用"和"同步"混写 | Hermes |
| 2026-04-25 | SKILL.md 数据获取规则重构：(1) 历史数据与当日数据分类，三层分类表更新；(2) 时段分策规则：盘中浏览器/API直拿，午间/盘后本地优先，盘前用T-1；(3) 降级规则边界：降级链仅适用于浏览器/API优先类，本地only禁止降级；(4) 恢复关键区分（top_list vs limit_list）；(5) 移除汇总JSON缓存机制。同步更新资产清单中汇总JSON、top_list、分钟线降级策略描述。 | Hermes |
| 2026-04-25 | 新增「实战分析中发现的额外问题」章节：记录 stk_factor_pro 空值、数据根目录不一致、文件命名模式差异、目录存在≠数据可用、read_csv_robust 实际位置、curl fallback 等6项实战中暴露的问题。修复降级策略速查表 top_list 行格式错误（多余管道符）。 | Hermes |
| 2026-04-25 | **结构不匹配与缺失数据盘点**（本次更新）：(1) 发现 `daily` 实际为 `year_stock` 结构（`daily/2026/daily_600103.SH.csv`），但 `registry.py` 中未配置 `save_granularity: year_stock` 和 `file_prefix: daily_`，`get_local_latest_date()` 按默认 `date` 结构查找会**永远找不到文件**；(2) 发现 `stk_factor_pro` 双结构并存：`year_stock` 文件为旧版/迁移脚本遗留，当前 `update_daily.py` 的 `update_stk_factor_pro` 只写平铺文件；(3) 确认 `moneyflow_data/` 下个股级别 flat 文件数为 0，仅 sector/industry 级别数据存在；(4) 确认 `adj_factor`、`stk_holdertrade`、`daily_basic` 完全缺失；(5) 补充盘点方法论：解析 skill 数据引用 → 检查 registry 配置 → 扫描实际文件结构 → 对比匹配性 → 实际读取验证行数。 | Hermes |
