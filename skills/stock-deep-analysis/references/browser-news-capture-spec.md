# 浏览器消息抓取 JSON 约定

说明：消息抓取脚本的主入口已经迁到 `market-news-intelligence`，旧路径仍保留兼容入口。

## 目标

让浏览器抓回来的消息可以直接进入：

- `market-news-intelligence/scripts/prepare_news_context.py`
- `scripts/score_next_day_bias.py --news-json`
- `scripts/build_stock_report.py --news-json`

避免每次都手工把浏览器结果重新整理成另一种格式。

## 推荐顶层结构

```json
{
  "schema_version": "1.0",
  "symbol": "300608.SZ",
  "trade_date": "2026-04-02",
  "captured_at": "2026-04-10T01:10:00+08:00",
  "capture_mode": "browser_manual_capture",
  "articles": [],
  "notes": [],
  "operator_judgment": {
    "main_driver": "",
    "market_interpretation": "",
    "core_or_follow": "",
    "wash_or_distribute": ""
  }
}
```

自动抓取模式也允许：

```json
{
  "capture_mode": "browser_auto_capture",
  "scanned_pages": [
    {
      "source": "eastmoney",
      "url": "https://so.eastmoney.com/...",
      "article_count": 6
    }
  ]
}
```

## `articles` 最小字段

每条消息建议至少包含：

```json
{
  "title": "标题",
  "source": "来源",
  "published_at": "2026-04-02 09:15",
  "url": "https://example.com/news",
  "content": "可选摘要",
  "channel": "announcement | policy | mainstream_media | market_platform | community"
}
```

其中：

- `title`：必须有
- `source`：尽量有
- `published_at`：尽量标准化到 `YYYY-MM-DD` 或 `YYYY-MM-DD HH:MM`
- `url`：尽量保留，便于回查
- `content`：可选，适合放浏览器摘录的 1-3 句摘要
- `channel`：可选，不填也可以，当前脚本会按来源自动推断

## 当前脚本会自动推断的字段

只要 `articles` 基本完整，当前链路会自动补：

- `checked_sources`
- `main_sources`
- `summary`
- `direction`
- `level`
- `freshness`
- `credibility`
- `is_new_catalyst`
- `impact_role`
- `impact_on_price`
- `narrative_context`

## 浏览器抓取建议

优先按下面顺序抓：

1. `announcement`
2. `policy`
3. `mainstream_media`
4. `market_platform`
5. `community`

至少抓到：

- 一条最可能的主驱动
- 一条市场如何解释这条驱动
- 一条能说明个股是前排、映射还是跟风的材料

## 操作建议

默认优先用统一入口：

```bash
python3 "/Users/penghongming/agent-skills/custom/market-news-intelligence/scripts/run_news_pipeline.py" --symbol 300608.SZ --trade-date 20260402 --preset eastmoney --preset cls --stock-name 思特奇 --deep-open-limit 3
```

如果已经有 raw JSON：

```bash
python3 "/Users/penghongming/agent-skills/custom/market-news-intelligence/scripts/run_news_pipeline.py" --symbol 300608.SZ --trade-date 20260402 --news-json /tmp/news_capture_300608_2026-04-02.json
```

初始化模板：

```bash
python3 "/Users/penghongming/agent-skills/custom/market-news-intelligence/scripts/init_news_capture.py" --symbol 300608.SZ --trade-date 20260402
```

检查归一化结果：

```bash
python3 "/Users/penghongming/agent-skills/custom/market-news-intelligence/scripts/prepare_news_context.py" --news-json /tmp/news_capture_300608_2026-04-02.json --trade-date 20260402
```

直接进分析：

```bash
python3 "/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/build_stock_report.py" --symbol 300608.SZ --trade-date 20260402 --news-json /tmp/news_capture_300608_2026-04-02.json --format json
```

自动抓取候选文章：

```bash
python3 "/Users/penghongming/agent-skills/custom/market-news-intelligence/scripts/fetch_browser_news.py" --symbol 300608.SZ --trade-date 20260402 --preset eastmoney --preset cls --stock-name 思特奇
```

自动抓取并打开部分正文补全：

```bash
python3 "/Users/penghongming/agent-skills/custom/market-news-intelligence/scripts/fetch_browser_news.py" --symbol 300608.SZ --trade-date 20260402 --preset eastmoney --stock-name 思特奇 --deep-open-limit 3
```
