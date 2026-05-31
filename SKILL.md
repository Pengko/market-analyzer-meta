---
name: market-analyzer-meta
description: A股市场分析聚合层。根据用户意图自动识别分析方向（个股/大盘板块/消息面），路由到对应子 skill 执行。支持三种分析模式：个股微观→宏观、大盘宏观→微观、消息面驱动→板块→个股。
---

# 市场分析聚合层

统一入口，根据用户意图路由到对应分析方向。不做具体分析，只做意图识别和调度。

## 分析方向

| 方向 | 入口 | 分析顺序 | 子 Skill |
|------|------|----------|----------|
| 个股分析 | 股票代码/名称 | 个股→板块→大盘→消息 | `skills/stock-deep-analysis/` |
| 大盘板块 | "大盘/板块怎么看" | 大盘→板块热点→个股→消息 | `skills/market-macro-analysis/` |
| 消息面驱动 | 新闻/公告/热点关键词 | 消息→板块映射→个股→大盘 | `skills/news-driven-analysis/` |

## 意图识别规则

根据用户输入的第一句话判断方向：

### 个股分析（stock-deep-analysis）
触发条件：
- 包含股票代码（6位数字）
- 包含股票名称（如"京东方"、"青山纸业"）
- "分析XX"、"XX怎么样"、"XX适合买吗"
- 提供了成本价/仓位信息

路由：直接调用 `skills/stock-deep-analysis/` skill，不经过其他子 skill。

### 大盘板块分析（market-macro-analysis）
触发条件：
- "大盘怎么看"、"今天市场怎么样"
- "XX板块怎么样"、"面板板块分析"
- "哪些板块在涨"、"热点板块"
- "市场情绪"、"风格偏向"

路由：调用 `skills/market-macro-analysis/` 子 skill。

### 消息面分析（news-driven-analysis）
触发条件：
- "有什么消息"、"最近新闻"
- "XX消息对什么板块有影响"
- "利好/利空哪些股票"
- 用户粘贴了公告/新闻内容

路由：调用 `skills/news-driven-analysis/` 子 skill。

### 混合意图
当用户同时提到多个方向时（如"大盘不好，京东方还能买吗"）：
1. 先执行大盘板块分析（宏观环境）
2. 再执行个股分析（微观判断）
3. 最后综合两个结果给出建议

## 执行脚本

每个子 skill 都有对应的 Python 入口脚本：

| 子 Skill | 执行脚本 | 用法 |
|----------|----------|------|
| 个股分析 | `skills/stock-deep-analysis/scripts/build_stock_report.py` | `python build_stock_report.py --symbol 000725 --trade-date 2026-05-29` |
| 大盘板块 | `skills/market-macro-analysis/scripts/market_macro_runner.py` | `python market_macro_runner.py --date 2026-05-29` |
| 消息面 | `skills/news-driven-analysis/scripts/news_driven_runner.py` | `python news_driven_runner.py --date 2026-05-29 --keyword "面板"` |

所有脚本共享 `skills/stock-deep-analysis/scripts/` 的模块，通过 `PYTHONPATH` 引用。

## 路由流程

```
用户输入
  ↓
intent_classifier.py（意图识别）
  ↓
┌─────────────────┬─────────────────┬─────────────────┐
│ direction=stock │ direction=market│ direction=news  │
│                 │                 │                 │
│ build_stock_    │ market_macro_   │ news_driven_    │
│ report.py       │ runner.py       │ runner.py       │
└─────────────────┴─────────────────┴─────────────────┘
  ↓
输出统一格式报告
```

## 共享组件

三个子 skill 共享以下底层模块：

| 组件 | 路径 | 用途 |
|------|------|------|
| 并行 Agent | `skills/stock-deep-analysis/scripts/parallel/agents.py` | 8 个并行分析 Agent |
| 决策引擎 | `skills/stock-deep-analysis/scripts/decision/decision_engine.py` | 上下文传导+最终决策 |
| 渲染层 | `skills/stock-deep-analysis/scripts/render/report_renderer.py` | 报告格式化 |
| 数据层 | `skills/stock-deep-analysis/scripts/data/` | 本地 parquet 读取 |
| 获取层 | `skills/stock-deep-analysis/scripts/fetchers/` | API/浏览器数据获取 |
| 信号层 | `skills/stock-deep-analysis/scripts/signals/` | 技术信号+竞价+分时 |
| 工具层 | `skills/stock-deep-analysis/scripts/time_util.py` 等 | 时间/融资/资金 |
| 数据同步 | `skills/tushare-pro/` | Tushare 数据下载/同步/补全 |

子 skill 不重复实现这些模块，通过 `sys.path` 引用 `skills/stock-deep-analysis/scripts/`。
数据同步能力通过 `skills/tushare-pro/` 提供，包括日线、周线、月线、指数、概念等数据的自动补全。

## 输出格式

三个方向统一使用以下报告结构（顺序可变）：

1. 场景与数据
2. 大盘环境
3. 板块判断
4. 对标股联动
5. 目标股结构
6. 交易结论
7. 置信度评分

具体章节顺序由子 skill 决定，但必须包含以上全部模块。

## 参考文件

| 文件 | 说明 |
|------|------|
| `skills/stock-deep-analysis/SKILL.md` | 个股深度分析完整规范 |
| `skills/market-macro-analysis/SKILL.md` | 大盘板块分析规范 |
| `skills/news-driven-analysis/SKILL.md` | 消息面分析规范 |
| `skills/tushare-pro/SKILL.md` | 数据同步规范（日线/周线/月线/指数/概念） |
| `skills/stock-deep-analysis/references/feishu-formatting-guide.md` | 飞书表格渲染问题与解决方案（含 Gateway streaming card 修复记录） |
| `references/check-data-freshness-bug.md` | check_data_freshness.py 日期格式bug与降级指南 |
