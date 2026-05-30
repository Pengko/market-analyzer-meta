# 根目录兼容入口清单

这个文件说明 `scripts/` 根目录下为什么仍然有很多文件，以及哪些文件是真入口，哪些只是兼容壳。

## 1. 真正长期入口

这些文件当前仍然应该保留在根目录，并被视为稳定入口：

- [build_stock_report.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/build_stock_report.py)
  - 主报告编排入口
- [common.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/common.py)
  - 共享工具与路径常量

## 2. 本地子目录实现的兼容壳

这些根目录文件当前主要是：
- `from xxx import *`
- 必要时转发 `main()`

它们存在的原因是：
- 不打断现有命令
- 不打断旧脚本导入路径
- 允许真实实现下沉到职责目录

### 分析层兼容壳
- [market_analyzer.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/market_analyzer.py) -> `analysis/market_analyzer.py`
- [sector_analyzer.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/sector_analyzer.py) -> `analysis/sector_analyzer.py`
- [stock_trend_analyzer.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/stock_trend_analyzer.py) -> `analysis/stock_trend_analyzer.py`

### 数据/决策/渲染/运行时兼容壳
- [data_access.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/data_access.py) -> `data/data_access.py`
- [decision_engine.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/decision_engine.py) -> `decision/decision_engine.py`
- [report_renderer.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/report_renderer.py) -> `render/report_renderer.py`
- [runtime_fetch.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/runtime_fetch.py) -> `runtime/runtime_fetch.py`
- [runtime_quality.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/runtime_quality.py) -> `runtime/runtime_quality.py`
- [news_runtime.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/news_runtime.py) -> `runtime/news_runtime.py`

### 抓取脚本兼容壳
- [fetch_minute_data.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/fetch_minute_data.py) -> `fetchers/fetch_minute_data.py`
- [fetch_open_auction.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/fetch_open_auction.py) -> `fetchers/fetch_open_auction.py`
- [fetch_close_auction.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/fetch_close_auction.py) -> `fetchers/fetch_close_auction.py`
- [fetch_tushare_auction.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/fetch_tushare_auction.py) -> `fetchers/fetch_tushare_auction.py`
- [fetch_eastmoney_auction.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/fetch_eastmoney_auction.py) -> `fetchers/fetch_eastmoney_auction.py`
- [fetch_open_auction_eastmoney.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/fetch_open_auction_eastmoney.py) -> `fetchers/fetch_open_auction_eastmoney.py`
- [fetch_close_auction_eastmoney.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/fetch_close_auction_eastmoney.py) -> `fetchers/fetch_close_auction_eastmoney.py`
- [fetch_eastmoney_historical_intraday.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/fetch_eastmoney_historical_intraday.py) -> `fetchers/fetch_eastmoney_historical_intraday.py`
- [hermes_browser_fetch.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/hermes_browser_fetch.py) -> `fetchers/hermes_browser_fetch.py`
- [get_quote_tencent.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/get_quote_tencent.py) -> `fetchers/get_quote_tencent.py`

### 移动端兼容壳
- [discover_ths_mobile_stock_concepts.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/discover_ths_mobile_stock_concepts.py) -> `mobile/discover_ths_mobile_stock_concepts.py`
- [discover_ths_mobile_subthemes.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/discover_ths_mobile_subthemes.py) -> `mobile/discover_ths_mobile_subthemes.py`
- [discover_ths_mobile_theme_leaders.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/discover_ths_mobile_theme_leaders.py) -> `mobile/discover_ths_mobile_theme_leaders.py`
- [browser_margin_eligibility.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/browser_margin_eligibility.py) -> `mobile/browser_margin_eligibility.py`

### 核心信号兼容壳
- [analyze_auction_intent.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/analyze_auction_intent.py) -> `signals/core/analyze_auction_intent.py`
- [check_data_freshness.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/check_data_freshness.py) -> `signals/core/check_data_freshness.py`
- [score_intraday_strength.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/score_intraday_strength.py) -> `signals/core/score_intraday_strength.py`
- [score_next_day_bias.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/score_next_day_bias.py) -> `signals/core/score_next_day_bias.py`
- [summarize_auction_strength.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/summarize_auction_strength.py) -> `signals/core/summarize_auction_strength.py`

### 研究型信号兼容壳
- [detect_divergence.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/detect_divergence.py) -> `signals/research/detect_divergence.py`
- [detect_divergence_enhanced.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/detect_divergence_enhanced.py) -> `signals/research/detect_divergence_enhanced.py`
- [detect_divergence_v2.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/detect_divergence_v2.py) -> `signals/research/detect_divergence_v2.py`
- [detect_divergence_v3.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/detect_divergence_v3.py) -> `signals/research/detect_divergence_v3.py`
- [run_next_day_bias_suite.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/run_next_day_bias_suite.py) -> `signals/research/run_next_day_bias_suite.py`

## 3. 外部 skill 代理壳

这些文件不是本目录真实实现，而是代理到其他 skill：

- [fetch_browser_news.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/fetch_browser_news.py)
- [prepare_news_context.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/prepare_news_context.py)
- [run_news_pipeline.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/run_news_pipeline.py)
- [news_context.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/news_context.py)
- [init_news_capture.py](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/init_news_capture.py)

它们当前主要代理到：
- `market-news-intelligence`

## 4. 后续建议

### 可以长期保留的
- `build_stock_report.py`
- `common.py`
- 根目录兼容入口（只要还有外部脚本或习惯命令依赖）

### 后续可逐步收缩的
- 纯兼容壳文件
- 前提是确认没有外部引用继续依赖根目录路径

### 使用建议
- 新代码优先直接导入子目录实现
- 根目录文件只作为历史兼容和命令入口保留
