# 大盘/板块/个股 日内分钟级联动分析设计

## 概述

将当前仅基于日线摘要的联动判断升级为**分钟级定量联动分析**。在现有 ETL + LLM 框架下，新增脚本计算层产出联动指标，LLM 做联动质量定性判断。

## 改造范围

### 新增文件
| 文件 | 责任 |
|------|------|
| `scripts/signals/intraday_linkage.py` | 分钟级联动指标计算引擎 |
| `config/sector_index_codes.json` | 概念名 → 板块指数代码 映射表 |

### 改造文件
| 文件 | 改动 |
|------|------|
| `scripts/runtime/runtime_fetch.py` | 新增大盘/板块分钟数据采集 |
| `scripts/parallel/agents.py` | 新增 `run_intraday_linkage_agent` (Phase 2 第7个agent) |
| `scripts/decision/context_propagation_rules.py` | 新增 `intraday_linkage` 规则组 |
| `scripts/fetchers/pre_collect_data.py` | `fetch_tencent_minute` 支持指数代码 |

### 不改的
- `scripts/analysis/market_analyzer.py` — 大盘日线分析不变
- `scripts/analysis/sector_analyzer.py` — 板块日线分析不变
- `scripts/decision/decision_engine.py` — 决策引擎不变（通过 build_final_decision LLM context 消费联动标签）
- `scripts/signals/core/score_intraday_strength.py` — 分时评分不变（联动信号不从该通道注入）
- research/test 脚本

## 数据流

```
实时/盘后触发 → runtime_fetch
    ├─ 拉取个股分钟CSV (已有)
    ├─ 腾讯API → sh000001 分钟K线 (大盘)
    └─ 腾讯API → BKxxxx 分钟K线 (板块)
          ↓
    三个序列对齐到统一时间轴
          ↓
    intraday_linkage.py
    ├─ compute_relative_strength()
    ├─ detect_time_conduction()
    ├─ sliding_correlation()
    └─ detect_divergence()
          ↓
    结构化指标 → llm_judge(LINKAGE_TASK)
           ↓
    ┌─ context_propagation_rules (bias_delta ±1~±2)
    └─ build_final_decision LLM context (linkage_label)
    
    注: 输出 **不注入** score_intraday_strength, 而是通过 Phase 2 第 7 个
    并行 agent (run_intraday_linkage_agent) 独立运行, 结果被 Phase 3
    的 context_propagation_rules + decision_engine 消费。
```

## 分钟数据采集

### 大盘指数分钟K线

复用已有 `fetch_tencent_minute()` 逻辑，传入指数腾讯代码：

| 指数 | 腾讯代码 |
|------|---------|
| 上证指数 | `sh000001` |
| 深证成指 | `sz399001` |
| 创业板指 | `sz399006` |
| 沪深300 | `sh000300` |

`fetch_tencent_minute()` 已在 `pre_collect_data.py:149` 实现并验证可用。需要将其抽取到共享模块或由 `runtime_fetch.py` 调用。

### 板块指数分钟K线

**Tier 1 — 腾讯 API 直接拉取**：板块指数代码格式为 `bkbkxxxx`（BK 代码前缀）。
需要映射层 `config/sector_index_codes.json`：

```json
{
  "固态电池": "bkbk0818",
  "LED概念": "bkbk0899",
  "AI硬件": "bkbk0999",
  ...
}
```

映射表初始通过预查填充。未命中的概念名：
**Tier 2 — Hermes 浏览器兜底**：通过同花顺/东财网页搜索板块指数代码。
**Tier 3 — 成分股合成**：取板块成分股分钟线中位数涨幅作近似，仅当 ≥5 只成分股有分钟数据时启用。

## 联动指标定义 (ETL)

### 1. 相对强度 (Relative Strength)

```
rs(t) = stock_ret(t) - benchmark_ret(t)
stock_ret(t) = (close(t) - open(0930)) / open(0930)
benchmark_ret(t) = (close(t) - open(0930)) / open(0930)
```

输出：
- `final_rs`: 收盘相对强度
- `trend`: "持续走强"/"先弱后强"/"先强后弱"/"持续走弱"
- `key_points`: 5 个时点快照 [1000, 1030, 1130, 1400, 1500]

### 2. 传导检测 (Time Conduction)

在大盘序列上滑动窗口检测极值点（5分钟窗口低点/高点，change > 0.5%）：
- 对每个极值点，在个股序列上检查后续 ±10 分钟内是否有同向反应
- `follow_ratio`: 跟随事件数 / 总极值事件数
- `avg_delay_min`: 平均传导时滞（分钟）
- `label`: "及时跟随"(follow_ratio>0.7) / "部分跟随"(0.3~0.7) / "不跟随"(<0.3)

### 3. 滑动相关系数 (Sliding Correlation)

15 分钟滚动 Pearson r：
- 个股 vs 大盘
- 个股 vs 板块（可用时）
- `avg_r`: 全天平均相关系数
- `breakdown_ratio`: 脱钩占比（r < 0.3 的窗口 / 总窗口）
- `label`: "紧密"(avg_r>0.6) / "中等"(0.3~0.6) / "松散"(<0.3)

### 4. 背离检测 (Divergence Detection)

个股涨跌幅方向与板块/大盘相反且幅度超 threshold = 2% → 背离事件
- `count`: 背离次数
- `max_pct`: 最大背离幅度
- `periods`: 背离时段 [{start, end, direction}]

## LLM Task

```python
LINKAGE_TASK = """基于联动分析数据，判断个股与大盘/板块的日内联动质量。
返回 JSON:
{
  "linkage_label": "强跟随"|"一般跟随"|"弱跟随"|"脱钩"|"独立走势",
  "relative_strength_judgment": "明显强于大盘"|"略强"|"同步"|"略弱"|"明显弱势",
  "conduction_quality": "及时"|"滞后"|"未跟随",
  "correlation_quality": "紧密"|"中等"|"松散",
  "divergence_risk": "高"|"中"|"低",
  "narrative": "一句话总结日内联动特征",
  "confidence": 0-1
}"""
```

传入 context：

```python
{
  "relative_strength": {"final_rs": 2.3, "trend": "先弱后强", "key_points": {...}},
  "time_conduction": {"follow_ratio": 0.8, "avg_delay_min": 2, "label": "及时跟随"},
  "correlation": {"market_avg_r": 0.72, "breakdown_ratio": 0.05, "label": "紧密"},
  "divergence": {"count": 1, "max_pct": 2.5, "periods": [...]},
  "sector_correlation": {"avg_r": 0.65, "label": "中等"},
  "stock_info": {"name": "...", "symbol": "...", "top_theme": "..."}
}
```

## 消费方集成

### run_intraday_linkage_agent (Phase 2 第7号 Agent)

新增并行 Agent, 独立于其他 6 个 Agent 运行:

```python
# parallel/agents.py
def run_intraday_linkage_agent(
    pure_symbol: str,
    trade_date_text: str,
    top_theme: str | None = None,
) -> dict:
    """
    1. fetch_index_minutes(sh000001, ...)  → 大盘分钟
    2. resolve_sector_code(top_theme)      → BK 代码
    3. fetch_sector_minutes(BK, ...)       → 板块分钟 (可选)
    4. import load_rows()                  → 个股分钟
    5. score_linkage(stock, market, sector)→ ETL 指标
    6. llm_judge(LINKAGE_TASK, ctx)        → 联动标签
    7. return {linkage_label, ..., linkage_indicators}
    """
```

输出通过 `parallel_results["intraday_linkage"]` 被 Phase 3 消费:

1. **context_propagation_rules** — `intraday_linkage` 规则组读 `linkage_label` + `divergence_risk` + `relative_strength_judgment` → bias_delta
2. **build_final_decision** — linkage_label 进 LLM context, 影响最终决策推演
3. **Markdown 报告** — 联动分析段落显示给用户

### context_propagation_rules.py

新增 `intraday_linkage` 规则组，输入 `linkage_label` + `divergence_risk` + `relative_strength_judgment`，输出 bias_delta：

| 条件 | bias_delta |
|------|-----------|
| 强跟随 + 相对强度优势 | +1 |
| 强跟随 + 无明显优势 | 0 |
| 脱钩 + divergence_risk=高 | -2 |
| 弱跟随 | -1 |
| 独立走势(明显强于大盘) | +1 |

## 验收

1. `intraday_linkage.score_linkage()` 对 000725.SZ 2026-05-26 能产出带联动指标的 dict
2. `llm_judge(LINKAGE_TASK, context)` 返回有效 JSON 标签
3. `run_intraday_linkage_agent()` 返回 `{linkage_label, relative_strength_judgment, ...}` 完整 payload
4. `context_propagation_rules.evaluate_chain()` 能消费联动标签产出 bias_delta
5. 在已有 `quick_analyze` 输出中能看到联动判断行

## 不做

- 不做行情 API 历史分钟数据的本地持久化（当前为实时获取，即用即弃）
- 不改 `build_peer_linkage()` 的日线对标逻辑
- 不改决策引擎的 final_decision 输出格式，只加联动信号输入
- 不改 Hermes 执行的中文进度提示
