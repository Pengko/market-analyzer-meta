"""
Infoway 分钟数据持久化模块

将 Infoway WebSocket 推送的分钟数据持久化到标准目录结构
目录结构: 分钟数据/YYYY/MM/DD/{symbol}/1m.csv

设计方案: Infoway分钟数据接入设计方案.md
"""

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from data.config_loader import cfg
from infoway_adapter import infoway_bar_to_standard, infoway_kline_array_to_standard

# 分钟数据根目录
MINUTE_ROOT = Path(os.environ.get("MINUTE_DATA_ROOT") or cfg.paths("minute"))


def persist_infoway_bar(
    symbol: str,
    infoway_bar: Dict[str, Any],
    trade_date: Optional[str] = None
) -> Optional[Path]:
    """
    持久化单条 Infoway 分钟数据
    
    Args:
        symbol: 股票代码，如 "002594.SZ"
        infoway_bar: Infoway 推送的单条K线数据
        trade_date: 可选，交易日期(YYYY-MM-DD)，默认从 bar 的时间戳推断
    
    Returns:
        写入的文件路径，失败返回 None
    """
    # 转换为标准格式
    standard_bar = infoway_bar_to_standard(infoway_bar)
    
    # 如果没有提供交易日期，从时间戳推断
    if not trade_date:
        datetime_str = standard_bar.get("datetime", "")
        if datetime_str and len(datetime_str) >= 10:
            trade_date = datetime_str[:10]
        else:
            trade_date = datetime.now().strftime("%Y-%m-%d")
    
    return _write_single_bar(symbol, standard_bar, trade_date)


def persist_infoway_bars(
    symbol: str,
    infoway_bars: list[Dict[str, Any]],
    trade_date: Optional[str] = None
) -> Optional[Path]:
    """
    批量持久化 Infoway 分钟数据
    
    Args:
        symbol: 股票代码
        infoway_bars: Infoway K线数组
        trade_date: 可选，默认从第一条数据推断
    
    Returns:
        写入的文件路径
    """
    if not infoway_bars:
        return None
    
    # 转换为标准格式
    standard_bars = infoway_kline_array_to_standard(infoway_bars, symbol)
    
    if not standard_bars:
        return None
    
    # 如果没有提供交易日期，从第一条推断
    if not trade_date:
        datetime_str = standard_bars[0].get("datetime", "")
        if datetime_str and len(datetime_str) >= 10:
            trade_date = datetime_str[:10]
        else:
            trade_date = datetime.now().strftime("%Y-%m-%d")
    
    return _write_multiple_bars(symbol, standard_bars, trade_date)


def _write_single_bar(
    symbol: str,
    bar: Dict[str, Any],
    trade_date: str
) -> Optional[Path]:
    """将单条分钟数据写入CSV（append模式）"""
    y, m, d = trade_date.split("-")
    target = MINUTE_ROOT / y / m / d / symbol / "1m.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    
    # 检查文件是否存在
    file_exists = target.exists()
    
    with target.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["datetime", "open", "high", "low", "close", "volume", "amount", "avg"]
        )
        
        # 如果文件不存在，写入表头
        if not file_exists:
            writer.writeheader()
        
        writer.writerow(bar)
    
    return target


def _write_multiple_bars(
    symbol: str,
    bars: list[Dict[str, Any]],
    trade_date: str
) -> Optional[Path]:
    """批量写入分钟数据（覆盖模式）"""
    y, m, d = trade_date.split("-")
    target = MINUTE_ROOT / y / m / d / symbol / "1m.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    
    with target.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["datetime", "open", "high", "low", "close", "volume", "amount", "avg"]
        )
        writer.writeheader()
        writer.writerows(bars)
    
    return target


def read_minute_data(
    symbol: str,
    trade_date: str
) -> Optional[List[Dict[str, Any]]]:
    """
    读取指定股票指定日期的分钟数据
    
    Args:
        symbol: 股票代码
        trade_date: 交易日期 (YYYY-MM-DD)
    
    Returns:
        分钟数据列表，每条包含 datetime, open, high, low, close, volume, amount, avg
    """
    y, m, d = trade_date.split("-")
    target = MINUTE_ROOT / y / m / d / symbol / "1m.csv"
    
    if not target.exists():
        return None
    
    bars = []
    with target.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append({
                "datetime": row["datetime"],
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": float(row.get("volume", 0)),
                "amount": float(row.get("amount", 0)),
                "avg": float(row.get("avg", 0)),
            })
    
    return bars


def append_raw_payload(
    symbol: str,
    payload: Dict[str, Any],
    trade_date: str
) -> Optional[Path]:
    """
    将原始 Infoway 响应保存到原始数据目录（用于调试和审计）
    
    Args:
        symbol: 股票代码
        payload: 原始响应数据
        trade_date: 交易日期
    
    Returns:
        保存的文件路径
    """
    y, m, d = trade_date.split("-")
    target = MINUTE_ROOT / y / m / d / symbol / "infoway_raw.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    
    # 读取已有数据（如果存在）
    data = []
    if target.exists():
        with target.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = []
    
    # 添加新数据（带时间戳）
    payload_with_ts = {
        "_received_at": datetime.now().isoformat(),
        **payload
    }
    data.append(payload_with_ts)
    
    # 写回文件
    with target.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    return target


# 兼容性包装（与 runtime_quality.py 的接口保持一致）
def persist_browser_minute_payload(
    symbol: str,
    trade_date_text: str,
    payload: Dict[str, Any],
) -> Optional[Path]:
    """
    兼容性包装器：接收 browser 取回的分钟数据并持久化
    
    这个函数的接口与 runtime_quality.py 中的函数保持一致，
    便于后续统一替换。
    
    Args:
        symbol: 股票代码
        trade_date_text: 交易日期
        payload: 包含 bars 字段的响应数据
    
    Returns:
        保存的文件路径
    """
    bars = payload.get("bars", [])
    if not bars:
        return None
    
    return persist_infoway_bars(symbol, bars, trade_date_text)
