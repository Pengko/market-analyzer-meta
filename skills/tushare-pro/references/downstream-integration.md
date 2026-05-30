# 下游 Skill Python 层对接指南

本文档说明下游 skill（如 `stock-deep-analysis`）如何在 Python 代码层直接复用 `tushare_pro` 的客户端封装，而不是重复实现 Tushare API 调用或浏览器补抓。

## 对接场景

当下游 skill 需要获取股票数据时，优先级应该是：

```
本地 parquet → tushare_pro API 回填 → 浏览器/第三方 API 补抓
```

而不是：

```
本地 parquet → 直接走浏览器/第三方 API
```

## 动态导入模式

由于各 skill 的 Python 脚本分布在独立目录，直接 `import` 会失败。使用 `sys.path` 动态注入解决：

```python
import sys
from pathlib import Path

# 推导 tushare_pro 根目录（假设与下游 skill 同级）
TUSHARE_PRO_ROOT = Path(__file__).resolve().parent.parent.parent.parent.parent / "tushare_pro"
if str(TUSHARE_PRO_ROOT) not in sys.path:
    sys.path.insert(0, str(TUSHARE_PRO_ROOT))

from utils.tushare_client import create_pro_api, diagnose_api_connection
```

## 单股缺失回填示例

```python
def fetch_daily_from_tushare(ts_code: str, trade_date: str) -> dict | None:
    """通过 tushare_pro API 获取单股单日日线，返回第一行数据。"""
    try:
        pro = create_pro_api()
        df = pro.query("daily", ts_code=ts_code, trade_date=trade_date)
        if df is not None and not df.empty:
            return df.iloc[0].to_dict()
    except Exception:
        pass
    return None
```

## 缓存到本地 parquet

获取到的数据应该立即写回本地 parquet，避免每次都走 API：

```python
import pandas as pd

STOCK_DATA_ROOT = Path("/Users/penghongming/quant-data/tushare/股票数据")

def append_to_stock_parquet(subdir: str, ts_code: str, row: dict) -> None:
    pq_path = STOCK_DATA_ROOT / subdir / f"{ts_code}.parquet"
    df_new = pd.DataFrame([row])
    if pq_path.exists():
        df_existing = pd.read_parquet(pq_path)
        df_combined = pd.concat([df_existing, df_new], ignore_index=True)
        if "trade_date" in df_combined.columns:
            df_combined = df_combined.drop_duplicates(subset=["trade_date"], keep="last")
        df_combined.to_parquet(pq_path)
    else:
        df_new.to_parquet(pq_path)
```

## 完整 fallback 链路

实战中的完整调用链路（以日线为例）：

```python
def sync_daily_kline(ts_code: str, trade_date: str) -> dict:
    compact = trade_date.replace("-", "")

    # 1. 检查本地
    row = load_daily_row_from_local(ts_code, compact)
    if row:
        return {"status": "already_available", "row": row}

    # 2. 尝试 tushare_pro API
    row = fetch_daily_from_tushare(ts_code, compact)
    if row:
        append_to_stock_parquet("daily", ts_code, row)
        return {"status": "fetched_tushare_fallback", "row": row}

    # 3. 降级到浏览器/第三方 API
    row = fetch_from_browser(ts_code, trade_date)
    if row:
        return {"status": "fetched_tencent_fallback", "row": row}

    return {"status": "fetch_failed"}
```

## 常见陷阱

1. **sys.path 注入顺序**: 必须在 `import utils.tushare_client` 之前注入，且必须是 `insert(0, ...)` 而不是 `append`，确保优先解析。
2. **token 和 API URL 无需重复配置**: `create_pro_api()` 已经在 `utils/tushare_bootstrap.py` 中硬编码了 token 和中转 URL，下游直接复用即可。
3. **parquet 字段类型**: Tushare API 返回的 DataFrame 可能包含 `float64` 等类型，写入 parquet 时 pandas 会自动处理，但读取方如果期望字符串则需要显式转换。
4. **日期格式**: Tushare `daily` 接口的 `trade_date` 参数使用 `YYYYMMDD` 格式，不是带横杠的日期。
