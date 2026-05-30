## 消息面协作约定

当分析步骤进入消息面时，默认按下面边界执行：

1. **消息获取优先走 TrendRadar MCP（全量拉取 + 本地过滤模式），0 命中时自动 browser fallback**
   - `parallel/agents.py` 中的 `run_news_agent()` 通过 subprocess 调用 `trendradar-mcp` CLI
   - **Step 1**: 拉取当日全量热榜 `get_latest_news(limit=500)` 和 全量 RSS `get_latest_rss(limit=500, days=3)`
   - **Step 2**: 本地 `_filter_items_for_stock()` 两阶段筛选：
     - **精确匹配**：标题+摘要中匹配 `stock_name`（不再使用 `pure_symbol`，避免数字误匹配）
     - **宽松匹配**（精确结果 < 3 条时触发）：加载个股所属行业，叠加行业关键词（33 个行业映射）进行二次匹配，同时检索 RSS 摘要字段
     - 匹配项标记 `_match_type`：`exact`（直接提及）或 `industry_context`（行业上下文）
   - **Step 3**: 当精确匹配 + 宽松匹配总数为 0 时，自动触发 `_fetch_browser_news_fallback()`，调用 `market-news-intelligence/scripts/fetch_browser_news.py` 抓取补充新闻
   - **Step 4**: `_trendradar_to_news_sentiment()` 归一化处理，exact 匹配项（含 browser fallback）情感权重为 industry_context 的 2 倍
   - 不再读取 SQLite 消息库
   - 具体实现详见 `references/trendradar-mcp-integration-pattern.md`（v3 两阶段过滤 + browser fallback 模式）和 `references/news-agent-optimization-pattern.md`（v4 无代码匹配 + browser fallback 模式）
2. 若已有 `news_sentiment` 或 `narrative_context`，直接消费
3. 本技能只负责把消息结果并入交易判断
   - 判断是核心驱动、辅助驱动还是噪音
   - 判断个股是核心受益、前排映射还是纯跟风
  - 归一化处理：`parallel/agents.py` 内 `_trendradar_to_news_sentiment()`
  - **返回格式兼容性陷阱：** TrendRadar 各接口返回结构不一致，`_trendradar_to_news_sentiment()` 必须同时兼容以下三种结构：
    - `search_news`: `{"results": [...]}`
    - `get_latest_news`: `{"data": [...]}` 或 `{"news": [...]}`
    - `get_latest_rss`: `{"data": {"rss_data": [...]}}` 或 `{"items": [...]}`
    - 实际代码应通过多路回落提取实际数据数组，而非假设固定键名

#### narrative_context 不可用时的降级规则

若 `narrative_context` 状态为 `unavailable`（缺失结构化归一化结果），但 `news_sentiment` 可用：
1. 不得因 `narrative_context` 缺失而将消息面整体标记为 `missing`
2. 降级消费 `news_sentiment` 产出的 `direction`、`level`、`freshness`、`credibility` 和 `main_sources`
3. 在报告中必须写明 `结构化归一化结果缺失，降级为新闻情感分析`
4. 交易结论中，消息面权重从 `中高'降为 `中低'，作为辅助参考

若 `news_sentiment` 和 `narrative_context` 同时缺失：
1. 必须明确写出 `消息面两级缺失（news_sentiment + narrative_context）`
2. 交易结论中消息面不得分，也不得扣分
3. 如果股价走势明显强于大盘/板块，应标注 `更像纯资金行为，消息面缺失不改变这一判断`

**消息面获取渠道（TrendRadar MCP v3 全量拉取 + 本地过滤 + Browser Fallback 模式）**

| 数据类型 | 优先渠道 | 降级渠道 | 关键脚本 |
|---|---|---|---|
| 消息/news | **TrendRadar MCP** `get_latest_news` + `get_latest_rss` + 本地两阶段过滤 | `fetch_browser_news.py` (market-news-intelligence) 当两阶段均为 0 条时 | `parallel/agents.py` 内 `_call_trendradar_mcp()` + `_fetch_browser_news_fallback()` |

#### 用户提供外部公告链接时的处理（2026-05-28 新增）

当分析过程中用户主动提供外部公告链接（如10jqka公告页、东财公告、巨潮公告）时，说明当前消息面管线遗漏了重要公告。处理规则：

1. **立即通过浏览器读取公告全文**（同花顺公告页为 Vue SPA，必须用 `browser_navigate` + `browser_snapshot`，curl 无法获取）
2. **判断公告类型**：
   - 公司治理变动（董事长/总经理辞职+新任命）→ 按 `references/corporate-governance-change-analysis.md` 框架执行
   - 业绩/重组/回购等 → 按标准消息面框架处理
3. **在原分析基础上追加补充分析章节**，不要推翻原分析，而是标注修正
4. **修正次日预期概率分布**，标注原概率和修正后概率
5. **说明遗漏原因**：如同日新发公告尚未进入TrendRadar管线、盘前分析时公告未发布等

#### 从 quick_analyze 数据生成标准盘后报告（2026-05-26 新增）

当 `build_stock_report.py` 的默认输出格式不满足需求时（如需要标准表格、明日推演等），可基于 `quick_analyze.py` JSON 数据生成符合技能规范的盘后深度分析报告。

关键认知：
- **没有 `full_analyze.py`**。技能目录下只有 `quick_analyze.py` 和 `build_stock_report.py` 两个主要入口。禁止尝试调用 `full_analyze.py`。
- `quick_analyze.py` 输出第一行是标题 `[快速分析] XXX @ YYYY-MM-DD`，不是纯 JSON。解析时必须跳过第一行。
- **minute_intent[].volume 为累计值，非区间值**：`quick_analyze.py` 的 `minute_intent` 中 `volume` 字段表示**累计到该时段的成交量**，不是该时段内的区间成交量。报告中必须标注 `(累计)`，禁止直接当作区间量能使用。
- 生成报告时常见 Python 陷阱：f-string 内部嵌套引号冲突、对字符串使用数字格式化、中文文本 typo（如"姓续"→"继续"）。
- 已提供自动化脚本：`scripts/generate_postmarket_report.py` — 直接读取 quick_analyze JSON 输出标准 Markdown 报告。
- 详见 `references/from-quick-analyze-to-postmarket-report.md`（含完整脚本模板和陷阱清单）。

默认输入来源：

- **统一消息入口（新）：**`trendradar-mcp` 通过 `parallel/agents.py` 内置的 `_call_trendradar_mcp()` 调用，0 命中时自动 fallback 到 `_fetch_browser_news_fallback()`
  - CLI wrapper：`/Users/penghongming/agent-skills/custom/trendradar-mcp/scripts/trendradar_mcp_cli.py`
  - venv Python：`/Users/penghongming/Documents/TrendRadar/.venv/bin/python`（禁止使用系统 python3）
  - 调用工具：`get_latest_news` + `get_latest_rss` + 本地 `_filter_items_for_stock()` 两阶段过滤 + Browser Fallback
  - 归一化处理：`parallel/agents.py` 内 `_trendradar_to_news_sentiment()`
- 下游接入：
  - `/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/score_next_day_bias.py --news-json ...`
  - `/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/build_stock_report.py --news-json ...`

