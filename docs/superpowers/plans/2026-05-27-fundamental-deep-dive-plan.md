# 基本面深度背调 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增基本面深度分析 Phase 2 Agent，基于财务数据 parquet 的 4 层 ETL + LLM 定性

**Architecture:** Phase 2 第 8 号并行 Agent `run_fundamental_agent`。新增 `fundamental_provider.py` 读取财务数据 parquet，ETL 产出结构化指标后 `llm_judge(FUNDAMENTAL_TASK)` 定性。结果进 `dimension_results["fundamental_deep"]`，消费方为 `build_final_decision` LLM context + Markdown 报告。

**Tech Stack:** Python 3.11+, pandas (parquet 读取), Hermes proxy (LLM)

**Frozen Baseline:**
- 范围: fundamental_provider / fundamental.py (ETL) / parallel/agents.py (新 agent) / build_stock_report (注册+聚合)
- 不做: 不改 `_build_fundamental()`、不注入 `context_propagation_rules`、不做产业链自动推断、不做行业对比
- 验收: `run_fundamental_agent('000725', '2026-05-22')` 返回 `{financial_health, trend_label, narrative}`，对只有 express 的股票也能出 tier 1 分析

---

### Task 1: fundamental_provider.py — 财务数据 parquet 读取

**Files:**
- Create: `scripts/data/fundamental_provider.py`

- [ ] **Step 1: Implement parquet read functions**

```python
"""从财务数据 parquet 读取基本面数据。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from data.data_access import FINANCIAL_DATA_ROOT, STOCK_DATA_ROOT


def _read_parquet(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        df = pd.read_parquet(path)
        return df.to_dict(orient="records")
    except Exception:
        return []


def get_fundamental_express(symbol: str) -> list[dict]:
    """业绩快报 (覆盖最广, ~1157只)"""
    for path in sorted(FINANCIAL_DATA_ROOT.glob("express/express_*.parquet"), reverse=True):
        rows = _read_parquet(path)
        matched = [r for r in rows if r.get("ts_code", "").startswith(symbol)]
        if matched:
            return matched
    return []


def get_fundamental_indicator(symbol: str) -> list[dict]:
    """财务指标 (~26只)"""
    for path in sorted(FINANCIAL_DATA_ROOT.glob("fina_indicator/fina_indicator_*.parquet"), reverse=True):
        rows = _read_parquet(path)
        matched = [r for r in rows if r.get("ts_code", "").startswith(symbol)]
        if matched:
            return matched
    return []


def get_fundamental_income(symbol: str) -> list[dict]:
    """利润表 (~26只)"""
    for path in sorted(FINANCIAL_DATA_ROOT.glob("income/income_*.parquet"), reverse=True):
        rows = _read_parquet(path)
        matched = [r for r in rows if r.get("ts_code", "").startswith(symbol)]
        if matched:
            return matched
    return []


def get_fundamental_balancesheet(symbol: str) -> list[dict]:
    """资产负债表 (~63只)"""
    for path in sorted(FINANCIAL_DATA_ROOT.glob("balancesheet/balancesheet_*.parquet"), reverse=True):
        rows = _read_parquet(path)
        matched = [r for r in rows if r.get("ts_code", "").startswith(symbol)]
        if matched:
            return matched
    return []


def get_fundamental_cashflow(symbol: str) -> list[dict]:
    """现金流量表 (~27只)"""
    for path in sorted(FINANCIAL_DATA_ROOT.glob("cashflow/cashflow_*.parquet"), reverse=True):
        rows = _read_parquet(path)
        matched = [r for r in rows if r.get("ts_code", "").startswith(symbol)]
        if matched:
            return matched
    return []


def get_fundamental_mainbz(symbol: str) -> list[dict]:
    """主营业务构成 (~30只, 2025年)"""
    for path in sorted(FINANCIAL_DATA_ROOT.glob("fina_mainbz/*.parquet"), reverse=True):
        rows = _read_parquet(path)
        matched = [r for r in rows if r.get("ts_code", "").startswith(symbol)]
        if matched:
            return matched
    return []


def get_top10_holders(symbol: str) -> list[dict]:
    """前十大股东"""
    # 格式: 股票数据/top10_holders/{symbol}.parquet 或 {full_symbol}.parquet
    for suffix in (f"{symbol}.parquet",):
        for root in [STOCK_DATA_ROOT / "top10_holders"]:
            path = root / suffix
            rows = _read_parquet(path)
            if rows:
                return rows
    return []


def get_top10_floatholders(symbol: str) -> list[dict]:
    """前十大流通股东"""
    for suffix in (f"{symbol}.parquet",):
        for root in [STOCK_DATA_ROOT / "top10_floatholders"]:
            path = root / suffix
            rows = _read_parquet(path)
            if rows:
                return rows
    return []
```

- [ ] **Step 2: Verify with real data**

Run: `python3 -c "
import sys; sys.path.insert(0, 'scripts')
from data.fundamental_provider import get_fundamental_express, get_fundamental_income, get_top10_holders
e = get_fundamental_express('000725.SZ')
print(f'express: {len(e)} rows')
if e: print(f'  latest: revenue={e[0].get(\"revenue\")}, n_income={e[0].get(\"n_income\")}, roe={e[0].get(\"diluted_roe\")}')
i = get_fundamental_income('000725.SZ')
print(f'income: {len(i)} rows')
t = get_top10_holders('000725.SZ')
print(f'top10_holders: {len(t)} rows')
if t: print(f'  sample: {t[0].get(\"holder_name\")} {t[0].get(\"hold_ratio\")}%')
"`

Expected: express has data, income/top10 may be empty (data coverage limited)

---

### Task 2: ETL 计算函数 (Tier 1-4)

**Note:** ETL 逻辑放在 agent 函数内部，不单独建文件（避免过度拆分）。如 ETL 逻辑超过 200 行，再抽到 `scripts/signals/fundamental_etl.py`。

**Files:**
- 内嵌在 `scripts/parallel/agents.py` 的 `run_fundamental_agent` 中

- [ ] **Step 1: 实现 Tier 1 指标计算 (express + daily_basic)**

在 `run_fundamental_agent` 中:

```python
def _calc_tier1_metrics(express_rows: list[dict], daily_basic: dict, industry: str) -> dict:
    """Tier 1: 财务趋势 + 估值 (always)"""
    if not express_rows:
        return {"tier": 1, "has_data": False}
    # 取最新一期
    latest = express_rows[0]
    revenue = safe_float(latest.get("revenue"))
    n_income = safe_float(latest.get("n_income"))
    roe = safe_float(latest.get("diluted_roe"))
    eps = safe_float(latest.get("diluted_eps"))
    yoy_net_profit = safe_float(latest.get("yoy_net_profit"))

    # 多期趋势 (取最近 3 期)
    revenues = [safe_float(r.get("revenue")) for r in express_rows[:3] if safe_float(r.get("revenue")) > 0]
    profits = [safe_float(r.get("n_income")) for r in express_rows[:3] if safe_float(r.get("n_income")) is not None]

    revenue_trend = _calc_trend(revenues) if len(revenues) >= 2 else "未知"
    profit_trend = _calc_trend(profits) if len(profits) >= 2 else "未知"

    # 估值
    pe = safe_float(daily_basic.get("pe_ttm"))
    pb = safe_float(daily_basic.get("pb"))
    total_mv = safe_float(daily_basic.get("total_mv"))

    return {
        "tier": 1, "has_data": True,
        "revenue": revenue, "n_income": n_income, "roe": roe, "eps": eps,
        "yoy_profit_growth": yoy_net_profit,
        "revenue_trend": revenue_trend, "profit_trend": profit_trend,
        "pe": pe, "pb": pb, "total_mv": total_mv,
        "industry": industry,
    }


def _calc_trend(values: list[float]) -> str:
    if len(values) < 2:
        return "未知"
    changes = [(values[i] - values[i+1]) / abs(values[i+1]) * 100 if values[i+1] != 0 else 0 for i in range(len(values)-1)]
    avg_change = sum(changes) / len(changes)
    if avg_change > 10:
        return "快速增长"
    elif avg_change > 0:
        return "温和增长"
    elif avg_change > -10:
        return "小幅下滑"
    else:
        return "大幅下滑"
```

- [ ] **Step 2: 实现 Tier 2-4 指标计算 (if data)**

```python
def _calc_tier2_metrics(income_rows: list[dict], bs_rows: list[dict], cf_rows: list[dict]) -> dict:
    if not income_rows and not bs_rows:
        return {"tier": 2, "has_data": False}
    result = {"tier": 2, "has_data": True}
    if bs_rows:
        bs = bs_rows[0]
        total_assets = safe_float(bs.get("total_assets", bs.get("total_liab_hldr_eqy")))
        total_liab = safe_float(bs.get("total_liab"))
        cur_assets = safe_float(bs.get("total_cur_assets"))
        cur_liab = safe_float(bs.get("total_cur_liab"))
        receiv = safe_float(bs.get("accounts_receiv"))
        invent = safe_float(bs.get("inventories"))
        goodwill = safe_float(bs.get("goodwill"))
        if total_assets and total_liab:
            result["debt_to_assets"] = round(total_liab / total_assets, 4)
        if cur_assets and cur_liab:
            result["current_ratio"] = round(cur_assets / cur_liab, 4)
        if receiv and total_assets:
            result["receivables_ratio"] = round(receiv / total_assets, 4)
        if invent and total_assets:
            result["inventory_ratio"] = round(invent / total_assets, 4)
        if goodwill and total_assets:
            result["goodwill_ratio"] = round(goodwill / total_assets, 4)
    if income_rows:
        inc = income_rows[0]
        revenue = safe_float(inc.get("revenue"))
        rd = safe_float(inc.get("rd_exp"))
        sell_exp = safe_float(inc.get("sell_exp"))
        impairment = safe_float(inc.get("assets_impair_loss"))
        if rd and revenue:
            result["rd_ratio"] = round(rd / revenue, 4)
        if sell_exp and revenue:
            result["sell_exp_ratio"] = round(sell_exp / revenue, 4)
        if impairment and revenue:
            result["impair_loss_ratio"] = round(impairment / revenue, 4)
    if cf_rows:
        cf = cf_rows[0]
        fcf = safe_float(cf.get("free_cashflow"))
        if fcf is not None:
            result["free_cashflow"] = fcf
    return result


def _calc_tier3_metrics(mainbz_rows: list[dict]) -> dict:
    if not mainbz_rows:
        return {"tier": 3, "has_data": False}
    result = {"tier": 3, "has_data": True}
    # 取最近一期
    latest_end = max(r.get("end_date", "") for r in mainbz_rows)
    current = [r for r in mainbz_rows if r.get("end_date") == latest_end]
    current.sort(key=lambda r: safe_float(r.get("bz_sales", 0)) or 0, reverse=True)
    if current:
        total_sales = sum(safe_float(r.get("bz_sales", 0)) or 0 for r in current)
        top = current[0]
        result["top_segment"] = top.get("bz_item", "")
        result["top_segment_ratio"] = round((safe_float(top.get("bz_sales", 0)) or 0) / total_sales, 4) if total_sales else 0
        result["segment_count"] = len(current)
        top3_ratio = sum(safe_float(r.get("bz_sales", 0)) or 0 for r in current[:3]) / total_sales if total_sales else 0
        result["diversity"] = "单一" if top3_ratio > 0.8 else "集中" if top3_ratio > 0.5 else "多元"
    return result


def _calc_tier4_metrics(holder_rows: list[dict]) -> dict:
    if not holder_rows:
        return {"tier": 4, "has_data": False}
    result = {"tier": 4, "has_data": True}
    total_ratio = sum(safe_float(r.get("hold_ratio", 0)) or 0 for r in holder_rows)
    result["top10_total_ratio"] = round(total_ratio, 4)
    if holder_rows:
        top = holder_rows[0]
        result["top1_name"] = top.get("holder_name", "")
        result["top1_ratio"] = safe_float(top.get("hold_ratio"))
    # 统计机构 (排除个人)
    inst_ratio = sum(
        safe_float(r.get("hold_ratio", 0)) or 0
        for r in holder_rows
        if r.get("holder_type") in ("基金", "QFII", "券商", "保险", "信托", "社保")
    )
    result["institution_ratio"] = round(inst_ratio, 4)
    return result
```

---

### Task 3: run_fundamental_agent — Phase 2 Agent 实现

**Files:**
- Modify: `scripts/parallel/agents.py`

- [ ] **Step 1: Add FUNDAMENTAL_TASK constant and agent function**

Add after `run_intraday_linkage_agent`:

```python
FUNDAMENTAL_TASK = """基于基本面数据，判断个股的财务健康状况和投资价值。
数据层级说明:
- Tier 1: 只有业绩快报+估值数据
- Tier 2: 有三表深度数据（利润表+资产负债表+现金流量表）
- Tier 3: 有主营业务构成数据
- Tier 4: 有前十大股东数据

返回 JSON:
{
  "financial_health": "优秀"|"良好"|"一般"|"关注"|"风险",
  "trend_label": "增长期"|"稳定期"|"下滑期"|"不确定",
  "growth_quality": "高质量增长"|"粗放增长"|"无增长"|"收缩",
  "valuation_judgment": "低估"|"合理"|"高估"|"不确定",
  "risk_flags": ["风险标签1", ...],
  "strength_flags": ["优势标签1", ...],
  "narrative": "一段总结基本面特征的话",
  "confidence": 0-1
}"""


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _calc_trend(values: list[float]) -> str:
    if len(values) < 2:
        return "未知"
    changes = [
        (values[i] - values[i+1]) / abs(values[i+1]) * 100
        if values[i+1] != 0 else 0
        for i in range(len(values) - 1)
    ]
    avg_change = sum(changes) / len(changes) if changes else 0
    if avg_change > 10:
        return "快速增长"
    elif avg_change > 0:
        return "温和增长"
    elif avg_change > -10:
        return "小幅下滑"
    else:
        return "大幅下滑"


def run_fundamental_agent(
    pure_symbol: str,
    full_symbol: str,
    trade_date_text: str,
) -> dict:
    """Phase 2 Agent 8: 基本面深度背调"""
    from scripts.data.fundamental_provider import (
        get_fundamental_express,
        get_fundamental_income,
        get_fundamental_balancesheet,
        get_fundamental_cashflow,
        get_fundamental_mainbz,
        get_fundamental_indicator,
        get_top10_holders,
    )
    from scripts.data.data_provider import get_daily_basic, get_stock_basic
    from scripts.llm.llm_client import llm_judge

    try:
        # Tier 1: express + daily_basic (always)
        express_rows = get_fundamental_express(full_symbol) or get_fundamental_express(pure_symbol)
        daily_basic = get_daily_basic(full_symbol, trade_date_text) or {}
        stock_basic = get_stock_basic(full_symbol) or {}
        industry = stock_basic.get("industry", "")

        if not express_rows and not daily_basic:
            return {"status": "no_data", "financial_health": "无数据"}

        t1 = _calc_tier1_metrics(express_rows, daily_basic, industry)
        tier_level = 1

        # Tier 2: income + balancesheet + cashflow
        income_rows = get_fundamental_income(full_symbol) or get_fundamental_income(pure_symbol)
        bs_rows = get_fundamental_balancesheet(full_symbol) or get_fundamental_balancesheet(pure_symbol)
        cf_rows = get_fundamental_cashflow(full_symbol) or get_fundamental_cashflow(pure_symbol)
        t2 = _calc_tier2_metrics(income_rows, bs_rows, cf_rows)
        if t2.get("has_data"):
            tier_level = 2

        # Tier 3: fina_mainbz
        mainbz_rows = get_fundamental_mainbz(full_symbol) or get_fundamental_mainbz(pure_symbol)
        t3 = _calc_tier3_metrics(mainbz_rows)
        if t3.get("has_data"):
            tier_level = 3

        # Tier 4: top10_holders
        holder_rows = get_top10_holders(full_symbol) or get_top10_holders(pure_symbol)
        t4 = _calc_tier4_metrics(holder_rows)
        if t4.get("has_data"):
            tier_level = 4

        # Build LLM context
        context = {"tier_level": tier_level, "industry": industry}
        context.update({k: v for k, v in t1.items() if k not in ("tier", "has_data")})
        if t2.get("has_data"):
            context.update({k: v for k, v in t2.items() if k not in ("tier", "has_data")})
        if t3.get("has_data"):
            context.update({k: v for k, v in t3.items() if k not in ("tier", "has_data")})
        if t4.get("has_data"):
            context.update({k: v for k, v in t4.items() if k not in ("tier", "has_data")})

        llm_result = llm_judge(FUNDAMENTAL_TASK, context, timeout=90)

        return {
            "status": "ok",
            "tier_level": tier_level,
            "financial_health": llm_result.get("financial_health", "未知"),
            "trend_label": llm_result.get("trend_label", "不确定"),
            "growth_quality": llm_result.get("growth_quality", ""),
            "valuation_judgment": llm_result.get("valuation_judgment", ""),
            "risk_flags": llm_result.get("risk_flags", []),
            "strength_flags": llm_result.get("strength_flags", []),
            "narrative": llm_result.get("narrative", ""),
            "confidence": llm_result.get("confidence", 0),
            "metrics": {k: v for k, v in context.items() if k != "stock_info"},
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "financial_health": "异常"}
```

Need to also add `_calc_tier1_metrics`, `_calc_tier2_metrics`, `_calc_tier3_metrics`, `_calc_tier4_metrics` as module-level functions in agents.py (before `run_fundamental_agent`).

- [ ] **Step 2: Add helper functions before the agent**

```python
def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _calc_tier1_metrics(express_rows: list[dict], daily_basic: dict, industry: str) -> dict:
    if not express_rows:
        return {"tier": 1, "has_data": False}
    latest = express_rows[0]
    revenues = [_safe_float(r.get("revenue")) for r in express_rows[:3] if _safe_float(r.get("revenue"))]
    profits = [_safe_float(r.get("n_income")) for r in express_rows[:3] if _safe_float(r.get("n_income")) is not None]
    return {
        "tier": 1, "has_data": True,
        "revenue": _safe_float(latest.get("revenue")),
        "n_income": _safe_float(latest.get("n_income")),
        "roe": _safe_float(latest.get("diluted_roe")),
        "eps": _safe_float(latest.get("diluted_eps")),
        "yoy_profit_growth": _safe_float(latest.get("yoy_net_profit")),
        "revenue_trend": _calc_trend(revenues) if len(revenues) >= 2 else "未知",
        "profit_trend": _calc_trend(profits) if len(profits) >= 2 else "未知",
        "pe": _safe_float(daily_basic.get("pe_ttm")),
        "pb": _safe_float(daily_basic.get("pb")),
        "total_mv": _safe_float(daily_basic.get("total_mv")),
        "industry": industry,
    }


def _calc_tier2_metrics(income_rows: list[dict], bs_rows: list[dict], cf_rows: list[dict]) -> dict:
    if not income_rows and not bs_rows:
        return {"tier": 2, "has_data": False}
    result: dict[str, Any] = {"tier": 2, "has_data": True}
    if bs_rows:
        bs = bs_rows[0]
        ta = _safe_float(bs.get("total_assets", bs.get("total_liab_hldr_eqy")))
        tl = _safe_float(bs.get("total_liab"))
        ca = _safe_float(bs.get("total_cur_assets"))
        cl = _safe_float(bs.get("total_cur_liab"))
        if ta and tl: result["debt_to_assets"] = round(tl / ta, 4)
        if ca and cl: result["current_ratio"] = round(ca / cl, 4)
        if ta:
            for name, key in [("receivables_ratio", "accounts_receiv"), ("inventory_ratio", "inventories"), ("goodwill_ratio", "goodwill")]:
                v = _safe_float(bs.get(key))
                if v: result[name] = round(v / ta, 4)
    if income_rows:
        inc = income_rows[0]
        rev = _safe_float(inc.get("revenue"))
        if rev:
            for name, key in [("rd_ratio", "rd_exp"), ("sell_exp_ratio", "sell_exp"), ("impair_loss_ratio", "assets_impair_loss")]:
                v = _safe_float(inc.get(key))
                if v: result[name] = round(v / rev, 4)
    if cf_rows:
        fcf = _safe_float(cf_rows[0].get("free_cashflow"))
        if fcf is not None: result["free_cashflow"] = fcf
    return result


def _calc_tier3_metrics(mainbz_rows: list[dict]) -> dict:
    if not mainbz_rows:
        return {"tier": 3, "has_data": False}
    result: dict[str, Any] = {"tier": 3, "has_data": True}
    latest_end = max(r.get("end_date", "") for r in mainbz_rows)
    current = [r for r in mainbz_rows if r.get("end_date") == latest_end]
    current.sort(key=lambda r: _safe_float(r.get("bz_sales", 0)) or 0, reverse=True)
    if current:
        total_sales = sum(_safe_float(r.get("bz_sales", 0)) or 0 for r in current)
        top = current[0]
        result["top_segment"] = top.get("bz_item", "")
        result["top_segment_ratio"] = round((_safe_float(top.get("bz_sales", 0)) or 0) / total_sales, 4) if total_sales else 0
        result["segment_count"] = len(current)
        top3_ratio = sum(_safe_float(r.get("bz_sales", 0)) or 0 for r in current[:3]) / total_sales if total_sales else 0
        result["diversity"] = "单一" if top3_ratio > 0.8 else "集中" if top3_ratio > 0.5 else "多元"
    return result


def _calc_tier4_metrics(holder_rows: list[dict]) -> dict:
    if not holder_rows:
        return {"tier": 4, "has_data": False}
    result: dict[str, Any] = {"tier": 4, "has_data": True}
    total_ratio = sum(_safe_float(r.get("hold_ratio", 0)) or 0 for r in holder_rows)
    result["top10_total_ratio"] = round(total_ratio, 4)
    top = holder_rows[0]
    result["top1_name"] = top.get("holder_name", "")
    result["top1_ratio"] = _safe_float(top.get("hold_ratio"))
    inst_ratio = sum(
        _safe_float(r.get("hold_ratio", 0)) or 0
        for r in holder_rows
        if r.get("holder_type") in ("基金", "QFII", "券商", "保险", "信托", "社保")
    )
    result["institution_ratio"] = round(inst_ratio, 4)
    return result
```

- [ ] **Step 3: Export in __init__.py**

Update `scripts/parallel/__init__.py` to export `run_fundamental_agent`.

---

### Task 4: build_stock_report.py — 注册 Agent + 聚合

**Files:**
- Modify: `scripts/build_stock_report.py`

- [ ] **Step 1: Import and register in _phase2_parallel**

Add import line after `run_intraday_linkage_agent`:
```python
    run_fundamental_agent = parallel_mod.run_fundamental_agent
```

After the intraday_linkage agent registration, add:

```python
        ParallelAgent(
            name="fundamental_deep",
            func=functools.partial(
                run_fundamental_agent,
                pure_symbol=pure_symbol,
                full_symbol=full_symbol,
                trade_date_text=trade_date_text,
            ),
            timeout=120.0,
            default_result={
                "status": "timeout",
                "financial_health": "超时",
            },
        ),
```

Update `max_workers` from 7 to 8.

- [ ] **Step 2: Aggregate in Phase 3**

After `intraday_linkage = parallel_results.get("intraday_linkage", {})`, add:

```python
    fundamental_deep = parallel_results.get("fundamental_deep", {})
```

Add to `dimension_results`:

```python
        "fundamental_deep": fundamental_deep,
```

- [ ] **Step 3: Verify import**

Run: `python3 -c "import sys; sys.path.insert(0, 'scripts'); import build_stock_report; print('import ok')"`

---

### Task 5: E2E 验证

- [ ] **Step 1: Verify all imports**

Run: `python3 -c "
import sys; sys.path.insert(0, 'scripts')
from data.fundamental_provider import get_fundamental_express, get_fundamental_income, get_top10_holders
from parallel.agents import run_fundamental_agent, FUNDAMENTAL_TASK
print('all imports OK')
"`

- [ ] **Step 2: Test Tier 1 with real data**

Run: `python3 -c "
import sys, json; sys.path.insert(0, 'scripts')
from parallel.agents import run_fundamental_agent
result = run_fundamental_agent('000725', '000725.SZ', '2026-05-22')
print(json.dumps({k:v for k,v in result.items() if k!='metrics'}, ensure_ascii=False, indent=2))
"`

Expected: returns financial_health + narrative (may be timeout depending on Hermes proxy load)

- [ ] **Step 3: Verify structural integration**

Run: `python3 -c "
import sys; sys.path.insert(0, 'scripts')
from build_stock_report import _phase2_parallel
import signal
class TE(Exception): pass
def h(s,f): raise TE()
signal.signal(signal.SIGALRM, h)
signal.alarm(120)
try:
    r = _phase2_parallel(dict(full_symbol='000725.SZ', pure_symbol='000725', trade_date_text='2026-05-22', trade_date_compact='20260522', now=__import__('datetime').datetime.now(), checkpoint='auto', news_reference_date='2026-05-22', news_json_path=None))
    signal.alarm(0)
    print(f'Phase 2 agents: {list(r.keys())}')
    assert 'fundamental_deep' in r, 'Missing fundamental_deep'
    print('OK: fundamental_deep in results')
except TE:
    print('Phase 2 timed out (non-critical)')
"`

Expected: fundamental_deep key exists in results
