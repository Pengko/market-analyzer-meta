# 项目当前状态（2026-04-14）

## 目的

把历史会话 `019d7db5-f9be-74c3-97ce-e4fb7ae7e479` 中与 `stock-deep-analysis` 相关的决策、已落地项、未完成项整理为当前可执行状态，避免后续继续翻长会话记录。

对应会话文件：

- `/Users/penghongming/.codex/sessions/2026/04/12/rollout-2026-04-12T02-02-51-019d7db5-f9be-74c3-97ce-e4fb7ae7e479.jsonl`

## 当前代码口径

### 数据根目录

- 当前统一数据根目录是 `/Users/penghongming/quant-data/tushare`
- 股票数据根目录是 `/Users/penghongming/quant-data/tushare/股票数据`
- 消息面数据根目录是 `/Users/penghongming/quant-data/tushare/消息面数据`
- 代码定义位置：`scripts/common.py`

### 已经接入主报告链路的模块

- `market_context`
- `sector_context`
- `financing_context`
- `auction_intent`
- `trend_structure`
- `chip_structure`
- `volatility_context`
- `peer_linkage`
- `context_propagation`
- `final_decision`

这些模块已经在 `scripts/build_stock_report.py` 主流程里组装进 `payload` 和 `dimension_results`。

## 历史会话里已确认、且当前代码仍成立的事项

### 1. `peer_linkage` 已不是纯占位

- 已实现基于 `stock_basic + daily` 的对标股选择
- 选股逻辑：优先同 `industry`，不足时回退同 `area`
- 固定产出 3 个角色：
  - `龙头`
  - `中军`
  - `高弹性`
- 会给出目标股相对位置：
  - `领先`
  - `中位`
  - `掉队`

### 2. `final_decision` 已动作化

- 当前是规则引擎，不再是纯 `manual_pending`
- 综合输入包括：
  - `intraday_strength`
  - `next_day_bias`
  - `capital_freshness`
  - `peer_linkage`
  - `auction_intent`
  - `news_sentiment`
  - `trend_structure`
  - `chip_structure`
  - `volatility_context`
- 当前主要输出：
  - `适合轻仓试仓`
  - `仅适合观察`
  - `观察确认`
  - `暂不适合建仓`

### 3. 周/月结构、筹码结构、波动率已接入

历史会话里当时提到这些仍待补，但从当前代码看，以下三块已经落到主报告流程：

- `trend_structure`
  - 基于 `weekly` 和 `monthly`
  - 判断周线、月线与均线关系
- `chip_structure`
  - 基于 `cyq_perf` 和 `cyq_chips`
  - 判断获利盘、筹码均价、现价附近集中度
- `volatility_context`
  - 基于 `stk_factor_pro`
  - 输出波动率环境与分位

结论：这三块现在不应再按“纯待补模块”理解，而应区分为“已实现，但可能受数据新鲜度/样本长度影响而降级”。

补充修正：

- `stk_factor_pro` 本地历史技术因子数据目前已补全，不应再归类为“数据没补齐”
- `volatility_context` 当前更可能只在以下情况降级：
  - 个别标的历史样本不足 20 条
  - 单文件内 `high/low/pre_close` 有效字段不足

### 4. 融资融券已接入

- 当前已有 `analyze_financing_context`
- 读取：
  - `margin_detail`
  - `margin`
  - `margin_eligibility_browser`
- 会在报告中输出融资标的判断与说明

结论：融资数据也不再是完全未接入状态。

### 5. `auction_intent` 不再是长期占位

历史会话中曾决定“`auction_intent` 先放后面”，但当前代码和 changelog 已更新：

- `auction_intent` 已拆到独立脚本 `scripts/analyze_auction_intent.py`
- 主报告通过导入调用，不再内嵌在 `build_stock_report.py`
- 当前口径是“集合竞价汇总意图判断”
- 数据来源以本地 `stk_auction_o / stk_auction_c` 为准

结论：`auction_intent` 现在不是“未实现”，而是“已实现简化版，不做 09:15-09:25 双阶段撤单行为分析”。

## 当前仍应视为主要缺口的部分

### 1. `sector_context` 仍有降级口径

- 虽已实现，但会出现：
  - `available`
  - `fallback_available`
  - `manual_pending`
- 说明板块理解链路不是全量稳定命中
- 仍需要继续增强题材归因、前排映射、事件加分项和龙头预测稳定性

### 2. `context_propagation` 已有实现，但偏摘要串联

- 当前会输出：
  - `market_to_sector`
  - `market_sector_news_to_stock`
  - `market_sector_stock_to_intraday`
- 但本质更像解释性文本拼接
- 还不是严格的“因果传播规则引擎”

### 3. 消息面字段仍可能不完整

历史会话里提到以下字段经常为空，这个判断仍然有效：

- `direction`
- `level`
- `impact_role`
- `impact_on_price`

也就是新闻抓到了，不代表消息结构化结论已经稳定补齐。

### 4. 数据新鲜度仍然是第一风险源

当前很多“待补”并不来自代码没写，而来自数据状态：

- `missing`
- `stale`
- 历史样本不足
- T+1 验证数据未同步

特别是这些目录的数据状态会直接影响报告质量：

- `cyq_perf`
- `cyq_chips`
- `weekly`
- `monthly`
- `stk_auction_o`
- `stk_auction_c`

说明：

- `stk_factor_pro` 现在更适合归为“已具备数据基础”，不再是当前主要数据缺口

### 5. 新闻链路曾有过兼容问题

历史会话里记录过 `scripts/run_news_pipeline.py` 转发参数时丢失 `executor` 的问题。是否已完全修复，需要在后续真实跑样时再复核一次。

## 现在对项目状态的准确认知

### 已完成

- 主报告骨架已从“只看分时/次日偏向”扩展到多维结构
- `peer_linkage` 和 `final_decision` 已可用
- 周/月、筹码、波动率、融资、集合竞价都已经有代码实现
- 数据根目录已统一到 `quant-data/tushare/股票数据`

### 未完成

- 题材理解与传播链路仍不够稳
- 消息面结构化字段不够完整
- 若本地数据过期，很多模块会重新降级成“待补/降级”
- 验证闭环文档里仍保留大量旧的“待补”记录，容易和当前实现状态混淆

## 建议的后续优先级

### P0

- 先做一次真实样本复跑，确认当前报告里还剩哪些字段仍频繁降级
- 区分“代码未实现”与“数据缺失/过期”两类问题

### P1

- 优先补强 `sector_context`
- 把 `context_propagation` 从文本摘要升级为更明确的规则链

### P2

- 修消息面结构化字段稳定性：
  - `direction`
  - `level`
  - `impact_role`
  - `impact_on_price`

### P3

- 清理或重写 `references/pending-validations/` 里和当前实现口径不一致的旧记录
- 避免后续误把旧待补文档当成当前代码状态

## 一句话结论

这个项目现在不是“很多核心模块还没写”，而是“核心模块大多已经写了，但题材链路、消息结构化和数据新鲜度仍然决定最终质量”。
