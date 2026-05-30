#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主要作用:
- 提供统一的 Tushare 客户端创建入口
- 确保主链脚本以同一种方式读取 token 和 API 端点

⚠️ 路由警示:
- 本项目默认走中转平台，不走官方域名。
- 请优先使用 `TUSHARE_API_URL` / `TUSHARE_PROXY_URL` 指向中转地址。
"""

import os
from typing import Any, Dict

# 清除本地代理环境变量，避免路由到未运行的本地代理（如 Clash）
for key in ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']:
    os.environ.pop(key, None)

import tushare as ts
from utils.tushare_bootstrap import get_tushare_bootstrap_config

BOOTSTRAP_CONFIG = get_tushare_bootstrap_config()
DEFAULT_TOKEN = BOOTSTRAP_CONFIG.token
DEFAULT_RELAY_API_URL = BOOTSTRAP_CONFIG.http_url
DEFAULT_API_URL = DEFAULT_RELAY_API_URL
FALLBACK_RELAY_API_URL = "http://8.136.22.187:8010/"


def _is_official_domain(url: str) -> bool:
    lowered = (url or "").strip().lower()
    return "api.tushare.pro" in lowered or "api.waditu.com" in lowered


def _resolve_api_url() -> str:
    configured = get_tushare_bootstrap_config().http_url
    api_url = (configured or "").strip() or DEFAULT_RELAY_API_URL
    if _is_official_domain(api_url):
        print(
            f"[Tushare][警示] 检测到官方域名配置({api_url})，已强制切回中转平台: {DEFAULT_RELAY_API_URL}"
        )
        return DEFAULT_RELAY_API_URL
    return api_url


def get_effective_api_url() -> str:
    """Return the effective API URL (relay-first, official domain blocked)."""
    return _resolve_api_url()


def classify_api_error(exc: Exception) -> tuple[str, str]:
    """Classify common Tushare connection/auth failures into stable categories."""
    text = str(exc).strip()
    lowered = text.lower()

    if "token expired" in lowered or "expired token" in lowered:
        return "token_expired", "TUSHARE_TOKEN 已过期"
    if "missing tushare_token" in lowered or "token" in lowered and "confirm" in lowered:
        return "invalid_token", "TUSHARE_TOKEN 无效或不被当前接口接受"
    if "token不对" in text or "token 不对" in text:
        return "invalid_token", "TUSHARE_TOKEN 无效或不被当前接口接受"
    if "nodename nor servname provided" in lowered or "name or service not known" in lowered:
        return "dns_error", "API 地址无法解析，请检查 TUSHARE_API_URL 或当前网络 DNS"
    if "127.0.0.1" in lowered and "18010" in lowered:
        return "relay_inner_service_down", "当前中转服务的内部转发异常，可切换备用中转重试"
    if "failed to establish a new connection" in lowered or "max retries exceeded" in lowered:
        return "connect_error", "API 地址不可达，请检查代理地址、端口和网络连通性"
    if "connectionerror" in lowered:
        return "connect_error", "连接失败，请检查网络或代理地址"
    if "read timed out" in lowered or "timeout" in lowered:
        return "timeout", "请求超时，请检查网络质量或适当提高 timeout"

    return "unknown_error", text or "未知错误"


def _looks_like_relay_inner_failure(exc: Exception) -> bool:
    text = str(exc or "").lower()
    return "127.0.0.1" in text and "18010" in text


def diagnose_api_connection(pro=None, timeout=30) -> Dict[str, Any]:
    """
    Run a lightweight health check and return structured diagnostics.

    Returns:
        {
            "ok": bool,
            "category": str,
            "message": str,
            "api_url": str,
            "has_token": bool,
        }
    """
    has_token = bool(os.getenv("TUSHARE_TOKEN", DEFAULT_TOKEN))
    api_url = get_effective_api_url()

    if not has_token:
        return {
            "ok": False,
            "category": "missing_token",
            "message": "未设置 TUSHARE_TOKEN",
            "api_url": api_url,
            "has_token": False,
        }

    try:
        if pro is None:
            pro = create_pro_api(timeout=timeout)
        df = pro.trade_cal(exchange="SSE", start_date="20260401", end_date="20260402")
        if df is not None and not df.empty:
            return {
                "ok": True,
                "category": "ok",
                "message": f"API 可用，trade_cal 返回 {len(df)} 条数据",
                "api_url": getattr(pro, "_DataApi__http_url", api_url),
                "has_token": True,
            }
        return {
            "ok": False,
            "category": "empty_response",
            "message": "API 已连接，但测试接口返回空数据",
            "api_url": getattr(pro, "_DataApi__http_url", api_url),
            "has_token": True,
        }
    except Exception as exc:
        category, message = classify_api_error(exc)
        return {
            "ok": False,
            "category": category,
            "message": message,
            "api_url": api_url,
            "has_token": True,
        }


def create_pro_api(token=None, timeout=30):
    """
    创建正确配置的 Tushare Pro API 客户端
    
    参数:
        token: Tushare token，默认从环境变量或默认配置获取
        timeout: 请求超时时间（秒）
    
    返回:
        ts.pro_api 实例，已正确配置 API 端点
    """
    if token is None:
        token = get_tushare_bootstrap_config().token
    if not token:
        raise ValueError("Missing TUSHARE_TOKEN. Export it before running data scripts.")
    
    # 创建 pro_api 实例
    pro = ts.pro_api(token=token, timeout=timeout)
    
    # 强制 relay-first 路由，避免误走官方域名
    api_url = _resolve_api_url()
    pro._DataApi__http_url = api_url
    print(f"[Tushare] 使用中转 API 端点: {api_url}")

    original_query = pro.query

    def _query_with_fallback(api_name, fields='', **kwargs):
        try:
            return original_query(api_name, fields=fields, **kwargs)
        except Exception as exc:
            current_url = getattr(pro, "_DataApi__http_url", "")
            if (
                current_url == DEFAULT_RELAY_API_URL
                and FALLBACK_RELAY_API_URL
                and _looks_like_relay_inner_failure(exc)
            ):
                print(
                    "[Tushare] 检测到主中转内部服务异常，自动切换备用中转: "
                    f"{FALLBACK_RELAY_API_URL}"
                )
                pro._DataApi__http_url = FALLBACK_RELAY_API_URL
                return original_query(api_name, fields=fields, **kwargs)
            raise

    pro.query = _query_with_fallback

    return pro


def test_api_connection(pro=None):
    """
    测试 API 连接是否正常
    
    参数:
        pro: Tushare pro_api 实例，为 None 时自动创建
    
    返回:
        (bool, str): (是否成功, 状态信息)
    """
    result = diagnose_api_connection(pro=pro)
    if result["ok"]:
        return True, f"✅ {result['message']}"
    return False, f"❌ {result['message']}"


if __name__ == "__main__":
    print("🔧 Tushare Pro 统一客户端测试")
    print("=" * 50)
    
    # 创建客户端
    pro = create_pro_api()
    print(f"API URL: {pro._DataApi__http_url}")
    print()
    
    # 测试连接
    success, msg = test_api_connection(pro)
    print(msg)
    
    print()
    print("=" * 50)
    if success:
        print("✅ 客户端配置正确，可以正常使用")
    else:
        print("❌ 客户端配置有问题，请检查")
