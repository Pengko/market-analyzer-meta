---
name: market-macro-analysis
description: 大盘板块分析。三Agent并行：市场方向（多空/回踩/支撑压力）+ 消息热点（热点方向/最高标/风险）+ 板块轮动（强势板块/回调到位/轮动预判）。适用于"大盘怎么看"、"哪些板块在涨"、"市场风格偏向"等宏观分析需求。
---

# 大盘板块分析

三 Agent 并行分析，最后汇总出完整报告。

## 架构

```
Phase 1: 3 个 Agent 并行（ThreadPoolExecutor）
├── Agent-市场方向：RSI/MACD/MA/成交量 → 多空判断+回踩分析+支撑压力位
├── Agent-消息热点：热度+涨停+连板 → 热点方向+最高标+风险评估
└── Agent-板块轮动：DC题材日线 → 强势板块+回调到位+轮动预判

Phase 2: 汇总 + 成分股分析 + LLM 综合判断
→ 输出完整的大盘板块分析报告
```

## 共享模块（依赖 skills/stock-deep-analysis/）

| 模块 | 路径 | 用途 |
|------|------|------|
| 交易日历 | `skills/stock-deep-analysis/scripts/data/data_access.py` | `resolve_trade_date_by_calendar` |
| 时间工具 | `skills/stock-deep-analysis/scripts/time_util.py` | `scenario_from_now` |
| Tushare 客户端 | `skills/tushare-pro/utils/tushare_client.py` | `create_pro_api()` |
| 个股分析模块 | `skills/stock-deep-analysis/scripts/analysis/` | 趋势/筹码/波动率/融资 |

## Agent-市场方向

**文件**：`scripts/agents/market_direction_agent.py`

**数据源**：本地 `index_daily` parquet + Tushare API

**分析逻辑**：
1. 加载三大指数（上证/深成/创业板）近60日日线
2. 计算技术指标：MA5/10/20/60、RSI(14)、MACD(12,26,9)
3. 计算支撑/压力位（MA均线）
4. 判断成交量信号（放量/缩量）
5. 综合多空判断（偏多/中性/偏空）
6. 判断回踩状态（接近支撑位/已跌破/未回踩）

**输出**：多空方向 + 回踩状态 + 支撑/压力位 + 各指数 RSI/MACD

## Agent-消息热点

**文件**：`scripts/agents/news_hotspot_agent.py`

**数据源**：DC 题材数据 + 涨停数据（limit_step/limit_list_d）+ 本地新闻

**分析逻辑**：
1. 加载 DC 题材热度数据（dc_concept）
2. 加载涨停数据（连板阶梯、涨停个股明细）
3. 判断热点板块类型（科技/消费/周期/金融/制造）
4. 评估热点风险（连板高度、封单变化）
5. 预判资金流向（从高位板块→回调板块）

**输出**：热点方向表 + 最高标 + 风险评估 + 资金流向预判

## Agent-板块轮动

**文件**：`scripts/agents/sector_rotation_agent.py`

**数据源**：DC 题材数据（dc_concept）+ 板块K线历史

**分析逻辑**：
1. 加载 DC 题材数据（当日 + 近5日历史）
2. 计算轮动阶段（加强/分化/轮动/退潮）
3. 识别强势板块（近5日累计涨幅最大）
4. 识别回调到位板块（近5日涨但近3日跌）
5. 预判轮动方向（资金从哪流向哪）

**输出**：轮动阶段 + 强势板块表 + 回调到位板块 + 轮动预判

## 成分股深度分析

**复用 stock-deep-analysis 模块**，对热点板块龙头个股做深度分析：

| 维度 | 函数 |
|------|------|
| 趋势结构 | `analyze_trend_structure` |
| 筹码分析 | `analyze_chip_structure` |
| 波动率 | `analyze_volatility_context` |
| 基本面 | `build_fundamental` |
| 融资融券 | `analyze_financing_context` |

## 输出格式

```
## 一、大盘环境
- 指数行情表
- 市场方向判断（多空/回踩/支撑压力）
- 消息热点分析（热点方向/最高标/风险）
- 板块轮动分析（轮动阶段/强势板块/回调到位）

## 二、板块热点（DC题材数据）
- 题材热度排行
- 涨停统计
- 连板阶梯

## 三、龙头个股
- 龙头个股表

## 四、成分股深度分析
- PE/PB/获利盘/套牢盘/集中度

## 五、交易结论
- 方向判断 + 操作建议 + 热点板块 + 关注个股
```

## 执行脚本

```bash
# 分析指定日期
PYTHONPATH=skills/stock-deep-analysis/scripts python3 scripts/market_macro_runner.py --date 2026-05-29

# JSON 输出
PYTHONPATH=skills/stock-deep-analysis/scripts python3 scripts/market_macro_runner.py --format json
```
