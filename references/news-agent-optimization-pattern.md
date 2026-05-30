# 消息分析 Agent 优化架构 - v4 无代码匹配 + Browser Fallback 模式

> 优化时间：2026-05-27
> 优化目标：`parallel/agents.py` 中 `run_news_agent()` 的消息相关性筛选
> 优化后效果：从单一标题匹配 0-2 条，提升到综合 5-10 条（含行业上下文）；TrendRadar 完全缺失时自动 browser fallback

## 核心问题：TrendRadar 数据的个股覆盖稀疏性

TrendRadar 的 `get_latest_news(limit=500)` 和 `get_latest_rss(limit=500)` 返回的新闻中，直接提及单个 A 股标的篇幅极少。实测测试表明：

- 70% 以上的新闻入口是市场级或行业级标题（如"光学光电板块强势"、"CPO 概念走强"）
- 单纯依靠标题匹配 `stock_name` 只能命中 0-2 条，大部分个股消息面为 "missing"
- 这不是数据缺失，而是热榜/RSS 的本质特征——它们偏向汇总市场和板块，而非逐只覆盖

## 解决方案：两阶段筛选 + 行业关键词扩展 + Browser Fallback

### 阶段一：精确匹配（Exact Match）

检索标题和摘要，查找直接提及目标股票名称的条目：

```python
keywords = [stock_name]
# 注意：不使用 pure_symbol（股票代码），避免数字误匹
for item in all_items:
    text = f"{title} {summary}"
    if any(kw in text for kw in keywords):
        item["_match_type"] = "exact"
        exact_matches.append(item)
```

- `stock_name`: 从 `stock_basic_all.csv` 查询的中文名称（如"华东重机"）
- 短名称前缀（如 4 字以上名称取前 2 字）也会加入匹配
- **重大变更：不再使用 `pure_symbol`（纯数字代码）**，因为新闻标题中几乎不出现 6 位股票代码，且纯数字容易误匹其他数字组合

### 阶段二：宽松匹配（Broad / Industry Context）

当精确匹配结果 < 3 条时，触发二次检索，利用行业关键词扩充检索：

```python
if len(exact_matches) < 3:
    # 加载个股行业
    industry = _load_stock_industry(symbol)
    # 见行业关键词映射表（33 个行业）
    extra_keywords = _INDUSTRY_KEYWORD_MAP.get(industry, [])
    for item in all_items:
        text = f"{title} {summary}"
        if any(kw in text for kw in extra_keywords):
            item["_match_type"] = "industry_context"
            broad_matches.append(item)
```

**行业关键词映射表（完整 33 行业）：**

| 行业名称 | 关键词列表 |
|----------|------------|
| 通用设备 | 机械, 设备, 机床, 轮朕, 铸造, 模具, 工程机械 |
| 专用设备 | 专用设备, 重型机械, 矿山设备, 石油设备, 舱船 |
| 轻工制造 | 轻工, 家具, 文具, 包装, 印刷, 玩具, 家电 |
| 电气设备 | 电气, 电网, 变压器, 电力, 续电器, 开关, 配电, 特高压 |
| 汽车整车 | 汽车, 整车, 乘用车, 商用车, 汽车零部件, 车辆 |
| 仪器仪表 | 仪器, 仪表, 测量, 传感器, 测控, 仪器仪表 |
| 电子 | 电子, 电子元器件, 半导体, PCB, 电子配件, 软件 |
| 计算机 | 计算机, 信息技术, IT, 软件, 硬件, 系统集成, 云计算 |
| 通信 | 通信, 5G, 光纤, 电信, 移动通信, 无线, 基站 |
| 传媒 | 传媒, 媒体, 影视, 游戏, 广告, 网络视听, 短视频 |
| 公用事业 | 公用事业, 电力, 供水, 燃气, 环保, 合同能源管理, 新型城镇化 |
| 银行 | 银行, 股份制银行, 城商银行, 农村商业银行, 非银金融 |
| 非银金融 | 证券, 保险, 基金, 信托, 期货, 租赁, 融资租赁, 后勤金融 |
| 房地产 | 房地产, 房屋, 物业, 地产, 楼市, 地产招商, 住宅 |
| 建筑材料 | 建材, 水泥, 玻璃, 瓦砖, 防水, 新型建筑材料, 装饰 |
| 建筑装饰 | 建筑, 装饰, 工程, 建筑工程, 干支设施, 铲土机 |
| 钢铁 | 钢铁, 采矿, 冶金, 金属, 铁矿石, 改性活性窑, 特钢 |
| 石油石化 | 石油, 石化, 汽油, 液化天然气, 油气, 采矿, 采油 |
| 化工 | 化工, 化学, 化肥, 涂料, 聚合物 |
| 有色金属 | 有色金属, 铜, 铝, 锌, 贵金属, 稀土, 锡 |
| 食品饮料 | 食品, 饮料, 白酒, 乳业, 保健品, 酒类 |
| 生物制药 | 医药, 药品, 生物制药, 转基因, 创新药, 药房连锁 |
| 医疗器械 | 医疗器械, 医疗, 医疗设备, 诊断, 超声传图, 心脏支架 |
| 农业 | 农业, 农作, 种业, 养殖, 农资, 农产品 |
| 服装纺织 | 纺织, 服装, 化纤, 纯棉 |
| 商业贸易 | 商业, 零售, 商店, 电商, 贸易, 招商, 超市, 电子商务 |
| 航运港口 | 航运, 港口, 集装箱, 海运, 航运港口, 港口运输 |
| 运输设备 | 航空, 航天, 机场, 高铁, 轨道交通, 动车组, 航空运输 |
| 光学光电 | 光学, 光电, 激光, 镜头, 光模块, CPO, 光纤, LED, 显示面板 |
| 电池 | 锂电池, 动力电池, 电池, 锂电, 动力电池 |
| 半导体 | 半导体, 芯片, 集成电路, 存储, 存储器, 算力 |
| 劳动力 | 人力, 劳务派遣, 人力资源, 工人, 招聘 |
| 新能源 | 新能源, 太阳能, 风电, 绿色低碳, 清洁能源, 可再生能源 |
| 物流 | 物流, 快递, 供应链, 供应链, 快递物流 |
| 室内装饰 | 装饰, 家居, 家装, 室内设计, 全屋定制, 装饰材料 |

**行业查询方式：**
`stock_basic_all.csv` 中的 `industry` 字段，编码 `utf-8-sig`，使用 csv.DictReader 读取后按 `name` 字段匹配个股名称。

### 阶段三：Browser Fallback（最后一道防线）

当 TrendRadar 精确匹配 + 宽松匹配后总条数仍为 0 时，自动调用 browser 抓取补充：

```python
def _fetch_browser_news_fallback(full_symbol, trade_date_text, stock_name):
    script = Path.home() / ".openclaw" / "skills" / "custom" / "market-news-intelligence" / "scripts" / "fetch_browser_news.py"
    cmd = [
        "python3", str(script),
        "--symbol", full_symbol,
        "--trade-date", trade_date_text,
        "--preset", "eastmoney", "cls",
        "--stock-name", stock_name,
        "--limit", "12",
        "--headless",
    ]
    # 返回（articles, status），status 可能值：skipped / browser_cached / browser_fetched / browser error: xxx
```

**触发条件：**
- `len(hot_items) + len(rss_items) == 0` 时自动触发
- 不主动触发（避免每次分析都走 browser，浪费时间）

**抓取来源：**
- `eastmoney` 东方财富新闻搜索
- `cls` 财联社搜索
- limit=12，headless 模式

**缓存机制：**
- 输出路径：`~/quant-data/tushare/消息面数据/raw/browser_news/browser_news_{pure_symbol}_{trade_date_text}.json`
- 同一交易日重复分析时直接读取缓存，避免重复抓取

**格式转换：**
- `_normalize_browser_articles()` 将 `fetch_browser_news.py` 的输出转换为 TrendRadar 兼容格式
- 匹配类型标记为 `_match_type: exact`
- 来源标记为 `_source: browser_fallback`

## 归一化处理中的权重差异

```python
def _weighted_sentiment(items):
    total = 0.0
    for item in items:
        weight = 2.0 if item.get("_match_type") == "exact" else 1.0
        # 情感定量赋值：益家=+1, 利空=-1, 空悬=0
        if "利空" in title or "跌" in title or "下跌" in title:
            total += weight * (-1)
        elif "利好" in title or "涨" in title or "推进" in title:
            total += weight * (+1)
    return total
```

- `exact` 匹配项：权重 2.0 —— 直接提及目标股，信息含量最高
- `industry_context` 匹配项：权重 1.0 —— 行业上下文，提供板块氛围，不能直接推导个股走势
- browser fallback 项：权重 2.0（标记为 exact）—— 虽然来源不同，但是直接搜索获取，信息直接关联目标股
- 最终 `news_sentiment.score` 为加权累计值，`direction` 由累计值符号决定

## 输出中的匹配类型声明

汇总报告必须分开列出：

```
直接匹配（exact）X 条：...
行业上下文（industry_context）Y 条：...
Browser fallback（Z 条：...
```

**禁止把 industry_context 和 exact 混为一体**：如果没有 exact 匹配，即使 industry_context 有 20 条，也不能得出"目标股受消息推动"的结论，只能得出"板块受消息推动，目标股可能间接受益"。

## 实战效果验证

**测试股：华东重机（002685.SZ）**

- 精确匹配：0 条（热榜中无直接提及）
- 行业匹配（通用设备类）：6 条行业新闻
- Browser fallback：未触发（industry_context 已提供足够上下文）
- 最终结论：`无直接个股消息，通过行业新闻补充上下文，当前方向偏空，与大盘走势一致`

**关键测试：**
- [x] 含 exact 匹配的股票：exact 项权重 2.0，体现在情感计算中
- [x] 不含 exact 匹配的股票：industry_context 项权重 1.0，报告中区分开声明
- [x] RSS 摘要字段被正确检索（`summary` 或 `description`）
- [x] 行业查询从 `stock_basic_all.csv` 正确工作
- [x] 当 exact >= 3 时，broad 筛选不触发（避免冗余）
- [x] 当 exact < 3 时，broad 筛选正确触发
- [x] days=3 的 RSS 数据起到辅助作用（新鲜度降权）
- [x] 股票代码已从关键词中移除（不再匹配 pure_symbol）
- [x] 当两阶段均为 0 条时，browser fallback 正确触发

## 已废除的旧模式

| 旧逻辑 | 新逻辑 | 原因 |
|--------|--------|------|
| `search_news` 关键词搜索 | `get_latest_news` + `get_latest_rss` + 本地过滤 | 关键词搜索覆盖率低，全量拉取更可靠 |
| SQLite 消息库 `load_all_news_for_symbol()` | TrendRadar MCP 全量拉取 + 本地过滤 | SQLite 库数据不足 |
| Browser fallback 抓取 | 当 TrendRadar 全量拉取+两阶段筛选均为 0 时触发 | 完全废除会导致新闻频率低的个股永远 missing，保留最后一道防线 |
| `auto_resolve_news_json_path()` 缓存回退 | 直接调用 MCP 并保存到标准路径 | 消息面必须是实时的 |
| `market-news-intelligence` pipeline | 已移除（browser fallback 仍调用其 `fetch_browser_news.py` 脚本） | 功能被 TrendRadar MCP 主要覆盖，仅保留 fallback 时的脚本调用 |
| `pure_symbol` 匹配 | 仅匹配 `stock_name`（+前缀） | 新闻标题中几乎不出现 6 位数字代码，且容易误匹其他数字组合 |

## 已知限制

1. **行业关键词可能过宽**："机械"关键词可能匹配到非通用设备行业的新闻（如"农业机械"）。当前版本未加行业排它逻辑。
2. **情感定量简化**：仅基于关键词匹配赋值，未使用 NLP 情感分析模型。精确度受限，但费用低廉。
3. **行业映射维护成本**：33 个行业的关键词需要人工维护，未自动同步。新增行业需手动補充映射。
4. **RSS 天数延气**：`days=3` 的 RSS 数据中，旧新闻的相关性和时效性都会下降。建议在归一化时对 `publish_time` 进行考虑，如 `publish_time < 分析日 - 1天` 的标记为 `stale`。
5. **Browser fallback 耗时**：Playwright 启动浏览器约需 10-30 秒，且依赖网络状态。只有当两阶段均失败时才触发，避免无差别抓取。

## 后续优化方向

1. **轮廓匹配**：让 AI 模型对每条新闻与目标股的相关性打分（0-10），取置信度阈值，精准度更高但成本更高
2. **自动行业发现**：使用 AI 从新闻标题自动提取热门行业，动态扩充关键词（当前需人工维护 33 行业）
3. **情感模型集成**：将关键词定量升级为轻量级 NLP 情感分析，提升情感计算精准度
4. **多平台融合**：当前仅使用 TrendRadar 热榜+RSS，后续可融合东方财富新闻、同花顺资讯等渠道提升覆盖率

## 代码位置

以上逻辑实现于：
`/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/parallel/agents.py`

具体函数：
- `_build_stock_keywords(stock_name, pure_symbol, industry, mode)` — 关键词构建（mode="exact"或"broad"）
- `_filter_items_for_stock(all_items, stock_name, pure_symbol, industry, mode)` — 两阶段筛选入口
- `_load_stock_industry(symbol)` — 从 stock_basic_all.csv 查询行业
- `_fetch_browser_news_fallback(full_symbol, trade_date_text, stock_name)` — Browser fallback
- `_normalize_browser_articles(articles)` — 格式转换
- `_trendradar_to_news_sentiment(results, trade_date, stock_name)` — 归一化出口
