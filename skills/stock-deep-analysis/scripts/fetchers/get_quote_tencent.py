#!/usr/bin/env python3
"""
K线形态分析脚本。

口径：
1. 优先使用本地分钟线（日内分钟主链当前以 Yahoo first / 东方财富 fallback 落盘）。
2. 本地分钟线缺失时，腾讯只作为最后兜底的实时快照源，不作为主事实源。
"""

import urllib.request
import re
import sys
import os
import csv
from datetime import datetime, timedelta
from pathlib import Path

# 腾讯API字段索引 (经过验证)
TENCENT_FIELDS = {
    "name": 1,
    "code": 2,
    "current": 3,
    "change": 4,
    "prev_close": 5,
    "open": 17,
    "high": 41,
    "low": 42,
    "volume": 8,
    "amount": 37,
}

# 本地分钟数据根目录
from data.config_loader import cfg

MINUTE_DATA_ROOT = str(cfg.paths('stock_data_root') / '分钟数据')


def get_latest_minute_data(symbol):
    """
    从本地分钟数据获取日K
    返回: (open, close, high, low, trade_date) or None
    """
    code = symbol.replace(".SZ", "").replace(".SH", "")
    root = Path(MINUTE_DATA_ROOT)

    csv_path = None
    latest_date = None

    # 1) 新结构A：分钟数据/YYYY/MM/DD/{code}_1m.csv
    if root.exists():
        for year_dir in sorted(root.iterdir(), reverse=True):
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            for month_dir in sorted(year_dir.iterdir(), reverse=True):
                if not month_dir.is_dir():
                    continue
                for day_dir in sorted(month_dir.iterdir(), reverse=True):
                    if not day_dir.is_dir():
                        continue
                    candidates = [
                        day_dir / f"{code}_1m.csv",
                        day_dir / symbol / "1m.csv",
                        day_dir / code / "1m.csv",
                    ]
                    for candidate in candidates:
                        if candidate.exists():
                            csv_path = candidate
                            latest_date = f"{year_dir.name}-{month_dir.name}-{day_dir.name}"
                            break
                    if csv_path is not None:
                        break
                if csv_path is not None:
                    break
            if csv_path is not None:
                break

    # 2) 旧结构 fallback：分钟数据/{code}/{date}/minute_kline.csv
    if csv_path is None:
        base_path = root / code
        if base_path.exists():
            dates = sorted([d for d in os.listdir(base_path) if d[:4].isdigit()])
            if dates:
                latest_date = dates[-1]
                csv_path = base_path / latest_date / "minute_kline.csv"

    if csv_path is None or not csv_path.exists():
        return None

    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        data = list(reader)

    if not data:
        return None

    first = data[0]
    last = data[-1]

    open_price = float(first["open"])
    close_price = float(last["close"])
    high = max(float(d["high"]) for d in data)
    low = min(float(d["low"]) for d in data)

    return {
        "source": "eastmoney",
        "trade_date": latest_date,
        "open": open_price,
        "close": close_price,
        "high": high,
        "low": low,
    }


def get_quote_tencent(code):
    """从腾讯 API 获取实时快照，仅作兜底源。"""
    url = f"https://qt.gtimg.cn/q={code}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=cfg.network('timeout_seconds', default=10)) as response:
        text = response.read().decode("gbk", errors="ignore")
    m = re.search(r'"([^"]+)"', text)
    if not m:
        return None
    parts = m.group(1).split("~")

    data = {}
    for key, idx in TENCENT_FIELDS.items():
        try:
            data[key] = float(parts[idx]) if idx < len(parts) else None
        except (ValueError, IndexError):
            data[key] = None

    data["name"] = parts[1] if len(parts) > 1 else ""
    return data


def validate_tencent_fields():
    """用茅台验证腾讯 API 字段映射。"""
    print("字段验证 (茅台 600519):")
    data = get_quote_tencent("sh600519")
    if not data:
        print("  获取失败")
        return False

    if data["open"] and data["high"] and data["low"] and data["current"]:
        if data["open"] <= data["high"] and data["open"] >= data["low"]:
            print(f"  ✓ 字段映射正确")
            print(
                f"    今开={data['open']}, 最高={data['high']}, 最低={data['low']}, 当前={data['current']}"
            )
            return True

    print("  ✗ 字段映射可能有误")
    return False


def analyze_kline(symbol, doji_threshold=None):
    if doji_threshold is None:
        doji_threshold = cfg.indicator('doji', 'threshold', default=0.01)
    """
    分析K线形态
    - 优先使用本地分钟数据（日内分钟主链已统一管理）
    - 腾讯 API 仅作为兜底快照源
    - 十字星: 实体 ≤ 1%
    - 上影长: 上影 ≥ 80%实体
    - 下影短: 下影 < 50%实体
    """
    # 优先从东财分钟数据获取
    ef_data = get_latest_minute_data(symbol)

    if ef_data:
        o, c, h, l = ef_data["open"], ef_data["close"], ef_data["high"], ef_data["low"]
        source = f"eastmoney ({ef_data['trade_date']})"
    else:
        # 最后兜底：腾讯实时快照
        if not validate_tencent_fields():
            print("  腾讯API验证失败")
            return None

        data = get_quote_tencent(symbol)
        if not data:
            return None

        o, c, h, l = data["open"], data["current"], data["high"], data["low"]
        if not all([o, c, h, l]):
            return None
        source = "tencent_fallback_api"

    body = c - o
    body_pct = abs(body) / o * 100
    upper_shadow = h - c
    lower_shadow = c - l

    # 基础形态判断
    if body_pct <= doji_threshold * 100:
        base = "十字星"
    elif body > 0:
        base = "阳线"
    else:
        base = "阴线"

    # K线形态分析 (基于全天波动空间的比例)
    # 波动范围 = 最高 - 最低
    # 各部分占比 = 各部分长度 / 波动范围

    body = c - o
    range_price = h - l

    if range_price == 0:
        return None

    # 根据阴阳线分别计算影线
    if body > 0:  # 阳线
        # 上影 = 最高点 - 收盘价
        # 下影 = 开盘价 - 最低点
        upper_shadow = h - c
        lower_shadow = o - l
    elif body < 0:  # 阴线
        # 上影 = 最高点 - 开盘价
        # 下影 = 收盘价 - 最低点
        upper_shadow = h - o
        lower_shadow = c - l
    else:  # 十字星
        upper_shadow = h - c
        lower_shadow = c - l

    body_pct = abs(body) / range_price * 100
    upper_shadow_pct = upper_shadow / range_price * 100
    lower_shadow_pct = lower_shadow / range_price * 100

    # 基础形态判断
    if body_pct <= 15:  # 实体占比≤15%是十字星
        base = "十字星"
    elif body > 0:
        base = "阳线"
    else:
        base = "阴线"

    # 特殊形态判断 (按优先级)
    # 1. 大实体 (实体≥70%)
    # 2. 光头 (上影<10%)
    # 3. 光脚 (下影<10%)
    # 4. 上影长/下影长组合

    extra = []

    if body_pct >= 70:
        extra.append("大实体")

    # 光头光脚判断
    if upper_shadow_pct < 10:
        extra.append("光头")
    if lower_shadow_pct < 10:
        extra.append("光脚")

    # 上影长+下影短
    if upper_shadow_pct > 35 and lower_shadow_pct < 25:
        extra.append("上影长+下影短")
    # 下影长+上影短
    elif lower_shadow_pct > 35 and upper_shadow_pct < 25:
        extra.append("下影长+上影短")
    # 上影长
    elif upper_shadow_pct > 35:
        extra.append("上影长")
    # 下影长
    elif lower_shadow_pct > 35:
        extra.append("下影长")

    if extra:
        shadow = " (" + "+".join(extra) + ")"
    else:
        shadow = ""

    return {
        "symbol": symbol,
        "source": source,
        "open": o,
        "close": c,
        "high": h,
        "low": l,
        "range": range_price,
        "body": body,
        "body_pct": body_pct,
        "upper_shadow_pct": upper_shadow_pct,
        "lower_shadow_pct": lower_shadow_pct,
        "shape": base + shadow,
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        symbol = sys.argv[1]
        result = analyze_kline(symbol)

        if result:
            print(f"\n{result['symbol']} (数据源: {result['source']})")
            print(f"  今开: {result['open']:.2f}")
            print(f"  收盘: {result['close']:.2f}")
            print(f"  最高: {result['high']:.2f}")
            print(f"  最低: {result['low']:.2f}")
            print(f"  波动空间: {result['range']:.2f}")
            print(f"  实体: {result['body']:+.3f} ({result['body_pct']:.1f}%)")
            print(f"  上影: {result['upper_shadow_pct']:.1f}%")
            print(f"  下影: {result['lower_shadow_pct']:.1f}%")
            print(f"  形态: {result['shape']}")
        else:
            print(f"获取 {symbol} 失败")
    else:
        print("用法: python3 get_quote_tencent.py 000823.SZ")
