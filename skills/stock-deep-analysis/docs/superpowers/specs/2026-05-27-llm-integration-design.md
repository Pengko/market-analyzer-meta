# LLM 集成改造设计

## 概述

将 stock-deep-analysis 中三处硬编码规则判定替换为 LLM 定性推理，同时统一数据读取层（CSV → parquet）和数据访问路径（走 tushare_pro 实际目录结构）。

## 改造范围

### 模块清单

| # | 模块 | 改动 |
|---|---|---|
| 1 | `scripts/data/data_provider.py` | **新增**。统一 parquet 读取接口，按 tushare_pro 实际目录结构查询 |
| 2 | `scripts/llm/llm_client.py` | **新增**。统一 LLM 调用封装（Hermes API，env 控制 endpoint/key/model） |
| 3 | `scripts/analysis/sector_analyzer.py` | 3 个函数改为 ETL + `llm_judge()`；其余保留 |
| 4 | `scripts/decision/decision_engine.py` | `build_final_decision()` 改为 ETL 摘要 → LLM，保留 data_score 脚本计算 |
| 5 | `scripts/agents/dragon_tiger_analyst.py` | "写文件等人手动" → `llm_judge()` |
| + | `pre_collect_data.py` | 修复 `fetch_industry_concept()` 路径，CSV → parquet |
| + | `sector_analyzer.py` | 内联的 CSV 读取改为走 `data_provider` |
| + | `market_analyzer.py` | `load_index_row()` 等 CSV 改为走 `data_provider` |
| + | `stock_trend_analyzer.py` | `load_timeseries_rows()` 等 CSV 改为走 `data_provider` |

### DataProvider 初始覆盖的接口

```python
def get_daily(symbol, trade_date) -> dict | None
def get_daily_basic(symbol, trade_date) -> dict | None
def get_moneyflow(symbol, trade_date) -> dict | None
def get_index_daily(index_code, trade_date) -> dict | None
def get_daily_rows(symbol, trade_date, limit=10) -> list[dict]  # 最近 N 根
def get_chips(symbol, trade_date) -> list[dict]
def get_factors(symbol, trade_date) -> dict | None
def get_weekly(symbol, trade_date) -> list[dict]
def get_monthly(symbol, trade_date) -> list[dict]
def get_stock_basic(symbol) -> dict | None
def get_stock_concepts(symbol) -> list[dict]         # KPL concept
def get_theme_constituents(symbol, trade_date) -> list[dict]  # dc_concept
```

### 不改的

- `market_analyzer.py` 中的纯量化计算（breadth_score、size_style）保留
- `stock_trend_analyzer.py` 中的 ATR/MA/volatility 计算保留
- research 脚本、test 脚本
- 分钟线（仍为 CSV，无 parquet 版本）

## 架构

```
pre_collect_data.py  ───  data_provider  ───  tushare_pro parquet
       │
       ▼
  sector_analyzer.py  ──  3 函数 → ETL → llm_judge()
  decision_engine.py  ──  build_final_decision → ETL → llm_judge()
  dragon_tiger.py     ──  llm_judge()

llm_judge ──  HTTP POST ──  Hermes API (env: LLM_BASE_URL/KEY/MODEL)
```

### llm_client.py 设计

```python
import os, requests, json

_BASE_URL = os.getenv("LLM_BASE_URL", "http://127.0.0.1:8642/v1")
_API_KEY  = os.getenv("LLM_API_KEY", "")
_MODEL    = os.getenv("LLM_MODEL", "hermes-agent")

def llm_judge(task: str, context: dict, temperature: float = 0.3) -> dict:
    """调用 LLM 做定性判断，返回解析后的 JSON"""
    resp = requests.post(
        f"{_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {_API_KEY}"},
        json={
            "model": _MODEL,
            "messages": [
                {"role": "system", "content": task},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}
            ],
            "response_format": {"type": "json_object"},
            "temperature": temperature,
        },
        timeout=30,
    )
    return json.loads(resp.json()["choices"][0]["message"]["content"])
```

### data_provider.py 设计

```python
from pathlib import Path
import pandas as pd

_STOCK_ROOT = Path("/Users/penghongming/quant-data/tushare/股票数据")
_INDEX_ROOT = Path("/Users/penghongming/quant-data/tushare/指数数据")

def _read_pq(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_parquet(path)
    except Exception:
        return None

def get_daily(symbol: str, trade_date: str) -> dict | None:
    df = _read_pq(_STOCK_ROOT / "daily" / f"{symbol}.parquet")
    if df is None:
        return None
    row = df[df["trade_date"] == trade_date]
    return row.iloc[0].to_dict() if not row.empty else None

def get_stock_concepts(symbol: str) -> list[dict]:
    """从 KPL concept 按月 parquet 查股票所属概念"""
    # ... 遍历 kpl_concept_cons/2026.parquet ...
```

具体实现按此接口契约。

### sector_analyzer.py 改造

保留函数：`load_stock_name()`、`load_stock_basic_index()`、`fetch_browser_concepts()`、`fetch_mobile_stock_concepts()`、`match_mobile_subthemes()`、`build_leader_prediction()`

删除并替换为 `llm_judge`：

| 原函数 | 替换方式 |
|---|---|
| `infer_sector_cycle_status()` | ETL 出热度集中度/总成分股/龙头名 → LLM 判阶段 |
| `analyze_theme_trend()` | ETL 出两日热度变化 → LLM 判趋势 |
| `infer_theme_progression()` | ETL 出各题材热度排行 → LLM 判轮动方向 |

所有概念数据改为走 `data_provider.get_stock_concepts()` 和 `data_provider.get_theme_constituents()`。

### decision_engine.py 改造

新增 `extract_decision_context()`，替代现在 `build_final_decision()` 中 80 行的加权循环。统计各维度摘要打包成 JSON 给 LLM。

保留 `compute_data_score()` 脚本计算（数据完整度 LLM 算不好）。

`build_final_decision()` 改为：
```python
def build_final_decision(payload):
    data_score = compute_data_score(payload)
    context = extract_decision_context(payload)
    llm_result = llm_judge(task=DECISION_TASK, context=context)
    return {**llm_result, "data_completeness": data_score}
```

### dragon_tiger_analyst.py 改造

删除 lines 199-213（写临时文件 + sys.exit），改为：
```python
result = llm_judge(task=DT_TASK, context=summary)
md_content, json_summary = format_dt_result(result)
```

## 不做的

- research 脚本（detect_divergence 系列）的 CSV 读取不修
- test 脚本不修
- 分钟线数据不修（无 parquet）
- market_analyzer 的量化计算不替换

### sector_analyzer LLM Task 定义

```python
SECTOR_CYCLE_TASK = """判断题材当前所处的阶段。
返回 JSON: {"cycle": "加强"|"分化"|"轮动"|"退潮", "confidence": 0-1, "reasoning": "简要推理依据"}"""

SECTOR_TREND_TASK = """判断题材热度趋势。
返回 JSON: {"trend": "上升"|"平稳"|"回落"|"退潮", "confidence": 0-1, "signals": ["信号1", "信号2"]}"""

SECTOR_PROGRESSION_TASK = """判断题材轮动方向，是否有接棒题材。
返回 JSON: {"next_theme": "题材名"|null, "confidence": 0-1, "reasoning": "简要推理"}"""
```

### decision_engine LLM Task 定义

```python
DECISION_TASK = """你是一个A股交易决策引擎。基于以下各维度分析摘要，给出综合裁决。
返回 JSON:
{
  "decision": "适合轻仓试仓"|"仅适合观察"|"观察确认"|"暂不适合建仓",
  "bullish_dimensions": ["偏多方面1", ...],
  "bearish_dimensions": ["偏空方面1", ...],
  "conflicts": ["矛盾项1", ...],
  "preconditions": ["放量站稳XX", ...],
  "invalidations": ["跌破XX且回抽无力", ...],
  "key_levels": {"observe": 价格, "confirm": 价格, "invalid": 价格},
  "reasoning": "综合推理过程"
}"""
```

### dragon_tiger LLM Task 定义

```python
DT_TASK = """分析龙虎榜数据，判断资金性质。
返回 JSON:
{
  "signal": "游资接力"|"机构出货"|"量化进出"|"散户主导"|"分歧加大"|"中性",
  "overall_score": 0-10,
  "confidence": 0-100,
  "reasoning": "简要推理",
  "key_seats": ["席位1", "席位2"]
}"""
```

## 验收标准

1. `quick_analyze.sh 000725.SZ 2026-05-26` 能正确产出板块/题材分析（不再只有"元器件"）
2. `decision_engine.py` 输出的交易结论有语境推理，而非规则触发
3. 所有数据读取走 parquet，无 CSV 路径报错
4. LLM 调用走 `LLM_BASE_URL` env var，不硬编码
