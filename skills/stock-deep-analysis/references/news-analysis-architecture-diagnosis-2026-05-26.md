# 消息分析模块架构诊断（2026-05-26）

## 核心痛点：数据存储与消费严重不匹配

`stock-deep-analysis` 的消息分析管道（`runtime/news_runtime.py` 中的 `auto_resolve_news_json_path()`）期望在以下路径找到 JSON 文件：
```
~/quant-data/news_data/raw/news_pipeline/{YYYY}/{MM}/{DD}/news_pipeline_{symbol}_{date}.json
```

但实际上，消息数据存储在：
```
~/quant-data/tushare/消息面数据/news/*.db   —— SQLite，表：news_items, platforms, title_changes, ai_filter_results...
~/quant-data/tushare/消息面数据/rss/*.db    —— SQLite，表：rss_feeds, rss_items...
```

**结果**：`auto_resolve_news_json_path` 几乎永远找不到 JSON 文件，导致 `status: missing`，然后 fallback 到外部子进程重新抓取，每次分析多花 60-90 秒，且仍常失败。

## 关键代码位置

| 文件 | 职责 |
|------|------|
| `scripts/runtime/news_runtime.py` | 核心调度器，负责 JSON 路径查找、外部 pipeline 调用、fallback 链 |
| `scripts/news_context.py` | shim 层，用 `importlib.util` 动态加载 `message-intelligence` skill，路径配置可能为空 |
| `scripts/prepare_news_context.py` | 另一个 shim，通过子进程调用 `message-intelligence` skill |
| `market-news-intelligence/scripts/run_news_pipeline.py` | 外部新闻抓取 pipeline，被 stock-deep-analysis 通过子进程调用 |
| `message-intelligence/normalize/news_sentiment.py` | normalize 逻辑，极其朴素（纯关键词匹配） |

## 具体问题

### 1. 消息去重与聚合缺失

- `_collect_raw_items()` 只是简单收集标题和来源
- 没有对相似消息进行去重（编辑距离 / 共现词）
- 没有对同一事件的多条报道进行聚合

### 2. normalize 逻辑过于简化

`normalize_news_sentiment()` 仅靠关键词匹配（利好/利空/催化等）：
- `direction`：偏多 / 偏空 —— 只是二元判断，没有强度分数
- `level`：标题中含 "涨停"/"跌停" 等关键词时为 `major`，否则 `minor`
- `credibility`：来源域名匹配（announcement/policy/mainstream_media/market_platform/community）
- `impact_role`：仅根据 sector_context 填充

**问题**：没有利用 LLM 做语义分析，没有情感强度打分，没有催化剂类型识别。

### 3. shim 层脆弱

`news_context.py` 使用 `importlib.util` 动态加载，路径配置可能返回 None，导致动态导入失败。

### 4. 时效性判断粗糙

`is_new_catalyst()` 只比较日期字符串，没有考虑具体发布时间（小时/分钟），盘前和盘中消息区分不够精细。

### 5. 个股关联弱

`news_items` 表中没有 `ts_code` 或 `symbol` 字段，只能通过标题关键词匹配来关联到个股。`stock-deep-analysis` 没有实现从 SQLite 消息库中按股票名查询的逻辑。

## 优化方向（待实施）

### A. 直连 SQLite 消息库（核心，解决 missing 问题）
- 新增 `load_news_items_from_db(symbol, date)`，直接从 `news/*.db` + `rss/*.db` 读取与该股票相关的新闻（通过标题关键词匹配）
- 修改 `auto_resolve_news_json_path` 优先检查 SQLite 库 → 再查已有 JSON → 最后才走外部子进程
- 简化或移除 `news_context.py` 和 `prepare_news_context.py` 的 shim 层

### B. 消息去重 + 聚合
- 标题相似度去重（编辑距离 / 共现词）
- 同一事件的多源报道聚合成一条，标注"多源交叉验证"
- 减少重复消息对方向判断的干扰

### C. 增强 normalize 规则库
- 扩展关键词库（政策类/业绩类/重组类/订单类/减持类等细分催化剂）
- 增加情感强度评分（-1 到 +1），不只是偏多/偏空
- 时效性判断更精细：区分"盘前突发"、"盘中异动"、"盘后发酵"

### D. 消息-技术联动
- 当消息方向与技术信号冲突时，给出冲突分析
- 例：利好消息 + 技术破位 = 可能是利好出尽

## 当前数据库结构速查

### news/*.db 关键表

| 表名 | 关键字段 | 与个股关联方式 |
|------|----------|----------------|
| `news_items` | `id`, `title`, `platform_id`, `rank`, `url`, `first_crawl_time` | 无 symbol 字段，需标题关键词匹配 |
| `platforms` | `id`, `name`, `display_name` | 消息来源分类（今日头条、百度热搜、华尔街见闻...） |
| `title_changes` | `news_item_id`, `old_title`, `new_title`, `changed_at` | 标题变更历史，可用于判断热点演化 |
| `ai_filter_analyzed_news` | `news_item_id`, `source_type`, `matched` | AI 筛选结果，可用于快速过滤无关新闻 |
| `ai_filter_results` | `news_item_id`, `tag_id`, `relevance_score`, `status` | 相关性打分，可用于排序优先级 |

### rss/*.db 关键表

| 表名 | 关键字段 | 与个股关联方式 |
|------|----------|----------------|
| `rss_items` | `id`, `title`, `feed_id`, `url`, `published_at`, `summary`, `author` | 无 symbol 字段，但 `summary` 字段有内容 |
| `rss_feeds` | `id`, `name`, `url`, `category` | RSS 源分类 |
| `rss_crawl_records` | `feed_id`, `crawl_time`, `items_count` | 爬取历史 |

## 建议优先级

1. **P0 — 直连 SQLite**：解决根本问题，消息 missing 率从 >80% 降至 <10%
2. **P1 — 去重聚合**：减少重复消息干扰，提升分析准确性
3. **P2 — normalize 增强**：更丰富的方向/强度/时效性判断
4. **P3 — 消息-技术联动**：增加冲突检测与联动输出
