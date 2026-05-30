# 拆分 build_stock_report.py 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 1145 行的 `build_stock_report.py` 单体拆分为职责清晰的模块，保持向后兼容。

**Architecture:** 3 步走：(1) 提取独立逻辑到新模块；(2) 更新 `parallel/agents.py` 改为从源模块直接导入；(3) 清理 `build_stock_report.py` 为薄编排层。全程保持 re-export 兼容，不破坏任何外部消费者。

**Tech Stack:** Python 3.14, pyarrow, pandas

**Frozen Baseline:** 拆分前 `build_stock_report.py` = 1145 行。外部消费者：`parallel/agents.py`（16+ 函数）、`tmp_*.py`（3 个文件用 `build_payload`）。

---

## 文件结构变更

| 操作 | 文件 | 职责 |
|------|------|------|
| 新建 | `scripts/time_util.py` | 时间/会话/交易日解析 |
| 新建 | `scripts/financing_analyzer.py` | 融资融券分析 + 基本面构建 |
| 新建 | `scripts/capital_context.py` | 主力资金新鲜度 + 混合时点上下文 + 降级逻辑 |
| 修改 | `scripts/build_stock_report.py` | 保留编排逻辑 + re-export 兼容层 |
| 修改 | `scripts/parallel/agents.py` | 改为从源模块直接导入（不经过 BSR） |

---

### Task 1: 提取 time_util.py

**Files:**
- Create: `scripts/time_util.py`
- Modify: `scripts/build_stock_report.py:150-244,524-541`

从 `build_stock_report.py` 提取以下纯函数到 `time_util.py`：
- `scenario_from_now(now)` → 返回 "盘前"/"上午盘中"/"午间休盘"/"下午盘中"/"盘后"
- `normalize_trade_date_for_session(now, trade_date_text, checkpoint_arg)` → 盘前自动回退到前一交易日
- `resolve_checkpoint(now, trade_date_text, checkpoint_arg)` → auto→具体 checkpoint
- `parse_date_candidates(values)` → 从多个候选值中提取有效日期
- `_normalize_trade_date_text(value)` → 统一日期格式为 YYYY-MM-DD
- `_next_trade_date_compact(trade_date_text)` → 获取下一交易日

- [ ] **Step 1: 创建 `scripts/time_util.py`**

```python
#!/usr/bin/env python3
"""时间/会话/交易日解析工具函数。"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Iterable


def scenario_from_now(now: datetime) -> str:
    current = now.time()
    if current < datetime.strptime("09:15", "%H:%M").time():
        return "盘前"
    if current <= datetime.strptime("11:30", "%H:%M").time():
        return "上午盘中"
    if current < datetime.strptime("13:00", "%H:%M").time():
        return "午间休盘"
    if current <= datetime.strptime("15:00", "%H:%M").time():
        return "下午盘中"
    return "盘后"


def normalize_trade_date_for_session(
    now: datetime, trade_date_text: str, checkpoint_arg: str,
    latest_open_trade_date_on_or_before_fn=None,
) -> tuple[str, dict[str, Any]]:
    session = scenario_from_now(now)
    should_use_close_logic = checkpoint_arg == "pre_open" or (
        checkpoint_arg == "auto" and session == "盘前"
    )
    if not should_use_close_logic:
        return trade_date_text, {
            "adjusted": False,
            "reason": "session_kept_requested_trade_date",
        }
    now_text = now.strftime("%Y-%m-%d")
    if trade_date_text != now_text:
        return trade_date_text, {
            "adjusted": False,
            "reason": "requested_trade_date_not_today",
        }
    previous_reference = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    fn = latest_open_trade_date_on_or_before_fn or _noop_latest_open
    previous_open_trade_date = fn(previous_reference)
    if not previous_open_trade_date:
        return trade_date_text, {
            "adjusted": False,
            "reason": "previous_open_trade_date_missing",
        }
    return previous_open_trade_date, {
        "adjusted": True,
        "reason": "pre_open_use_previous_close_logic",
        "requested_trade_date": trade_date_text,
        "resolved_trade_date": previous_open_trade_date,
    }


def _noop_latest_open(date_text: str) -> str | None:
    return None


def resolve_checkpoint(now: datetime, trade_date_text: str, checkpoint_arg: str) -> str:
    if checkpoint_arg == "pre_open":
        return "close"
    if checkpoint_arg != "auto":
        return checkpoint_arg
    session = scenario_from_now(now)
    if session == "盘前":
        return "close"
    trade_date_obj = datetime.strptime(trade_date_text, "%Y-%m-%d").date()
    now_date = now.date()
    if now_date > trade_date_obj:
        return "next_close"
    mapping = {
        "上午盘中": "open",
        "午间休盘": "noon",
        "下午盘中": "afternoon",
        "盘后": "close",
    }
    return mapping.get(session, "close")


def parse_date_candidates(values: Iterable[str | None]) -> list[str]:
    result: list[str] = []
    for value in values:
        if not value:
            continue
        candidate = value[:10]
        try:
            normalized = datetime.strptime(candidate, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            continue
        result.append(normalized)
    return result


def normalize_trade_date_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidate = text[:10]
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(candidate, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def next_trade_date_compact(trade_date_text: str, next_trade_dates_fn=None) -> str:
    if next_trade_dates_fn:
        dates = next_trade_dates_fn(trade_date_text, count=1)
        if dates:
            return dates[0]
    return (
        datetime.strptime(trade_date_text, "%Y-%m-%d").date() + timedelta(days=1)
    ).strftime("%Y%m%d")
```

- [ ] **Step 2: 验证 time_util 模块**

Run: `cd ~/agent-skills/custom/stock-deep-analysis/scripts && python3 -c "from time_util import scenario_from_now, resolve_checkpoint; from datetime import datetime; print(scenario_from_now(datetime(2026,5,29,10,30)))"`
Expected: `上午盘中`

---

### Task 2: 提取 financing_analyzer.py

**Files:**
- Create: `scripts/financing_analyzer.py`
- Modify: `scripts/build_stock_report.py:415-510,715-726`

从 `build_stock_report.py` 提取：
- `analyze_financing_context(full_symbol, trade_date_text)` → 融资融券分析（四层判定）
- `_build_fundamental(full_symbol, trade_date_compact)` → daily_basic 基本面数据
- `_resolve_symbol(symbol)` → 中文名称→股票代码解析
- `safe_float(value)` → 通用浮点转换

- [ ] **Step 3: 创建 `scripts/financing_analyzer.py`**

```python
#!/usr/bin/env python3
"""融资融券分析 + 基本面构建 + 股票代码解析。"""
from __future__ import annotations

import re
import sys
from typing import Any

from data.data_access import (
    _read_single_parquet,
    _read_stock_parquet,
    load_browser_margin_signal as _load_browser_margin_signal,
    load_daily_basic_row as _load_daily_basic_row,
    load_margin_rows as _load_margin_rows,
)


def safe_float(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def analyze_financing_context(full_symbol: str, trade_date_text: str) -> dict[str, Any]:
    trade_date_compact = trade_date_text.replace("-", "")
    margin_detail_rows = _read_stock_parquet("margin_detail", full_symbol)
    browser_signal = _load_browser_margin_signal(full_symbol)
    browser_eligibility = str(browser_signal.get("eligibility") or "unknown")

    md_latest: str | None = None
    if margin_detail_rows:
        md_dates = sorted(
            {
                d
                for d in (str(row.get("trade_date") or "").strip() for row in margin_detail_rows)
                if len(d) == 8 and d.isdigit() and d <= trade_date_compact
            }
        )
        if md_dates:
            md_latest = md_dates[-1]

    if md_latest:
        return {
            "status": "available",
            "is_margin_stock": True,
            "label": "融资标的",
            "summary": f"检测到融资融券明细，最新交易日 {md_latest}",
            "latest_margin_detail_trade_date": md_latest,
            "browser_eligibility": browser_eligibility,
            "browser_signal": browser_signal,
            "assumption": None,
        }

    margin_latest: str | None = None
    margin_rows = _load_margin_rows(full_symbol)
    if margin_rows:
        margin_dates = sorted(
            {
                d
                for d in (str(row.get("trade_date") or "").strip() for row in margin_rows)
                if len(d) == 8 and d.isdigit() and d <= trade_date_compact
            }
        )
        if margin_dates:
            margin_latest = margin_dates[-1]

    if browser_eligibility == "non_margin":
        note = "浏览器识别非融资标的，且 margin_detail 无数据，双重验证判定为非融资股"
        if margin_latest:
            note += f"（margin 汇总最新 {margin_latest}）"
        return {
            "status": "verified_non_margin",
            "is_margin_stock": False,
            "label": "非融资股（双重验证）",
            "summary": note,
            "latest_margin_detail_trade_date": None,
            "latest_margin_trade_date": margin_latest,
            "browser_eligibility": browser_eligibility,
            "browser_signal": browser_signal,
            "assumption": "browser_non_margin_and_no_margin_detail",
        }

    if margin_latest:
        note = "margin 汇总存在历史记录，但 margin_detail 为空，暂不能直接确认为融资股或非融资股"
        if browser_eligibility == "margin":
            note += "（浏览器侧显示疑似可融资）"
        elif browser_eligibility == "unknown":
            note += "（浏览器侧未给出明确结论）"
        note += f"（margin 汇总最新 {margin_latest}）"
        return {
            "status": "likely_margin",
            "is_margin_stock": None,
            "label": "疑似融资股",
            "summary": note,
            "latest_margin_detail_trade_date": None,
            "latest_margin_trade_date": margin_latest,
            "browser_eligibility": browser_eligibility,
            "browser_signal": browser_signal,
            "assumption": "margin_history_without_detail",
        }

    note = "未检测到 margin_detail，且当前缺少足够证据确认是否为融资股"
    if browser_eligibility == "margin":
        note += "（浏览器侧显示疑似可融资）"
    elif browser_eligibility == "unknown":
        note += "（浏览器侧未给出明确可融资结论）"
    return {
        "status": "unknown",
        "is_margin_stock": None,
        "label": "未知待确认",
        "summary": note,
        "latest_margin_detail_trade_date": None,
        "latest_margin_trade_date": None,
        "browser_eligibility": browser_eligibility,
        "browser_signal": browser_signal,
        "assumption": "insufficient_evidence",
    }


def build_fundamental(full_symbol: str, trade_date_compact: str) -> dict[str, Any]:
    row = _load_daily_basic_row(full_symbol, trade_date_compact)
    if not row:
        return {"status": "missing", "reason": "daily_basic 本地数据缺失"}
    return {
        "status": "available",
        "pe": safe_float(row.get("pe")),
        "pe_ttm": safe_float(row.get("pe_ttm")),
        "pb": safe_float(row.get("pb")),
        "total_mv": safe_float(row.get("total_mv")),
        "circ_mv": safe_float(row.get("circ_mv")),
    }


def resolve_symbol(symbol: str) -> str:
    """若传入中文股票名称，查 stock_basic_all.parquet 解析为 ts_code。"""
    raw = symbol.strip()
    if re.match(r'^\d{6}(\.(SH|SZ))?$', raw, re.IGNORECASE):
        return raw
    try:
        rows = _read_single_parquet("stock_basic", "stock_basic_all.parquet")
        for row in rows:
            if row.get("name", "").strip() == raw:
                resolved = row["ts_code"].strip()
                print(f"[resolve_symbol] '{raw}' → {resolved}", file=sys.stderr, flush=True)
                return resolved
        for row in rows:
            name = row.get("name", "").strip()
            if raw in name:
                resolved = row["ts_code"].strip()
                print(f"[resolve_symbol] '{raw}' → {resolved}({name})", file=sys.stderr, flush=True)
                return resolved
        print(f"[resolve_symbol] 未找到 '{raw}'，按原样使用", file=sys.stderr, flush=True)
    except Exception as e:
        print(f"[resolve_symbol] 查询失败: {e}，按原样使用", file=sys.stderr, flush=True)
    return raw
```

- [ ] **Step 4: 验证 financing_analyzer**

Run: `cd ~/agent-skills/custom/stock-deep-analysis/scripts && python3 -c "from financing_analyzer import safe_float, resolve_symbol; print(safe_float('3.14'), resolve_symbol('000725'))"`
Expected: `3.14 000725`

---

### Task 3: 提取 capital_context.py

**Files:**
- Create: `scripts/capital_context.py`
- Modify: `scripts/build_stock_report.py:300-369,574-712`

从 `build_stock_report.py` 提取：
- `summarize_capital_freshness(next_day)` → 主力资金新鲜度判断
- `build_mixed_trade_date_context(...)` → 混合时点上下文检测
- `_degrade_prediction_bundle(mixed_context, payload)` → 混合时点降级逻辑
- `is_event_theme(name)` → 事件题材判断
- `_persist_analysis_history(payload)` → 分析历史写入 SQLite

- [ ] **Step 5: 创建 `scripts/capital_context.py`**

```python
#!/usr/bin/env python3
"""主力资金新鲜度 + 混合时点上下文 + 事件题材判断。"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from data.config_loader import cfg
from data.db_adapter import init_schema as init_sqlite_schema
from data.db_adapter import insert_analysis_history as insert_analysis_history_row
from time_util import normalize_trade_date_text

EVENT_THEMES = set(cfg.decision("event_themes", default=[
    "回购增持再贷款",
    "并购重组",
    "股权转让(并购重组)",
    "融资融券",
    "回购",
    "增持",
]))


def is_event_theme(name: str) -> bool:
    text = (name or "").strip()
    if not text:
        return False
    if text in EVENT_THEMES:
        return True
    return any(keyword in text for keyword in ("回购", "增持", "并购", "重组", "融资融券"))


def summarize_capital_freshness(next_day: dict) -> dict[str, Any]:
    if next_day.get("status") != "available":
        return {
            "status": "unavailable",
            "label": "当前个股本地数据缺失",
            "summary": next_day.get("reason") or "隔夜脚本未生成，无法提取主力资金新鲜度",
            "signals": [],
        }
    result = next_day["result"]
    features = result.get("features", {})
    leaderboard = features.get("leaderboard_context", {}) or {}
    signals = result.get("signals", [])

    positive_hits = [
        s for s in signals
        if any(k in s for k in ("新增主导资金介入", "新资金介入", "新资金关注", "量价", "协同较强"))
    ]
    negative_hits = [
        s for s in signals
        if any(k in s for k in ("派发", "兑现", "高位换手分歧", "净卖", "抛压"))
    ]

    if positive_hits and not negative_hits:
        label = "偏新资金介入"
    elif positive_hits and negative_hits:
        label = "新老资金换手"
    elif negative_hits:
        label = "偏派发分歧"
    else:
        label = "中性待确认"

    summary_parts: list[str] = []
    if features.get("is_bullish_candle"):
        summary_parts.append("T日为阳线")
    amount_ratio = features.get("amount_ratio_vs_prev1")
    turnover_ratio = features.get("turnover_ratio_vs_prev1")
    if amount_ratio is not None:
        summary_parts.append(f"成交额比前一日 {amount_ratio:.2f}")
    if turnover_ratio is not None:
        summary_parts.append(f"换手比前一日 {turnover_ratio:.2f}")
    if leaderboard.get("is_listed"):
        summary_parts.append(f"龙虎榜净买占比 {leaderboard.get('top_list_net_rate') or 0:.2f}%")
    if not summary_parts:
        summary_parts.append("当前量价与龙虎榜信号不足")

    return {
        "status": "available",
        "label": label,
        "summary": "；".join(summary_parts),
        "signals": positive_hits[:2] + negative_hits[:2],
        "leaderboard_context": leaderboard,
    }


def build_mixed_trade_date_context(
    trade_date_text: str,
    now: datetime,
    freshness: dict[str, Any],
    kline_sync: dict[str, Any] | None,
    factor_sync: dict[str, Any] | None,
    latest_open_trade_date_on_or_before_fn=None,
) -> dict[str, Any]:
    from time_util import normalize_trade_date_text as _norm
    fn = latest_open_trade_date_on_or_before_fn or (lambda x: None)
    latest_open_trade_date = fn(now.strftime("%Y-%m-%d"))
    is_latest_trade_date = bool(
        latest_open_trade_date and trade_date_text == latest_open_trade_date
    )
    items = freshness.get("items") or {}
    core_dates = {
        "daily": _norm((items.get("daily") or {}).get("latest_trade_date")),
        "stk_factor_pro": _norm(
            (factor_sync or {}).get("latest_trade_date")
            or (items.get("stk_factor_pro") or {}).get("latest_trade_date")
        ),
        "moneyflow": _norm((items.get("moneyflow") or {}).get("latest_trade_date")),
        "cyq_perf": _norm((items.get("cyq_perf") or {}).get("latest_trade_date")),
        "cyq_chips": _norm((items.get("cyq_chips") or {}).get("latest_trade_date")),
    }
    core_statuses = {
        name: str((items.get(name) or {}).get("status") or "") for name in core_dates
    }

    if not is_latest_trade_date:
        return {
            "status": "aligned_or_not_latest",
            "is_latest_trade_date": False,
            "target_trade_date": trade_date_text,
            "latest_open_trade_date": latest_open_trade_date,
            "core_dates": core_dates,
            "core_statuses": core_statuses,
            "blocking_items": [],
            "summary": "目标日不是当前最新交易日，不触发混合时点拦截。",
        }

    hard_blocking_fields = {"daily", "stk_factor_pro"}
    blocking_items: list[str] = []
    warning_items: list[str] = []
    for name, latest_date in core_dates.items():
        if latest_date == trade_date_text:
            continue
        if name in hard_blocking_fields:
            blocking_items.append(name)
        else:
            warning_items.append(name)
    if (
        str((kline_sync or {}).get("status") or "") == "browser_fetch_failed"
        and "daily" not in blocking_items
    ):
        blocking_items.append("daily")
    blocking_items = sorted(set(blocking_items))
    warning_items = sorted(set(warning_items))

    if not blocking_items:
        warning_suffix = ""
        if warning_items:
            detail = "；".join(
                f"{name}={core_dates.get(name) or core_statuses.get(name) or '缺失'}"
                for name in warning_items
            )
            warning_suffix = f"；辅助维度仍非当天（{detail}），相关结论降权使用。"
        return {
            "status": "aligned",
            "is_latest_trade_date": True,
            "target_trade_date": trade_date_text,
            "latest_open_trade_date": latest_open_trade_date,
            "core_dates": core_dates,
            "core_statuses": core_statuses,
            "blocking_items": [],
            "warning_items": warning_items,
            "summary": "最新交易日硬核心维度已对齐到当天，可继续完整推演。" + warning_suffix,
        }

    detail = "；".join(
        f"{name}={core_dates.get(name) or core_statuses.get(name) or '缺失'}"
        for name in blocking_items
    )
    return {
        "status": "mixed_trade_date_context",
        "is_latest_trade_date": True,
        "target_trade_date": trade_date_text,
        "latest_open_trade_date": latest_open_trade_date,
        "core_dates": core_dates,
        "core_statuses": core_statuses,
        "blocking_items": blocking_items,
        "warning_items": warning_items,
        "summary": f"当前是最新交易日，但核心维度未全部同步到当天（{detail}），仅允许结构复盘，禁止完整 T+1/T+2/建仓推演。",
    }


def degrade_prediction_bundle(mixed_context: dict[str, Any], payload: dict[str, Any]) -> None:
    summary = mixed_context.get("summary") or "当前最新交易日存在混合时点上下文，已降级。"
    payload["next_day_bias"] = {
        "status": "mixed_trade_date_context",
        "reason": summary,
        "result": None,
    }
    payload["capital_freshness"] = {
        "status": "mixed_trade_date_context",
        "label": "混合时点已降级",
        "summary": summary,
        "signals": [],
    }
    payload["t_plus_two_bias"] = {
        "status": "mixed_trade_date_context",
        "label": "混合时点已降级",
        "score": None,
        "view": summary,
        "signals": [],
    }
    payload["final_decision"] = {
        "status": "mixed_trade_date_context",
        "data_completeness": None,
        "signal_score": None,
        "bullish_dimensions": [],
        "bearish_dimensions": [],
        "conflicts": mixed_context.get("blocking_items") or [],
        "decision": "仅保留结构复盘",
        "reason": summary,
        "preconditions": ["等待 daily / stk_factor_pro 等硬核心维度同步到当天后再做完整推演"],
        "invalidations": ["若继续使用混合日期数据直接推演，则结论无效"],
        "key_levels": {"observe": None, "confirm": None, "invalid": None},
        "news_supporting_sources": [],
    }


def persist_analysis_history(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        final_decision = payload.get("final_decision") or {}
        final_summary = str(
            final_decision.get("decision") or final_decision.get("reason") or ""
        ).strip()
        checkpoint = str(payload.get("checkpoint") or "").strip() or "unknown"
        created_at = str(
            payload.get("analysis_time") or datetime.now().isoformat(timespec="seconds")
        )
        init_sqlite_schema()
        row_id = insert_analysis_history_row({
            "symbol": str(payload.get("symbol") or ""),
            "trade_date": str(payload.get("trade_date") or ""),
            "checkpoint": checkpoint,
            "final_decision_summary": final_summary,
            "payload": payload,
            "status": "ok",
            "created_at": created_at,
        })
        return {"status": "written", "analysis_history_id": row_id}
    except Exception as exc:
        return {"status": "write_failed", "reason": str(exc)}
```

- [ ] **Step 6: 验证 capital_context**

Run: `cd ~/agent-skills/custom/stock-deep-analysis/scripts && python3 -c "from capital_context import is_event_theme, summarize_capital_freshness; print(is_event_theme('回购增持再贷款'), summarize_capital_freshness({'status':'timeout'}))"`
Expected: `True {'status': 'unavailable', ...}`

---

### Task 4: 重写 build_stock_report.py 为薄编排层

**Files:**
- Modify: `scripts/build_stock_report.py` (全文件重写)

保留：`build_payload()`、`parse_args()`、`main()`、`_phase2_parallel()`、re-export 兼容层。
删除：所有已提取到新模块的函数实现，改为从新模块导入。

- [ ] **Step 7: 重写 `build_stock_report.py`**

```python
#!/usr/bin/env python3
"""
把常用分析脚本的结果汇总成可直接复用的报告骨架。

示例：
  python3 build_stock_report.py --symbol 002639 --trade-date 2026-04-08
  python3 build_stock_report.py --symbol 002639.SZ --trade-date 20260408 --format json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any

from render import report_renderer
from common import normalize_symbol, normalize_trade_date
from data.data_access import (
    load_daily_row as _load_daily_row,
    latest_open_trade_date_on_or_before as _latest_open_trade_date,
    resolve_trade_date_by_calendar as _resolve_trade_date,
    next_trade_dates_compact as _next_trade_dates,
)
from data.portfolio_loader import get_position as _get_position
from signals.core.check_data_freshness import build_report as build_freshness_report
from signals.core.score_next_day_bias import load_narrative_context
from data.config_loader import cfg

# ── 从新模块导入 ──
from time_util import (
    scenario_from_now,
    normalize_trade_date_for_session,
    resolve_checkpoint,
    parse_date_candidates,
    normalize_trade_date_text as _normalize_trade_date_text,
)
from financing_analyzer import (
    safe_float,
    analyze_financing_context,
    build_fundamental as _build_fundamental,
    resolve_symbol as _resolve_symbol,
)
from capital_context import (
    is_event_theme,
    summarize_capital_freshness,
    build_mixed_trade_date_context,
    degrade_prediction_bundle as _degrade_prediction_bundle,
    persist_analysis_history as _persist_analysis_history,
)

# ── 兼容层：从源模块直接 re-export，供 parallel/agents.py 等消费者使用 ──
from time_util import scenario_from_now  # noqa: F811
from analysis.market_analyzer import analyze_market_context as analyze_market_context_impl
from analysis.sector_analyzer import (
    analyze_sector_context as analyze_sector_context_impl,
    build_leader_prediction as build_leader_prediction_impl,
    discover_mobile_subthemes_if_needed as discover_mobile_subthemes_if_needed_impl,
    discover_mobile_theme_leaders_if_needed as discover_mobile_theme_leaders_if_needed_impl,
    load_stock_name as load_stock_name_impl,
    match_mobile_subthemes as match_mobile_subthemes_impl,
)
from analysis.stock_trend_analyzer import (
    analyze_chip_structure as analyze_chip_structure_impl,
    analyze_trend_structure as analyze_trend_structure_impl,
    analyze_volatility_context as analyze_volatility_context_impl,
    safe_next_day as safe_next_day_impl,
)
from runtime.runtime_fetch import safe_intraday as safe_intraday_impl
from signals.core.analyze_auction_intent import analyze_auction_intent as analyze_auction_intent_impl
from decision.decision_engine import (
    analyze_context_propagation as analyze_context_propagation_impl,
    build_final_decision as build_final_decision_impl,
    build_peer_linkage as build_peer_linkage_impl,
    build_validation_tracking as build_validation_tracking_impl,
    persist_pending_validation as persist_pending_validation_impl,
)


# ── Re-export 兼容层（parallel/agents.py 通过 BSR.xxx 调用） ──
def load_stock_name(full_symbol: str) -> str | None:
    return load_stock_name_impl(full_symbol)

def safe_intraday(symbol, trade_date_text, now=None, checkpoint=None):
    return safe_intraday_impl(symbol, trade_date_text, now=now, checkpoint=checkpoint)

def analyze_market_context(full_symbol, trade_date_text):
    return analyze_market_context_impl(full_symbol, trade_date_text)

def analyze_sector_context(symbol, trade_date_text):
    return analyze_sector_context_impl(symbol, trade_date_text)

def analyze_trend_structure(full_symbol, trade_date_text):
    return analyze_trend_structure_impl(full_symbol, trade_date_text)

def analyze_chip_structure(full_symbol, trade_date_text):
    return analyze_chip_structure_impl(full_symbol, trade_date_text)

def analyze_volatility_context(full_symbol, trade_date_text):
    return analyze_volatility_context_impl(full_symbol, trade_date_text)

def safe_next_day(full_symbol, trade_date_compact, narrative_context=None):
    return safe_next_day_impl(full_symbol, trade_date_compact, narrative_context=narrative_context)

def build_peer_linkage(full_symbol, trade_date_text):
    return build_peer_linkage_impl(full_symbol, trade_date_text)

def build_final_decision(payload):
    return build_final_decision_impl(payload)

def analyze_t_plus_two_bias(payload):
    from analysis.stock_trend_analyzer import analyze_t_plus_two_bias as _impl
    return _impl(payload)

def analyze_context_propagation(payload):
    return analyze_context_propagation_impl(payload)

def build_validation_tracking(payload, now):
    return build_validation_tracking_impl(payload, now)

def persist_pending_validation(payload, checkpoint):
    return persist_pending_validation_impl(payload, checkpoint)

def render_status_text(value):
    return report_renderer.render_status_text(value)

def render_action_bias_text(value):
    return report_renderer.render_action_bias_text(value)

def render_acquisition_method_text(value):
    return report_renderer.render_acquisition_method_text(value)


def resolve_now_china() -> tuple[datetime, str]:
    from runtime.runtime_fetch import fetch_china_network_time, parse_network_datetime_text
    try:
        time_text = fetch_china_network_time()
        if time_text:
            return parse_network_datetime_text(time_text), "china_network"
    except Exception:
        pass
    return datetime.now(), "local"


INDEX_DATA_ROOT = cfg.paths("index_data_root")
REFERENCES_ROOT = cfg.paths("references_dir")


def _next_trade_date_compact(trade_date_text: str) -> str:
    dates = _next_trade_dates(trade_date_text, count=1)
    return dates[0] if dates else (
        datetime.strptime(trade_date_text, "%Y-%m-%d").date() + __import__("datetime").timedelta(days=1)
    ).strftime("%Y%m%d")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成股票深度分析报告骨架")
    parser.add_argument("--symbol", required=True, help="如 002639 或 002639.SZ")
    parser.add_argument("--trade-date", required=True, help="格式 YYYY-MM-DD 或 YYYYMMDD")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--news-json", help="手工消息输入 JSON 文件路径")
    parser.add_argument(
        "--checkpoint",
        choices=("auto", "pre_open", "open", "noon", "afternoon", "close", "next_close"),
        default="auto",
    )
    return parser.parse_args()


# ═══════════════════════════════════════════════════════
# Phase 2 — 并行 Agent 调度
# ═══════════════════════════════════════════════════════

def _phase2_parallel(ctx: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Phase 2: 并行执行 8 个分析 Agent。"""
    import functools
    import importlib
    parallel_mod = importlib.import_module("parallel")
    parallel_mod.clear_tmp(ctx.get("trade_date_compact"))
    ParallelAgent = parallel_mod.ParallelAgent
    run_parallel = parallel_mod.run_parallel

    full_symbol = ctx["full_symbol"]
    pure_symbol = ctx["pure_symbol"]
    trade_date_text = ctx["trade_date_text"]
    trade_date_compact = ctx["trade_date_compact"]
    now = ctx["now"]
    resolved_checkpoint = ctx["checkpoint"]
    news_reference_date = ctx["news_reference_date"]

    try:
        from data.data_provider import get_stock_concepts
        concepts = get_stock_concepts(full_symbol) or []
        top_theme = concepts[0] if concepts else None
    except Exception:
        top_theme = None

    agents = [
        ParallelAgent(
            name="kline_sync",
            func=functools.partial(parallel_mod.run_kline_sync_agent, full_symbol=full_symbol, trade_date_text=trade_date_text, now=now),
            timeout=120.0,
            default_result={"status": "timeout", "kline_sync": {"status": "timeout"}, "factor_sync": {"status": "timeout"}},
        ),
        ParallelAgent(
            name="news",
            func=functools.partial(parallel_mod.run_news_agent, full_symbol=full_symbol, trade_date_text=trade_date_text, news_json_path=ctx.get("news_json_path"), news_reference_date=news_reference_date),
            timeout=120.0,
            default_result={"status": "timeout", "narrative_context": {}, "manual_news_raw": {}},
        ),
        ParallelAgent(
            name="intraday",
            func=functools.partial(parallel_mod.run_intraday_agent, pure_symbol=pure_symbol, trade_date_text=trade_date_text, now=now, resolved_checkpoint=resolved_checkpoint),
            timeout=60.0,
            default_result={"status": "timeout", "intraday": {"status": "timeout"}},
        ),
        ParallelAgent(
            name="sector",
            func=functools.partial(parallel_mod.run_sector_agent, full_symbol=full_symbol, trade_date_text=trade_date_text),
            timeout=30.0,
            default_result={"status": "timeout", "market_context": {}, "sector_context": {}},
        ),
        ParallelAgent(
            name="stock_dims",
            func=functools.partial(parallel_mod.run_stock_dims_agent, full_symbol=full_symbol, trade_date_text=trade_date_text, trade_date_compact=trade_date_compact),
            timeout=30.0,
            default_result={"status": "timeout", "financing_context": {}, "auction_intent": {}, "trend_structure": {}, "chip_structure": {}, "volatility_context": {}, "fundamental": {}},
        ),
        ParallelAgent(
            name="dragon_tiger",
            func=functools.partial(parallel_mod.run_dragon_tiger_agent, full_symbol=full_symbol, trade_date_text=trade_date_text, trade_date_compact=trade_date_compact),
            timeout=15.0,
            default_result={"status": "timeout", "signal": None, "overall_score": None},
        ),
        ParallelAgent(
            name="intraday_linkage",
            func=functools.partial(parallel_mod.run_intraday_linkage_agent, pure_symbol=pure_symbol, trade_date_text=trade_date_text, top_theme=top_theme),
            timeout=120.0,
            default_result={"status": "timeout", "linkage_label": "超时"},
        ),
        ParallelAgent(
            name="fundamental_deep",
            func=functools.partial(parallel_mod.run_fundamental_agent, pure_symbol=pure_symbol, full_symbol=full_symbol, trade_date_text=trade_date_text),
            timeout=120.0,
            default_result={"status": "timeout", "financial_health": "超时"},
        ),
    ]
    return run_parallel(agents, trade_date_compact, max_workers=8)


def build_payload(symbol: str, trade_date: str, news_json_path: str | None = None, checkpoint: str = "auto") -> dict:
    symbol = _resolve_symbol(symbol)
    pure_symbol, full_symbol = normalize_symbol(symbol)
    _requested_compact, requested_trade_date_text = normalize_trade_date(trade_date)

    now, time_source = resolve_now_china()
    normalized_requested, session_resolution = normalize_trade_date_for_session(
        now, requested_trade_date_text, checkpoint, _latest_open_trade_date
    )
    resolved_checkpoint = resolve_checkpoint(now, normalized_requested, checkpoint)

    news_reference_date = now.strftime("%Y-%m-%d")
    trade_date_text, trade_cal_meta = _resolve_trade_date(normalized_requested)
    trade_date_compact = trade_date_text.replace("-", "")

    # ── Phase 2: 并行执行 8 个 Agent ──
    ctx = dict(
        full_symbol=full_symbol, pure_symbol=pure_symbol,
        trade_date_text=trade_date_text, trade_date_compact=trade_date_compact,
        now=now, checkpoint=resolved_checkpoint,
        news_reference_date=news_reference_date, news_json_path=news_json_path,
    )
    parallel_results = _phase2_parallel(ctx)

    # ── Phase 3: 合并结果 ──
    from signals.core.analyze_auction_intent import analyze_auction_intent

    kline_sync = (parallel_results.get("kline_sync") or {}).get("kline_sync", {})
    factor_sync = (parallel_results.get("kline_sync") or {}).get("factor_sync", {})
    news_agent = parallel_results.get("news", {})
    narrative_context = news_agent.get("narrative_context", {})
    manual_news_raw = news_agent.get("manual_news_raw", {})
    intraday = (parallel_results.get("intraday") or {}).get("intraday", {})
    market_context = (parallel_results.get("sector") or {}).get("market_context", analyze_market_context(full_symbol, trade_date_text))
    sector_context = (parallel_results.get("sector") or {}).get("sector_context", analyze_sector_context(full_symbol, trade_date_text))
    dims = parallel_results.get("stock_dims", {})
    financing_context = dims.get("financing_context", analyze_financing_context(full_symbol, trade_date_text))
    auction_intent = dims.get("auction_intent", analyze_auction_intent(full_symbol, trade_date_text))
    trend_structure = dims.get("trend_structure", analyze_trend_structure(full_symbol, trade_date_text))
    chip_structure = dims.get("chip_structure", analyze_chip_structure(full_symbol, trade_date_text))
    volatility_context = dims.get("volatility_context", analyze_volatility_context(full_symbol, trade_date_text))
    fundamental = dims.get("fundamental", _build_fundamental(full_symbol, trade_date_compact))
    dragon_tiger_result = parallel_results.get("dragon_tiger", {})
    intraday_linkage = parallel_results.get("intraday_linkage", {})
    fundamental_deep = parallel_results.get("fundamental_deep", {})

    freshness = build_freshness_report(full_symbol, pure_symbol, trade_date_text)
    next_day = safe_next_day(full_symbol, trade_date_compact, narrative_context=narrative_context)
    capital_freshness = summarize_capital_freshness(next_day)
    from runtime.news_runtime import enrich_news_sentiment as _enrich, load_manual_news as _load_manual
    from runtime.news_runtime import auto_resolve_news_json_path as _auto_resolve, enrich_news_pipeline_meta as _enrich_meta
    news_sentiment = _enrich(
        manual_news_raw if manual_news_raw else _load_manual(news_agent.get("resolved_news_json_path"), news_reference_date),
        sector_context,
    )
    stock_name = load_stock_name(full_symbol) or full_symbol

    payload: dict[str, Any] = {
        "symbol": full_symbol, "stock_name": stock_name, "pure_symbol": pure_symbol,
        "trade_date": trade_date_text, "requested_trade_date": requested_trade_date_text,
        "session_trade_date_resolution": session_resolution,
        "news_reference_date": news_reference_date,
        "news_json_path": news_agent.get("resolved_news_json_path"),
        "news_pipeline_meta": _enrich_meta(news_agent.get("news_pipeline_meta", {})),
        "trade_calendar_resolution": trade_cal_meta,
        "kline_sync": kline_sync, "factor_sync": factor_sync,
        "analysis_time": now.isoformat(timespec="seconds"), "time_source": time_source,
        "current_session": scenario_from_now(now),
        "freshness": freshness, "intraday_strength": intraday,
        "next_day_bias": next_day, "capital_freshness": capital_freshness,
        "market_context": market_context, "sector_context": sector_context,
        "financing_context": financing_context, "auction_intent": auction_intent,
        "trend_structure": trend_structure, "chip_structure": chip_structure,
        "volatility_context": volatility_context, "news_sentiment": news_sentiment,
        "narrative_context": narrative_context, "fundamental": fundamental,
    }
    payload["checkpoint"] = resolved_checkpoint
    payload["mixed_trade_date_context"] = build_mixed_trade_date_context(
        trade_date_text, now, freshness, kline_sync, factor_sync, _latest_open_trade_date,
    )
    payload["dimension_results"] = {
        "market_context": market_context, "sector_context": sector_context,
        "news_sentiment": payload["news_sentiment"],
        "peer_linkage": build_peer_linkage(full_symbol, trade_date_text),
        "stock_structure": next_day, "intraday_structure": intraday,
        "auction_intent": auction_intent, "capital_chip_tech": capital_freshness,
        "financing_context": financing_context, "trend_structure": trend_structure,
        "chip_structure": chip_structure, "volatility_context": volatility_context,
        "dragon_tiger": dragon_tiger_result, "intraday_linkage": intraday_linkage,
        "fundamental_deep": fundamental_deep,
    }
    payload["context_propagation"] = analyze_context_propagation(payload)
    payload["t_plus_two_bias"] = analyze_t_plus_two_bias(payload)
    payload["final_decision"] = build_final_decision(payload)
    if payload["mixed_trade_date_context"].get("status") == "mixed_trade_date_context":
        _degrade_prediction_bundle(payload["mixed_trade_date_context"], payload)
    payload["validation_tracking"] = build_validation_tracking(payload, now)
    payload["validation_record_path"] = persist_pending_validation(payload, payload["checkpoint"])
    daily_row = _load_daily_row(full_symbol, trade_date_compact)
    if daily_row is None:
        daily_row = {"close": None}
    payload["current_price"] = safe_float(daily_row.get("close")) if daily_row else None
    payload["portfolio"] = _get_position(full_symbol)
    payload["analysis_history_write"] = _persist_analysis_history(payload)
    return payload


def render_markdown(payload: dict) -> str:
    return report_renderer.render_markdown(payload)


def main() -> int:
    args = parse_args()
    payload = build_payload(args.symbol, args.trade_date, args.news_json, args.checkpoint)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: 验证 build_stock_report 编译**

Run: `cd ~/agent-skills/custom/stock-deep-analysis/scripts && python3 -c "import ast; ast.parse(open('build_stock_report.py').read()); print('syntax OK')"`
Expected: `syntax OK`

---

### Task 5: 更新 parallel/agents.py 改为从源模块直接导入

**Files:**
- Modify: `scripts/parallel/agents.py` (import 部分)

将 `parallel/agents.py` 中 `importlib.import_module("build_stock_report")` 改为从各源模块直接导入，减少对 BSR 的依赖。

- [ ] **Step 9: 查看 agents.py 当前的 BSR 用法**

Run: `cd ~/agent-skills/custom/stock-deep-analysis/scripts && grep -n "BSR\." parallel/agents.py | head -20`

- [ ] **Step 10: 修改 agents.py 的导入**

将 `BSR = importlib.import_module("build_stock_report")` 替换为直接导入：

```python
# 旧: BSR = importlib.import_module("build_stock_report")
# 新: 直接从源模块导入
from time_util import scenario_from_now
from financing_analyzer import safe_float, analyze_financing_context
from analysis.stock_trend_analyzer import (
    analyze_trend_structure, analyze_chip_structure, analyze_volatility_context, safe_next_day,
)
from analysis.market_analyzer import analyze_market_context
from analysis.sector_analyzer import (
    analyze_sector_context, build_leader_prediction,
    discover_mobile_subthemes_if_needed, discover_mobile_theme_leaders_if_needed,
    match_mobile_subthemes, load_stock_name,
)
from signals.core.analyze_auction_intent import analyze_auction_intent
from data.data_access import load_daily_basic_row
from runtime.runtime_fetch import safe_intraday
```

然后将 `BSR.xxx()` 调用替换为直接调用导入的函数名。

- [ ] **Step 11: 验证 agents.py 编译**

Run: `cd ~/agent-skills/custom/stock-deep-analysis/scripts && python3 -c "import ast; ast.parse(open('parallel/agents.py').read()); print('agents.py syntax OK')"`

---

### Task 6: 最终验证

- [ ] **Step 12: 全量语法检查**

Run: `cd ~/agent-skills/custom/stock-deep-analysis/scripts && python3 -c "
import ast
for f in ['build_stock_report.py', 'time_util.py', 'financing_analyzer.py', 'capital_context.py', 'parallel/agents.py']:
    ast.parse(open(f).read())
    print(f'{f}: OK')
"`

- [ ] **Step 13: 导入链验证**

Run: `cd ~/agent-skills/custom/stock-deep-analysis/scripts && python3 -c "from build_stock_report import build_payload; print('import chain OK')"`

- [ ] **Step 14: 统计行数变化**

Run: `cd ~/agent-skills/custom/stock-deep-analysis/scripts && wc -l build_stock_report.py time_util.py financing_analyzer.py capital_context.py`
