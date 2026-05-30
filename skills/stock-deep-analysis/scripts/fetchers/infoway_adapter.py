"""
Infoway 数据格式转换器

将 Infoway WebSocket 推送的原始数据转换为 skill 内部标准格式
兼容 Infoway MCP Server 的 get_kline 返回格式

设计方案: Infoway分钟数据接入设计方案.md
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Union


def infoway_bar_to_standard(infoway_bar: Dict[str, Any]) -> Dict[str, Union[float, str]]:
    """
    将 Infoway WebSocket 推送的单条K线数据转换为标准格式
    
    Infoway 字段:
        - s: 标的代码 (002594.SZ)
        - o: 开盘价
        - h: 最高价
        - l: 最低价
        - c: 收盘价
        - v: 成交量
        - vw: 成交额
        - t: 秒时间戳 (UTC+8)
        - ty: K线类型 (1=1分钟, 2=5分钟, ...)
        - pca: 涨跌额
        - pfr: 涨跌幅
    
    标准字段:
        - datetime: 日期时间 (格式: YYYY-MM-DD HH:MM)
        - open: 开盘价
        - high: 最高价
        - low: 最低价
        - close: 收盘价
        - volume: 成交量
        - amount: 成交额
        - avg: 均价 (计算得出)
    """
    # 解析时间戳
    ts = infoway_bar.get("t")
    if isinstance(ts, (int, float)):
        dt = datetime.fromtimestamp(ts)
    else:
        # 如果 t 是字符串或其他格式，尝试转换
        dt = datetime.fromtimestamp(float(ts))
    
    datetime_str = dt.strftime("%Y-%m-%d %H:%M")
    
    # 解析价格数据
    open_price = float(infoway_bar.get("o", 0))
    high_price = float(infoway_bar.get("h", 0))
    low_price = float(infoway_bar.get("l", 0))
    close_price = float(infoway_bar.get("c", 0))
    
    # 成交量和成交额
    volume = float(infoway_bar.get("v", 0))
    amount = float(infoway_bar.get("vw", 0))
    
    # 计算均价: (high + low + close) / 3
    # 备用方案: amount / volume / 100 (如果 volume > 0)
    if volume > 0:
        avg_price = round((high_price + low_price + close_price) / 3, 4)
    else:
        avg_price = close_price
    
    return {
        "datetime": datetime_str,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
        "amount": amount,
        "avg": avg_price,
    }


def infoway_kline_array_to_standard(
    kline_array: list[Dict[str, Any]],
    symbol: Optional[str] = None
) -> list[Dict[str, Union[float, str]]]:
    """
    将 Infoway 返回的K线数组批量转换为标准格式
    
    适用于: MCP Server get_kline 返回的批量数据
    
    Args:
        kline_array: Infoway 返回的K线数组
        symbol: 可选，股票代码（用于校验）
    
    Returns:
        标准格式的K线列表
    """
    result = []
    for bar in kline_array:
        # 如果 bar 是 Infoway 格式（有 s/t/ty 字段），转换
        if "s" in bar or "t" in bar:
            # 校验 symbol
            if symbol and bar.get("s") != symbol:
                continue
            result.append(infoway_bar_to_standard(bar))
        # 如果已经是标准格式，直接使用
        elif "datetime" in bar:
            result.append(bar)
        else:
            # 其他格式，尝试通用转换
            result.append(_generic_bar_to_standard(bar))
    
    return result


def _generic_bar_to_standard(bar: Dict[str, Any]) -> Dict[str, Union[float, str]]:
    """
    通用K线格式转换（兼容其他数据源）
    
    支持字段映射:
        - 时间: datetime/dt/time/t/timestamp
        - 开盘: open/o
        - 最高: high/h
        - 最低: low/l
        - 收盘: close/c
        - 成交量: volume/vol/v
        - 成交额: amount/amt/vw
    """
    # 时间字段
    datetime_str = ""
    for key in ["datetime", "dt", "time"]:
        if key in bar:
            val = str(bar[key])
            if len(val) >= 16:
                datetime_str = val[:16].replace("T", " ")
            break
    
    # 如果没有日期时间，尝试从时间戳解析
    if not datetime_str and "t" in bar:
        ts = bar["t"]
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts)
            datetime_str = dt.strftime("%Y-%m-%d %H:%M")
    
    # 价格字段
    open_price = _extract_number(bar, ["open", "o"])
    high_price = _extract_number(bar, ["high", "h"])
    low_price = _extract_number(bar, ["low", "l"])
    close_price = _extract_number(bar, ["close", "c"])
    
    # 成交量和额
    volume = _extract_number(bar, ["volume", "vol", "v"])
    amount = _extract_number(bar, ["amount", "amt", "vw"])
    
    # 计算均价
    if volume > 0:
        avg_price = round((high_price + low_price + close_price) / 3, 4)
    else:
        avg_price = close_price
    
    return {
        "datetime": datetime_str,
        "open": open_price,
        "high": high_price,
        "low": low_price,
        "close": close_price,
        "volume": volume,
        "amount": amount,
        "avg": avg_price,
    }


def _extract_number(bar: Dict[str, Any], keys: list[str]) -> float:
    """从 bar 中按优先级提取数值"""
    for key in keys:
        if key in bar and bar[key] is not None:
            try:
                return float(bar[key])
            except (ValueError, TypeError):
                continue
    return 0.0


# 周期映射表
INFOWAY_PERIOD_MAP = {
    1: "1m",    # 1分钟
    2: "5m",    # 5分钟
    3: "15m",   # 15分钟
    4: "30m",   # 30分钟
    5: "60m",   # 1小时
    6: "120m",  # 2小时
    7: "d",     # 日线
    8: "w",     # 周线
    9: "m",     # 月线
    10: "q",    # 季线
    11: "y",    # 年线
}

PERIOD_TO_INFOWAY = {v: k for k, v in INFOWAY_PERIOD_MAP.items()}


def period_to_infoway_type(period: str) -> int:
    """
    将标准周期标识转换为 Infoway type 参数
    
    Args:
        period: 周期标识，如 "1m", "5m", "d", "w"
    
    Returns:
        Infoway type 值
    """
    return PERIOD_TO_INFOWAY.get(period, 1)  # 默认1分钟


def infoway_type_to_period(type_value: int) -> str:
    """
    将 Infoway type 参数转换为标准周期标识
    """
    return INFOWAY_PERIOD_MAP.get(type_value, "1m")