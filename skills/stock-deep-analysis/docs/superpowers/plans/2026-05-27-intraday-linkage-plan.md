# 大盘/板块/个股 日内分钟级联动分析 — 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增分钟级联动分析，将大盘/板块/个股的日内走势关联起来，注入分时评分和规则链

**Architecture:** `intraday_linkage.py` (ETL指标计算) + `runtime_fetch.py` 新增采集层 → 联动指标通过 Phase 2 第7号 Agent (`run_intraday_linkage_agent`) 独立运行 → LLM 定性 → 消费方: `context_propagation_rules.py` (规则组) + `build_final_decision` (LLM context)

**Note on Score Intraday Strength:** 不注入 `score_intraday_strength.py`。原 spec/plan 中修改该文件的 Task 已移除。联动信号走 Agent + 规则链 + LLM 三条消费路径。

**Tech Stack:** Python 3.11+, pandas, Tencent API (分钟K线), Hermes browser (兜底)

**Frozen Baseline:**
- 范围: intraday_linkage / runtime_fetch / parallel/agents.py (新增 agent) / context_propagation_rules / sector_index_codes
- 不做: 分钟数据本地持久化、日线对标逻辑改造、决策引擎输出格式改造、大盘日线分析改造、score_intraday_strength 改造
- 验收: `python3 -c "from scripts.signals.intraday_linkage import score_linkage; print(score_linkage(...))"` 产出联动指标 dict

---

### Task 1: sector_index_codes.json — 板块指数代码映射表

**Files:**
- Create: `config/sector_index_codes.json`

- [ ] **Step 1: Create mapping file**

```json
{
  "固态电池": "bkbk0818",
  "LED概念": "bkbk0899",
  "AI硬件": "bkbk0999",
  "人工智能": "bkbk0800",
  "新能源汽车": "bkbk0900",
  "储能": "bkbk0910",
  "数据中心": "bkbk0920",
  "无人机": "bkbk0930",
  "商业航天": "bkbk0940",
  "消费电子概念": "bkbk0950"
}
```

初始化 10 个常用映射，后续通过 Hermes 浏览器抓取动态补充。

---

### Task 2: intraday_linkage.py — 分钟级联动分析引擎

**Files:**
- Create: `scripts/signals/intraday_linkage.py`

- [ ] **Step 1: Implement core analysis functions**

```python
"""
分钟级联动分析引擎

输入: 个股/大盘/板块 三个分钟级时间序列 (list[dict], 含 dt/close/open 字段)
输出: 结构化联动指标
"""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
import statistics


def align_series(
    stock_rows: list[dict],
    bench_rows: list[dict],
) -> tuple[list[dict], list[dict]]:
    """以个股时间轴为基准对齐两个序列"""
    stock_map = {}
    for r in stock_rows:
        dt_str = r["dt"] if isinstance(r["dt"], str) else r["dt"].strftime("%Y-%m-%d %H:%M")
        stock_map[dt_str] = r
    aligned_stock = []
    aligned_bench = []
    for r in bench_rows:
        dt_str = r["dt"] if isinstance(r["dt"], str) else r["dt"].strftime("%Y-%m-%d %H:%M")
        if dt_str in stock_map:
            aligned_stock.append(stock_map[dt_str])
            aligned_bench.append(r)
    return aligned_stock, aligned_bench


def compute_relative_strength(
    stock_rows: list[dict], bench_rows: list[dict]
) -> dict:
    if not stock_rows or not bench_rows:
        return {"final_rs": 0, "trend": "数据不足", "key_points": {}}

    s_aligned, b_aligned = align_series(stock_rows, bench_rows)
    if len(s_aligned) < 5:
        return {"final_rs": 0, "trend": "数据不足", "key_points": {}}

    s_open = s_aligned[0].get("open", s_aligned[0].get("close", 0)) or 0.001
    b_open = b_aligned[0].get("open", b_aligned[0].get("close", 0)) or 0.001
    if s_open == 0 or b_open == 0:
        return {"final_rs": 0, "trend": "数据不足", "key_points": {}}

    snapshots = ["10:00", "10:30", "11:30", "14:00", "15:00"]
    key_points = {}
    rs_values = []
    for r in s_aligned:
        s_ret = (r["close"] - s_open) / s_open * 100
        # find matching bench row by dt
        for br in b_aligned:
            br_dt = br["dt"] if isinstance(br["dt"], str) else br["dt"].strftime("%H:%M")
            r_dt = r["dt"] if isinstance(r["dt"], str) else r["dt"].strftime("%H:%M")
            if br_dt == r_dt:
                b_ret = (br["close"] - b_open) / b_open * 100
                rs_values.append({"dt": r_dt, "rs": round(s_ret - b_ret, 2)})
                break

    for snap in snapshots:
        snap_h, snap_m = snap.split(":")
        for item in rs_values:
            ih = int(item["dt"].split(":")[0]) if ":" in item["dt"] else 0
            im = int(item["dt"].split(":")[1]) if ":" in item["dt"] else 0
            if ih == int(snap_h) and im >= int(snap_m) - 1 and im <= int(snap_m) + 1:
                key_points[snap] = item["rs"]
                break

    final_rs = rs_values[-1]["rs"] if rs_values else 0
    first_half = [rs_values[i] for i in range(len(rs_values) // 2)] if rs_values else []
    second_half = [rs_values[i] for i in range(len(rs_values) // 2, len(rs_values))] if rs_values else []
    first_avg = statistics.mean([x["rs"] for x in first_half]) if first_half else 0
    second_avg = statistics.mean([x["rs"] for x in second_half]) if second_half else 0

    if final_rs > 2:
        trend = "持续走强" if first_avg > 0 else "先弱后强"
    elif final_rs < -2:
        trend = "持续走弱" if first_avg < 0 else "先强后弱"
    else:
        trend = "窄幅波动"

    return {"final_rs": round(final_rs, 2), "trend": trend, "key_points": key_points}


def detect_time_conduction(
    market_rows: list[dict], stock_rows: list[dict], threshold_pct: float = 0.5
) -> dict:
    """检测大盘极值点到个股的传导"""
    if not market_rows or not stock_rows:
        return {"follow_ratio": 0, "avg_delay_min": 0, "label": "数据不足"}

    m_aligned, s_aligned = align_series(market_rows, stock_rows)
    if len(m_aligned) < 10:
        return {"follow_ratio": 0, "avg_delay_min": 0, "label": "数据不足"}

    m_prices = [r["close"] for r in m_aligned]
    s_prices = [r["close"] for r in s_aligned]

    extremes = []
    for i in range(5, len(m_aligned) - 5):
        window_high = max(m_prices[i - 5:i + 5])
        window_low = min(m_prices[i - 5:i + 5])
        if m_prices[i] == window_high and (m_prices[i] - m_prices[i - 5]) / m_prices[i - 5] * 100 > threshold_pct:
            extremes.append({"idx": i, "dir": "up", "pct": (m_prices[i] - m_prices[i - 5]) / m_prices[i - 5] * 100})
        elif m_prices[i] == window_low and (m_prices[i - 5] - m_prices[i]) / m_prices[i - 5] * 100 > threshold_pct:
            extremes.append({"idx": i, "dir": "down", "pct": (m_prices[i - 5] - m_prices[i]) / m_prices[i - 5] * 100})

    follow_count = 0
    delays = []
    for e in extremes:
        e_price = m_prices[e["idx"]]
        for j in range(e["idx"] + 1, min(e["idx"] + 10, len(s_aligned))):
            s_change = (s_prices[j] - s_prices[e["idx"]]) / s_prices[e["idx"]] * 100
            if e["dir"] == "up" and s_change > 0:
                follow_count += 1
                delays.append(j - e["idx"])
                break
            elif e["dir"] == "down" and s_change < 0:
                follow_count += 1
                delays.append(j - e["idx"])
                break

    follow_ratio = follow_count / len(extremes) if extremes else 0
    avg_delay = round(statistics.mean(delays)) if delays else 0
    label = "及时跟随" if follow_ratio > 0.7 else ("部分跟随" if follow_ratio > 0.3 else "不跟随")
    return {"follow_ratio": round(follow_ratio, 2), "avg_delay_min": avg_delay, "label": label}


def sliding_correlation(series_a: list[float], series_b: list[float], window: int = 15) -> dict:
    n = min(len(series_a), len(series_b))
    if n < window:
        return {"avg_r": 0, "breakdown_ratio": 0, "label": "样本不足"}
    def pearson(x, y):
        n = len(x)
        if n < 3:
            return 0
        sx = sum(x); sy = sum(y)
        sxx = sum(v * v for v in x)
        syy = sum(v * v for v in y)
        sxy = sum(x[i] * y[i] for i in range(n))
        num = n * sxy - sx * sy
        den = ((n * sxx - sx * sx) * (n * syy - sy * sy)) ** 0.5
        return num / den if den != 0 else 0
    rs = []
    breakdown = 0
    for i in range(n - window + 1):
        r = pearson(series_a[i:i + window], series_b[i:i + window])
        rs.append(r)
        if r < 0.3:
            breakdown += 1
    avg_r = statistics.mean(rs) if rs else 0
    breakdown_ratio = breakdown / len(rs) if rs else 0
    label = "紧密" if avg_r > 0.6 else ("中等" if avg_r > 0.3 else "松散")
    return {"avg_r": round(avg_r, 3), "breakdown_ratio": round(breakdown_ratio, 3), "label": label}


def detect_divergence(
    stock_rows: list[dict], bench_rows: list[dict], threshold: float = 2.0
) -> dict:
    """检测个股与大盘/板块的方向背离"""
    s_aligned, b_aligned = align_series(stock_rows, bench_rows)
    if len(s_aligned) < 5:
        return {"count": 0, "max_pct": 0, "periods": []}

    s_open = s_aligned[0].get("open", s_aligned[0].get("close", 0)) or 0.001
    b_open = b_aligned[0].get("open", b_aligned[0].get("close", 0)) or 0.001
    if s_open == 0 or b_open == 0:
        return {"count": 0, "max_pct": 0, "periods": []}

    periods = []
    in_divergence = False
    div_start = None
    max_div = 0
    count = 0

    for i in range(len(s_aligned)):
        s_ret = (s_aligned[i]["close"] - s_open) / s_open * 100
        b_ret = (b_aligned[i]["close"] - b_open) / b_open * 100
        if s_ret * b_ret < 0 and abs(s_ret - b_ret) > threshold:
            if not in_divergence:
                in_divergence = True
                div_start = s_aligned[i].get("dt") if isinstance(s_aligned[i].get("dt"), str) else s_aligned[i]["dt"].strftime("%H:%M")
            count += 1
            max_div = max(max_div, abs(s_ret - b_ret))
        else:
            if in_divergence:
                periods.append({
                    "start": div_start,
                    "end": s_aligned[i - 1].get("dt") if isinstance(s_aligned[i - 1].get("dt"), str) else s_aligned[i - 1]["dt"].strftime("%H:%M"),
                    "direction": "个股逆势" if s_ret > 0 else "个股逆跌",
                })
                in_divergence = False
    return {"count": count, "max_pct": round(max_div, 2), "periods": periods[:5]}


def score_linkage(
    stock_rows: list[dict],
    market_rows: list[dict],
    sector_rows: list[dict] | None = None,
) -> dict:
    """综合联动评分入口"""
    result = {}

    rs_market = compute_relative_strength(stock_rows, market_rows)
    result["vs_market"] = rs_market

    conduction = detect_time_conduction(market_rows, stock_rows)
    result["time_conduction"] = conduction

    s_prices = [r["close"] for r in stock_rows]
    m_prices = [r["close"] for r in market_rows]
    corr_market = sliding_correlation(s_prices, m_prices)
    result["correlation_market"] = corr_market

    div = detect_divergence(stock_rows, market_rows)
    result["divergence"] = div

    if sector_rows:
        rs_sector = compute_relative_strength(stock_rows, sector_rows)
        result["vs_sector"] = rs_sector
        s_prices_s = [r["close"] for r in sector_rows]
        corr_sector = sliding_correlation(s_prices, s_prices_s)
        result["correlation_sector"] = corr_sector
        div_s = detect_divergence(stock_rows, sector_rows)
        result["divergence_sector"] = div_s

    return result
```

- [ ] **Step 2: Verify with synthetic data**

Run: `python3 -c "
from scripts.signals.intraday_linkage import compute_relative_strength, detect_time_conduction, sliding_correlation, detect_divergence, score_linkage
import json

stock = [{'dt': f'2026-05-26 {h:02d}:{m:02d}', 'open': 100.0, 'close': 100.0 + h * 0.5 + m * 0.1} for h in range(9, 15) for m in range(0, 60, 5)]
market = [{'dt': f'2026-05-26 {h:02d}:{m:02d}', 'open': 4000.0, 'close': 4000.0 + h * 2 + m * 0.5} for h in range(9, 15) for m in range(0, 60, 5)]
sector = [{'dt': f'2026-05-26 {h:02d}:{m:02d}', 'open': 2000.0, 'close': 2000.0 + h + m * 0.3} for h in range(9, 15) for m in range(0, 60, 5)]

r = score_linkage(stock, market, sector)
print(json.dumps(r, ensure_ascii=False, indent=2))
"`
Expected: valid dict with all indicators

---

### Task 3: runtime_fetch.py — 新增大盘/板块分钟采集

**Files:**
- Modify: `scripts/runtime/runtime_fetch.py`

- [ ] **Step 1: Add index/sector minute fetch function**

Add after `auto_fetch_minute_via_infoway()` (around line 215):

```python
def fetch_index_minutes(
    index_code: str, trade_date_text: str
) -> list[dict] | None:
    """通过腾讯分钟K线 API 获取指数分钟级数据"""
    import urllib.request
    import json as _json
    code_map = {
        "sh000001": "sh000001",
        "sz399001": "sz399001",
        "sz399006": "sz399006",
        "sh000300": "sh000300",
    }
    tencent_code = code_map.get(index_code)
    if not tencent_code:
        return None
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
        f"?_var=min_data_{tencent_code}&code={tencent_code}&day={trade_date_text}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
    except Exception:
        return None
    var_name = f"min_data_{tencent_code}"
    if f"{var_name}=" not in text:
        return None
    try:
        json_str = text.split("=", 1)[1].rstrip(";")
        data = _json.loads(json_str)
    except Exception:
        return None
    if "data" not in data or tencent_code not in data["data"]:
        return None
    raw = data["data"][tencent_code].get("data", {}).get("data", [])
    if not raw:
        return None
    parsed = []
    for line in raw:
        parts = line.split()
        if len(parts) >= 4:
            trade_time = f"{trade_date_text} {parts[0]}"
            parsed.append({
                "dt": trade_time,
                "price": float(parts[1]),
                "volume": int(parts[2]),
                "amount": float(parts[3]),
                "open": None,
                "close": float(parts[1]),
            })
    return parsed if parsed else None


def fetch_sector_minutes(
    sector_code: str, trade_date_text: str
) -> list[dict] | None:
    """通过腾讯分钟K线 API 获取板块指数分钟级数据"""
    import urllib.request
    import json as _json
    url = (
        "https://web.ifzq.gtimg.cn/appstock/app/minute/query"
        f"?_var=min_data_{sector_code}&code={sector_code}&day={trade_date_text}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8")
    except Exception:
        return None
    var_name = f"min_data_{sector_code}"
    if f"{var_name}=" not in text:
        return None
    try:
        json_str = text.split("=", 1)[1].rstrip(";")
        data = _json.loads(json_str)
    except Exception:
        return None
    if "data" not in data or sector_code not in data["data"]:
        return None
    raw = data["data"][sector_code].get("data", {}).get("data", [])
    if not raw:
        return None
    parsed = []
    for line in raw:
        parts = line.split()
        if len(parts) >= 4:
            trade_time = f"{trade_date_text} {parts[0]}"
            parsed.append({
                "dt": trade_time,
                "price": float(parts[1]),
                "volume": int(parts[2]),
                "amount": float(parts[3]),
                "open": None,
                "close": float(parts[1]),
            })
    return parsed if parsed else None


def fetch_index_and_sector_minutes(
    index_codes: list[str], sector_code: str | None, trade_date_text: str
) -> dict:
    """同时获取大盘和板块分钟数据"""
    result = {"indexes": {}, "sector": None}
    for code in index_codes:
        data = fetch_index_minutes(code, trade_date_text)
        if data:
            result["indexes"][code] = data
    if sector_code:
        result["sector"] = fetch_sector_minutes(sector_code, trade_date_text)
    return result
```

- [ ] **Step 2: Add sector code lookup**

Add after `fetch_index_and_sector_minutes`:

```python
def resolve_sector_code(top_theme: str | None) -> str | None:
    """从配置映射表查概念名→板块指数代码"""
    if not top_theme:
        return None
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "sector_index_codes.json"
    if not cfg_path.exists():
        return None
    try:
        mapping = json.loads(cfg_path.read_text(encoding="utf-8"))
        return mapping.get(top_theme)
    except Exception:
        return None
```

- [ ] **Step 3: Verify**

Run: `python3 -c "
from scripts.runtime.runtime_fetch import fetch_index_minutes, fetch_sector_minutes, resolve_sector_code
m = fetch_index_minutes('sh000001', '2026-05-26')
print('index minutes:', len(m) if m else 'None')
s = fetch_sector_minutes('bkbk0818', '2026-05-26')
print('sector minutes:', len(s) if s else 'None (expected, may not exist)')
c = resolve_sector_code('固态电池')
print('resolved code:', c)
"`
Expected: index minutes has data, sector may or may not depending on BK code validity

---

### Task 4: parallel/agents.py — 新增 run_intraday_linkage_agent

**Files:**
- Modify: `scripts/parallel/agents.py`

- [ ] **Step 1: Add the new agent function**

在 `run_dragon_tiger_agent` 之后 (或其他现有 agent 函数之后)，新增:

```python
def run_intraday_linkage_agent(
    pure_symbol: str,
    trade_date_text: str,
    top_theme: str | None = None,
) -> dict:
    """Phase 2 Agent 7: 分钟级联动分析 (大盘/板块/个股)
    
    1. 取个股分钟数据 (自有 parquet)
    2. 腾讯 API 拉大盘分钟 (sh000001)
    3. 查映射表拉板块分钟 (BKxxxx, 可选)
    4. score_linkage() → ETL 联动指标
    5. llm_judge(LINKAGE_TASK) → 定性标签
    6. 返回 {linkage_label, ..., linkage_indicators}
    """
    from scripts.runtime.runtime_fetch import (
        fetch_index_minutes,
        fetch_sector_minutes,
        resolve_sector_code,
    )
    from scripts.signals.intraday_linkage import score_linkage
    from scripts.llm.llm_client import llm_judge
    from scripts.signals.core.score_intraday_strength import load_rows, candidate_paths

    # 1. 个股分钟
    paths = candidate_paths(pure_symbol, trade_date_text)
    stock_path = next((p for p in paths if p.exists()), None)
    if not stock_path:
        return {"status": "no_data", "linkage_label": "无数据"}
    stock_rows_raw = load_rows(stock_path)
    stock_rows = [
        {
            "dt": r.dt.strftime("%Y-%m-%d %H:%M"),
            "open": r.open, "close": r.close,
            "high": r.high, "low": r.low,
            "volume": r.volume, "amount": r.amount,
        }
        for r in stock_rows_raw
    ]

    # 2. 大盘分钟
    market_raw = fetch_index_minutes("sh000001", trade_date_text) or []
    market_rows = [
        {"dt": r["dt"], "open": r.get("price", r["close"]), "close": r["close"]}
        for r in market_raw
    ]
    if not market_rows:
        return {"status": "no_market_data", "linkage_label": "无大盘数据"}

    # 3. 板块分钟 (可选)
    sector_rows = None
    sector_code = resolve_sector_code(top_theme) if top_theme else None
    if sector_code:
        sector_raw = fetch_sector_minutes(sector_code, trade_date_text)
        if sector_raw:
            sector_rows = [
                {"dt": r["dt"], "open": r.get("price", r["close"]), "close": r["close"]}
                for r in sector_raw
            ]

    # 4. ETL 联动指标
    indicators = score_linkage(stock_rows, market_rows, sector_rows)

    # 5. LLM 定性
    llm_context = {
        "relative_strength": indicators.get("vs_market", {}),
        "time_conduction": indicators.get("time_conduction", {}),
        "correlation": indicators.get("correlation_market", {}),
        "divergence": indicators.get("divergence", {}),
        "sector_correlation": indicators.get("correlation_sector", {}),
        "stock_info": {"symbol": pure_symbol},
    }
    linkage_result = llm_judge(LINKAGE_TASK, llm_context)

    return {
        "status": "ok",
        "linkage_label": linkage_result.get("linkage_label", "未知"),
        "relative_strength_judgment": linkage_result.get("relative_strength_judgment", ""),
        "conduction_quality": linkage_result.get("conduction_quality", ""),
        "correlation_quality": linkage_result.get("correlation_quality", ""),
        "divergence_risk": linkage_result.get("divergence_risk", ""),
        "narrative": linkage_result.get("narrative", ""),
        "confidence": linkage_result.get("confidence", 0),
        "linkage_indicators": indicators,
    }
```

- [ ] **Step 2: Register in _phase2_parallel**

Add the new agent to the `_phase2_parallel()` function in `build_stock_report.py`:

```python
    from parallel.agents import run_intraday_linkage_agent  # add with other imports
    
    # After dragon_tiger agent registration:
    ParallelAgent(
        name="intraday_linkage",
        func=functools.partial(
            run_intraday_linkage_agent,
            pure_symbol=pure_symbol,
            trade_date_text=trade_date_text,
            top_theme=ctx.get("top_theme"),
        ),
        timeout=30.0,
        default_result={"status": "timeout", "linkage_label": "超时"},
    ),
```

Increase `max_workers` from 6 to 7 (or keep 6 if agents should share workers).

- [ ] **Step 3: Add LINKAGE_TASK to agent module**

在 `agents.py` 文件开头或现有 task 常量附近，添加：

```python
LINKAGE_TASK = """基于联动分析数据，判断个股与大盘/板块的日内联动质量。
返回 JSON:
{
  "linkage_label": "强跟随"|"一般跟随"|"弱跟随"|"脱钩"|"独立走势",
  "relative_strength_judgment": "明显强于大盘"|"略强"|"同步"|"略弱"|"明显弱势",
  "conduction_quality": "及时"|"滞后"|"未跟随",
  "correlation_quality": "紧密"|"中等"|"松散",
  "divergence_risk": "高"|"中"|"低",
  "narrative": "一句话总结日内联动特征",
  "confidence": 0-1
}"""
```

- [ ] **Step 4: Verify**

Run: `python3 -c "
from parallel.agents import run_intraday_linkage_agent, LINKAGE_TASK
print('LINKAGE_TASK loaded:', bool(LINKAGE_TASK))
print('agent function loaded:', callable(run_intraday_linkage_agent))
"`
Expected: import succeeds, LINKAGE_TASK is non-empty

---

### Task 5: context_propagation_rules.py — 新增分钟级联动规则组

### Task 5: parallel/agents.py — 新增分钟级联动规则组 (消费端)

**注意:** 联动规则的消费方不在 `context_propagation_rules.py` (该模块处理日线级别的传播链), 而是在 **`build_final_decision()` 的 LLM context 中** 传递联动标签。

- [ ] **Step 1: 确认 build_final_decision 的 context 包含 linkage_label**

查看 `build_stock_report.py` 中 Phase 3 聚合代码, 确认 `parallel_results["intraday_linkage"]` 被合并到 decision context:

```python
    # Phase 3 聚合处
    intraday_linkage = parallel_results.get("intraday_linkage", {})
    ctx["intraday_linkage_label"] = intraday_linkage.get("linkage_label", "")
    ctx["intraday_linkage_narrative"] = intraday_linkage.get("narrative", "")
    ctx["intraday_linkage_risk"] = intraday_linkage.get("divergence_risk", "")
```

然后 `build_final_decision()` 的 LLM prompt 中能看到类似:

```
日内联动: {intraday_linkage_label} - {intraday_linkage_narrative}
背离风险: {intraday_linkage_risk}
```

- [ ] **Step 2: Validate consumption path**

Run syntax check: `python3 -c "from scripts.build_stock_report import build_payload; print('import ok')"`
Expected: import succeeds (actual runtime requires real data)

---

### Task 6: End-to-end 验证

- [ ] **Step 1: Verify all imports**

Run: `python3 -c "
from scripts.signals.intraday_linkage import score_linkage, compute_relative_strength, detect_time_conduction, sliding_correlation, detect_divergence
from scripts.runtime.runtime_fetch import fetch_index_minutes, fetch_sector_minutes, resolve_sector_code
from parallel.agents import run_intraday_linkage_agent, LINKAGE_TASK
from scripts.llm.llm_client import llm_judge
print('all imports OK')
"`
Expected: all imports succeed

- [ ] **Step 2: Run full ETL pipeline on real data**

Run: `python3 -c "
import json
from scripts.runtime.runtime_fetch import fetch_index_minutes, fetch_sector_minutes, resolve_sector_code
from scripts.signals.intraday_linkage import score_linkage
from parallel.agents import load_rows, candidate_paths

# 1. Get stock minute data
symbol = '000725'
td = '2026-05-26'
paths = candidate_paths(symbol, td)
stock_path = next((p for p in paths if p.exists()), None)
stock_rows = [{'dt': r.dt.strftime('%Y-%m-%d %H:%M'), 'open': r.open, 'close': r.close, 'high': r.high, 'low': r.low, 'volume': r.volume, 'amount': r.amount} for r in load_rows(stock_path)]

# 2. Get market minute data
m = fetch_index_minutes('sh000001', td) or []
market_rows = [{'dt': r['dt'], 'open': r.get('price', r.get('close', 0)), 'close': r.get('close', r.get('price', 0))} for r in m]

# 3. Try sector data
sector_code = resolve_sector_code('LED概念')
sector_rows = None
if sector_code:
    s = fetch_sector_minutes(sector_code, td)
    if s:
        sector_rows = [{'dt': r['dt'], 'open': r.get('price', r.get('close', 0)), 'close': r.get('close', r.get('price', 0))} for r in s]

# 4. Run linkage analysis
result = score_linkage(stock_rows, market_rows, sector_rows)
print(json.dumps(result, ensure_ascii=False, indent=2))
"`
Expected: outputs linkage indicators with real data

- [ ] **Step 3: Verify LLM integration**

Run: `python3 -c "
from scripts.llm.llm_client import llm_judge

LINKAGE_TASK = '''基于联动分析数据，判断个股与大盘/板块的日内联动质量。
返回 JSON:
{
  \"linkage_label\": \"强跟随\"|\"一般跟随\"|\"弱跟随\"|\"脱钩\"|\"独立走势\",
  \"relative_strength_judgment\": \"明显强于大盘\"|\"略强\"|\"同步\"|\"略弱\"|\"明显弱势\",
  \"conduction_quality\": \"及时\"|\"滞后\"|\"未跟随\",
  \"correlation_quality\": \"紧密\"|\"中等\"|\"松散\",
  \"divergence_risk\": \"高\"|\"中\"|\"低\",
  \"narrative\": \"一句话总结日内联动特征\",
  \"confidence\": 0-1
}'''

context = {
  'relative_strength': {'final_rs': 2.3, 'trend': '先弱后强', 'key_points': {'10:00': -1.2, '11:30': 1.5, '15:00': 2.3}},
  'time_conduction': {'follow_ratio': 0.8, 'avg_delay_min': 2, 'label': '及时跟随'},
  'correlation': {'market_avg_r': 0.72, 'breakdown_ratio': 0.05, 'label': '紧密'},
  'divergence': {'count': 1, 'max_pct': 2.5, 'periods': [{'start': '10:30', 'end': '10:45', 'direction': '个股逆势'}]},
}
result = llm_judge(LINKAGE_TASK, context)
print('linkage_label:', result.get('linkage_label'))
print('narrative:', result.get('narrative'))
print('confidence:', result.get('confidence'))
"`
Expected: LLM returns valid JSON with linkage judgment
