#!/usr/bin/env python3
"""
数据质量自动检测模块

在分析前自动检测 parquet 数据中的垃圾数据、占位值、异常值。
每个检测规则独立函数，返回结构化结果，不抛异常。

Usage:
    from data.validate_data_quality import validate_stock_data
    result = validate_stock_data('000725.SZ', '20260529')
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from data.config_loader import cfg


def _read_parquet(path: Path) -> Any | None:
    """安全读取 parquet 文件，失败返回 None。"""
    if not path.exists():
        return None
    try:
        return pq.read_table(path)
    except Exception:
        return None


def _col_values(table: Any, name: str) -> list[Any] | None:
    """提取列值为 Python list，列不存在返回 None。"""
    if name not in table.column_names:
        return None
    return table.column(name).to_pylist()


def _all_equal(values: list[Any]) -> bool:
    """检查列表所有值是否相等。"""
    if len(values) < 2:
        return True
    first = values[0]
    return all(v == first for v in values)


def _check_empty_table(table: Any, data_type: str) -> dict | None:
    """通用检测：空文件 / 仅 1 行占位数据。"""
    rows = len(table)
    if rows == 0:
        return {
            "data_type": data_type,
            "status": "invalid",
            "message": f"{data_type} 文件为空（0 行）",
            "recommendation": "需要重新抓取数据",
        }
    if rows == 1:
        return {
            "data_type": data_type,
            "status": "stale",
            "message": f"{data_type} 仅 1 行，疑似占位数据",
            "recommendation": f"检查 {data_type} 数据是否已过期",
        }
    return None


# ── cyq_chips 检测 ──────────────────────────────────────────────────────────


def check_cyq_chips(table: Any, latest_close: float | None) -> dict:
    """检测 cyq_chips 数据质量：percent 全等、price 偏离实际股价。"""
    empty = _check_empty_table(table, "cyq_chips")
    if empty:
        return empty

    issues: list[str] = []

    percents = _col_values(table, "percent")
    if percents and _all_equal(percents):
        issues.append(f"所有 percent={percents[0]}，疑似占位数据")

    prices = _col_values(table, "price")
    if prices and latest_close is not None:
        avg_price = sum(p for p in prices if p is not None and p > 0) / max(
            1, sum(1 for p in prices if p is not None and p > 0)
        )
        if avg_price > 0 and latest_close > 0:
            deviation = abs(avg_price - latest_close) / latest_close
            if deviation > 0.5:
                issues.append(
                    f"price 均值 {avg_price:.2f} 与最新收盘价 {latest_close:.2f} 偏离 {deviation:.0%}"
                )

    if issues:
        return {
            "data_type": "cyq_chips",
            "status": "invalid",
            "message": "；".join(issues),
            "recommendation": "建议废弃 cyq_chips，使用 cyq_perf",
        }
    return {
        "data_type": "cyq_chips",
        "status": "ok",
        "message": f"cyq_chips 数据正常（{len(table)} 行）",
        "recommendation": "",
    }


# ── cyq_perf 检测 ──────────────────────────────────────────────────────────


def check_cyq_perf(table: Any) -> dict:
    """检测 cyq_perf 数据质量：winner_rate 范围、分位数递增。"""
    empty = _check_empty_table(table, "cyq_perf")
    if empty:
        return empty

    issues: list[str] = []

    winner_rates = _col_values(table, "winner_rate")
    if winner_rates:
        invalid = [
            w for w in winner_rates
            if w is not None and (w < 0 or w > 100)
        ]
        if invalid:
            issues.append(f"winner_rate 超出 0-100 范围（{len(invalid)} 行）")

    weight_avgs = _col_values(table, "weight_avg")
    if weight_avgs:
        non_pos = [
            w for w in weight_avgs
            if w is not None and w <= 0
        ]
        if non_pos:
            issues.append(f"weight_avg 非正数（{len(non_pos)} 行）")

    cost5 = _col_values(table, "cost_5pct")
    cost50 = _col_values(table, "cost_50pct")
    cost95 = _col_values(table, "cost_95pct")
    if cost5 and cost50 and cost95:
        bad_order = 0
        for c5, c50, c95 in zip(cost5, cost50, cost95):
            if (
                c5 is not None and c50 is not None and c95 is not None
                and not (c5 <= c50 <= c95)
            ):
                bad_order += 1
        if bad_order > 0:
            issues.append(f"cost_5pct <= cost_50pct <= cost_95pct 不满足（{bad_order} 行）")

    if issues:
        return {
            "data_type": "cyq_perf",
            "status": "invalid",
            "message": "；".join(issues),
            "recommendation": "检查数据源是否异常",
        }
    return {
        "data_type": "cyq_perf",
        "status": "ok",
        "message": f"cyq_perf 数据正常（{len(table)} 行）",
        "recommendation": "",
    }


# ── daily 检测 ──────────────────────────────────────────────────────────────


def check_daily(table: Any) -> dict:
    """检测 daily 数据质量：close 正数、vol 非负、关键字段无 NaN。"""
    empty = _check_empty_table(table, "daily")
    if empty:
        return empty

    issues: list[str] = []

    closes = _col_values(table, "close")
    if closes:
        bad_close = [
            c for c in closes
            if c is None or (isinstance(c, float) and math.isnan(c)) or c <= 0
        ]
        if bad_close:
            issues.append(f"close 含无效值（{len(bad_close)} 行为空/NaN/非正数）")

    vols = _col_values(table, "vol")
    if vols:
        bad_vol = [
            v for v in vols
            if v is not None and (isinstance(v, float) and math.isnan(v) or v < 0)
        ]
        if bad_vol:
            issues.append(f"vol 含负数或 NaN（{len(bad_vol)} 行）")

    for field in ["open", "high", "low", "close"]:
        vals = _col_values(table, field)
        if vals:
            nan_count = sum(
                1 for v in vals
                if v is None or (isinstance(v, float) and math.isnan(v))
            )
            if nan_count > 0:
                issues.append(f"{field} 含 {nan_count} 个 NaN/None")

    if issues:
        return {
            "data_type": "daily",
            "status": "invalid",
            "message": "；".join(issues),
            "recommendation": "检查数据抓取逻辑",
        }
    return {
        "data_type": "daily",
        "status": "ok",
        "message": f"daily 数据正常（{len(table)} 行）",
        "recommendation": "",
    }


# ── stk_factor_pro 检测 ────────────────────────────────────────────────────


def check_stk_factor_pro(table: Any) -> dict:
    """检测 stk_factor_pro：最近 5 日全 NaN 行、RSI 范围。"""
    empty = _check_empty_table(table, "stk_factor_pro")
    if empty:
        return empty

    issues: list[str] = []

    close_vals = _col_values(table, "close")
    if close_vals and len(close_vals) >= 5:
        last5 = close_vals[-5:]
        nan_rows = sum(
            1 for v in last5
            if v is None or (isinstance(v, float) and math.isnan(v))
        )
        if nan_rows == 5:
            issues.append("最近 5 日 close 全为 NaN，数据可能已断更")

    rsi_cols = [c for c in table.column_names if c.startswith("rsi_")]
    for col in rsi_cols:
        vals = _col_values(table, col)
        if vals:
            bad_rsi = [
                v for v in vals[-20:]
                if v is not None and (v < 0 or v > 100)
            ]
            if bad_rsi:
                issues.append(f"{col} 最近 20 行有 {len(bad_rsi)} 个超出 0-100 范围")

    if issues:
        return {
            "data_type": "stk_factor_pro",
            "status": "invalid",
            "message": "；".join(issues),
            "recommendation": "检查因子计算逻辑",
        }
    return {
        "data_type": "stk_factor_pro",
        "status": "ok",
        "message": f"stk_factor_pro 数据正常（{len(table)} 行）",
        "recommendation": "",
    }


# ── 主入口 ──────────────────────────────────────────────────────────────────


def validate_stock_data(full_symbol: str, trade_date_compact: str) -> dict:
    """校验单只股票的数据质量，返回检测结果。

    Args:
        full_symbol: 完整股票代码，如 "000725.SZ"
        trade_date_compact: 交易日期，如 "20260529"

    Returns:
        {
            "status": "ok" | "warnings" | "critical",
            "checks": [...],
            "summary": "2/5 维度通过校验"
        }
    """
    stock_root = cfg.paths("stock_data_root")
    code = full_symbol

    # 读取所有数据
    daily_table = _read_parquet(stock_root / "daily" / f"{code}.parquet")
    cyq_chips_table = _read_parquet(stock_root / "cyq_chips" / f"{code}.parquet")
    cyq_perf_table = _read_parquet(stock_root / "cyq_perf" / f"{code}.parquet")
    stk_factor_table = _read_parquet(stock_root / "stk_factor_pro" / f"{code}.parquet")

    # 获取最新收盘价供 cyq_chips 偏离检测
    latest_close: float | None = None
    if daily_table is not None:
        closes = _col_values(daily_table, "close")
        if closes:
            for c in reversed(closes):
                if c is not None and not (isinstance(c, float) and math.isnan(c)) and c > 0:
                    latest_close = float(c)
                    break

    checks: list[dict[str, str]] = []

    # cyq_chips: 文件不存在标记为 missing
    if cyq_chips_table is not None:
        checks.append(check_cyq_chips(cyq_chips_table, latest_close))
    else:
        checks.append({
            "data_type": "cyq_chips",
            "status": "missing",
            "message": "cyq_chips 文件不存在",
            "recommendation": "需要抓取筹码分布数据",
        })

    if cyq_perf_table is not None:
        checks.append(check_cyq_perf(cyq_perf_table))
    else:
        checks.append({
            "data_type": "cyq_perf",
            "status": "missing",
            "message": "cyq_perf 文件不存在",
            "recommendation": "需要抓取筹码性能数据",
        })

    if daily_table is not None:
        checks.append(check_daily(daily_table))
    else:
        checks.append({
            "data_type": "daily",
            "status": "missing",
            "message": "daily 文件不存在",
            "recommendation": "需要抓取日线数据",
        })

    if stk_factor_table is not None:
        checks.append(check_stk_factor_pro(stk_factor_table))
    else:
        checks.append({
            "data_type": "stk_factor_pro",
            "status": "missing",
            "message": "stk_factor_pro 文件不存在",
            "recommendation": "需要抓取因子数据",
        })

    # 汇总
    invalid_count = sum(1 for c in checks if c["status"] in ("invalid", "missing"))
    ok_count = sum(1 for c in checks if c["status"] == "ok")
    total = len(checks)

    if invalid_count >= 2:
        status = "critical"
    elif invalid_count >= 1:
        status = "warnings"
    else:
        status = "ok"

    return {
        "status": status,
        "checks": checks,
        "summary": f"{ok_count}/{total} 维度通过校验",
    }


if __name__ == "__main__":
    import json
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "000725.SZ"
    date = sys.argv[2] if len(sys.argv) > 2 else "20260529"
    result = validate_stock_data(symbol, date)
    print(json.dumps(result, ensure_ascii=False, indent=2))
