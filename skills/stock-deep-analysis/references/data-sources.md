## 数据源

### 实时行情API（腾讯行情）
- **股票**: `sz000725` (深圳), `sh600xxx` (上海)
- **指数**: `sh000001` (上证), `sz399001` (深成指), `sz399006` (创业板)
- **URL**: `https://qt.gtimg.cn/q=<code>&r=0.<random>`
- **注意**: 东方财富 push2 API 在代理环境下可能返回空，优先用腾讯行情

本技能为 **tushare_pro** skill 的下游消费者，数据获取与维护全部交由 tushare_pro 负责。`stock-deep-analysis` 不重复实现数据下载或同步逻辑，只做两件事：

1. **读取** 本地 parquet 文件（通过 `scripts/data/data_access.py` 提供的统一读取函数）
2. **缺失时回填** 通过直接调用 tushare_pro 的 Tushare API 客户端获取，并缓存到本地 parquet

**代码层对接：** `data_access.py` 已通过 `sys.path` 动态注入 tushare_pro 路径，直接复用其 `utils.tushare_client.create_pro_api()` 客户端。当本地 `daily` 缺失时，优先走 Tushare API 回填，失败后才降级到腾讯补抓。

**读取接口：**`scripts/data/data_access.py` 提供一组 parquet-only 的统一读取函数，内部自动处理不同的存储结构。

**parquet 存储结构：**

| 数据类型 | 目录结构 | 文件名示例 | 说明 |
|----------|----------|------|------|
| 日线历史 | `daily/` | `000001.SZ.parquet` | 按股票分区，扁平 |
| 日线基础 | `daily_basic/` | `000001.SZ.parquet` | 按股票分区，扁平 |
| 周线 | `weekly/` | `weekly_000001.SZ.parquet` | 按股票分区，带 prefix |
| 月线 | `monthly/` | `monthly_000001.SZ.parquet` | 按股票分区，带 prefix |
| 龙虎榜明细 | `top_list/` | `2026.parquet` | 按年份全市场表 |
| 龙虎榜机构 | `top_inst/` | `2026.parquet` | 按年份全市场表 |
| 资金流向 | `moneyflow_data/individual/ths/` | `000001.SZ.parquet` | 按股票分区，ths 格式 |
| 筹码分布 | `cyq_chips/` | `000001.SZ.parquet` | 按股票分区，扁平 | **已废弃**：上游数据质量普遍无效（percent全为0.01，price为占位值），不再用于分析 |
| 筹码胜率 | `cyq_perf/` | `000001.SZ.parquet` | 按股票分区，扁平 | **主数据源**：winner_rate/weight_avg/cost_*pct 可支撑完整筹码分析 |
| 技术因子 | `stk_factor_pro/` | `000001.SZ.parquet` | 按股票分区，扁平，261 字段 |
| 融资融券 | `margin/` | `000001.SZ.parquet` | 按股票分区，扁平 |
| 融资融券明细 | `margin_detail/` | `000001.SZ.parquet` | 按股票分区，扁平 |
| 涨停列表 | `limit_list_d/` | `limit_list_d.parquet` | 单一文件，全市场 |
| 贸易日历 | `trade_cal/` | `trade_days.parquet` | parquet 主格式，CSV 仅作备份 |
| 题材概念 | `theme_data/dc_concept/` | `dc_concept_*.parquet` | parquet 主格式，CSV 仅作备份 |
| 题材成分 | `theme_data/dc_concept_cons/` | `{stock_name}.parquet` | parquet 或 CSV 混合 |
| 大盘指数 | `指数数据/index_daily/` | `{index_code}.parquet` | 按指数分区，存放在独立的 `指数数据/` 目录下，不在股票数据根目录 |
| 分钟线 | `分钟数据/YYYY/MM/DD/` | `{code}.{EXCHANGE}/1m.csv` | 仍为 CSV，分粒度单独文件 |
| 集合竞价 | `stk_auction_c/` / `stk_auction_o/` | `000001.SZ.parquet` | 按股票分区，扁平 |

**数据获取优先级策略：**

| 级别 | 数据类型 | 策略 | 备注 |
|---|---|---|---|
| **本地only** | 龙虎榜(top_list/top_inst) | 只用本地，禁止浏览器补抓 | 本地无记录=当日未上榜 |
| | 筹码胜率(cyq_perf) | 只用本地 | cyq_chips 已废弃；cyq_perf 滞后1-3日属正常，标 stale 降权使用 |
| | 历史日线/weekly/monthly (T-1及以前) | 只用本地 | 不浏览器补 |
| | 融资融券(margin_detail) | 只用本地 | 当前严重滞后(T-16)，需修复同步脚本 |
| **本地优先，滞后/缺失时 tushare_pro API 补全** | 当日日线(daily T日) | 本地优先；未更新则走 tushare_pro API 回填，再失败才降级腾讯API | 禁止用T-1数据冒充T日 |
| | 大盘指数(index_daily) | 本地优先，滞后用腾讯API | 必须补全 |
| | 板块/概念(theme_data) | 本地优先 | |
| | 当日分钟线(午间/盘后) | 先本地检查，缺失/过期则降级 | 盘中直接走浏览器/API |
| **浏览器/API优先** | 实时行情/分钟线(盘中) | 直接走浏览器/API | 当日以浏览器为准 |
| | 竞价(auction) | 浏览器/API优先 | |
| 消息/news | TrendRadar MCP 全量拉取+本地过滤；0命中时 fallback 到 `fetch_browser_news.py` | `parallel/agents.py` 内 `_call_trendradar_mcp()` + `_fetch_browser_news_fallback()` |

**关键区分：**
- `top_list`：龙虎榜明细，含买入/卖出金额、净买入、上榜原因
- `limit_list_d` / `limit_list_ths`：涨停/异动列表，**不含龙虎榜买卖金额**，禁止互相替代

**实时补充接口状态（2026-05-27 更新）：**

| 接口 | 状态 | 备注 |
|------|------|------|
| Tushare `rt_k` 实时日线 | 可用 | 需有效 Token |
| Tushare `rt_min` 实时分钟 | 可用 | 需有效 Token |
| Tushare `stk_auction_c` 集合竞价 | 可用 | 需有效 Token |
| 腾讯财经快照 API | 可用 | `qt.gtimg.cn`，实时行情/成交额 |
| 腾讯资金流向 API | 部分可用 | 行情快照内嵌流入/流出可用，专用个股接口已下线 |
| **东方财富 push2 实时行情** | **可用** | `push2.eastmoney.com/api/qt/stock/get`，含量比/换手率/振幅/市值等丰富字段 |
| 同花顺 MCP | 不可用 | 连接失败 |

#### 东方财富 push2 实时行情 API（2026-05-27 新增）

当需要获取实时行情快照（含量比、换手率、振幅、总市值等丰富字段）时，可使用东方财富 push2 API：

```bash
curl -s "https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=..."
```

**secid 格式**：
- 沪市：`1.{code}`（如 `1.603305`）
- 深市：`0.{code}`（如 `0.000555`）

**常用 fields 字段**（逗号分隔，按需组合）：
- `f43` = 最新价（需 ÷100）
- `f44` = 最高价（需 ÷100）
- `f45` = 最低价（需 ÷100）
- `f46` = 开盘价（需 ÷100）
- `f47` = 成交量（股）
- `f48` = 成交额（元）
- `f50` = 量比
- `f51` = 振幅（%）
- `f57` = 股票代码
- `f58` = 股票名称
- `f60` = 昨收（需 ÷100）
- `f170` = 涨跌幅（%）

**响应格式**：JSON，`data` 字段包含实际数据。价格类字段需除以 100。

**优势**：
- 字段丰富：量比、振幅、换手率、总市值、流通市值、PE、PB 等一次返回
- 稳定性高：在 `NO_PROXY="*"` 环境下仍通常可用（ unlike 部分 datacenter API）
- 速度快：响应通常 < 500ms

**降级链中的位置**：
- 实时行情优先：东财 push2 > 腾讯 `qt.gtimg.cn`
- 当东财 push2 返回空/异常时，降级至腾讯 API
- 当两者均失败时，降级至浏览器导航个股页面

## 集成缺陷诊断：功能声明与代码实现交叉验证
- `sync_to_sqlite.py` `_clean_date()`: 原仅支持 `YYYYMMDD`，导致 `stk_nineturn` 等数据源（`YYYY-MM-DD`）同步 0 行。已扩展支持 `YYYY-MM-DD` 及 `YYYY-MM-DD HH:MM:SS`。
- `sync_to_sqlite.py` `TABLE_SPECS`: 新增 `date_column` 字段覆盖（默认 `trade_date`，可改为 `ann_date`），支持 `repurchase`/`pledge_detail`/`top10_holders`/`top10_floatholders` 等表。
- `sync_to_sqlite.py` 新增 `file_mode: "static"`（`stock_basic` 使用），无日期分区数据做 full-replace 而非增量窗口。
- SQLite 新增表并完成全量同步：`monthly`(346K)、`weekly`(898K)、`stk_weekly_monthly`(533K)、`stk_nineturn`(1.33M)、`top10_holders`(1.27M)、`top10_floatholders`(2.15M)、`share_float`(962K)、`margin_detail`(243K)、`pledge_detail`(16K)、`pledge_stat`(709K)、`limit_list_ths`(44K)、`limit_step`(4.5K)、`repurchase`(20)、`stock_basic`(21K)。总计约 900 万行。

**通用适配模式**（双结构兼容读取器）：
当一个数据类型可能以两种形式存储时，读取逻辑应该使用 `load_yearly_or_flat_rows(root_dir, filename)` 而非硬编码路径：
|- 先检查 `root_dir / filename` （扁平结构）
|- 若不存在，再递归扫描 `root_dir / YYYY / filename` （年份分区）
|- 这样无论数据库管理员将数据迁移到年份子目录还是保持扁平，代码都能自动适配


## 集成缺陷诊断：功能声明与代码实现交叉验证

### 问题模式
脚本在 CLI 参数或文档中声称支持某项功能（如 `--no-browser` 跳过消息面获取），但代码中**没有任何实际调用链路**读取或处理对应数据。这是一种比"路径错误"更隐蔽的缺陷——数据可能已存在，但主流程完全不用。

### 诊断方法
1. **检查 CLI 参数/文档声明**：搜索 `--news`、`--no-browser`、`消息面` 等关键词，确认功能是否在用户接口层被宣传
2. **追踪调用链路**：从入口函数（如 `build_payload`、`quick_analyze`）出发，向下追踪是否有函数实际调用新闻读取/处理逻辑
3. **检查输出字段**：确认最终输出 JSON/Markdown 中是否包含 `news_sentiment`、`narrative_context`、`browser_news` 等字段
4. **检查数据消费点**：确认其他模块（如 `score_next_day_bias.py`）是否独立实现了该功能但未被主流程调用

### 实战案例（2026-04-25）：quick_analyze.py 消息面未集成

**症状**：`quick_analyze.py --symbol 600103.SH` 的 `data_status` 中消息面始终缺失，但磁盘上存在 `market-news-intelligence` 已抓取的归一化新闻数据。

**根因**：
1. `quick_analyze.py` 有 `--no-browser` 参数（暗示支持消息面），但**代码中没有任何地方调用新闻读取函数**
2. `prepare_news_context.py`、`run_news_pipeline.py` 等脚本只是**代理/转发脚本**，把调用转发到 `market-news-intelligence` skill，但 `quick_analyze.py` 从未调用它们
3. `score_next_day_bias.py` 从 `market-news-intelligence` 的 `news_context.py` 导入了 `load_news_payload`，但它是**独立脚本**，不是 `quick_analyze.py` 调用的子模块
4. 本地新闻数据路径：`~/quant-data/tushare/消息面数据/raw/news_pipeline/{YYYY}/{MM}/{DD}/news_pipeline_{symbol}_{YYYY-MM-DD}.json`

**数据实际存在示例**（2026-04-24，600103.SH）：
- `news_sentiment`: 个股级、旧消息重炒、公告实锤、硬催化
- `narrative_context`: hard_catalyst=True, core_stock=False, theme_active=False
- 3篇文章：龙虎榜数据(04-22, 04-23) + 异常波动公告(04-21)

**处理建议**：
1. 修改 `quick_analyze.py` 在生成输出 JSON 之前，按日期和股票代码查找本地 `news_pipeline` 文件
2. 通过 `load_news_payload` + `normalize_news_sentiment` + `narrative_context_from_news` 处理
3. 将结果注入输出 JSON 的 `news_sentiment` 和 `narrative_context` 字段

**教训**：盘点数据时，不仅要看"文件是否存在"，还要验证"主流程是否真的在用"。


### 今日数据获取默认规则

根据数据类型与分析时段，采用不同的获取策略，禁止一刀切地"浏览器优先"或"本地优先"：

#### 当日日线（T日历史日线）

- **T日本地已更新**：直接使用本地 `daily/` 数据，不需浏览器补抓。
- **T日本地未更新**（分析时本地日线最新日期 < T）：必须通过浏览器/API（如腾讯 `qt.gtimg.cn`）获取当日日K数据，不得用T-1日数据冒充当日。
- **盘中分析时的特殊情况**：若当前时间为盘中（如 10:30），本地 `daily` 最新日期为 T-1 是**正常状态**，不应标记为 `stale`。因为 T 日尚未收盘，本地日线自然只更新到 T-1。此时当日行情应通过实时快照获取，而非要求本地 daily 有 T 日数据。
- 判断方法：先检查本地 `daily/{code}.parquet` 最新 `trade_date`，若等于分析日则用本地，否则走浏览器/API。

### 分钟线

- **盘中（09:30-11:30 / 13:00-15:00）**：直接走浏览器/API（东财API或腾讯API）获取实时分钟线，不检查本地。
- **午间休盘（11:30-13:00）**：先检查本地 `分钟数据/YYYY/MM/DD/{code}.EXCHANGE/1m.csv`，若本地存在且日期匹配则用本地；若本地缺失/过期，降级到浏览器/API补抓上午分时。
- **盘后（>15:00）**：先检查本地分钟线是否已更新至T日，已更新则用本地；未更新则走浏览器/API补抓全天分时。
- **盘前（<09:30）**：分钟线分析对象为前一交易日（T-1），优先使用本地历史分钟线。

### 其他实时数据

- 实时行情、当日竞价、新闻：浏览器/API优先，本地仅作降级。

### 超时与失败处理

| 场景 | 超时 | 失败处理 |
|---|---|---|
| Hermes 浏览器抓取 | 60秒 | 重试1次；仍失败则标记 `网络渠道不可用` 并回落 |
| Tushare API | 30秒 | 记录错误并降级该维度 |
| 本地文件读取 | 5秒 | 标记 `missing`，不得伪装成功 |

触发降级时必须在结论区追加 `数据获取降级说明`。

### 数据渠道与脚本速查

| 数据类型 | 优先渠道 | 降级渠道 | 关键脚本 |
|---|---|---|---|
| 实时行情 | 浏览器 | 腾讯API | `hermes_browser_fetch.py`, `get_quote_tencent.py` |
|| 当日日线 | 本地 `daily/` (若T日已更新) | tushare_pro API 回填 → 腾讯API (若T日未更新) | `data_access.py` 内部回填 |
| 当日分钟线 | 时段分策：盘中浏览器/API，午间/盘后先本地后浏览器 | 本地 `minute_kline.csv` | `fetch_minute_data.py`, `fetch_eastmoney_minute_kline.mjs` |
| 历史分钟线 | 本地 `minute_kline.csv` | `minute_kline_5m/15m/30m/60m.csv` | `fetch_eastmoney_historical_intraday.py` |
| 开盘竞价 | `stk_auction_o` | `fetch_open_auction_eastmoney.py` | `fetch_open_auction.py`, `fetch_eastmoney_auction.py` [→auction-analysis] |
| 收盘竞价 | `stk_auction_c` | `fetch_close_auction_eastmoney.py` | `fetch_close_auction.py`, `fetch_eastmoney_auction.py` [→auction-analysis] |
| 竞价摘要 | `summarize_auction_strength.py` [→auction-analysis] | — | `summarize_auction_strength.py` [→auction-analysis] |
| 午间强度 | `score_intraday_strength.py` | — | `score_intraday_strength.py` |

- 所有脚本位于 `scripts/` 目录下
- 竞价相关脚本已迁移至独立 skill `auction-analysis`（`~/agent-skills/custom/auction-analysis/`），父 skill 通过 subprocess shim（`signals/core/analyze_auction_intent_skill.py`）调用。data_access.py 中竞价数据采集也改为从 auction-analysis 路径导入。
- 竞价数据缺失时必须在报告中明确写出，不得用开盘价替代
- 历史分钟线回退时必须明确写出实际使用的粒度

#### fetch_browser_news.py 空输出失败（2026-05-26 新增）

`scripts/fetch_browser_news.py` 在部分环境中可能返回 exit code 2 且 stdout 完全为空，即使该股票存在大量新闻。这与"空壳脚本"不同，是运行失败。

**识别方法**：
- 脚本运行后 stdout 为空或仅含换行
- exit code 为 2（通常表示解析错误或未捕获内容）
- 重复运行结果相同

**处理**：
1. 第一次运行后若输出为空，立即在报告中标注 `fetch_browser_news.py 运行失败（exit 2 / 空输出），已降级处理`
2. 立即降级至浏览器导航同花顺 F10 页面：`basic.10jqka.com.cn/{code}/news.html`
3. 使用 `browser_vision` 提取新闻内容，提示词示例：`"从这个页面中提取所有新闻标题、日期和来源"`
4. 禁止在没有尝试浏览器降级之前，直接将消息面标记为 `missing`

#### 批量腾讯 API 查询板块对标股（2026-05-26 新增）

获取板块内多只对标股行情时，使用腾讯 API 的批量查询模式，一次返回多只股票的实时快照：

```bash
curl -s "http://qt.gtimg.cn/q=sz000725,sz000100,sz000536,sh600707" | iconv -f gb2312 -t utf-8
```

**解析规则**：
- 返回格式：分号分隔各股的字符串，每个字符串内逗号分隔字段
- 典型字段：3=最新价、4=昨收、32=涨跌幅(%)、37=成交额(万元)
- 示例解析代码参见 `references/session-notes-2026-05-26-multi-stock-analysis.md`

**优势**：
- 减少 HTTP 请求次数，三只股票一次请求即可
- 与单股查询比，延迟更低，更适合板块联动分析

#### 关于 fetch_minute_data.py 的注意事项（2026-05-26 新增）

`scripts/fetch_minute_data.py` 的 CLI 参数名为 `--trade-date`，而非 `--date`。错误用法会导致脚本返回 `exit 2`：

```bash
# 正确用法
python3 scripts/fetch_minute_data.py --symbol 000555 --trade-date 2026-05-26

# 错误用法（会报错）
python3 scripts/fetch_minute_data.py --symbol 000555 --date 2026-05-26
```

若脚本执行失败，常见原因包括：
1. **参数名错误**：使用了 `--date` 而非 `--trade-date`
2. **东方财富 API 空响应**：在 `NO_PROXY="*"` 环境下，API 返回 HTTP 200 但 body 为空
3. **腾讯 fallback 也失败**：腾讯 API 返回的分钟线点数较少（通常不足 240 条）

**处理**：
- 首先确认参数名是 `--trade-date`
- 若东财 API 空响应，降级至直接 `curl` 腾讯分钟线 API 或标注 `missing`
- 不要因为分钟线不完整而取消整体分析，降级为"基于实时快照推断分时结构"即可


## 已知数据问题与约束

### stk_factor_pro 数据结构冲突（2026-04-25 发现）

**问题描述**：
`股票数据/stk_factor_pro/` 目录下同时存在两种不同格式的文件：

1. **扁平旧文件**：`股票数据/stk_factor_pro/stk_factor_pro_{code}.csv`
   - 包含全历史合并数据（约6000+行）
   - **字段不完整**：缺少 `kdj_k_bfq`、`macd_bfq`、`boll_*_bfq`、`cci_bfq` 等关键技术指标字段
   - 仅包含 `rsi_bfq_6`、`ma_bfq_*` 等基础字段

2. **年份新文件**：`股票数据/stk_factor_pro/2026/stk_factor_pro_{code}.csv`
   - 仅2026年分区数据（约72行）
   - **字段完整**：包含全部261个字段（含 kdj、macd、boll、cci 等）
   - 但最新日期可能比扁平文件少一天

**根因**：
`data_access.py` 的 `load_yearly_or_flat_rows()` 优先检查扁平根目录下的文件，若存在则直接返回，**完全不会读取年份子目录下的文件**。

**影响**：
运行 `quick_analyze.py` 时，`fetch_local_factors()` 返回的 `kdj_k`、`macd`、`boll`、`cci` 等字段全部为 `N/A`，即使本地实际上存在字段完整的年份文件。

**状态**：✅ 已修复（2026-05-26）。通过统一 parquet-only 改造，所有读取逻辑改为直接读取 parquet，彻底解决了 CSV 与 parquet 并存导致的字段缺失问题。详情参见 `references/parquet-only-migration-2026-05-26.md`。

**原修复建议**（仅作为历史记录）：
- 方案A（推荐）：修改 `load_yearly_or_flat_rows()`，当扁平文件与年份文件同时存在时，优先读取年份子目录下的最新文件，并与扁平文件合并/补齐
- 方案B：删除扁平根目录下的旧格式文件，强制走年份分区读取
- 方案C：在 `fetch_local_factors()` 中显式指定年份子目录路径，绕过 `load_yearly_or_flat_rows()` 的自动探测

### `_resolve_symbol()` 名称→代码解析（2026-04-29 新增）

**问题描述**：
`build_payload()` 原先只接受股票代码（如 `002217.SZ`），不识别中文名称（如"合力泰"）。agent 调用时若凭记忆传递中文名称，会使用错误代码（如将合力泰的 `002217.SZ` 误传为 `600103.SH` 青山纸业），产生完全错误的分析报告。

**修复**：
在 `scripts/build_stock_report.py` 的 `build_payload()` 入口增加 `_resolve_symbol()` 函数。该函数通过读取 `stock_basic_all.csv` 自动将中文名称解析为 ts_code：

```python
def _resolve_symbol(symbol: str) -> str:
    # 如果已是代码格式（6位纯数字或带.SH/.SZ后缀），直接返回
    if re.match(r'^\d{6}(\.\w+)?$', symbol.strip()):
        return symbol
    # 否则查询stock_basic_all.csv做名称匹配
    csv_path = STOCK_DATA_ROOT / "stock_basic" / "stock_basic_all.csv"
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["name"] == symbol:
                return row["ts_code"]
    # 精确匹配不到，尝试子串匹配（带警告）
    ...
```

**注意**：
- `stock_basic_all.csv` 路径在 `股票数据/stock_basic/` 下，由 `STOCK_DATA_ROOT` 决定
- 编码必须是 `utf-8-sig`（含 BOM 头）
- 精确匹配优先，子串匹配作为 fallback 并打印警告
- 任何其他 agent 在调用 `build_payload()` 时，可以直接传中文名称，不必手动解析代码

### `load_daily_row()` parquet 优先导致最新日数据不可读 + 浏览器补抓写入管道不连通（2026-04-29 发现）

**问题现象**：彩虹股份（600707.SH）4/29 报告缺少当日数据，而同日同管道的合力泰（002217.SZ）报告数据完整。

**根因分析（两层bug叠加）**：

**第一层 - parquet 优先读取**：
`load_daily_row()` 的 fallback 链路为：SQLite → `_resolve_yearly_path(传.csv)` → 第260行优先返回 `.parquet` 文件。parquet 比 CSV 旧时（parquet 到 20260428，CSV 到 20260429），CSV 的最新行永远被忽略。

**第二层 - 浏览器补抓写入管道不连通**：
`sync_latest_daily_kline_via_browser()`（kline_sync 并行 agent 调用）是应对 SQLite 缺失的 HTTP/浏览器 fallback。但它写入的是 CSV（`_upsert_daily_row` 第463-492行），而 `load_daily_row()` 的 fallback 路径读的是 parquet。两套管道彼此不通：

```
kline_sync agent → sync_latest_daily_kline_via_browser()
    → _upsert_daily_row() → 写 CSV ✅
load_daily_row() fallback path → _resolve_yearly_path()
    → 读 parquet ❌  (永远看不到 kline_sync 写入的 CSV)
```

**更深的循环问题**：
`_upsert_daily_row()` 内部先调用 `_read_all_yearly_rows()`（第479行，同样 parquet 优先读取旧数据），然后 `_write_all_yearly_csv_rows()` 写回 CSV。这导致：即使浏览器补抓成功，`_upsert_daily_row` 读到的 parquet 旧数据 + 新写入的浏览器数据 一起覆盖 CSV，但 parquet 本身永不更新，下次 `load_daily_row` fallback 仍然读 parquet，仍然看不到最新数据。

**为何合力泰不受影响**：
合力泰的 SQLite 中已有 20260429 行，`load_daily_row` 的第一层（第168行 `_query_one`）直接短路返回，根本不会走到 fallback 路径。彩虹的 SQLite 漏掉了 4/29，fallback 后才暴露出两层 bug。

**影响范围**：
- SQLite 同步有遗漏的股票都会受此影响（数据仓库同步并非100%覆盖）
- `current_price` → `None`，`signal_score` → `None`，当日分析失效
- 浏览器补抓机制形同虚设：补了也白补，因为读取管道不通

**状态**：✅ 已修复（2026-05-26）。通过统一 parquet-only 改造，所有读取逻辑统一使用 parquet，彻底解决了 parquet 与 CSV 新旧不一致导致的数据不可读问题。详情参见 `references/parquet-only-migration-2026-05-26.md`。

**原修复方向**（仅作为历史记录）：
1. 方案A（推荐 - 修读取）：`_resolve_yearly_path()` 比较 parquet 和 CSV 的修改时间或最新日期，返回更新较新的格式
2. 方案B（修写入）：`_upsert_daily_row()` 写入后，也更新 parquet（`df.to_parquet()`）或 SQLite（`upsert_rows`），确保 `_query_one` 层能命中
3. 方案C（修读取+写入双向）：方案A + 浏览器补抓成功后主动刷新 SQLite
4. 注意：parquet 和 CSV 谁更新取决于数据导入脚本的执行顺序，不能假设 parquet 一定是最新的

#### 财务 API 数据质量异常（2026-05-25 新增）

东方财富财务数据 API（`datacenter-web.eastmoney.com/api/data/v1/get`）在实战中暴露多个数据质量问题，具体详情参见 `references/data-inventory.md` 第 11-14 项：

1. **"同比"字段内容异常**：API 返回的 `同比` 字段实际包含的是上年同期绝对金额，而非同比增长率百分比。必须手动计算同比增长率，并与新闻/公告渠道交叉验证。
2. **融资融券 API 排序列名不一致**：实际有效排序列为 `HOLD_DATE`，而非文档中的 `TRADE_DATE`。
3. **季度报告行缺失日期标识**：部分行缺少 `REPORT_DATE`。季度报告数据不宜作为权威来源，优先用浏览器获取东方财富个股页面/巨潮资讯网公告。
4. **浏览器新闻搜索可作交叉验证渠道**：当 API 数据异常时，`so.eastmoney.com/web/s?keyword={股票名}+业绩` 可提供已标注报告期的财务摘要。

#### ~~cyq_chips 筹码数据异常识别~~（2026-05-26 新增，2026-05-29 废弃）

**已废弃**：`cyq_chips` 上游数据质量普遍无效（所有 percent=0.01，price 为占位值），不再用于分析。筹码分析已全面切换至 `cyq_perf`（详见 Step 8 筹码数据源说明）。

#### 分钟线 VWAP 计算单位陷阱（2026-05-26 新增）

分钟线 CSV 中的 `volume` 字段单位为**手**（1 手 = 100 股），而 `amount` 字段单位为**元**。计算 VWAP 时必须将 volume 转换为股数，否则结果会偏离 100 倍：

```python
# 错误（用原始 volume 计算，结果偏差 100 倍）
vwap_wrong = cum_amount / cum_volume  # 错误

# 正确（volume 转换为股数）
vwap_correct = cum_amount / (cum_volume * 100)  # 正确
```

**验证方法**：用当日平均成交价（由行情 API 或日线数据提供）与手算 VWAP 对比，二者应该接近。若手算 VWAP 是平均成交价的 100 倍或 1/100，则确认存在单位转换问题。

**通用规则**：
- 所有从分钟线 CSV 读取的 `volume` 字段，默认单位为手，需累计成交额时必须 `* 100`
- 仅当数据源明确标注为"股"时才可免去转换
- 从 parquet 读取的分钟线数据（如有）应查看 schema 中 volume 字段的单位说明

### EastMoney API 空响应与降级链（2026-05-26 新增）

在 `NO_PROXY="*" HTTPS_PROXY="" HTTP_PROXY="" ALL_PROXY=""` 环境下（Feishu 网关正常工作的必要设置），东方财富 push2 / datacenter API 频繁返回 HTTP 200 但 body 为空 JSON 或解析失败。这不是网络不通，而是代理清空后 API 返回异常。

**降级链（按优先级）：**
1. **板块/对标股**：`discover_ths_mobile_stock_concepts.py` → 若为空壳/失败 → **腾讯行情 API** (`wss://qt.gtimg.cn/q=sh/sz{ticker}`) 获取板块内多股快照，手动识别龙头/中军
2. **当日日 K**：本地 `daily/` 优先 → 未更新则 **腾讯行情 K 线 API** → 仍失败则 **浏览器抓取** 东财个股页面
3. **新闻/公告**：`market-news-intelligence` pipeline → 失败则 **浏览器搜索** `so.eastmoney.com`
4. **财务数据**：**东方财富 datacenter API** `datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_LICO_FN_CPD` → 失败则浏览器补抓
5. **资金流向**：本地 `moneyflow_data/` → 延迟则标注 `stale_1d` → 缺失则 **同花顺浏览器** 或标注 `missing`

**关键原则**：
- 禁止因单个 API 空响应而将整个维度标记为 `missing`
- 必须尝试至少两种独立渠道后才允许降级
- 报告中必须写明 `因 XX API 空响应，已降级为 YY 渠道获取`

#### 关于东方财富 datacenter 财务 API（2026-05-26 新增）

当 Tushare API 不可用或本地财务数据缺失时，可使用东方财富 datacenter API 获取个股基本财务数据：

```bash
curl -s "https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_LICO_FN_CPD&columns=ALL&filter=(SECURITY_CODE%3D%22{ticker}%22)&pageNumber=1&pageSize=500&sortColumns=REPORT_DATE&sortTypes=-1"
```

**返回字段说明**：
- `SECURITY_CODE`: 股票代码
- `REPORT_DATE`: 报告期
- `BASIC_EPS`: 基本每股收益
- `TOTAL_OPERATE_INCOME_SQ`: 营业总收入
- `TOTAL_OPERATE_INCOME_SQ`: 归母净利润
- `MGJYXJJE`: 每股经营现金流量
- `TOTAL_OPERATE_INCOME_QOQ`: 营收环比
- `PARENT_NETPROFIT_QOQ`: 净利润环比

**数据质量注意事项**：
- `同比` 字段可能包含的是上年同期绝对金额，而非同比增长率百分比。必须手动计算同比增长率并与新闻/公告渠道交叉验证。
- 部分行可能缺少 `REPORT_DATE`。季度报告数据不宜作为权威来源，优先用浏览器获取巨潮资讯网公告。

在 `NO_PROXY="*" HTTPS_PROXY="" HTTP_PROXY="" ALL_PROXY=""` 环境下（Feishu 网关正常工作的必要设置），东方财富 push2 / datacenter API 频繁返回 HTTP 200 但 body 为空 JSON 或解析失败。这不是网络不通，而是代理清空后 API 返回异常。

**症状**：
- `curl` 命令本身成功（exit 0），但返回内容为空或 `{}`
- `json.loads()` 抛出 `JSONDecodeError` 或得到空 dict
- 多次重试同一 URL 结果相同（不是瞬时故障）

**降级链（按优先级）**：
1. **板块/对标股**：`discover_ths_mobile_stock_concepts.py` → 若为空壳/失败 → **腾讯行情 API** (`wss://qt.gtimg.cn/q=sh/sz{ticker}`) 获取板块内多股快照，手动识别龙头/中军
2. **当日日 K**：本地 `daily/` 优先 → 未更新则 **腾讯行情 K 线 API** → 仍失败则 **浏览器抓取** 东财个股页面
3. **新闻/公告**：`market-news-intelligence` pipeline → 失败则 **浏览器搜索** `so.eastmoney.com`
4. **资金流向**：本地 `moneyflow_data/` → 延迟则标注 `stale_1d` → 缺失则 **同花顺浏览器** 或标注 `missing`

**关键原则**：
- 禁止因单个 API 空响应而将整个维度标记为 `missing`
- 必须尝试至少两种独立渠道后才允许降级
- 报告中必须写明 `因 XX API 空响应，已降级为 YY 渠道获取`

### discover_ths_mobile_stock_concepts.py 空壳问题（2026-05-26 新增）

`scripts/discover_ths_mobile_stock_concepts.py` 在部分环境中实际为空壳脚本（仅含 import/空函数，无实际业务逻辑）。运行时可能返回 exit 0 但输出为空，或 exit 1。

**识别方法**：
- 脚本运行后 stdout 为空或仅含 `""`
- 无论传入何种 ticker，返回的概念列表始终为空

**处理**：
1. 运行后若输出为空，立即在报告中标注 `discover_ths_mobile_stock_concepts.py 为空壳/无输出，已降级`
2. 降级至腾讯行情 API 获取板块内多股快照，手动推导对标股关系
3. 不要反复重试同一空壳脚本

### quick_analyze.py data_availability 缺失（2026-04-25 发现）

自动生成的 JSON 中 `data_availability` 字段为空 `{}`，未能正确填充各维度状态。需检查 `quick_analyze.py` 主流程中该字段的赋值逻辑。

### 换手率字段异常（2026-04-25 发现）

腾讯API返回的原始换手率字段值为 `229658.0`（缺少百分比单位），而 `daily_basic` 中的 `turnover_rate` 和 `turnover_rate_f` 是正确的百分比格式（21.15%）。推荐在 `get_quote_tencent.py` 中对换手率字段做单位校准。

## 数据源盘点与 SQLite 同步维护（2026-04-27 更新）

### 盘点方法

1. 读取 SKILL.md 确认理论上需要的所有数据类型
2. 读取 `build_stock_report.py` / `quick_analyze.py` 确认代码实际调用了哪些数据
3. 扫描本地目录确认各数据类型的存在性和覆盖率
4. 对每个本地存在但未同步至 SQLite 的数据源，执行：
   - 在 `references/data/schema.sql` 追加表结构
   - 在 `scripts/data/sync_to_sqlite.py` 的 `TABLE_SPECS` 中添加映射
   - 运行 `python3 sync_to_sqlite.py --tables <new_table>` 灌入
   - 验证：`sqlite3 <db> "SELECT COUNT(*) FROM <table>"`

### 常见陷阱与修复

| 问题 | 现象 | 修复 |
|---|---|---|
| SQLite 保留字列名 | `rank` 等列导致 `syntax error` | 在 `db_adapter.py` 的 `upsert_rows` 中给所有列名加双引号：`"{c}"` |
| CSV BOM 头 | pandas 读取正常但 Python csv 模块头行带 `﻿` | `_iter_csv_rows` 使用 `encoding="utf-8-sig"` |
| 日期列缺失 | by_date 模式下 CSV 内无 `trade_date` 列 | 从文件名提取日期并通过 `normalized[date_column] = row_date` 写入 |
| 列名不一致 | `sector_analysis` 的 CSV 使用中文列名 | 在 `TABLE_SPECS` 中添加 `column_mapping`，如 `{"sector": "板块", ...}` |
| 表名配置错误 | `dc_concept_cons` 被锡到 `dc_concept` | 严格检查 `TABLE_SPECS` 中 `table` 字段与 schema 一致 |

### 已同步表清单（2026-04-27 状态）

- `cyq_perf` 筹码绩效：887万+行，2018-2026
- `hm_list` / `hm_detail` 游资名单/明细
- `limit_cpt_list` 涨停概念列表
- `sector_analysis` 板块分析
- `stk_shock` 异常波动
- `kpl_list` / `kpl_concept_cons` 开盘啦涨停/概念
- `dc_concept` / `dc_concept_cons` Datayes 概念

### 仍缺失数据

- `moneyflow_by_stock` 逐笔资金流向：本地无目录
- `news normalized` 新闻归一化：0 条
- `minute` 分钟线：覆盖率 <1%，目录结构混杂

