# 统一数据路径与 Tushare 聚合层重构计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除散乱的硬编码路径，让所有数据访问通过统一入口

**Architecture:** `common.py` 已从 `cfg.paths()` 读取路径，但 6+ 文件仍硬编码。本次重构让所有文件改用 `common.py` 导出的路径常量，`tushare_client.py` 作为唯一的 Tushare API 聚合层。

**Tech Stack:** Python, Path, pandas

**Frozen Baseline:** 已完成的 parquet 迁移、history comparison、validation 简化均保持不变。本次仅重构路径引用方式。

---

### Task 1: 确认 common.py 路径导出完整性

**Files:**
- Modify: `scripts/common.py`

**现状：** `common.py:20-24` 已导出 5 个路径常量：
- `STOCK_DATA_ROOT` = `cfg.paths("stock_data_root")`
- `NEWS_DATA_ROOT` = `cfg.paths("news_data_root")`
- `INDEX_DATA_ROOT` = `cfg.paths("index_data_root")`
- `FINANCIAL_DATA_ROOT` = `cfg.paths("financial_data_root")`
- `MINUTE_DATA_ROOT` = `cfg.paths("minute")`

**缺失：** 需补充 `THEME_DATA_ROOT`（`skill-config.yaml` 无此 key，但 `dataslicer.py` 需要 `股票数据/theme_data`）

- [ ] **Step 1: 在 common.py 添加 THEME_DATA_ROOT**

```python
# 在 common.py 第 24 行后添加：
THEME_DATA_ROOT = STOCK_DATA_ROOT / "theme_data"
```

- [ ] **Step 2: 验证 common.py 可正常 import**

Run: `cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis && python -c "from common import STOCK_DATA_ROOT, THEME_DATA_ROOT; print(STOCK_DATA_ROOT, THEME_DATA_ROOT)"`
Expected: 输出两个路径，无报错

---

### Task 2: 重构 dataslicer.py — 消除硬编码路径

**Files:**
- Modify: `scripts/data/dataslicer.py:26-31`

**现状（第 27-30 行）：**
```python
STOCK_ROOT   = Path.home() / "quant-data" / "tushare" / "股票数据"
INDEX_ROOT   = Path.home() / "quant-data" / "tushare" / "指数数据"
THEME_ROOT   = Path.home() / "quant-data" / "tushare" / "股票数据" / "theme_data"
TRADE_CAL    = STOCK_ROOT / "trade_cal" / "trade_cal_all.csv"
```

- [ ] **Step 1: 替换为从 common 导入**

删除第 27-30 行的 4 个硬编码，改为：

```python
from common import STOCK_DATA_ROOT, INDEX_DATA_ROOT, THEME_DATA_ROOT

STOCK_ROOT = STOCK_DATA_ROOT
INDEX_ROOT = INDEX_DATA_ROOT
THEME_ROOT = THEME_DATA_ROOT
TRADE_CAL  = STOCK_ROOT / "trade_cal" / "trade_cal_all.csv"
```

保留 `STOCK_ROOT` 等别名以避免改动文件内其他引用。

- [ ] **Step 2: 验证 dataslicer 可正常 import**

Run: `cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis && python -c "from data.dataslicer import resolve_trade_date; print(resolve_trade_date())"`
Expected: 输出当前交易日（如 20260606），无报错

---

### Task 3: 重构 fundamental_provider.py — 消除硬编码路径

**Files:**
- Modify: `scripts/data/fundamental_provider.py:14-15`

**现状：**
```python
FINANCIAL_ROOT = Path.home() / "quant-data" / "tushare" / "财务数据"
STOCK_ROOT = Path.home() / "quant-data" / "tushare" / "股票数据"
```

- [ ] **Step 1: 替换为从 common 导入**

```python
from common import FINANCIAL_DATA_ROOT, STOCK_DATA_ROOT

FINANCIAL_ROOT = FINANCIAL_DATA_ROOT
STOCK_ROOT = STOCK_DATA_ROOT
```

- [ ] **Step 2: 验证**

Run: `cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis && python -c "from data.fundamental_provider import get_fundamental_express; print(get_fundamental_express('000001')[:1])"`
Expected: 返回列表（可能为空），无报错

---

### Task 4: 重构 data_provider.py — 消除硬编码路径

**Files:**
- Modify: `scripts/data/data_provider.py:5-6`

**现状：**
```python
_STOCK_ROOT = Path("/Users/penghongming/quant-data/tushare/股票数据")
_INDEX_ROOT = Path("/Users/penghongming/quant-data/tushare/指数数据")
```

- [ ] **Step 1: 替换为从 common 导入**

```python
from common import STOCK_DATA_ROOT, INDEX_DATA_ROOT

_STOCK_ROOT = STOCK_DATA_ROOT
_INDEX_ROOT = INDEX_DATA_ROOT
```

- [ ] **Step 2: 验证**

Run: `cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis && python -c "from data.data_provider import get_daily; print(get_daily('000001.SZ', '20260606'))"`
Expected: 返回 dict 或 None，无报错

---

### Task 5: 重构 decision_engine.py — 消除硬编码路径

**Files:**
- Modify: `scripts/decision/decision_engine.py:77, 201`

**现状：**
- 第 77 行: `theme_root = _Path("/Users/penghongming/quant-data/tushare/股票数据/theme_data")`
- 第 201 行: `daily_root = Path("/Users/penghongming/quant-data/tushare/股票数据/daily")`

- [ ] **Step 1: 在文件顶部添加 import**

在 `from pathlib import Path` 附近添加：
```python
from common import STOCK_DATA_ROOT
```

- [ ] **Step 2: 替换第 77 行**

```python
theme_root = STOCK_DATA_ROOT / "theme_data"
```

- [ ] **Step 3: 替换第 201 行**

```python
daily_root = STOCK_DATA_ROOT / "daily"
```

- [ ] **Step 4: 验证**

Run: `cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis && python -c "from decision.decision_engine import _build_dc_index; _build_dc_index(); print('OK')"`
Expected: OK，无报错

---

### Task 6: 重构 parallel/agents.py — 消除硬编码路径

**Files:**
- Modify: `scripts/parallel/agents.py:193, 287, 559`

**现状：**
- 第 193 行: `Path.home() / "quant-data" / "tushare" / "股票数据" / "stock_basic" / "stock_basic_all.csv"`
- 第 287 行: `Path.home() / "quant-data" / "tushare" / "面消息数据" / "raw" / "browser_news"` (疑似拼写错误)
- 第 559 行: `Path.home() / "quant-data" / "tushare" / "消息面数据"`

- [ ] **Step 1: 在文件顶部添加 import**

```python
from common import STOCK_DATA_ROOT, NEWS_DATA_ROOT
```

- [ ] **Step 2: 替换第 193 行**

```python
path = STOCK_DATA_ROOT / "stock_basic" / "stock_basic_all.csv"
```

- [ ] **Step 3: 替换第 287 行**（修正拼写 "面消息数据" → "消息面数据"）

```python
output_dir = NEWS_DATA_ROOT / "raw" / "browser_news"
```

- [ ] **Step 4: 替换第 559 行**

```python
news_data_root = NEWS_DATA_ROOT
```

- [ ] **Step 5: 验证**

Run: `cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis && python -c "from parallel.agents import *; print('import OK')"`
Expected: import OK，无报错

---

### Task 7: 重构 scan_data_inventory_v2.py — 消除硬编码路径

**Files:**
- Modify: `scripts/scan_data_inventory_v2.py:22`

**现状：**
```python
STOCK_DATA_ROOT = Path.home() / "quant-data/tushare/股票数据"
```

- [ ] **Step 1: 替换为从 common 导入**

```python
from common import STOCK_DATA_ROOT
```

删除原来的硬编码定义。

- [ ] **Step 2: 验证**

Run: `cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis && python -c "import scan_data_inventory_v2; print('OK')"`
Expected: OK，无报错

---

### Task 8: 重构 data_access.py — 移除冗余 NEWS_ROOT 硬编码

**Files:**
- Modify: `scripts/data/data_access.py:537`

**现状：**
```python
NEWS_ROOT = Path("/Users/penghongming/quant-data/tushare/消息面数据")
```

- [ ] **Step 1: 替换为从 common 导入**

```python
from common import NEWS_DATA_ROOT

NEWS_ROOT = NEWS_DATA_ROOT
```

- [ ] **Step 2: 验证**

Run: `cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis && python -c "from data.data_access import NEWS_ROOT; print(NEWS_ROOT)"`
Expected: 输出路径，无报错

---

### Task 9: 全量验证 — 运行完整分析确认无回归

- [ ] **Step 1: 运行完整京东方分析**

Run: `cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis && python quick_analyze.py 京东方A --no-open 2>&1 | head -30`
Expected: 正常输出报告，无 ImportError / FileNotFoundError

- [ ] **Step 2: 检查报告生成**

Run: `ls -la references/pending-validations/ | tail -3`
Expected: 看到最新的 parquet 文件

---

### Task 10: 提交代码

- [ ] **Step 1: 查看变更**

Run: `cd /Users/penghongming/agent-skills/custom/market-analyzer-meta && git status`

- [ ] **Step 2: 提交**

Run: `git add -A && git commit -m "refactor: 统一数据路径引用，消除 6 处硬编码 - common.py 作为唯一路径入口"`

---

## Self-Review

1. **Spec coverage:** 所有 6 个硬编码路径文件均已覆盖（dataslicer, fundamental_provider, data_provider, decision_engine, parallel/agents, scan_data_inventory_v2）+ data_access.py 的 NEWS_ROOT
2. **Placeholder scan:** 无 TBD/TODO
3. **Type consistency:** 所有文件使用相同的 `STOCK_DATA_ROOT` / `INDEX_DATA_ROOT` / `NEWS_DATA_ROOT` / `FINANCIAL_DATA_ROOT` 常量名
4. **Baseline clarity:** 完成标准 = 代码中无 `quant-data/tushare` 硬编码 + 完整分析正常运行
