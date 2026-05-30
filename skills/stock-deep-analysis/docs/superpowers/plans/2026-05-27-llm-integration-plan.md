# LLM 集成改造实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 stock-deep-analysis 中三处硬编码规则判定替换为 LLM 推理,同时统一数据读取层为 parquet + tushare_pro 路径

**Architecture:** 新增 `data_provider.py` 统一 parquet 读取 + `llm_client.py` 统一 LLM 调用 → 各分析模块只保留 ETL,定性判断走 `llm_judge()`

**Tech Stack:** Python 3.11, pandas, requests, Hermes/kimi-k2.6

**Frozen Baseline:**
- 范围: data_provider / llm_client / sector_analyzer(3函数) / decision_engine / dragon_tiger / pre_collect_data(CSV→parquet) / market_analyzer(index) / stock_trend_analyzer(timeseries)
- 不做: research脚本 / test脚本 / 分钟线 / market_analyzer量化计算
- 验收: `quick_analyze.sh 000725.SZ 2026-05-26` 板块分析不再只有"元器件"

---

### Task 1: llm_client.py — 统一 LLM 调用

**Files:**
- Create: `scripts/llm/__init__.py`
- Create: `scripts/llm/llm_client.py`

- [ ] **Step 1: Create package init**

```python
# scripts/llm/__init__.py
```

- [ ] **Step 2: Implement llm_client.py**

```python
import json
import os
import requests

_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8320/v1")
_API_KEY = os.getenv("LLM_API_KEY", "")
_MODEL = os.getenv("LLM_MODEL", "kimi-k2.6")


def llm_judge(task: str, context: dict, temperature: float = 0.3) -> dict:
    resp = requests.post(
        f"{_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {_API_KEY}"},
        json={
            "model": _MODEL,
            "messages": [
                {"role": "system", "content": task},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            "temperature": temperature,
        },
        timeout=30,
    )
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return json.loads(content)
```

- [ ] **Step 3: Verify it works**

Run: `python3 -c "from scripts.llm.llm_client import llm_judge; print(llm_judge('返回JSON: {\"result\": \"ok\"}', {'test': True}, temperature=0))"`
Expected: prints a dict like `{'result': 'ok'}`

---

### Task 2: data_provider.py — 统一 parquet 读取

**Files:**
- Create: `scripts/data/data_provider.py`
- Modify: `scripts/data/data_access.py` (将 CSV 读取委托给 data_provider)

- [ ] **Step 1: Implement core data provider**

```python
# scripts/data/data_provider.py
# 统一 parquet 读取接口,按 tushare_pro 实际目录结构
# 不兜底 CSV 回退,只读 parquet;缺失则返回 None

from pathlib import Path
from typing import Any
import pandas as pd

_STOCK_ROOT = Path("/Users/penghongming/quant-data/tushare/股票数据")
_INDEX_ROOT = Path("/Users/penghongming/quant-data/tushare/指数数据")

def _read_one(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(path)
    except Exception:
        return None

def get_daily(symbol: str, trade_date: str) -> dict[str, Any] | None:
    df = _read_one(_STOCK_ROOT / "daily" / f"{symbol}.parquet")
    if df is None:
        return None
    row = df[df["trade_date"] == trade_date]
    return row.iloc[0].to_dict() if not row.empty else None

def get_daily_rows(symbol: str, trade_date: str, limit: int = 10) -> list[dict[str, Any]]:
    df = _read_one(_STOCK_ROOT / "daily" / f"{symbol}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    rows = df[mask].sort_values("trade_date").tail(limit)
    return rows.to_dict("records") if not rows.empty else []

def get_daily_basic(symbol: str, trade_date: str) -> dict[str, Any] | None:
    df = _read_one(_STOCK_ROOT / "daily_basic" / f"{symbol}.parquet")
    if df is None:
        return None
    row = df[df["trade_date"] == trade_date]
    return row.iloc[0].to_dict() if not row.empty else None

def get_index_daily(index_code: str, trade_date: str) -> dict[str, Any] | None:
    df = _read_one(_INDEX_ROOT / "index_daily" / f"{index_code}.parquet")
    if df is None:
        return None
    row = df[df["trade_date"] == trade_date]
    return row.iloc[0].to_dict() if not row.empty else None

def get_index_daily_rows(index_code: str, trade_date: str, limit: int = 30) -> list[dict[str, Any]]:
    df = _read_one(_INDEX_ROOT / "index_daily" / f"{index_code}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    rows = df[mask].sort_values("trade_date").tail(limit)
    return rows.to_dict("records") if not rows.empty else []

def get_chips(symbol: str, trade_date: str) -> list[dict[str, Any]]:
    df = _read_one(_STOCK_ROOT / "cyq_chips" / f"{symbol}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    return df[mask].sort_values("trade_date").tail(10).to_dict("records")

def get_chips_perf(symbol: str, trade_date: str) -> list[dict[str, Any]]:
    df = _read_one(_STOCK_ROOT / "cyq_perf" / f"{symbol}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    return df[mask].sort_values("trade_date").tail(5).to_dict("records")

def get_factors(symbol: str, trade_date: str) -> dict[str, Any] | None:
    df = _read_one(_STOCK_ROOT / "stk_factor_pro" / f"{symbol}.parquet")
    if df is None:
        return None
    mask = df["trade_date"] <= trade_date
    rows = df[mask].sort_values("trade_date")
    if rows.empty:
        return None
    return rows.iloc[-1].to_dict()

def get_weekly(symbol: str, trade_date: str) -> list[dict[str, Any]]:
    df = _read_one(_STOCK_ROOT / "weekly" / f"{symbol}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    return df[mask].sort_values("trade_date").tail(20).to_dict("records")

def get_monthly(symbol: str, trade_date: str) -> list[dict[str, Any]]:
    df = _read_one(_STOCK_ROOT / "monthly" / f"{symbol}.parquet")
    if df is None:
        return []
    mask = df["trade_date"] <= trade_date
    return df[mask].sort_values("trade_date").tail(12).to_dict("records")

def get_stock_concepts(symbol: str) -> list[str]:
    """从 KPL concept 年 parquet 查股票所属题材"""
    for year_pq in sorted(_STOCK_ROOT.glob("theme_data/kpl_concept_cons/20*.parquet"), reverse=True):
        df = _read_one(year_pq)
        if df is None or "con_code" not in df.columns:
            continue
        match = df[df["con_code"] == symbol]
        if not match.empty and "name" in match.columns:
            return match["name"].dropna().unique().tolist()
    return []

def get_theme_constituents(symbol: str, trade_date: str) -> list[dict[str, Any]]:
    """从 dc_concept 年 parquet 查股票所属概念"""
    for year_pq in sorted(_STOCK_ROOT.glob("theme_data/dc_concept_cons/20*.parquet"), reverse=True):
        df = _read_one(year_pq)
        if df is None:
            continue
        col = "ts_code" if "ts_code" in df.columns else "con_code"
        match = df[df[col] == symbol]
        if not match.empty:
            return match.to_dict("records")
    return []

def get_stock_basic(symbol: str) -> dict[str, Any] | None:
    df = _read_one(_STOCK_ROOT / "stock_basic" / f"{symbol}.parquet")
    if df is None:
        # 兜底 stock_basic_all.csv (元数据,非交易数据)
        import csv
        path = _STOCK_ROOT / "stock_basic" / "stock_basic_all.csv"
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("ts_code") == symbol:
                    return row
        return None
    return df.iloc[0].to_dict() if not df.empty else None
```

- [ ] **Step 2: Verify stock_basic parquet exists**

Run: `python3 -c "from scripts.data.data_provider import get_stock_basic; print(get_stock_basic('000725.SZ'))"`
Expected: dict with stock info

- [ ] **Step 3: Verify daily parquet reading**

Run: `python3 -c "from scripts.data.data_provider import get_daily; print(get_daily('000725.SZ', '20260526'))"`
Expected: dict with daily OHLCV data or None (if daily parquet missing)

---

### Task 3: pre_collect_data.py — 修复路径 + 改用 data_provider

**Files:**
- Modify: `scripts/fetchers/pre_collect_data.py`

- [ ] **Step 1: Replace fetch_local_daily()**

Replace the CSV-based daily fetching with data_provider:

```python
def fetch_local_daily(ts_code: str) -> dict:
    from data.data_provider import get_daily_rows
    rows = get_daily_rows(ts_code, datetime.now().strftime("%Y%m%d"), limit=10)
    if not rows:
        return {"status": "missing", "rows": [], "latest_date": None}
    latest = max(r.get("trade_date", "") for r in rows)
    return {"status": "available", "rows": rows, "latest_date": latest}
```

- [ ] **Step 2: Replace fetch_local_factors()**

```python
def fetch_local_factors(ts_code: str) -> dict:
    from data.data_provider import get_factors
    factor = get_factors(ts_code, datetime.now().strftime("%Y%m%d"))
    if not factor:
        return {"status": "missing", "latest": None}
    return {"status": "available", "latest": factor, "latest_date": factor.get("trade_date", "")}
```

- [ ] **Step 3: Replace fetch_local_chips()**

```python
def fetch_local_chips(ts_code: str) -> dict:
    from data.data_provider import get_chips
    rows = get_chips(ts_code, datetime.now().strftime("%Y%m%d"))
    if not rows:
        return {"status": "missing", "rows": [], "latest_date": None}
    latest = max(r.get("trade_date", "") for r in rows)
    return {"status": "available", "rows": rows[:10], "latest_date": latest}
```

- [ ] **Step 4: Replace fetch_local_daily_basic()**

```python
def fetch_local_daily_basic(ts_code: str) -> dict:
    from data.data_provider import get_daily_basic
    basic = get_daily_basic(ts_code, datetime.now().strftime("%Y%m%d"))
    if not basic:
        return {"status": "missing", "latest": None}
    return {"status": "available", "latest": basic, "latest_date": basic.get("trade_date", "")}
```

- [ ] **Step 5: Replace fetch_local_moneyflow()**

```python
def fetch_local_moneyflow(ts_code: str, end_date: str = "") -> dict:
    # 资金流向数据路径: moneyflow_data/individual/tushare/{ts_code}.parquet
    from data.data_provider import _STOCK_ROOT
    import pandas as pd
    path = _STOCK_ROOT / "moneyflow_data" / "individual" / "tushare" / f"{ts_code}.parquet"
    try:
        df = pd.read_parquet(path)
    except Exception:
        return {"status": "missing", "rows": [], "latest_date": None}
    if end_date:
        end_compact = end_date.replace("-", "")
        df = df[df["trade_date"] <= end_compact]
    if df.empty:
        return {"status": "missing", "rows": [], "latest_date": None}
    latest = df["trade_date"].max()
    rows = df.sort_values("trade_date").tail(5).to_dict("records")
    return {"status": "available", "rows": rows, "latest_date": str(latest)}
```

- [ ] **Step 6: Rewrite fetch_industry_concept() — 走 data_provider 查概念**

Replace the entire function:

```python
def fetch_industry_concept(ts_code: str) -> dict:
    from data.data_provider import get_stock_basic
    import pandas as pd
    from data.data_provider import _STOCK_ROOT

    basic = get_stock_basic(ts_code)
    industries = [basic.get("industry")] if basic and basic.get("industry") else []
    stock_name = basic.get("name") if basic else None

    concepts = []
    # 从 kpl_concept_cons 按年 parquet 查
    for year_pq in sorted(_STOCK_ROOT.glob("theme_data/kpl_concept_cons/20*.parquet"), reverse=True):
        try:
            df = pd.read_parquet(year_pq)
            match = df[df["con_code"] == ts_code]
            if not match.empty:
                concepts = match["name"].dropna().unique().tolist()
                break
        except Exception:
            continue

    if not concepts:
        # 从 dc_concept_cons 按年 parquet 查
        for year_pq in sorted(_STOCK_ROOT.glob("theme_data/dc_concept_cons/20*.parquet"), reverse=True):
            try:
                df = pd.read_parquet(year_pq)
                match = df[df["con_code"] == ts_code] if "con_code" in df.columns else df[df["ts_code"] == ts_code]
                if not match.empty:
                    col = "industry" if "industry" in match.columns else "name"
                    concepts = match[col].dropna().unique().tolist()
                    break
            except Exception:
                continue

    if industries or concepts:
        return {"status": "available", "industry": industries, "concept": concepts}
    return {"status": "missing", "industry": [], "concept": []}
```

- [ ] **Step 7: Remove unused CSV helpers and imports**

Remove `read_csv_robust()` function and `import csv` from pre_collect_data.py if no longer used.

- [ ] **Step 8: Verify the fix**

Run: `python3 scripts/fetchers/pre_collect_data.py --symbol 000725.SZ --date 2026-05-26 2>/dev/null | tail -1`
Then check the JSON output has non-empty `industry_concept.concept`.

---

### Task 4: market_analyzer.py — CSV→parquet

**Files:**
- Modify: `scripts/analysis/market_analyzer.py`

- [ ] **Step 1: Replace load_index_row() and load_index_rows()**

Replace CSV reading with data_provider:

```python
def load_index_row(index_code: str, trade_date_text: str) -> dict[str, Any] | None:
    from data.data_provider import get_index_daily
    compact = trade_date_text.replace("-", "")
    return get_index_daily(index_code, compact)

def load_index_rows(index_code: str, trade_date_text: str, limit: int = 30) -> list[dict[str, Any]]:
    from data.data_provider import get_index_daily_rows
    compact = trade_date_text.replace("-", "")
    return get_index_daily_rows(index_code, compact, limit)
```

- [ ] **Step 2: Verify**

Run: `python3 -c "from scripts.analysis.market_analyzer import load_index_row; print(load_index_row('000001.SH', '2026-05-26'))"`
Expected: dict with index data

---

### Task 5: stock_trend_analyzer.py — CSV→parquet

**Files:**
- Modify: `scripts/analysis/stock_trend_analyzer.py`

- [ ] **Step 1: Add data_provider import and replace load_timeseries_rows() for each data type**

Replace the CSV-based `load_timeseries_rows()` usage:

In `analyze_trend_structure()`:
```python
def analyze_trend_structure(full_symbol: str, trade_date_text: str) -> dict[str, Any]:
    from data.data_provider import get_weekly, get_monthly, get_daily
    td = trade_date_text.replace("-", "")
    w_rows = get_weekly(full_symbol, td)
    m_rows = get_monthly(full_symbol, td)
    d_row = get_daily(full_symbol, td)
    # ... rest unchanged (still uses safe_float, rolling_mean, etc.)
```

In `analyze_chip_structure()`:
```python
def analyze_chip_structure(full_symbol: str, trade_date_text: str) -> dict[str, Any]:
    from data.data_provider import get_chips, get_chips_perf, get_daily
    td = trade_date_text.replace("-", "")
    d_row = get_daily(full_symbol, td)
    perf_rows = get_chips_perf(full_symbol, td)
    chips_rows = get_chips(full_symbol, td)
    # ... rest unchanged
```

In `analyze_volatility_context()`:
```python
def analyze_volatility_context(full_symbol: str, trade_date_text: str) -> dict[str, Any]:
    from data.data_provider import get_factors
    td = trade_date_text.replace("-", "")
    rows_df = get_factors(full_symbol, td)
    # Note: need multi-row, not single. Wrap get_factors in a multi-row version or iterate.
```

- [ ] **Step 2: Keep safe_float/rolling_mean — pure calculation stays**

- [ ] **Step 3: Verify**

Run: `python3 -c "from scripts.analysis.stock_trend_analyzer import analyze_trend_structure; print(analyze_trend_structure('000725.SZ', '2026-05-26'))"`
Expected: dict with week_state/month_state

---

### Task 6: sector_analyzer.py — 3 函数改为 ETL + LLM

**Files:**
- Modify: `scripts/analysis/sector_analyzer.py`

- [ ] **Step 1: Replace infer_sector_cycle_status()**

```python
def infer_sector_cycle_status(concept_name: str | None, trade_date: str | None,
                               kpl_concepts: list[dict],
                               theme_leader_name: str | None = None) -> dict[str, Any]:
    if not concept_name or not trade_date:
        return {'status': 'insufficient_data', 'cycle': None, 'confidence': 0, 'signals': []}

    # ETL: compute stats
    total = len(kpl_concepts)
    hots = sorted([int(r.get('hot_num') or 0) for r in kpl_concepts], reverse=True)
    top3_concentration = sum(hots[:3]) / sum(hots) if hots and sum(hots) > 0 else 0

    context = {
        "concept_name": concept_name,
        "total_constituents": total,
        "top3_concentration": round(top3_concentration, 3),
        "leader_name": theme_leader_name,
        "top_hot_values": hots[:5],
    }

    from llm.llm_client import llm_judge
    result = llm_judge(SECTOR_CYCLE_TASK, context)
    status = 'analyzed' if result.get('cycle') else 'insufficient_data'
    return {'status': status, 'cycle': result.get('cycle'),
            'confidence': result.get('confidence', 0),
            'signals': [result.get('reasoning', '')] if result.get('reasoning') else []}
```

- [ ] **Step 2: Replace analyze_theme_trend()**

```python
def analyze_theme_trend(concept_name: str | None, trade_date: str | None) -> dict[str, Any]:
    if not concept_name or not trade_date:
        return {'status': 'insufficient_data', 'trend': None, 'confidence': 0, 'signals': []}

    # ETL: find 2 most recent KPL files
    kpl_root = THEME_DATA_ROOT / 'kpl_concept_cons'
    kpl_files = sorted(kpl_root.glob('kpl_concept_cons_*.csv'), reverse=True)[:2]
    current_hot = past_hot = None
    for idx, kpl_file in enumerate(kpl_files):
        file_date = kpl_file.stem.rsplit('_', 1)[-1]
        if file_date > trade_date.replace('-', ''):
            continue
        for row in csv.DictReader(kpl_file.open('r', encoding='utf-8-sig', newline='')):
            if str(row.get('name') or '').strip() == concept_name:
                hot = int(row.get('hot_num') or 0)
                if idx == 0:
                    current_hot = hot
                elif idx == 1:
                    past_hot = hot
                break

    context = {
        "concept_name": concept_name,
        "current_hot": current_hot,
        "past_hot": past_hot,
        "hot_change_pct": round((current_hot - past_hot) / past_hot * 100, 1) if current_hot and past_hot and past_hot > 0 else None,
    }

    from llm.llm_client import llm_judge
    result = llm_judge(SECTOR_TREND_TASK, context)
    status = 'analyzed' if result.get('trend') else 'insufficient_data'
    return {'status': status, 'trend': result.get('trend'),
            'current_hot': current_hot, 'past_hot': past_hot,
            'confidence': result.get('confidence', 0),
            'signals': result.get('signals', [])}
```

- [ ] **Step 3: Replace infer_theme_progression()**

Keep ETL lines 184-221 (读取 KPL 文件、计算 theme_hots、current_hot、candidates 列表),replace lines 222-233 (硬编码阈值判定 next_theme 和置信度) with llm_judge:

```python
def infer_theme_progression(current_theme: str | None, trade_date: str | None) -> dict[str, Any]:
    if not current_theme or not trade_date:
        return {'status': 'insufficient_data', 'next_theme': None, 'confidence': 0, 'reasoning': []}

    kpl_root = THEME_DATA_ROOT / 'kpl_concept_cons'
    kpl_files = sorted(kpl_root.glob('kpl_concept_cons_*.csv'), reverse=True)[:10]
    theme_hots: dict[str, list[tuple[str, int]]] = {}
    for kpl_file in kpl_files:
        file_date = kpl_file.stem.rsplit('_', 1)[-1]
        if not file_date.isdigit() or file_date > trade_date.replace('-', ''):
            continue
        try:
            with kpl_file.open('r', encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    theme = str(row.get('name') or '').strip()
                    if not theme: continue
                    hot = int(row.get('hot_num') or 0)
                    theme_hots.setdefault(theme, []).append((file_date, hot))
        except Exception:
            continue

    current_hot = 0
    if current_theme in theme_hots:
        sorted_hots = sorted(theme_hots[current_theme], key=lambda x: x[0], reverse=True)
        if sorted_hots:
            current_hot = sorted_hots[0][1]

    candidates = []
    for theme, hots in theme_hots.items():
        if theme == current_theme: continue
        sorted_hots = sorted(hots, key=lambda x: x[0], reverse=True)
        if not sorted_hots: continue
        recent_hot = sorted_hots[0][1]
        past_hot = sorted_hots[1][1] if len(sorted_hots) > 1 else 0
        if past_hot > 0:
            change = (recent_hot - past_hot) / past_hot * 100
            if change > 10 and recent_hot > current_hot * 0.5:
                candidates.append((theme, recent_hot, change))
    candidates.sort(key=lambda x: x[1], reverse=True)

    context = {
        "current_theme": current_theme,
        "current_hot": current_hot,
        "candidates": [{"theme": c[0], "hot": c[1], "change_pct": round(c[2], 1)} for c in candidates[:5]],
    }

    from llm.llm_client import llm_judge
    result = llm_judge(SECTOR_PROGRESSION_TASK, context)
    return {'status': 'analyzed' if result.get('next_theme') else 'insufficient_data',
            'current_theme': current_theme,
            'next_theme': result.get('next_theme'),
            'confidence': result.get('confidence', 0),
            'candidates': candidates[:3],
            'reasoning': [result.get('reasoning', '')] if result.get('reasoning') else []}
```

- [ ] **Step 4: Remove unused imports** (remove `csv`, `hashlib`, `json`, `os`, `re`, `subprocess` if no longer needed)

- [ ] **Step 5: Verify**

Run: `python3 -c "from scripts.analysis.sector_analyzer import infer_sector_cycle_status; print(infer_sector_cycle_status('固态电池', '20260526', []))"`
Expected: dict with cycle field from LLM

---

### Task 7: decision_engine.py — ETL + LLM 裁决

**Files:**
- Modify: `scripts/decision/decision_engine.py`

- [ ] **Step 1: Add DECISION_TASK constant and llm_judge import**

```python
DECISION_TASK = """你是一个A股交易决策引擎。基于以下各维度分析摘要,给出综合裁决。
返回 JSON:
{
  "decision": "适合轻仓试仓"|"仅适合观察"|"观察确认"|"暂不适合建仓",
  "bullish_dimensions": ["偏多方面1", ...],
  "bearish_dimensions": ["偏空方面1", ...],
  "conflicts": ["矛盾项1", ...],
  "preconditions": ["放量站稳XX", ...],
  "invalidations": ["跌破XX且回抽无力", ...],
  "key_levels": {"observe": 数值, "confirm": 数值, "invalid": 数值},
  "reasoning": "综合推理过程"
}"""
```

- [ ] **Step 2: Add extract_decision_context() — 打包各维度摘要**

```python
def extract_decision_context(payload: dict[str, Any]) -> dict[str, Any]:
    ctx = {}

    intraday = payload.get('intraday_strength') or {}
    if intraday.get('status') == 'available':
        r = intraday.get('result') or {}
        ctx['intraday'] = {'score': r.get('score'), 'label': r.get('label'), 'view': r.get('afternoon_view')}

    next_day = payload.get('next_day_bias') or {}
    if next_day.get('status') == 'available':
        r = next_day.get('result') or {}
        ctx['next_day'] = {'score': r.get('score'), 'label': r.get('label'), 'view': r.get('next_day_view')}

    capital = payload.get('capital_freshness') or {}
    ctx['capital'] = {'label': capital.get('label')}

    news = payload.get('news_sentiment') or {}
    if news.get('status') == 'available':
        ctx['news'] = {'direction': news.get('direction'), 'level': news.get('level'), 'is_new': news.get('is_new_catalyst')}

    peer = (payload.get('dimension_results') or {}).get('peer_linkage') or {}
    if peer.get('status') == 'available':
        ctx['peer'] = {'target_position': peer.get('target_position'), 'peer_count': peer.get('peer_count')}

    auction = (payload.get('dimension_results') or {}).get('auction_intent') or {}
    ctx['auction'] = {'intent': auction.get('overall_intent'), 'score': auction.get('score')}

    trend = (payload.get('dimension_results') or {}).get('trend_structure') or {}
    if trend.get('status') == 'available':
        ctx['trend_structure'] = {'score': trend.get('score'), 'summary': trend.get('summary')}

    chip = (payload.get('dimension_results') or {}).get('chip_structure') or {}
    if chip.get('status') == 'available':
        ctx['chip'] = {'score': chip.get('score'), 'summary': chip.get('summary')}

    dt = (payload.get('dimension_results') or {}).get('dragon_tiger') or {}
    ctx['dragon_tiger'] = {'signal': dt.get('signal'), 'score': dt.get('overall_score')}

    market = payload.get('market_context') or {}
    ctx['market'] = {'bias': market.get('market_bias'), 'style': market.get('size_style')}

    sector = payload.get('sector_context') or {}
    ctx['sector'] = {'summary': sector.get('summary'), 'cycle': (sector.get('theme_cycle') or {}).get('cycle')}

    ctx['context_propagation'] = payload.get('context_propagation', {}).get('action_bias')

    return ctx
```

- [ ] **Step 3: Refactor build_final_decision()**

Replace signal_score weighting loop with:

```python
def build_final_decision(payload: dict[str, Any]) -> dict[str, Any]:
    data_score = compute_data_score(payload)  # keep as-is
    context = extract_decision_context(payload)

    from llm.llm_client import llm_judge
    llm_result = llm_judge(DECISION_TASK, context)

    return {
        **llm_result,
        'data_completeness': data_score,
        'source': 'llm+script',
        'status': 'ready',
    }
```

- [ ] **Step 4: Extract compute_data_score() — 从原有 build_final_decision() 中提取**

```python
def compute_data_score(payload: dict[str, Any]) -> int:
    freshness = payload.get('freshness', {}).get('summary', {})
    missing = freshness.get("missing", [])
    stale = freshness.get("stale", [])
    _dq = cfg.decision("data_quality", default={})
    score = 100 - len(missing) * _dq.get("missing_penalty", 8) - len(stale) * _dq.get("stale_penalty", 5)

    intraday = payload.get('intraday_strength') or {}
    next_day = payload.get('next_day_bias') or {}
    peer = (payload.get('dimension_results') or {}).get('peer_linkage') or {}
    news = payload.get('news_sentiment') or {}

    if intraday.get('status') != 'available': score -= _dq.get("intraday_unavailable", 15)
    if next_day.get('status') != 'available': score -= _dq.get("next_day_unavailable", 15)
    if peer.get('status') != 'available': score -= _dq.get("peer_linkage_unavailable", 12)
    if news.get('status') != 'available': score -= _dq.get("news_unavailable", 10)

    return max(_dq.get("min_score", 20), min(_dq.get("max_score", 98), score))
```

- [ ] **Step 5: Verify**

Run: `python3 -c "from scripts.decision.decision_engine import compute_data_score; print(compute_data_score({'freshness': {'summary': {'missing': [], 'stale': []}}}))"`
Expected: an integer score

---

### Task 8: dragon_tiger_analyst.py — 自动化 LLM 调用

**Files:**
- Modify: `scripts/agents/dragon_tiger_analyst.py`

- [ ] **Step 1: Replace lines 194-213 with llm_judge call**

```python
    if args.fallback:
        md_content, json_summary = generate_fallback_output(summary)
    else:
        from llm.llm_client import llm_judge
        result = llm_judge(DT_TASK, summary)
        md_content = format_dt_markdown(summary, result)
        json_summary = {**summary, 'llm_analysis': result}
```

- [ ] **Step 2: Add format_dt_markdown() helper**

```python
def format_dt_markdown(summary: dict, llm_result: dict) -> str:
    lines = [f"# 龙虎榜分析: {summary.get('symbol', 'unknown')}"]
    lines.append(f"信号: {llm_result.get('signal', 'N/A')}")
    lines.append(f"评分: {llm_result.get('overall_score', 'N/A')}/10")
    lines.append(f"置信度: {llm_result.get('confidence', 0)}%")
    lines.append(f"推理: {llm_result.get('reasoning', '')}")
    return "\n".join(lines)
```

- [ ] **Step 3: Remove temporary file writing and sys.exit(0)**

Remove `output_dir.mkdir()`, `prompt_path.write_text()`, `print("请手动传给 LLM")`, `sys.exit(0)`

- [ ] **Step 4: Verify**

Run: `python3 scripts/agents/dragon_tiger_analyst.py --input /path/to/summary.json --output-dir /tmp/dt_test --fallback`
Expected: fallback output written to disk (no LLM call since fallback)

---

### Task 9: 端到端验证

- [ ] **Step 1: Run full quick_analyze**

Run: `bash scripts/quick_analyze.sh 000725.SZ 2026-05-26`
Expected: runs without error

- [ ] **Step 2: Check sector analysis in output**

Read the generated report and verify `行业与概念` section now contains concept names (not just "元器件")

- [ ] **Step 3: Run hermes API test**

Run: `python3 -c "from scripts.llm.llm_client import llm_judge; print(llm_judge('返回JSON: {\"r\":1}', {'test': 1}, temperature=0))"`
Expected: llama returns valid JSON
