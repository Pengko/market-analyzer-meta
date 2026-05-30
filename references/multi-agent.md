## 多代理分析架构

### 概述
通过 `delegate_task` 实现六代理分层分析架构。Orchestrator 负责数据收集与浏览器抓取，四个专业 Analyst Agent 并行分析，Meta-Reviewer Agent 负责冲突检测与最终仲裁。

### 架构设计

```
Orchestrator (数据收集 + 本地数据读取 + 浏览器抓取补全)
    |
    ├→ DragonTiger Analyst Agent   ──┐
    ├→ Technical Analyst Agent     ──┤
    ├→ Fundamental Analyst Agent   ──┤ 并行执行
    ├→ Sentiment Analyst Agent     ──┤
    └→ Risk Analyst Agent          ──┘
                                        |
                                        ▼
                               Meta-Reviewer Agent (冲突检测 + 加权仲裁 + 最终决策)
```

**DragonTiger Agent 定位**：
- 作为**首个并行代理**，第一时间获取龙虎榜数据并判断
- 判断标准：
  1. 当日是否上榜？
  2. 近10日上榜次数？
  3. 是否连续上榜（>=2天）？
- 输出格式：Markdown 章节（含表格）+ JSON 摘要
- **数据源**：本地 `top_list` / `top_inst` **only**，禁止浏览器补抓
- 连续上榜时，额外分析同批上榜股票的关联特征

### 代理职责与输入输出

| 代理 | 输入 | 输出 | 典型耗时 |
|------|------|------|----------|
| **Orchestrator** | 股票代码、分析日期 | 标准化 JSON 数据包 + 浏览器补全数据 | ~5秒 |
| **DragonTiger Analyst** | 股票代码、分析日期 | 龙虎榜 Markdown + JSON 摘要 | ~3秒 |
| **Technical Analyst** | 原始数据包 (daily/factors/chips/moneyflow/minute) | 技术面 Markdown + JSON 摘要 | ~70秒 |
| **Fundamental Analyst** | 原始数据包 + 浏览器基本面数据 | 基本面 Markdown + JSON 摘要 | ~75秒 |
| **Sentiment Analyst** | 原始数据包 + 浏览器消息数据 | 消息面 Markdown + JSON 摘要 | ~300秒 |
| **Risk Analyst** | 原始数据包 + Technical 输出 + DragonTiger 输出 | 风险评估 Markdown + JSON 摘要 | ~190秒 |
| **Meta-Reviewer** | 五个 Analyst 的 JSON 摘要 | 最终统一报告 + 决策 JSON | ~80秒 |

> 全流程总耗时：**约12分钟** (各代理并行执行 + Meta-Reviewer 串行)

### 加权评分公式

```
综合评分 = Technical × 25% + DragonTiger × 15% + Fundamental × 20% + Sentiment × 20% + Risk × 20%
```

| 维度 | 权重 | 理由 |
|------|------|------|
| Technical | 25% | 短线交易最直接参考 |
| DragonTiger | 15% | 主力资金真实意图，连续上榜为强信号 |
| Risk | 20% | 风控优先，风险决定仓位 |
| Fundamental | 20% | 中长期价值锚定 |
| Sentiment | 20% | 短期情绪催化/压制 |

### 冲突检测与仲裁规则

**1. 轻度分歧（方向一致，强度不同）**
- 定义：评分差距 1-3 分，方向相同（如 5/10 观望 vs 3/10 回避）
- 处理：Meta-Reviewer 按权重计算加权均值，给出倾向性结论
- 例：300265.SZ 中 Technical 5/10(观望) vs 其他三位 3/10(回避)，三维度形成共识（70%权重）压倒技术支撑，最终 3.6/10 回避

**2. 严重分歧（方向矛盾）**
- 定义：评分差距 > 4 分，或推荐方向矛盾（如两看多 + 两看空）
- 处理：检查分歧原因，按权重计算后给出倾向性结论，并标注争议点
- 待验证：未遇到严重分歧场景，需要在实战中验证

**3. 仲裁原则**
- 基本面/情绪/风险三维度形成共识时，其系统权重（70%）可压倒技术维度的短期支撑信号
- 技术支撑在高风险环境下可能快速失效
- 风险评级为"高风险"时，Technical 的"观望"建议应降级为"减仓/回避"

### 数据包规范

Orchestrator 生成的标准化数据包必须包含：

```json
{
  "symbol": "600103.SH",
  "trade_date": "20260423",
  "data_sources": {"daily": "available", "factors": "available", "chips": "stale", "moneyflow": "available", "minute": "available"},
  "daily": [日线数据数组],
  "factors": [因子数据数组],
  "chips": [筹码分布数据数组],
  "moneyflow": [资金流向数据数组],
  "minute": [分钟线数据数组],
  "index_data": {"上证指数": {...}, "深成指": {...}, "创业板指": {...}},
  "stock_sectors": {"industry": ["行业板块1", ...], "concept": ["概念板块1", ...]},
  "browser_data": {
    "company_core": {"PE": "...", "PB": "...", "ROE": "...", "总市值": "..."},
    "industry_comparison": {"行业排名": "...", "行业平均PE": "..."},
    "recent_announcements": [...],
    "news_highlights": [...],
    "top_list": {...},
    "intraday_summary": {...}
  }
}
```

### JSON 标准化接口

每个 Analyst Agent 必须输出：
1. **Markdown 分析报告**（不超过1500字，精炼聚焦结论）
2. **JSON 摘要文件**（保存到 /tmp/）

JSON 必须包含字段：
```json
{
  "agent": "Technical",
  "symbol": "600103.SH",
  "trade_date": "20260423",
  "overall_score": 4.0,
  "score_range": "1-10",
  "recommendation": "SHORT_TERM_CAUTION",
  "direction": "neutral_bearish",
  "key_levels": {"support": "4.95", "resistance": "5.20"},
  "risk_level": "medium",
  "time_horizon": "1-5交易日",
  "confidence": 7
}
```

Meta-Reviewer 输出必须包含：
```json
{
  "symbol": "600103.SH",
  "trade_date": "20260423",
  "weighted_score": 3.26,
  "final_recommendation": "强烈回避",
  "final_action": "SELL",
  "risk_rating": "高风险",
  "confidence": 9,
  "conflict_detected": false,
  "conflict_resolution": "四维度一致看空，无冲突",
  "analyst_scores": {
    "technical": 4.0,
    "fundamental": 4.5,
    "sentiment": 2.8,
    "risk": 2.0
  }
}
```

### 实战验证记录

**1. 600103.SH（青山纸业）@ 2026-04-23 盘后**
- Technical: 4.0/10 | Fundamental: 4.5/10 | Sentiment: 2.8/10 | Risk: 2.0/10
- **四维度一致看空，无分歧**
- Meta-Reviewer: 3.26/10 | 强烈回避 | 置信度 9/10
- 总耗时：**约27分钟**（未优化前）

**2. 300265.SZ（通光线缆）@ 2026-04-23 盘后**
- Technical: 5.0/10(观望) | Fundamental: 3.0/10(回避) | Sentiment: 3.0/10(不建议抄底) | Risk: 3.0/10(减仓)
- **轻度分歧**: Technical 观望 vs 其他三位回避
- 仲裁: 三维度形成"戴维斯双杀"共识（权重70%），Technical 的观望降级为回避
- Meta-Reviewer: 3.6/10 | 回避 | 置信度 7.5/10
- 总耗时：**约12分钟**（Technical Agent 限制输出后从 826秒 降至 69秒）

### 性能优化策略

1. **Technical Agent 耗时优化**
   - 优化前：826秒，优化后：69秒
   - 方法：给 delegate_task 的 context 添加 `"你的分析报告必须精炼，不超过1500字"` 约束
   - 全流程从 ~27分钟 降至 ~12分钟（提升2.3倍）

2. **Sentiment Agent 耗时优化**
   - 现状：~300秒，仍是瓶颈
   - 方法：限制输出长度或移除冗余工具调用

3. **并行化改进**
   - 四个 Analyst Agent 已实现完全并行
   - Meta-Reviewer 串行依赖四个 Analyst 输出，无法并行
   - 最大并行数受 `delegate_task` 的 `max_concurrent_children=3` 限制，实际使用3+1分批

### delegate_task 参数模板

```python
# 示例：并行发起四个 Analyst Agent
date_info = "2026-04-23"

tasks = [
    {
        "goal": "作为 Technical Analyst，分析 300265.SZ 的技术面",
        "context": f"## 任务背景...\n## 数据包...\n"
                   "你的分析报告必须精炼，不超过1500字，聚焦结论。"
                   "同时生成JSON摘要保存到 /tmp/technical_300265.json"
    },
    {
        "goal": "作为 Fundamental Analyst，分析 300265.SZ 的基本面",
        "context": "..."
    },
    {
        "goal": "作为 Sentiment Analyst，分析 300265.SZ 的情绪面",
        "context": "..."
    },
    {
        "goal": "作为 Risk Analyst，分析 300265.SZ 的风险",
        "context": "..."
    }
]

delegate_task(tasks=tasks, toolsets=[])
```

### 待验证场景

- [ ] 严重分歧：两看多 + 两看空的冲突仲裁
- [ ] 盘中分析：利用 Infoway 实时推送补全分钟线
- [ ] 消息面增强：整合 market-news-intelligence 的归一化新闻数据
- [ ] 批量分析：同时分析多只股票的性能与资源消耗


## 多股票并行分析工作流（2026-05-26 新增）

当用户在同一个请求中提供多只股票（如"分析神州信息、聚灿光电和旭升集团"）时，禁止串行逐只分析。采用并行化策略减少总耗时。

### 并行化原则

| 维度 | 并行策略 | 说明 |
|------|------------|------|
| 数据获取 | **完全并行** | 同时运行多个独立的终端/浏览器调用，取决于工具并发能力 |
| AI推演 | **逐只但连续** | 单个LLM上下文中依次生成多只报告，避免信息混杂 |
| 报告保存 | **并行** | 文件IO互不影响，可并行写入 |

### 推荐执行流程

1. **统一获取市场数据**（一次）
   - 大盘指数、两市成交额（腾讯 API 或本地 index_daily）
   - 所有个股共用同一套大盘判断

2. **并行获取个股数据**（多线程）
   - 对每只股票同时运行：
     - `本地日线 daily` 读取
     - `本地因子 stk_factor_pro` 读取
     - `本地筹码 cyq_chips` 读取
     - `腾讯行情快照` API
     - `东方财富公告` API（若需要）
   - 实现方式：多个 `terminal()` 调用在不同会话中并行执行，或单个 execute_code 中用 ThreadPoolExecutor 并行化

3. **逐只生成分析报告**（串行，但快速）
   - 基于已获取的结构化数据，逐只生成标准版报告
   - 不需要在报告生成过程中再次获取数据

4. **并行保存报告**
   - 同时写入 `references/pending-validations/{date}/`
   - 同时生成对应的 `-meta.json` 文件

5. **结尾统一追问**（一次）
   - 所有股票报告输出完成后，统一追问：
     - `是否需要做T执行版？`
     - `是否已持有其中某些股票？`

### 实测效益

以三只股票（60326/神州信息、300708/聚灿光电、603305/旭升集团）为例：
- 串行数据获取（每只按 30秒计）：~90秒
- 并行数据获取（4线程并行 API）：~20秒
- 数据层节省：~70%

### 当 quick_analyze.py 不可用时的手动并行数据获取流程（2026-05-26 新增）

当 `quick_analyze.py` 因缺少 `pandas` 或其他依赖无法运行时，采用以下手动并行化流程，确保多股票分析不被阻断：

1. **统一大盘**（一次）
   - 腾讯 API `curl -s "http://qt.gtimg.cn/q=sh000001,sz399001,sz399006"`
   - 两市成交额从字段37汇总或本地 `moneyflow_data/market`

2. **并行获取个股实时快照**（多只股票一起）
   - 每只股票同时运行：`curl -s "http://qt.gtimg.cn/q=sz{ticker}"`
   - 或批量获取：`curl -s "http://qt.gtimg.cn/q=sz000555,sh603305"`
   - 字段解析后得到：最新价、涨跌幅、成交额、换手率、开盘、最高、最低
   - **优先使用东财 push2 API**（更丰富）：`curl -s "https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=f43,f44,f45,f46,f47,f48,f50,f51,f57,f58,f60,f170"`

3. **并行获取本地数据**（pyarrow 直读 parquet）
   ```python
   import pyarrow.parquet as pq
   import concurrent.futures

   def load_stock_data(symbol):
       daily = pq.read_table(f'{root}/daily/{symbol}.parquet').to_pandas()
       factor = pq.read_table(f'{root}/stk_factor_pro/{symbol}.parquet').to_pandas()
       money = pq.read_table(f'{root}/moneyflow_data/individual/ths/{symbol}.parquet').to_pandas()
       return {'daily': daily, 'factor': factor, 'money': money}

   with concurrent.futures.ThreadPoolExecutor() as ex:
       results = list(ex.map(load_stock_data, ['000555.SZ', '603305.SH']))
   ```

4. **并行获取对标股行情**（批量）
   - 批量腾讯 API：`curl -s "http://qt.gtimg.cn/q=sz{ticker1},sz{ticker2},sz{ticker3}"`

5. **逐只生成报告**（串行，但基于已有数据，无需额外 IO）
   - 每只股票使用上述获取的结构化数据
   - 通用大盘判断共享给所有股票

6. **结尾统一追问**（一次）
   - `是否需要做T执行版？`
   - `是否已持有其中某些股票？`

### delegate_task 子代理超时降级（2026-05-27 新增）

当使用 `delegate_task` 并行发起多个子代理执行深度分析时，子代理可能因数据读取量大/分析内容多而超时（默认超时阈值 600 秒）。此时不可等待或重试子代理，应立即降级为直接执行模式。

**降级判断信号**：
- `delegate_task` 返回结果中某个/某些子任务状态为 `timeout`、`cancelled` 或空输出
- 子代理耗时接近或超过 600 秒
- 返回报告构建失败（如 `NameError`、`ModuleNotFoundError` 等）

**降级执行步骤**：
1. 立即终止子代理模式，切换到当前会话直接执行
2. 使用 `execute_code` 在单个 Python 脚本中完成全部数据读取（不可拆分多个 `execute_code` 调用——每次调用是独立环境，变量不保留）
3. 数据源组合：
   - 本地 parquet（`daily/`、`stk_factor_pro/`、`moneyflow_data/`、`cyq_chips/`、`stk_auction_c/` 等）
   - 东财 push2 API 或腾讯 API 实时行情
4. 在同一脚本内完成数据聚合、报告模板填充和文件写入
5. 报告中必须注明：`因子代理分析超时，已降级为直接执行模式，数据来源为本地 parquet + 实时 API`

**关键原则**：
- `execute_code` 每次调用是独立 Python 进程，变量不会在调用间保留
- 若需要多步骤，必须在单个 `execute_code` 脚本内完成，或通过文件中转保存结果
- 直接执行模式下仍应保持多股票并行分析——在 `execute_code` 内用 `ThreadPoolExecutor` 并行读取多只股票数据

### 穿越性调用约定

当 `quick_analyze.py` 未能正常集成新闻数据（已知 issue）时，多股票场景下的消息面获取采用以下降级策略：
1. 每只股票单独调用东方财富公告 API（`datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_FCI_PERFORMANCEE...）
2. 若 API 返回空/异常，降级至浏览器搜索 `so.eastmoney.com/web/s?keyword={股票名}+公告`
3. 三只股票的消息面获取可以并行化（同时运行多个 curl）

