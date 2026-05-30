"""
Infoway REST API 客户端

通过 HTTP REST API 获取历史 K 线数据（当天已完成的分钟线）
与 WebSocket 实时推送互补

文档: https://docs.infoway.io/rest-api/http-endpoints
"""

import json
import os
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional

from infoway_adapter import infoway_kline_array_to_standard

# REST API 基础地址
REST_BASE_URL = "https://api.infoway.io/v1"


def _get_api_key() -> str:
    """获取 API Key"""
    api_key = os.getenv("INFOWAY_API_KEY")
    if not api_key:
        raise ValueError("INFOWAY_API_KEY 未设置")
    return api_key


def _make_request(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    发起 HTTP 请求
    
    Args:
        endpoint: API 端点路径（不含基础 URL）
        params: 请求参数
    
    Returns:
        JSON 响应
    """
    api_key = _get_api_key()
    
    # 构建 URL
    url = f"{REST_BASE_URL}{endpoint}"
    if params:
        query = "&".join([f"{k}={v}" for k, v in params.items()])
        url = f"{url}?{query}"
    
    # 构建请求
    req = urllib.request.Request(
        url,
        headers={
            "X-API-Key": api_key,
            "Content-Type": "application/json",
        }
    )
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
    except urllib.error.HTTPError as e:
        raise Exception(f"HTTP {e.code}: {e.reason}")
    except Exception as e:
        raise Exception(f"请求失败: {e}")


def get_kline_history(
    symbol: str,
    period: str = "1m",
    count: int = 240
) -> List[Dict[str, Any]]:
    """
    获取历史 K 线数据（当天已完成的分钟线）
    
    Infoway REST API 端点: /market/kline
    
    Args:
        symbol: 股票代码，如 "600103.SH"
        period: 周期，"1m"、"5m"、"15m"、"30m"、"60m"、"d"、"w"、"m"
        count: 返回条数，默认240（大约一天的分钟线）
    
    Returns:
        标准格式的 K 线列表
    """
    from infoway_adapter import period_to_infoway_type
    
    # 调用 REST API
    params = {
        "symbol": symbol,
        "period": period,
        "count": count,
    }
    
    response = _make_request("/market/kline", params)
    
    # 解析响应
    if response.get("code") != 0:
        raise Exception(f"API 错误: {response.get('msg', 'Unknown error')}")
    
    data = response.get("data", [])
    
    # 转换为标准格式
    return infoway_kline_array_to_standard(data, symbol)


def get_kline_today(
    symbol: str
) -> List[Dict[str, Any]]:
    """
    获取当天已完成的分钟线数据
    
    等价于 get_kline_history(symbol, period="1m", count=240)
    
    Args:
        symbol: 股票代码
    
    Returns:
        当天已有的分钟线数据（标准格式）
    """
    return get_kline_history(symbol, period="1m", count=240)


def check_api_health() -> bool:
    """检查 API 健康状态"""
    try:
        response = _make_request("/market/kline", {"symbol": "000001.SZ", "period": "1m", "count": 1})
        return response.get("code") == 0
    except Exception:
        return False


if __name__ == "__main__":
    # 测试
    print("测试 Infoway REST API...")
    try:
        bars = get_kline_today("600103.SH")
        print(f"成功获取 {len(bars)} 条分钟线")
        if bars:
            print(f"最新一条: {bars[-1]}")
    except Exception as e:
        print(f"失败: {e}")
