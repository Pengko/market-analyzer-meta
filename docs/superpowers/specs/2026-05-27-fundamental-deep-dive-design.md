# 基本面深度背调设计

## 概述

在现有 `_build_fundamental` (仅返回 PE/PB/市值 5 个字段) 基础上，新增分钟级联动分析同等模式(ETL + LLM)的**基本面深度分析 Agent**。按数据可用性自动降级，覆盖 1157+ 只股票。

## 架构

Phase 2 第 8 号并行 Agent: `run_fundamental_agent`，不依赖其他 Agent，结果进 `dimension_results["fundamental_deep"]`。

```
Phase 2 (并行) → run_fundamental_agent
      │
      ├─ Tier 1 (always): express + daily_basic → 财务趋势 + 估值
      ├─ Tier 2 (if data): income + balancesheet + cashflow → 三表深度
      ├─ Tier 3 (if data): fina_mainbz → 业务构成
      └─ Tier 4 (if data): top10_holders → 机构持仓
      │
      ▼
  llm_judge(FUNDAMENTAL_TASK, tiered_context)
      │
      ▼
  { financial_health, trend_label, risk_flags, narrative }
```

消费方:
- `build_final_decision` LLM context (长周期背景)
- Markdown 报告段落
- 不注入 `context_propagation_rules` (基本面偏长周期)

## 数据层

新增 `scripts/data/fundamental_provider.py` — 从 `财务数据/` 和 `股票数据/top10_*` 读取 parquet。

参考现有 `data_provider.py` 模式，使用 `FINANCIAL_DATA_ROOT` (已在 `common.py` 定义但从未使用)。

可用数据:

| 表 | 路径 | 覆盖 |
|----|------|------|
| express (业绩快报) | `财务数据/express/express_2026.parquet` | 1157 只 |
| daily_basic | `股票数据/daily_basic/{symbol}.parquet` | 全量 |
| fina_indicator (财务指标) | `财务数据/fina_indicator/fina_indicator_2026.parquet` | 26 只 |
| income (利润表) | `财务数据/income/income_2026.parquet` | 26 只 |
| balancesheet (资产负债表) | `财务数据/balancesheet/balancesheet_2026.parquet` | 63 只 |
| cashflow (现金流量表) | `财务数据/cashflow/cashflow_2026.parquet` | 27 只 |
| fina_mainbz (主营业务) | `财务数据/fina_mainbz/2025.parquet` | 30 只 (2025) |
| top10_holders (十大股东) | `股票数据/top10_holders/{symbol}.parquet` | ~42 只 |
| top10_floatholders (十大流通股东) | `股票数据/top10_floatholders/{symbol}.parquet` | ~42 只 |
| forecast (业绩预告) | `财务数据/forecast/forecast_2026.parquet` | 12 只 |

## ETL 指标

### Tier 1 — 财务趋势 + 估值 (always)

从 express + daily_basic 计算:

| 指标 | 来源 | 说明 |
|------|------|------|
| revenue_growth | express.yoy_net_profit (推算营收同比) | 收入增长趋势 |
| profit_growth | express.yoy_net_profit | 净利润同比 |
| roe_latest | express.diluted_roe (或 fina_indicator.roe) | 资本回报率 |
| eps_latest | express.diluted_eps | 每股收益 |
| revenue_3y_trend | 多期 express 对比 | 收入增长稳定性 |
| profit_3y_trend | 多期 express 对比 | 利润增长稳定性 |
| pe | daily_basic.pe_ttm | 估值 |
| pb | daily_basic.pb | 估值 |
| pe_percentile | daily_basic 1 年分位 | 估值水位 |
| pb_percentile | daily_basic 1 年分位 | 估值水位 |
| total_mv | daily_basic.total_mv | 总市值 |
| industry | stock_basic.industry | 所属行业 |

### Tier 2 — 三表深度 (if income + balancesheet 有数据)

| 指标 | 公式/来源 | 说明 |
|------|----------|------|
| debt_to_assets | 总负债/总资产 | 杠杆率 |
| current_ratio | 流动资产/流动负债 | 短期偿债 |
| gross_margin | fina_indicator.gross_margin | 毛利率 |
| netprofit_margin | fina_indicator.netprofit_margin | 净利率 |
| free_cashflow | cashflow.free_cashflow | 自由现金流 |
| rd_expense_ratio | income.rd_exp / revenue | 研发投入强度 |
| sell_expense_ratio | income.sell_exp / revenue | 销售费用率 |
| assets_impair_loss_ratio | income.assets_impair_loss / revenue | 资产减值风险 |
| receivables_ratio | balancesheet.accounts_receiv / total_assets | 应收账款占比 |
| inventory_ratio | balancesheet.inventories / total_assets | 存货占比 |
| goodwill_ratio | balancesheet.goodwill / total_assets | 商誉占比 |

### Tier 3 — 业务构成 (if fina_mainbz 有数据)

| 指标 | 说明 |
|------|------|
| top_segment_name | 第一大业务线名称 |
| top_segment_ratio | 第一大业务营收占比 |
| segment_count | 业务线数量 |
| segment_diversity | "单一"/"集中"/"多元" (基于 top3 占比) |

### Tier 4 — 机构持仓 (if top10_holders 有数据)

| 指标 | 说明 |
|------|------|
| institution_hold_ratio | 机构合计持仓比例 |
| holder_change | 最近一期增/减持方向 |
| top1_holder_name | 第一大股东名称 |
| top1_holder_ratio | 第一大股东持股比例 |
| holder_concentration | 前 10 大股东合计占比 |

## LLM Task

```python
FUNDAMENTAL_TASK = """基于基本面数据，判断个股的财务健康状况和投资价值。
当前数据层级: {tier_level} (1=基础, 2=三表深, 3=+业务构, 4=+机构持)

返回 JSON:
{
  "financial_health": "优秀"|"良好"|"一般"|"关注"|"风险",
  "trend_label": "增长期"|"稳定期"|"下滑期"|"不确定",
  "growth_quality": "高质量增长"|"粗放增长"|"无增长"|"收缩",
  "valuation_judgment": "低估"|"合理"|"高估"|"不确定",
  "risk_flags": ["风险标签1", "风险标签2"],
  "strength_flags": ["优势标签1", "优势标签2"],
  "narrative": "一段总结基本面特征的话",
  "confidence": 0-1
}"""
```

传入 context:

```python
{
  "tier_level": 3,
  "revenue_growth": 0.15,
  "profit_growth": 0.22,
  "roe": 0.12,
  "pe": 25.0,
  "pb": 3.5,
  "debt_to_assets": 0.45,
  "gross_margin": 0.30,
  "business_segments": "第一大业务LED面板占65%, 3条业务线",
  "holders": "机构持仓35%, 前10大股东合计52%",
  "industry": "电子",
  "stock_name": "京东方A",
}
```

## 数据访问

新增 `scripts/data/fundamental_provider.py`，遵循 `data_provider.py` 的 parquet 读取模式：

```python
def get_fundamental_express(symbol: str) -> dict | None
def get_fundamental_income(symbol: str) -> list[dict]
def get_fundamental_balancesheet(symbol: str) -> list[dict]
def get_fundamental_cashflow(symbol: str) -> list[dict]
def get_fundamental_mainbz(symbol: str) -> list[dict]
def get_fundamental_indicator(symbol: str) -> list[dict]
def get_top10_holders(symbol: str) -> list[dict]
def get_top10_floatholders(symbol: str) -> list[dict]
```

## 消费方集成

1. `dimension_results["fundamental_deep"]` — 与 intraday_linkage 同模式
2. `build_final_decision` LLM context 中增加 `fundamental_narrative` + `financial_health` 字段，供决策模型参考长周期背景
3. Markdown 报告在"基本面"段展示 `fundamental_deep` 输出

## 不做

- 不改 `_build_fundamental()` 的逻辑 (保留现有 PE/PB 5字段)
- 不注入 `context_propagation_rules`
- 不做产业链位置自动推断 (需要外部数据源，超出当前 parquet 范围)
- 不做同行业竞争格局对比 (需要全行业批量查询，超出单 stock agent 范围)

## 验收

1. `fundamental_provider.get_fundamental_express('000725.SZ')` 返回有效 dict
2. `run_fundamental_agent('000725', '2026-05-22')` 返回含 tier_level + financial_health 的 payload
3. `build_payload()` 的 dimension_results 中包含 `fundamental_deep`
4. 对只有 express 数据的股票也能产出 tier 1 级分析
