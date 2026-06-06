# Fundamental Research Patterns (产品技术壁垒/业务增量/客户份额)

> Session: 2026-06-06 | Stock: 三峡新材 (600293.SH)

## When to Use

When the user asks for **business/fundamental analysis** rather than trading analysis:
- 产品技术壁垒 (product technology barriers)
- 业务增量前景 (business growth prospects)
- 客户份额/竞争格局 (customer share / competitive landscape)
- 财务健康度 (financial health)
- 行业对标 (industry peer comparison)

These are **NOT** trading signals — do NOT invoke the full stock-deep-analysis pipeline (minute klines, chips, auction, technical factors, etc.).

## Key Differences from Trading Analysis

| Dimension | Trading Analysis | Fundamental Research |
|-----------|-----------------|---------------------|
| Data sources | Local parquet (daily, factor, chips, moneyflow) | Browser/web (F10, annual reports, news) |
| Primary tool | build_stock_report.py / quick_analyze.py | delegate_task with browser + web search |
| Time context | Real-time (current session) | Any recent period |
| Output format | 7-module trading report | 3-dimension structured analysis |
| Key metrics | Price, volume, chips, technical factors | Revenue, margins, patents, market share |

## Recommended Workflow

### 1. Confirm Stock Identity
- User may use colloquial names (e.g., "三峡材料" → 三峡新材 600293.SH)
- Use Tencent quote API to verify: `curl -s "http://qt.gtimg.cn/q=sh600293" | iconv -f gb2312 -t utf-8`
- If name doesn't match exactly, search eastmoney to confirm

### 2. Parallel Data Collection via delegate_task
The most effective pattern: delegate to a subagent with `toolsets: ["web", "browser"]` that can:
- Navigate eastmoney F10 pages (company profile, business analysis, core themes)
- Read analyst reports and news via web search
- Access TrendRadar for recent news/catalysts

**Why delegate_task**: Eastmoney F10 pages use iframes that can't be read by browser_snapshot. A subagent can navigate multiple pages and extract content more reliably than trying to scrape iframes directly.

### 3. Eastmoney F10 Data Extraction
The most reliable data sources for fundamental research:

**Company Profile API** (works):
```
https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO&columns=ALL&filter=(SECURITY_CODE=%22{code}%22)&source=HSF10&client=PC
```
Returns: company name, industry, chairman, employees, main business, org_profile, business_scope

**Business Composition**: F10 pages via browser navigation (iframe-based, need subagent)
- Navigate to: `https://emweb.securities.eastmoney.com/pc_hsf10/pages/index.html?type=web&code=SH{code}&color=b#/jyfx`
- Content loads in iframes — subagent needs to navigate and snapshot

**Financial Data**: Eastmoney datacenter API (report names vary, trial-and-error needed)
- `RPT_F10_BASIC_ORGINFO` — company profile ✅
- `RPT_F10_FN_*` — financial reports (report names are unreliable, API returns "报表配置不存在")
- Fallback: browser navigate to individual F10 pages

### 4. Output Format for Fundamental Research

Standard 3-dimension structure:

```
## {公司名} ({代码}) 深度分析
**产品技术壁垒 · 业务增量前景 · 客户份额**

### 一、产品技术壁垒
- Product matrix table (产品线 / 产线规模 / 技术壁垒 / 盈利能力)
- Core barrier breakdown (resource / R&D / scale / brand)
- Barrier rating summary

### 二、业务增量前景
- Financial trend table (3-5 years)
- Growth direction assessment table (方向 / 当前状态 / 增量潜力 / 时间窗口)
- Industry context

### 三、客户份额与竞争格局
- Market positioning
- Competitive landscape comparison table
- Market share estimates by segment

### 四、综合判断
- Conclusion table (维度 / 判断 / 置信度)
- Key risks
- Turning point signals to watch
```

## Pitfalls

1. **Eastmoney F10 iframes**: Browser tools can't read iframe content. Use delegate_task or API endpoints.
2. **Eastmoney datacenter API report names**: Most `RPT_F10_FN_*` report names return "报表配置不存在". Only `RPT_F10_BASIC_ORGINFO` is reliable. For financial data, use browser navigation or TrendRadar articles.
3. **Tushare token may expire**: The local proxy token (`lianghua.nanyangqiankun.top`) can expire. If "您的token不对" error, fall back to browser/web sources.
4. **Colloquial stock names**: User may say "三峡材料" when the actual name is "三峡新材". Always verify the exact stock code.
5. **Scope creep**: Don't expand a fundamental research request into a full trading analysis. Keep the output focused on the dimensions the user asked about.

## Example: 三峡新材 Analysis Dimensions

For a regional glass manufacturer like 三峡新材:
- **Technology barriers**: Resource (silica sand), R&D platform (provincial center + university partnership), patents (40+), brand (中国驰名商标)
- **Growth prospects**: Revenue trend (shrinking), strategic directions (solar/electronic/pharma glass — all in planning stage), industry context (overcapacity)
- **Customer share**: Regional dominance (Hubei/central China), competitive position vs national leaders (信义/福莱特/金晶)
