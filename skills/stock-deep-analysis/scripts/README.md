# Scripts 目录说明

> 更新记录见：[../CHANGELOG.md](../CHANGELOG.md)

## 目录结构

```text
scripts/
  analysis/    大盘、板块、个股趋势分析
  data/        本地数据读取与交易日历定位
  decision/    综合裁决、验证跟踪、联动解释
  render/      Markdown / JSON 渲染
  runtime/     运行时抓取、质量校验、消息联动
  fetchers/    数据抓取脚本（分钟、竞价、Hermes/网页抓取）
  mobile/      移动端同花顺辅助脚本
  signals/     单因子/单模块评分与信号脚本
  tests/       历史回放与测试脚本
  build_stock_report.py  主编排入口
  common.py    共享常量与基础工具
```

## 各目录职责

### `analysis/`
- `market_analyzer.py`：大盘环境分析
- `sector_analyzer.py`：题材/板块分析、龙头预测、小题材匹配
- `stock_trend_analyzer.py`：周月结构、T+1、T+2、筹码、波动率

### `data/`
- `data_access.py`：交易日历、本地日线、融资浏览器快照等基础读取

### `decision/`
- `decision_engine.py`：
  - `peer_linkage`
  - `final_decision`
  - `context_propagation`
  - `validation_tracking`

### `render/`
- `report_renderer.py`：主报告 Markdown / 文本格式化

### `runtime/`
- `runtime_fetch.py`：
  - 网络时间
  - 分钟运行时补抓
  - 分时可用性判断
- `runtime_quality.py`：
  - 分钟文件路径
  - 关键时窗完整性校验
  - 浏览器分钟 payload 落盘
  - 抓取失败原因归一化
- `news_runtime.py`：
  - 消息 JSON 读取
  - 消息增强
  - 自动消息 pipeline 路径解析

### `fetchers/`
- `fetch_minute_data.py`：分钟抓取统一入口
- `fetch_*auction*.py`：竞价抓取脚本
- `hermes_browser_fetch.py`：Hermes 浏览器抓取执行入口
- `get_quote_tencent.py`：腾讯行情补充抓取

### `mobile/`
- `discover_ths_mobile_*`：移动端同花顺题材/龙头/概念辅助识别
- `browser_margin_eligibility.py`：融资标的浏览器/移动端辅助识别

### `signals/`
- `core/`：主报告依赖的核心信号模块
  - `analyze_auction_intent.py`
  - `check_data_freshness.py`
  - `score_intraday_strength.py`
  - `score_next_day_bias.py`
  - `summarize_auction_strength.py`
- `research/`：策略因子与实验性研究脚本
  - `detect_divergence*.py`
  - `run_next_day_bias_suite.py`

### `tests/`
- `test_*`：历史验证与测试脚本

## 根目录兼容入口

以下文件仍保留在 `scripts/` 根目录，当前只做兼容转发：

- `market_analyzer.py`
- `sector_analyzer.py`
- `stock_trend_analyzer.py`
- `data_access.py`
- `decision_engine.py`
- `report_renderer.py`
- `runtime_fetch.py`
- `runtime_quality.py`
- `news_runtime.py`

这样做的目的是：
1. 不打断现有脚本导入路径
2. 先把实现下沉到职责目录
3. 后续再逐步清理旧入口引用

## 关键入口

### 主报告
```bash
python3 build_stock_report.py --symbol 000815.SZ --trade-date 2026-04-13
```

### 分钟抓取统一入口
```bash
python3 fetch_minute_data.py --symbol 000815.SZ --trade-date 20260413
```

当前分钟抓取策略：
- 沪市：`SH -> SS`，`Yahoo` 首源
- 深市：`SZ -> SZ`，`Yahoo` 首源
- 北交所：跳过 `Yahoo`，走其他源
- `Eastmoney` 作为回退源

### 架构说明
- 详见：[ARCHITECTURE.md](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/ARCHITECTURE.md)
