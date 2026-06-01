# 大盘分析增强计划 — 市场多空 + 消息热点 + 板块轮动

> 日期：2026-06-01
> 目标：增强 market-macro-analysis skill，从简单的大盘指数分析升级为多维度市场分析

---

## 一、现状分析

### 当前 market_macro_runner.py 的能力
- ✅ 大盘指数行情（腾讯 API / 本地 index_daily）
- ✅ DC 题材热度排行
- ✅ 涨停数据（limit_cpt/list_d/list_ths/step）
- ✅ 成分股深度分析（复用个股模块）
- ✅ 交易日校准

### 缺失能力
1. **市场多空判断** — 没有支撑/压力位分析，没有回踩到位判断
2. **消息热点分析** — 没有分析热点方向、最高标、风险评估
3. **板块轮动分析** — 没有分析哪些板块回调到位、资金流向
4. **多维度并行** — 只有串行流程，没有并行 Agent

---

## 二、架构设计

### 2.1 三 Agent 并行架构

```
输入："大盘怎么看" / "市场热点方向" / "板块轮动分析"
          ↓
    意图识别（聚合层）
          ↓
    Phase 1: 3 个 Agent 并行
    ├── Agent-市场方向 (MarketDirectionAgent)
    ├── Agent-消息热点 (NewsHotspotAgent)
    └── Agent-板块轮动 (SectorRotationAgent)
          ↓
    Phase 2: 汇总 + LLM 综合判断
          ↓
    输出报告
```

### 2.2 Agent-市场方向（MarketDirectionAgent）

**职责**：判断市场多空状态、是否回踩到位、支撑/压力位

**数据源**：
- 上证/深成/创业板日线（本地 index_daily + Tushare API）
- 技术指标（MA5/10/20/60、RSI、MACD）
- 成交量（放量/缩量判断）

**分析逻辑**：
1. 计算三大指数的关键支撑/压力位（MA5/10/20/60）
2. 判断当前价格与均线的关系（偏离度）
3. 计算 RSI、MACD 等指标的多空信号
4. 判断成交量是否放量/缩量
5. 综合判断：是否回踩到位、多空信号

**输出格式**：
```json
{
  "agent": "market_direction",
  "status": "available",
  "indices": {
    "上证指数": {
      "close": 4068.57,
      "pct_change": -0.73,
      "support": [4050, 4000, 3950],
      "resistance": [4100, 4150, 4200],
      "rsi": 45.2,
      "rsi_signal": "中性",
      "ma_relation": "低于MA5/MA10，接近MA20",
      "deviation_ma5": -1.2,
      "deviation_ma20": +0.5,
      "volume_signal": "缩量"
    },
    ...
  },
  "overall": {
    "direction": "中性偏弱",
    "pullback_status": "接近支撑位，但未确认到位",
    "key_levels": {"support": 4050, "resistance": 4100},
    "reasoning": "..."
  }
}
```

### 2.3 Agent-消息热点（NewsHotspotAgent）

**职责**：分析当前市场热点方向、最高标、风险评估

**数据源**：
- TrendRadar MCP（热榜 + RSS）
- 涨停数据（limit_cpt/list_d/list_ths/step）
- DC 题材热度（dc_concept）

**分析逻辑**：
1. 从 TrendRadar 获取当日热榜，分析热点方向
2. 从涨停数据提取最高标（连板最高、封单最强）
3. 分析热点板块类型（科技/消费/周期/金融）
4. 评估热点是否面临回调风险（连板高度、封单变化）
5. 预判资金可能流向的方向

**输出格式**：
```json
{
  "agent": "news_hotspot",
  "status": "available",
  "hot_directions": [
    {"direction": "AI硬件", "type": "科技", "heat": 1200, "risk": "中等"},
    {"direction": "贵金属", "type": "周期", "heat": 800, "risk": "低"}
  ],
  "highest_boards": [
    {"name": "*ST岩石", "code": "600696.SH", "days": 6, "type": "ST连板"},
    {"name": "华映科技", "code": "000536.SZ", "days": 3, "type": "光学光电子龙头"}
  ],
  "risk_assessment": {
    "overall_risk": "中等",
    "high_risk_sectors": ["AI硬件（连板高度偏高）"],
    "low_risk_sectors": ["贵金属（回调到位）"]
  },
  "money_flow_prediction": {
    "from": "AI硬件（高位兑现）",
    "to": "贵金属/面板（回调到位）",
    "reasoning": "..."
  }
}
```

### 2.4 Agent-板块轮动（SectorRotationAgent）

**职责**：分析板块轮动、哪些板块回调到位、资金流向

**数据源**：
- DC 概念板块行情（dc_daily）
- 板块资金流向（moneyflow_data）
- 板块K线（指数日线）

**分析逻辑**：
1. 计算各板块近5日涨跌幅，判断轮动方向
2. 识别回调到位的板块（跌幅足够、成交量萎缩）
3. 分析板块资金流向（哪些板块在流入、哪些在流出）
4. 预判轮动方向（哪些板块可能接力）

**输出格式**：
```json
{
  "agent": "sector_rotation",
  "status": "available",
  "rotation_status": "轮动",
  "hot_sectors": [
    {"name": "光学光电子", "pct_5d": +5.2, "status": "强势", "money_flow": "流入"},
    {"name": "贵金属", "pct_5d": -3.1, "status": "回调到位", "money_flow": "流出减缓"}
  ],
  "pullback_ready_sectors": [
    {"name": "贵金属", "pullback_pct": -3.1, "volume_shrink": true, "ready": true},
    {"name": "面板", "pullback_pct": -2.5, "volume_shrink": false, "ready": false}
  ],
  "rotation_prediction": {
    "from": "AI硬件（高位）",
    "to": "贵金属/面板（回调到位）",
    "confidence": 0.7
  }
}
```

---

## 三、文件结构

```
skills/market-macro-analysis/
├── SKILL.md                              ← 更新：三 Agent 架构说明
├── scripts/
│   ├── market_macro_runner.py            ← 重构：Phase 1 并行 + Phase 2 汇总
│   ├── agents/
│   │   ├── market_direction_agent.py     ← 新增：市场方向分析
│   │   ├── news_hotspot_agent.py         ← 新增：消息热点分析
│   │   └── sector_rotation_agent.py      ← 新增：板块轮动分析
│   └── render/
│       └── market_report_renderer.py     ← 新增：报告渲染
```

---

## 四、实施步骤

### Step 1: 创建 Agent-市场方向
- 文件：`agents/market_direction_agent.py`
- 数据源：本地 index_daily + Tushare API
- 分析：支撑/压力位、RSI/MACD、成交量
- 输出：多空判断 + 回踩预判

### Step 2: 创建 Agent-消息热点
- 文件：`agents/news_hotspot_agent.py`
- 数据源：TrendRadar MCP + 涨停数据 + DC 题材
- 分析：热点方向、最高标、风险评估
- 输出：热点方向 + 风险评估

### Step 3: 创建 Agent-板块轮动
- 文件：`agents/sector_rotation_agent.py`
- 数据源：DC 概念行情 + 板块资金流向
- 分析：轮动方向、回调到位、资金流向
- 输出：轮动判断 + 回调到位板块

### Step 4: 重构 market_macro_runner.py
- Phase 1：并行执行 3 个 Agent
- Phase 2：汇总 + LLM 综合判断
- 输出：完整的大盘板块分析报告

### Step 5: 更新 SKILL.md
- 三 Agent 架构说明
- 各 Agent 的输入输出规范
- 与个股分析的交互规则

---

## 五、与个股分析的交互

大盘分析完成后，如果用户接着问个股分析：
1. 大盘分析结果自动传递给个股分析
2. 个股分析直接使用大盘结论（多空判断、板块轮动）
3. 个股分析不需要重复分析大盘

这就是聚合层 skill 的价值：共享上下文，避免重复分析。

---

## 六、预期效果

**分析报告结构**：
```
## 一、市场方向分析
- 多空判断：中性偏弱
- 回踩状态：接近支撑位，但未确认到位
- 关键价位：支撑 4050 / 压力 4100
- RSI：45.2（中性）

## 二、消息热点分析
- 热点方向：AI硬件（科技）、贵金属（周期）
- 最高标：*ST岩石 6连板（ST类型）、华映科技 3连板（光学光电子）
- 风险评估：AI硬件连板高度偏高，面临回调风险
- 资金流向：从AI硬件流向贵金属/面板

## 三、板块轮动分析
- 轮动阶段：轮动
- 强势板块：光学光电子（+5.2%，资金流入）
- 回调到位：贵金属（-3.1%，成交量萎缩）
- 轮动预判：从AI硬件→贵金属/面板

## 四、综合交易建议
- 方向：中性偏弱，防守为主
- 重点板块：贵金属（回调到位）、面板（回调中）
- 回避板块：AI硬件（高位回调风险）
- 关键价位：上证 4050 支撑
```

---

## 七、与 stock-deep-analysis 的关系

| 维度 | market-macro-analysis | stock-deep-analysis |
|------|----------------------|---------------------|
| 入口 | 大盘/板块/消息 | 个股代码/名称 |
| 分析顺序 | 宏观→微观 | 微观→宏观 |
| 共享组件 | 同一套数据层、分析模块 | 同一套数据层、分析模块 |
| 交互 | 分析结果传递给个股 | 接收大盘结论，避免重复 |

---

## 八、待确认

1. **三个 Agent 的分析逻辑** — 上面描述的分析逻辑是否合理？
2. **输出格式** — JSON 结构是否满足需求？
3. **与个股的交互** — 如何传递大盘结论给个股分析？
4. **LLM 综合判断** — Phase 2 的 LLM prompt 应该侧重什么？
