# 报告生成时自动对比历史 - 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成分析报告时自动查找同一只股票的历史报告，在报告中展示对比变化

**Architecture:** 
- 在 `parquet_io.py` 中新增 `load_latest_report()` 函数，查询同股票最新盘后报告
- 在 `build_stock_report.py` 中新增 `build_history_comparison()` 对比逻辑
- 在 `report_renderer.py` 的 `render_markdown()` 中添加"历史对比"模块

**Tech Stack:** Python 3.9+, pandas, pyarrow

**Frozen Baseline:** 
- 当前分支: `个股开发`
- 报告存储: `pending-validations/YYYYMMDD/` 目录
- 只对比 `checkpoint == "收盘"` 的盘后报告
- 报告结构: 六个章节（大盘环境→板块判断→个股结构→技术因子→交易结论→风险提示）

---

## File Structure

| 文件 | 操作 | 说明 |
|------|------|------|
| `scripts/data/parquet_io.py` | 修改 | 新增 `load_latest_report()` 函数 |
| `scripts/build_stock_report.py` | 修改 | 新增 `build_history_comparison()` 对比逻辑 |
| `scripts/render/report_renderer.py` | 修改 | 在报告末尾添加"历史对比"模块 |

---

### Task 1: 在 parquet_io.py 中新增查询函数

**Files:**
- Modify: `scripts/data/parquet_io.py` (在文件末尾添加)

- [ ] **Step 1: 新增 load_latest_report() 函数**

```python
def load_latest_report(
    base_dir: Path,
    symbol: str,
    current_trade_date: str,
    checkpoint: str = "收盘",
) -> dict[str, Any] | None:
    """
    查询同一只股票最新的盘后分析报告（排除当前分析日期）
    
    Args:
        base_dir: 报告根目录（pending-validations）
        symbol: 股票代码
        current_trade_date: 当前分析日期，排除此日期
        checkpoint: 只对比盘后报告
    
    Returns:
        最新报告的扁平化数据，如果没有则返回 None
    """
    all_dfs: list[pd.DataFrame] = []
    
    for parquet_file in base_dir.rglob("*.parquet"):
        try:
            df = pq.read_table(parquet_file).to_pandas()
            all_dfs.append(df)
        except Exception:
            continue
    
    if not all_dfs:
        return None
    
    combined = pd.concat(all_dfs, ignore_index=True)
    
    # 筛选条件：同股票、盘后报告、排除当前日期
    filtered = combined[
        (combined["symbol"] == symbol) &
        (combined["checkpoint"] == checkpoint) &
        (combined["trade_date"] != current_trade_date)
    ]
    
    if filtered.empty:
        return None
    
    # 按日期降序，取最新的一条
    latest = filtered.sort_values("trade_date", ascending=False).iloc[0]
    
    return latest.to_dict()
```

- [ ] **Step 2: 验证语法**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python3 -c "from data.parquet_io import load_latest_report; print('OK')"
```

Expected: OK

---

### Task 2: 在 build_stock_report.py 中新增对比逻辑

**Files:**
- Modify: `scripts/build_stock_report.py`

- [ ] **Step 1: 导入新函数**

在文件顶部导入部分添加：

```python
from data.parquet_io import load_latest_report
```

- [ ] **Step 2: 新增对比函数**

在 `build_payload()` 函数之前添加：

```python
def build_history_comparison(payload: dict) -> dict[str, Any]:
    """
    生成历史对比数据
    
    对比当前分析与同股票最新的盘后报告，展示关键指标变化
    """
    from data.config_loader import cfg
    
    symbol = payload.get("symbol")
    trade_date = payload.get("trade_date")
    pending_dir = Path(cfg.paths("report_output"))
    
    if not symbol or not trade_date:
        return {"status": "missing_info"}
    
    latest = load_latest_report(pending_dir, symbol, trade_date)
    
    if not latest:
        return {"status": "no_history"}
    
    # 对比关键指标
    comparison = {
        "status": "available",
        "previous_date": latest.get("trade_date"),
        "previous_checkpoint": latest.get("checkpoint"),
        "changes": {},
    }
    
    # 价格变化
    current_price = payload.get("current_price")
    previous_price = latest.get("current_price")
    if current_price and previous_price:
        price_change = current_price - previous_price
        price_change_pct = (price_change / previous_price) * 100
        comparison["changes"]["price"] = {
            "current": current_price,
            "previous": previous_price,
            "change": round(price_change, 2),
            "change_pct": round(price_change_pct, 2),
        }
    
    # 决策变化
    current_decision = payload.get("final_decision", {}).get("decision")
    previous_decision = latest.get("decision")
    if current_decision and previous_decision:
        comparison["changes"]["decision"] = {
            "current": current_decision,
            "previous": previous_decision,
            "changed": current_decision != previous_decision,
        }
    
    # 筹码变化
    current_chip = payload.get("chip_structure") or {}
    previous_winner_rate = latest.get("winner_rate")
    current_winner_rate = current_chip.get("winner_rate")
    if current_winner_rate and previous_winner_rate:
        comparison["changes"]["winner_rate"] = {
            "current": current_winner_rate,
            "previous": previous_winner_rate,
            "change": round(current_winner_rate - previous_winner_rate, 4),
        }
    
    # 波动率变化
    current_vol = payload.get("volatility_context") or {}
    previous_atr = latest.get("atr14")
    current_atr = current_vol.get("atr14")
    if current_atr and previous_atr:
        comparison["changes"]["atr14"] = {
            "current": current_atr,
            "previous": previous_atr,
            "change": round(current_atr - previous_atr, 2),
        }
    
    return comparison
```

- [ ] **Step 3: 在 build_payload() 中调用对比逻辑**

在 `return payload` 之前添加：

```python
    # 历史对比
    payload["history_comparison"] = build_history_comparison(payload)
```

- [ ] **Step 4: 验证语法**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python3 -c "from build_stock_report import build_history_comparison; print('OK')"
```

Expected: OK

---

### Task 3: 在 report_renderer.py 中添加历史对比模块

**Files:**
- Modify: `scripts/render/report_renderer.py:800-802`

- [ ] **Step 1: 在 render_markdown() 末尾添加历史对比模块**

在 `return '\n'.join(lines)` 之前添加：

```python
    # 历史对比模块
    history_comparison = payload.get("history_comparison", {})
    if history_comparison.get("status") == "available":
        lines.extend([
            '',
            '---',
            '',
            '## 七、历史对比',
            '',
            f'- 上次分析日期：{history_comparison.get("previous_date")}',
        ])
        changes = history_comparison.get("changes", {})
        if "price" in changes:
            price_info = changes["price"]
            direction = "↑" if price_info["change"] > 0 else "↓" if price_info["change"] < 0 else "→"
            lines.append(f'- 价格变化：{price_info["previous"]:.2f} → {price_info["current"]:.2f} ({direction} {price_info["change"]:+.2f}, {price_info["change_pct"]:+.2f}%)')
        if "decision" in changes:
            decision_info = changes["decision"]
            if decision_info["changed"]:
                lines.append(f'- 决策变化：{decision_info["previous"]} → {decision_info["current"]}')
            else:
                lines.append(f'- 决策一致：{decision_info["current"]}')
        if "winner_rate" in changes:
            wr_info = changes["winner_rate"]
            lines.append(f'- 获利盘变化：{wr_info["previous"]:.1f}% → {wr_info["current"]:.1f}% ({wr_info["change"]:+.2f}%)')
        if "atr14" in changes:
            atr_info = changes["atr14"]
            lines.append(f'- 波动率变化：{atr_info["previous"]:.2f} → {atr_info["current"]:.2f} ({atr_info["change"]:+.2f})')
    elif history_comparison.get("status") == "no_history":
        lines.extend([
            '',
            '---',
            '',
            '## 七、历史对比',
            '',
            '- 首次分析该股票，无历史对比数据',
        ])
```

- [ ] **Step 2: 验证语法**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python3 -c "from render.report_renderer import render_markdown; print('OK')"
```

Expected: OK

---

## Acceptance Criteria

1. ✅ `load_latest_report()` 函数可正确查询历史报告
2. ✅ `build_history_comparison()` 函数可正确对比关键指标
3. ✅ `build_payload()` 返回的 payload 包含 `history_comparison` 字段
4. ✅ 报告末尾显示"历史对比"模块
5. ✅ 无语法错误
6. ✅ 仅对比盘后（收盘）报告
