#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主要作用:
- 提供本仓库统一的 Tushare 初始化参数来源
- 后续脚本统一引用这个文件创建 `pro`，避免 token / URL 分散在多处

当前标准调用方式:
    import tushare as ts
    pro = ts.pro_api("...")
    pro._DataApi__http_url = "http://124.220.22.110:8020/"

⚠️ 如果上游返回“Token 不对”，优先检查:
- 是否真的通过本文件返回的 token 初始化
- 是否真的给 `pro._DataApi__http_url` 赋值成了这里的 relay URL
"""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_TUSHARE_TOKEN = "6be0552842c69a4c84636359df4028459ce14d13d092cdce491ce77d361ab5a6"
DEFAULT_TUSHARE_HTTP_URL = "http://124.220.22.110:8020/"


@dataclass(frozen=True)
class TushareBootstrapConfig:
    token: str
    http_url: str


def get_tushare_bootstrap_config() -> TushareBootstrapConfig:
    """
    返回统一的 Tushare 初始化配置。

    说明:
    - 当前按用户要求使用文件内固定初始化参数
    - 后续脚本统一引用这里，不再依赖外部环境变量覆盖
    """
    return TushareBootstrapConfig(
        token=DEFAULT_TUSHARE_TOKEN,
        http_url=DEFAULT_TUSHARE_HTTP_URL,
    )


def get_tushare_token() -> str:
    return get_tushare_bootstrap_config().token


def get_tushare_http_url() -> str:
    return get_tushare_bootstrap_config().http_url
