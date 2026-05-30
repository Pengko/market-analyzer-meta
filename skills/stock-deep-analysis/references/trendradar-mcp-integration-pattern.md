> 存档日期：2026-05-27（v3）
> 关联代码：`scripts/parallel/agents.py`（`run_news_agent`、`_call_trendradar_mcp`、`_filter_items_for_stock`、`_trendradar_to_news_sentiment`、`_fetch_browser_news_fallback`、`_normalize_browser_articles`）

## 问题背景

`stock-deep-analysis` 的并行分析架构使用 `ThreadPoolExecutor` 运行各 Agent。
Agent-A（news）需要调用 TrendRadar MCP 服务获取消息，但存在多个技术障碍：

1. **MCP 客户端是 async 的**：`trendradar_mcp_cli.py` 使用 `mcp.client.stdio` 和 `asyncio`，而 `ThreadPoolExecutor` 中的线程已经有事件循环（或没有），直接在线程中运行 `asyncio.run()` 会导致事件循环冲突。
2. **Hermes MCP 工具需要新会话才能发现**：`hermes mcp add` 配置后，MCP 工具不能在当前会话中立即使用，需要重新启动 Hermes 会话。
3. **`search_news` 关键词搜索覆盖率低**：热榜中并非每只股票都有相关新闻，按个股名称关键词搜索经常返回 0 条（如比亚迪）。
4. **数据路径不一致**：TrendRadar MCP Server 读取 `~/Documents/TrendRadar/output/{news,rss}/`，但 Docker 爬虫实际写入 `~/quant-data/tushare/消息面数据/{news,rss}/`。
5. **Python 环境缺失**：系统 `python3` 缺少 `mcp` 包，导致 CLI wrapper 无法运行。

## 解决方案演进

### v1：search_news 关键词搜索（已废弃）
```python
search_result = _call_trendradar_mcp("search_news", {
    "query": stock_name or pure_symbol,
    "include_rss": True,
    "limit": 20,
})
```
问题：热榜是全网 trending，某只股票未必有标题命中关键词，导致 `missing` 率过高。

### v2：全量拉取 + 本地过滤（已升级）
```python
# Step 1: 拉取当日全量热榜 + 全量 RSS
hot_result = _call_trendradar_mcp("get_latest_news", {"limit": 500})
rss_result = _call_trendradar_mcp("get_latest_rss", {"limit": 500, "days": 1})

# Step 2: 本地按标题匹配过滤
all_items = _filter_items_for_stock(hot_items + rss_items, stock_name, pure_symbol)

# Step 3: 归一化分析
news_sentiment = _trendradar_to_news_sentiment(all_items, trade_date_text, stock_name)
```
优势：热榜覆盖率从"关键词是否命中"变为"标题是否提及该股票"，显著提升消息可见性。

### v3：两阶段筛选 + 行业上下文 + Browser Fallback（当前模式）
```python
# Step 1: 拉取全量热榜 + RSS
hot_result = _call_trendradar_mcp("get_latest_news", {"limit": 500})
rss_result = _call_trendradar_mcp("get_latest_rss", {"limit": 500, "days": 3})
hot_raw = hot_result.get("data", []) if hot_result.get("success") else []
rss_raw = rss_result.get("data", []) if rss_result.get("success") else []

# Step 2: 两阶段筛选
# 2a) 精确匹配（只匹配 stock_name，不含 pure_symbol）
hot_exact = _filter_items_for_stock(hot_raw, stock_name, pure_symbol, industry=None, mode="exact")
rss_exact = _filter_items_for_stock(rss_raw, stock_name, pure_symbol, industry=None, mode="exact")

# 2b) 宽松匹配（精确 < 3 条时触发，加行业关键词）
if len(hot_exact) + len(rss_exact) < 3:
    hot_broad = _filter_items_for_stock(hot_raw, stock_name, pure_symbol, industry=stock_industry, mode="broad")
    rss_broad = _filter_items_for_stock(rss_raw, stock_name, pure_symbol, industry=stock_industry, mode="broad")

# Step 3: Browser Fallback（两阶段均为 0 条时触发）
if len(hot_items) + len(rss_items) == 0:
    browser_articles, status = _fetch_browser_news_fallback(full_symbol, trade_date_text, stock_name)
    rss_items.extend(browser_articles)

# Step 4: 归一化
news_sentiment = _trendradar_to_news_sentiment(search_result, trade_date_text, stock_name)
```

**重大变更：**
1. **不再使用 `pure_symbol` 匹配**：新闻标题中几乎不出现 6 位数字代码，且容易误匹其他数字组合。只保留 `stock_name`（全称 + 前两字前缀）。
2. **重新启用 Browser Fallback**：当 TrendRadar 全量拉取+两阶段筛选均为 0 条时，自动调用 `fetch_browser_news.py` 通过浏览器抓取补充。这避免了"新闻频率低的个股永远 missing"的问题。
3. **行业关键词映射**：33 个行业的关键词扩展，提升宽松匹配的命中率。

## 关键代码结构（v3）

```python
TRENDRADAR_MCP_CLI = Path("/Users/penghongming/agent-skills/custom/trendradar-mcp/scripts/trendradar_mcp_cli.py")
TRENDRADAR_PYTHON = Path("/Users/penghongming/Documents/TrendRadar/.venv/bin/python")

def _build_stock_keywords(stock_name: str | None, pure_symbol: str, industry: str | None, mode: str = "exact") -> list[str]:
    """构建股票相关关键词列表
    mode="exact": 只匹配股票名（不含代码）
    mode="broad":  宽松匹配（加行业关键词、简称前缀）
    """
    keywords = []
    if stock_name:
        keywords.append(stock_name)
        if len(stock_name) >= 4:
            keywords.append(stock_name[:2])
    # 注意：不添加 pure_symbol，避免数字误匹
    if mode == "broad" and industry:
        industry_keywords = _INDUSTRY_KEYWORD_MAP.get(industry, [industry])
        keywords.extend(industry_keywords)
    return keywords

def _filter_items_for_stock(items: list[dict], stock_name: str, pure_symbol: str, industry: str | None = None, mode: str = "exact") -> list[dict]:
    """在全量热榜/RSS中按关键词匹配筛选个股相关条目"""
    keywords = _build_stock_keywords(stock_name, pure_symbol, industry, mode)
    matched = []
    for item in items:
        title = item.get("title", "")
        summary = item.get("summary", item.get("description", ""))
        text = f"{title} {summary}"
        if any(kw in text for kw in keywords):
            matched.append(item)
    return matched

def _fetch_browser_news_fallback(full_symbol: str, trade_date_text: str, stock_name: str | None) -> tuple[list[dict], str]:
    """当 TrendRadar 无匹配时，通过 browser 抓取新闻作为 fallback"""
    script = Path.home() / ".openclaw" / "skills" / "custom" / "market-news-intelligence" / "scripts" / "fetch_browser_news.py"
    output_path = Path.home() / "quant-data" / "tushare" / "面消息数据" / "raw" / "browser_news" / f"browser_news_{pure_symbol}_{trade_date_text}.json"
    # 缓存检查
    if output_path.exists():
        cached = json.loads(output_path.read_text(encoding="utf-8"))
        return _normalize_browser_articles(cached.get("articles", [])), "browser_cached"
    # 调用脚本
    cmd = ["python3", str(script), "--symbol", full_symbol, "--trade-date", trade_date_text,
           "--preset", "eastmoney", "cls", "--stock-name", stock_name or pure_symbol, "--limit", "12", "--headless"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
    if result.returncode != 0:
        return [], f"browser exit {result.returncode}"
    data = json.loads(output_path.read_text(encoding="utf-8"))
    return _normalize_browser_articles(data.get("articles", [])), "browser_fetched"

def _normalize_browser_articles(articles: list[dict]) -> list[dict]:
    """将 fetch_browser_news.py 输出转换为 TrendRadar 兼容格式"""
    return [{
        "title": a.get("title", ""),
        "url": a.get("url", ""),
        "platform_name": a.get("source", "browser"),
        "published_at": a.get("published_at", ""),
        "summary": a.get("content", ""),
        "_match_type": "exact",
        "_source": "browser_fallback",
    } for a in articles if a.get("title")]

def _call_trendradar_mcp(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """通过 subprocess 调用 TrendRadar MCP CLI，返回结构化结果"""
    args_json = json.dumps(arguments, ensure_ascii=False)
    env = os.environ.copy()
    env["FASTMCP_SHOW_SERVER_BANNER"] = "false"
    result = subprocess.run(
        [str(TRENDRADAR_PYTHON), str(TRENDRADAR_MCP_CLI), "call", tool_name, "--args-json", args_json],
        capture_output=True, text=True, timeout=30, check=False, env=env,
    )
    # 解析 wrapper 输出
    wrapper = json.loads(result.stdout)
    content = wrapper.get("content", [])
    for item in content:
        if item.get("type") == "text" and "text" in item:
            return json.loads(item["text"])
    return {"status": "error", "error": "No text content"}

def run_news_agent(full_symbol: str, trade_date_text: str, ...) -> dict[str, Any]:
    """消息面分析：通过 TrendRadar MCP 全量拉取 + 本地两阶段过滤 + Browser Fallback"""
    # 1. 拉取全量热榜 + RSS
    hot_result = _call_trendradar_mcp("get_latest_news", {"limit": 500, "include_url": True})
    hot_raw = hot_result.get("data", []) if hot_result.get("success") else []
    
    rss_result = _call_trendradar_mcp("get_latest_rss", {"limit": 500, "days": 3, "include_summary": True})
    rss_raw = rss_result.get("data", []) if rss_result.get("success") else []
    
    # 2. 两阶段筛选
    hot_exact = _filter_items_for_stock(hot_raw, stock_name, pure_symbol, industry=None, mode="exact")
    rss_exact = _filter_items_for_stock(rss_raw, stock_name, pure_symbol, industry=None, mode="exact")
    
    hot_items = list(hot_exact)
    rss_items = list(rss_exact)
    
    broad_added = 0
    if len(hot_exact) + len(rss_exact) < 3 and stock_industry:
        hot_broad = _filter_items_for_stock(hot_raw, stock_name, pure_symbol, industry=stock_industry, mode="broad")
        rss_broad = _filter_items_for_stock(rss_raw, stock_name, pure_symbol, industry=stock_industry, mode="broad")
        # 合并去重...
        broad_added = ...
    
    # 3. Browser Fallback（两阶段均为 0 时触发）
    browser_articles = []
    browser_fallback_status = "skipped"
    if len(hot_items) + len(rss_items) == 0:
        browser_articles, browser_fallback_status = _fetch_browser_news_fallback(full_symbol, trade_date_text, stock_name)
        rss_items.extend(browser_articles)
    
    # 4. 组装并归一化
    search_result = {"data": hot_items, "rss_data": rss_items, ...}
    news_sentiment = _trendradar_to_news_sentiment(search_result, trade_date_text, stock_name)
    
    # 5. 保存并返回
    return {
        "status": "available" if (hot_items or rss_items) else "missing",
        "news_pipeline_meta": {
            "mode": "trendradar_mcp",
            "hot_total": len(hot_raw),
            "rss_total": len(rss_raw),
            "filtered_hot_exact": len(hot_exact),
            "filtered_rss_exact": len(rss_exact),
            "filtered_hot_broad": len(hot_items) - len(hot_exact),
            "filtered_rss_broad": len(rss_items) - len(rss_exact) - len(browser_articles),
            "used_broad_match": broad_added > 0,
            "browser_fallback_status": browser_fallback_status,
        },
        ...
    }
```

## 数据路径修复

**问题**：TrendRadar MCP Server 内部硬编码查找 `~/Documents/TrendRadar/output/news/` 和 `output/rss/`，但 Docker 爬虫写入 `~/quant-data/tushare/消息面数据/news/` 和 `rss/` 。

**修复**：通过符号链接统一两个路径，避免修改 TrendRadar 源码。

```bash
# 备份原目录
mv ~/Documents/TrendRadar/output/news ~/Documents/TrendRadar/output/news.local
mv ~/Documents/TrendRadar/output/rss ~/Documents/TrendRadar/output/rss.local

# 创建符号链接
ln -s ~/quant-data/tushare/消息面数据/news ~/Documents/TrendRadar/output/news
ln -s ~/quant-data/tushare/消息面数据/rss ~/Documents/TrendRadar/output/rss
```

验证：
```bash
ls -la ~/Documents/TrendRadar/output/
# 预期：news -> /Users/penghongming/quant-data/tushare/消息面数据/news
#       rss  -> /Users/penghongming/quant-data/tushare/消息面数据/rss
```

## Python 解释器修复

**问题**：系统 `python3` 缺少 `mcp` 包，调用 CLI wrapper 时抛出 `ModuleNotFoundError: No module named 'mcp'`。

**修复**：使用 TrendRadar 项目自身的 venv Python，而非系统 `python3`。

```python
# 之前（失败）
["python3", str(TRENDRADAR_MCP_CLI), "call", ...]

# 之后（成功）
TRENDRADAR_PYTHON = "/Users/penghongming/Documents/TrendRadar/.venv/bin/python"
[str(TRENDRADAR_PYTHON), str(TRENDRADAR_MCP_CLI), "call", ...]
```

## 返回格式兼容性

**问题**：TrendRadar 各接口返回的 JSON 结构不一致，导致 `_trendradar_to_news_sentiment()` 在切换工具后可能解析失败。

**已知的三种返回结构**：

| 接口 | 实际返回键名 | 示例 |
|------|----------|------|
| `search_news` | `results` | `{"results": [{"title": "..."}]}` |
| `get_latest_news` | `data` / `news` | `{"data": [{"title": "..."}]}` 或 `{"news": [{"title": "..."}]}` |
| `get_latest_rss` | `data.rss_data` / `items` | `{"data": {"rss_data": [{"title": "..."}]}}` 或 `{"items": [{"title": "..."}]}` |

**建议的降级提取逻辑**：

```python
def _extract_items(result: dict) -> list[dict]:
    """从不同接口的返回结构中提取条目列表"""
    if "results" in result and isinstance(result["results"], list):
        return result["results"]
    if "data" in result and isinstance(result["data"], list):
        return result["data"]
    if "news" in result and isinstance(result["news"], list):
        return result["news"]
    if "data" in result and isinstance(result["data"], dict):
        inner = result["data"].get("rss_data", [])
        if isinstance(inner, list):
            return inner
    if "items" in result and isinstance(result["items"], list):
        return result["items"]
    return []
```

**关键原则**：不要假设某个接口永远返回固定键名；MCP 工具的返回结构可能随版本变化，提取逻辑应多路回落。

## 增强元数据输出

`news_pipeline_meta` 新增字段用于排查消息覆盖率：

| 字段 | 说明 |
|------|------|
| `hot_total` | 当日热榜总条数（如 271） |
| `rss_total` | 当日 RSS 总条数（如 500） |
| `filtered_hot_exact` | 标题命中该股票的热榜条数 |
| `filtered_rss_exact` | 标题命中该股票的 RSS 条数 |
| `filtered_hot_broad` | 宽松匹配新增的热榜条数 |
| `filtered_rss_broad` | 宽松匹配新增的 RSS 条数 |
| `used_broad_match` | 是否使用了宽松匹配 |
| `browser_fallback_status` | 状态：skipped / browser_cached / browser_fetched / browser error: xxx |

**若 `filtered_hot_exact == 0` 且 `filtered_rss_exact == 0` 且 `browser_fallback_status != skipped`，说明数据来源已经 fallback 到 browser。**

## 已废除的旧模式

| 旧逻辑 | 新逻辑 | 原因 |
|--------|--------|------|
| `search_news` 关键词搜索 | `get_latest_news` + `get_latest_rss` + 本地过滤 | 关键词搜索覆盖率低，全量拉取更可靠 |
| SQLite 消息库 `load_all_news_for_symbol()` | TrendRadar MCP 全量拉取 + 本地过滤 | SQLite 库数据不足 |
| Browser fallback 抓取 | 保留最后一道防线：两阶段均为 0 时触发 | 完全废除会导致新闻频率低的个股永远 missing |
| `auto_resolve_news_json_path()` 缓存回退 | 直接调用 MCP 并保存到标准路径 | 消息面必须是实时的 |
| `market-news-intelligence` pipeline | 已移除（browser fallback 仍调用其 `fetch_browser_news.py` 脚本） | 主功能被 TrendRadar MCP 覆盖，仅保留 fallback 时的脚本调用 |
| `pure_symbol` 匹配 | 仅匹配 `stock_name` | 新闻标题中几乎不出现 6 位数字代码，且容易误匹 |

## 关键配置

- TrendRadar MCP CLI wrapper: `/Users/penghongming/agent-skills/custom/trendradar-mcp/scripts/trendradar_mcp_cli.py`
- TrendRadar venv Python: `/Users/penghongming/Documents/TrendRadar/.venv/bin/python`
- TrendRadar 项目路径: `/Users/penghongming/Documents/TrendRadar`
- 数据符号链接: `~/Documents/TrendRadar/output/news` -> `~/quant-data/tushare/消息面数据/news`
- 数据符号链接: `~/Documents/TrendRadar/output/rss` -> `~/quant-data/tushare/消息面数据/rss`
- 输出保存路径: `~/quant-data/tushare/消息面数据/raw/news_pipeline/YYYY/MM/DD/`
- Browser fallback 缓存: `~/quant-data/tushare/消息面数据/raw/browser_news/`

## 故障排查速查表

| 症状 | 排查步骤 |
|------|----------|
| `get_latest_news` 返回空或失败 | 1. 检查符号链接：`ls -la ~/Documents/TrendRadar/output/news`<br>2. 检查数据目录：`ls ~/quant-data/tushare/消息面数据/news/`<br>3. 检查 Docker 容器：`docker ps \| grep trendradar` |
| `ModuleNotFoundError: No module named 'mcp'` | 确认使用 `~/Documents/TrendRadar/.venv/bin/python`，而非系统 `python3` |
| 返回 `Success: False` | 检查 CLI wrapper 的 `--args-json` 参数格式是否正确（必须为有效 JSON） |
| 过滤后条数为 0 | 正常现象。热榜是全网 trending，并非每只股票都有。查看 `hot_total` / `rss_total` 确认全量拉取成功；若均为 0 则检查 browser_fallback_status |
| Browser fallback 失败 | 检查 `fetch_browser_news.py` 是否存在；检查 `market-news-intelligence` 技能是否安装；检查 Playwright/CDP 是否可用 |
| 数据日期不对 | 确认 Docker 爬虫已运行并写入当日 `.db` 文件到 `quant-data` 目录 |

## 注意事项

1. **subprocess timeout**: 默认 30 秒，包含 FastMCP 服务器启动时间（约 2-3 秒）+ 工具调用时间。
2. **JSON 解析层次**: CLI wrapper 返回的是 MCP 协议 wrapper，需要提取 `content[0].text` 中的真正工具返回值。
3. **线程安全**: subprocess 是进程级隔离，在 ThreadPoolExecutor 中完全线程安全。
4. **环境变量**: 必须设置 `FASTMCP_SHOW_SERVER_BANNER=false`，避免 banner 污染 stdout 导致 JSON 解析失败。
5. **数据新鲜度**: `get_latest_news` / `get_latest_rss` 返回的是爬虫最近一次写入的数据，非实时推送。若需最新数据，先确认爬虫运行状态。
6. **Browser fallback 时间成本**: Playwright 启动浏览器约需 10-30 秒。只有当两阶段均失败时才触发，避免无差别抓取。
7. **股票代码不匹配**: 不要在新闻筛选中使用 `pure_symbol`。新闻标题中几乎不出现 6 位数字代码，且纯数字容易误匹其他数字组合。
