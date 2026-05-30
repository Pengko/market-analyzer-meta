#!/usr/bin/env python3
"""
股票深度分析脚本共享的通用工具。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


from data.config_loader import get_config, cfg

SKILL_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
REFERENCES_ROOT = cfg.paths("references_dir")
LOGS_ROOT = SKILL_ROOT / "logs"
TUSHARE_ROOT = cfg.paths("stock_data_root").parent.parent
STOCK_DATA_ROOT = cfg.paths("stock_data_root")
NEWS_DATA_ROOT = cfg.paths("news_data_root")
INDEX_DATA_ROOT = cfg.paths("index_data_root")
FINANCIAL_DATA_ROOT = cfg.paths("financial_data_root")
MINUTE_DATA_ROOT = cfg.paths("minute")
DEFAULT_MOBILE_STOCK_APP_PACKAGE = cfg.mobile("stock_app_package", default="com.hexin.plat.android")

_ADB_CANDIDATES_CFG = cfg.mobile("adb_path_candidates", default=[])
ADB_PATH_CANDIDATES = []
for c in _ADB_CANDIDATES_CFG:
    if c and c.strip():
        ADB_PATH_CANDIDATES.append(Path(c))



def resolve_adb_path() -> Path:
    for candidate in ADB_PATH_CANDIDATES:
        if candidate and candidate.exists():
            return candidate
    return Path("adb")


def normalize_symbol(value: str) -> tuple[str, str]:
    code = value.strip().upper()
    if "." in code:
        pure = code.split(".", 1)[0]
        return pure, code
    market = "SH" if code.startswith(("6", "9")) else "SZ"
    return code, f"{code}.{market}"


def normalize_trade_date(value: str) -> tuple[str, str]:
    raw = value.strip()
    compact = raw.replace("-", "")
    try:
        dt = datetime.strptime(compact, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"invalid trade date: {value}") from exc
    return compact, dt.strftime("%Y-%m-%d")
