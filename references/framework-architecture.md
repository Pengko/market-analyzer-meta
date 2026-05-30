# 股票深度分析框架结构图

## 目标

把 skill 的分析逻辑从“规则很多但分散”整理成可扩展的三层架构。

## 三层结构

### 第一层：Dimension Analyzers

每个维度先独立产出自己的判断，不互相污染原始结论。

当前建议维度：

- `market_context`
- `sector_context`
- `news_sentiment`
- `peer_linkage`
- `stock_structure`
- `intraday_structure`
- `auction_intent`
- `capital_chip_tech`

其中 `capital_chip_tech` 当前可先承接：

- 量价突变度
- 主力资金新鲜度
- 龙虎榜确认
- 活跃席位净买/净卖摘要

### 第二层：Context Propagation

各维度不是完全孤立，默认存在有方向的上下文传递：

- `market_context -> sector_context`
- `market_context + sector_context + news_sentiment -> stock_structure`
- `market_context + sector_context + stock_structure -> intraday_structure`
- `intraday_structure + auction_intent + stock_structure -> final decision inputs`

注意：

- 这里传递的是背景和约束
- 不是把前一层的结论强行覆盖后一层

### 第三层：Final Decision

最后统一裁决，至少回答：

- 哪些维度偏多
- 哪些维度偏空
- 哪些维度冲突
- 当前最主要的驱动是什么
- 当前交易结论是什么

## 当前代码映射

- `check_data_freshness.py`
  - 数据可用性，不属于核心判断维度，但为所有维度提供前置状态
- `score_intraday_strength.py`
  - 属于 `intraday_structure`
- `score_next_day_bias.py`
  - 属于 `stock_structure + sector_context + partial capital/tech`
- `build_stock_report.py`
  - 当前是统一出口，后续可逐步变成三层结构的汇总器

## 后续演进方向

1. 先把现有输出整理进 `dimension_results`
2. 再显式补 `context_propagation`
3. 最后让 `final_decision` 独立成一层
