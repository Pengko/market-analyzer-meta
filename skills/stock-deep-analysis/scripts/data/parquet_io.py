#!/usr/bin/env python3
"""
Parquet 读写工具模块
用于替代 jsonl，存储分析报告的结构化数据
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def flatten_payload_for_parquet(payload: dict[str, Any]) -> dict[str, Any]:
    """
    将嵌套的 payload 扁平化为一行 parquet 数据
    
    提取关键字段用于后续分析和查询
    """
    # 基础信息
    row: dict[str, Any] = {
        "symbol": payload.get("symbol"),
        "stock_name": payload.get("stock_name"),
        "trade_date": payload.get("trade_date"),
        "checkpoint": payload.get("checkpoint"),
        "analysis_time": payload.get("analysis_time"),
        "current_price": payload.get("current_price"),
    }
    
    # 决策结果
    final_decision = payload.get("final_decision") or {}
    row["decision"] = final_decision.get("decision")
    row["decision_score"] = final_decision.get("score")
    row["decision_confidence"] = final_decision.get("confidence")
    row["decision_reason"] = final_decision.get("reason")
    
    # 次日偏次
    next_day = payload.get("next_day_bias") or {}
    if next_day.get("status") == "available":
        result = next_day.get("result") or {}
        row["next_day_label"] = result.get("label")
        row["next_day_score"] = result.get("score")
        row["next_day_view"] = result.get("next_day_view")
    
    # T+2 推演
    t_plus_two = payload.get("t_plus_two_bias") or {}
    if t_plus_two.get("status") == "available":
        row["t2_label"] = t_plus_two.get("label")
        row["t2_score"] = t_plus_two.get("score")
        row["t2_view"] = t_plus_two.get("t2_view")
    
    # 筹码结构
    chip = payload.get("chip_structure") or {}
    row["winner_rate"] = chip.get("winner_rate")
    row["cost_avg"] = chip.get("cost_avg")
    row["cost_85"] = chip.get("cost_85")
    row["cost_95"] = chip.get("cost_95")
    
    # 波动率
    volatility = payload.get("volatility_context") or {}
    row["atr14"] = volatility.get("atr14")
    row["atr_percentile"] = volatility.get("atr_percentile")
    row["volatility_level"] = volatility.get("volatility_level")
    
    # 分时强度
    intraday = payload.get("intraday_strength") or {}
    if intraday.get("status") == "available":
        result = intraday.get("result") or {}
        row["intraday_label"] = result.get("label")
        row["intraday_score"] = result.get("score")
    
    # 资金面
    capital = payload.get("capital_freshness") or {}
    row["capital_freshness_status"] = capital.get("status")
    row["capital_freshness_summary"] = capital.get("summary")
    
    # 验证追踪
    tracking = payload.get("validation_tracking") or {}
    row["record_status"] = tracking.get("record_status")
    row["t_plus_1_trade_date"] = tracking.get("t_plus_1_trade_date")
    row["t_plus_2_trade_date"] = tracking.get("t_plus_2_trade_date")
    
    # 持仓信息
    portfolio = payload.get("portfolio") or {}
    row["has_position"] = bool(portfolio.get("positions"))
    row["position_shares"] = portfolio.get("total_shares")
    row["position_cost"] = portfolio.get("avg_cost")
    row["position_pnl_pct"] = portfolio.get("pnl_pct")
    
    return row


def save_analysis_parquet(
    target_path: Path,
    payload: dict[str, Any],
    mode: str = "overwrite"
) -> Path:
    """
    保存分析结果为 parquet 文件
    
    Args:
        target_path: 目标文件路径（不含扩展名）
        payload: 分析结果 payload
        mode: "overwrite" 覆盖, "append" 追加
    
    Returns:
        实际保存的文件路径
    """
    row = flatten_payload_for_parquet(payload)
    df = pd.DataFrame([row])
    
    parquet_path = target_path.with_suffix(".parquet")
    parquet_path.parent.mkdir(parents=True, exist_ok=True)
    
    if mode == "append" and parquet_path.exists():
        existing_df = pq.read_table(parquet_path).to_pandas()
        # 去重：同一天同一 checkpoint 只保留最新
        df = pd.concat([existing_df, df], ignore_index=True)
        df = df.drop_duplicates(
            subset=["symbol", "trade_date", "checkpoint"],
            keep="last"
        )
    
    table = pa.Table.from_pandas(df)
    pq.write_table(table, parquet_path)
    
    return parquet_path


def load_analysis_parquet(
    parquet_path: Path,
    symbol: str | None = None,
    trade_date: str | None = None,
) -> pd.DataFrame:
    """
    读取分析结果 parquet 文件
    
    Args:
        parquet_path: parquet 文件路径
        symbol: 可选，筛选股票代码
        trade_date: 可选，筛选交易日期
    
    Returns:
        DataFrame
    """
    if not parquet_path.exists():
        return pd.DataFrame()
    
    df = pq.read_table(parquet_path).to_pandas()
    
    if symbol:
        df = df[df["symbol"] == symbol]
    if trade_date:
        df = df[df["trade_date"] == trade_date]
    
    return df


def load_all_analyses(
    base_dir: Path,
    symbol: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> pd.DataFrame:
    """
    加载目录下所有分析结果
    
    Args:
        base_dir: 报告根目录
        symbol: 可选，筛选股票代码
        start_date: 可选，起始日期 (YYYY-MM-DD)
        end_date: 可选，结束日期 (YYYY-MM-DD)
    
    Returns:
        DataFrame
    """
    all_dfs: list[pd.DataFrame] = []
    
    for parquet_file in base_dir.rglob("*.parquet"):
        try:
            df = pq.read_table(parquet_file).to_pandas()
            all_dfs.append(df)
        except Exception:
            continue
    
    if not all_dfs:
        return pd.DataFrame()
    
    combined = pd.concat(all_dfs, ignore_index=True)
    
    if symbol:
        combined = combined[combined["symbol"] == symbol]
    if start_date:
        combined = combined[combined["trade_date"] >= start_date]
    if end_date:
        combined = combined[combined["trade_date"] <= end_date]
    
    return combined
