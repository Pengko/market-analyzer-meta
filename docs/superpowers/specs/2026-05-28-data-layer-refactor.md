# Data Layer 重构 — 三层架构

## 概述

将当前每个 Agent 自行拉数据的模式，重构为 **数据层 → 纯分析 Agent → 决策引擎** 三层架构。

## 三层模型

```
Phase 0: 交易日定位 + 数据新鲜度校验 + 补缺失
    │
Phase 1: Data Slice 构建 (DataSlicer)
    │
    ├── MarketSlice     (行情/指数)
    ├── ConceptSlice    (概念/对标)
    ├── FinancialSlice  (财务/估值)
    ├── NewsSlice       (消息面)
    └── MinuteSlice     (分钟线)
    │
Phase 2: 纯分析 Agent (只做分析, 不拉数据)
    │
    ├── analyze_peer(ConceptSlice, MarketSlice) → PeerResult
    ├── analyze_fundamental(FinancialSlice) → FundamentalResult
    ├── analyze_linkage(MinuteSlice, MarketSlice) → LinkageResult
    └── ...
    │
Phase 3: 决策引擎 (不变)
```

## Agent 接口

每个 Agent 变成纯函数，输入打平的 dict，输出结构化 dict：

```python
def analyze_peer(
    concepts: list[str],
    sector_daily: SECTOR_DAILY,       # {概念名: {"bk_code": "BK0580.DC", "pct_change": [20d]}}
    peer_stocks: PEER_STOCKS,         # {概念名: [{"ts_code": "000100.SZ", "name": "TCL科技"}, ...]}
    daily_pcts: list[float],          # 个股 20d pct_chg
    daily_latest: DICT,               # {pct_chg, amount, close, amplitude}
) → PeerResult
```

## Data Class 定义

```python
# scripts/data/slices.py

@dataclass
class MarketSlice:
    daily: STRICT      # 个股日线: {open, close, pct_chg, amount, 20d_pcts, amplitude}
    daily_basic: STRICT
    index_daily: dict[str, INDEX_ROW]  # sh000001 → {pct_chg, 20d}
    stock_basic: STRICT

@dataclass
class ConceptSlice:
    names: list[str]
    peer_map: dict[str, list[str]]  # 概念名 → [ts_code]
    sector_codes: dict[str, str]    # 概念名 → BK代码
    sector_daily: dict[str, list[float]]  # BK代码 → [20d pct_change]

@dataclass
class FinancialSlice:
    has_financials: bool
    express: list[DICT]
    income: list[DICT]
    balancesheet: list[DICT]
    cashflow: list[DICT]
    mainbz: list[DICT]
    holders: list[DICT]

@dataclass
class MinuteSlice:
    stock: list[DICT]
    indexes: dict[str, list[DICT]]  # sh000001 → [{dt, close, ...}]
    sector: list[DICT]              # BK sector minute
```

## 数据获取

1. **交易日定位**: `trade_cal_all.csv` 查 today 是否交易日；9:30 前用 t-1
2. **缺失补全**: tushare pro → 腾讯 API → 浏览器 (三级降级)
3. **tushare 初始化**: 独立模块 `scripts/data/tushare_client.py`

## 降级链

每类数据独立降级:

| 数据 | Tier 1 | Tier 2 | Tier 3 |
|------|--------|--------|--------|
| 日线 | 本地 parquet | tushare pro.daily() | 腾讯 API |
| 分钟线 | 本地 parquet | 腾讯 API | 浏览器 |
| 概念成分 | dc_member 缓存 | kpl_concept_cons | stock_basic.industry |
| 财务 | 本地 parquet | tushare pro | 降级 |
| 新闻 | TrendRadar MCP | 浏览器抓取 | 降级 |

## 改造文件

| 文件 | 改动 |
|------|------|
| `scripts/data/tushare_client.py` | **新建** — tushare pro 初始化 |
| `scripts/data/slices.py` | **新建** — Data Class 定义 |
| `scripts/data/dataslicer.py` | **新建** — Phase 0/1: 交易日定位 + Slice 构建 |
| `scripts/parallel/agents.py` | **改造** — Agent 改为纯函数 |
| `scripts/build_stock_report.py` | **改造** — 调用 DataSlicer → 分发 Slice → 聚合结果

## 不做

- 不改 Phase 3 决策引擎逻辑
- 不改报告渲染
- 不改 SKILL.md 规则
- Agent 不改内部 ETL 计算逻辑（只抽掉数据获取）
