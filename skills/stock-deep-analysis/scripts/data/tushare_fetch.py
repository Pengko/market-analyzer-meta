"""
轻量级 Tushare 数据拉取 —— 分析专用，不落盘。

用途：
- 分析时直接调 API 拿最新数据
- 不写本地文件，不更新白名单
- 结果直接返回给分析脚本使用

用法：
    from data.tushare_fetch import fetch_daily, fetch_factors, fetch_moneyflow
    
    daily = fetch_daily("000725.SZ", "20260605")
    factors = fetch_factors("000725.SZ", "20260605")
    moneyflow = fetch_moneyflow("000725.SZ", "20260605")
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# 加载 tushare client
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

from data.config_loader import cfg

# tushare_pro 路径
_TUSHARE_PRO_ROOT = Path.home() / ".openclaw" / "skills" / "custom" / "tushare_pro"
if str(_TUSHARE_PRO_ROOT) not in sys.path:
    sys.path.insert(0, str(_TUSHARE_PRO_ROOT))


def _get_pro():
    """获取 tushare pro API 客户端"""
    from utils.tushare_client import create_pro_api
    return create_pro_api()


def _get_trade_dates(start: str, end: str) -> list[str]:
    """获取交易日历"""
    pro = _get_pro()
    cal = pro.trade_cal(exchange="SSE", start_date=start, end_date=end)
    open_days = cal[cal["is_open"] == 1]["cal_date"].astype(str).tolist()
    return sorted(open_days)


def fetch_daily(symbol: str, trade_date: str) -> dict[str, Any] | None:
    """
    拉取个股日线数据（不落盘）
    
    Args:
        symbol: 股票代码，如 "000725.SZ"
        trade_date: 交易日期，如 "20260605"
    
    Returns:
        日线数据字典，包含 open/high/low/close/vol/amount/pct_chg 等
    """
    pro = _get_pro()
    df = pro.daily(ts_code=symbol, trade_date=trade_date)
    if df is not None and len(df) > 0:
        return df.iloc[0].to_dict()
    return None


def fetch_daily_history(symbol: str, days: int = 10) -> list[dict]:
    """
    拉取个股近 N 个交易日日线（不落盘）
    
    Args:
        symbol: 股票代码
        days: 拉取天数
    
    Returns:
        日线数据列表，按日期升序
    """
    pro = _get_pro()
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    
    df = pro.daily(ts_code=symbol, start_date=start_date, end_date=end_date)
    if df is not None and len(df) > 0:
        df = df.sort_values("trade_date").tail(days)
        return df.to_dict("records")
    return []


def fetch_factors(symbol: str, trade_date: str) -> dict[str, Any] | None:
    """
    拉取技术因子数据（不落盘）
    
    Args:
        symbol: 股票代码
        trade_date: 交易日期
    
    Returns:
        技术因子字典，包含 KDJ/MACD/RSI/MA/BOLL 等
    """
    pro = _get_pro()
    df = pro.stk_factor(ts_code=symbol, trade_date=trade_date)
    if df is not None and len(df) > 0:
        return df.iloc[0].to_dict()
    return None


def fetch_moneyflow(symbol: str, trade_date: str) -> dict[str, Any] | None:
    """
    拉取个股资金流向（不落盘）
    
    Args:
        symbol: 股票代码
        trade_date: 交易日期
    
    Returns:
        资金流向字典，包含 net_amount/buy_lg_amount/sell_lg_amount 等
    """
    pro = _get_pro()
    df = pro.moneyflow(ts_code=symbol, trade_date=trade_date)
    if df is not None and len(df) > 0:
        return df.iloc[0].to_dict()
    return None


def fetch_moneyflow_ths(symbol: str, trade_date: str) -> dict[str, Any] | None:
    """
    拉取同花顺资金流向（不落盘）
    
    Args:
        symbol: 股票代码
        trade_date: 交易日期
    
    Returns:
        资金流向字典
    """
    pro = _get_pro()
    df = pro.moneyflow_ths(ts_code=symbol, trade_date=trade_date)
    if df is not None and len(df) > 0:
        return df.iloc[0].to_dict()
    return None


def fetch_index_daily(index_code: str, trade_date: str) -> dict[str, Any] | None:
    """
    拉取指数日线（不落盘）
    
    Args:
        index_code: 指数代码，如 "000001.SH"
        trade_date: 交易日期
    
    Returns:
        指数日线数据字典
    """
    pro = _get_pro()
    df = pro.index_daily(ts_code=index_code, trade_date=trade_date)
    if df is not None and len(df) > 0:
        return df.iloc[0].to_dict()
    return None


def fetch_top_list(trade_date: str) -> list[dict]:
    """
    拉取龙虎榜数据（不落盘）
    
    Args:
        trade_date: 交易日期
    
    Returns:
        龙虎榜数据列表
    """
    pro = _get_pro()
    df = pro.top_list(trade_date=trade_date)
    if df is not None and len(df) > 0:
        return df.to_dict("records")
    return []


def fetch_snapshot_tencent(symbol: str) -> dict[str, Any] | None:
    """
    从腾讯 API 拉取实时行情快照（不落盘）
    
    Args:
        symbol: 股票代码，如 "000725" 或 "000725.SZ"
    
    Returns:
        实时行情字典
    """
    import urllib.request
    
    code = symbol.replace(".SZ", "").replace(".SH", "")
    if symbol.endswith(".SH") or (not symbol.endswith(".SZ") and code.startswith("6")):
        prefix = "sh"
    else:
        prefix = "sz"
    url = f"http://qt.gtimg.cn/q={prefix}{code}"
    
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("gbk")
        
        parts = data.split("~")
        if len(parts) < 50:
            return None
        
        return {
            "name": parts[1],
            "code": parts[2],
            "current": float(parts[3]) if parts[3] else 0,
            "prev_close": float(parts[4]) if parts[4] else 0,
            "open": float(parts[5]) if parts[5] else 0,
            "high": float(parts[33]) if parts[33] else 0,
            "low": float(parts[34]) if parts[34] else 0,
            "change_pct": float(parts[32]) if parts[32] else 0,
            "volume": float(parts[6]) if parts[6] else 0,
            "amount": float(parts[37]) if parts[37] else 0,
            "turnover_rate": float(parts[38]) if parts[38] else 0,
            "pe": float(parts[39]) if parts[39] else 0,
            "total_mv": float(parts[45]) if parts[45] else 0,
            "circ_mv": float(parts[44]) if parts[44] else 0,
            "volume_ratio": float(parts[49]) if parts[49] else 0,
        }
    except Exception:
        return None


def fetch_all_for_analysis(symbol: str, trade_date: str) -> dict[str, Any]:
    """
    一次性拉取分析所需的全部数据（不落盘）
    
    Args:
        symbol: 股票代码
        trade_date: 交易日期
    
    Returns:
        包含所有分析数据的字典
    """
    result = {
        "symbol": symbol,
        "trade_date": trade_date,
        "snapshot": None,
        "daily": None,
        "daily_history": [],
        "factors": None,
        "moneyflow": None,
        "index_daily": {},
    }
    
    # 实时行情
    result["snapshot"] = fetch_snapshot_tencent(symbol)
    
    # 日线数据
    result["daily"] = fetch_daily(symbol, trade_date)
    
    # 近 10 日历史
    result["daily_history"] = fetch_daily_history(symbol, days=10)
    
    # 技术因子
    result["factors"] = fetch_factors(symbol, trade_date)
    
    # 资金流向
    result["moneyflow"] = fetch_moneyflow(symbol, trade_date)
    result["moneyflow_ths"] = fetch_moneyflow_ths(symbol, trade_date)
    
    # 大盘指数
    for idx_code in ["000001.SH", "399001.SZ", "399006.SZ"]:
        result["index_daily"][idx_code] = fetch_index_daily(idx_code, trade_date)
    
    return result


if __name__ == "__main__":
    import json
    
    symbol = sys.argv[1] if len(sys.argv) > 1 else "000725.SZ"
    trade_date = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y%m%d")
    
    print(f"拉取 {symbol} @ {trade_date} ...")
    data = fetch_all_for_analysis(symbol, trade_date)
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
