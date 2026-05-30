# 直接执行降级模式：子代理超时后的应急分析流程

> 当 `delegate_task` 子代理因数据量大、分析复杂度高而超时时，直接在当前会话中使用 `execute_code` 完成全部分析的标准流程。

## 触发条件

- `delegate_task` 返回任务状态为 `timeout` 或 `cancelled`
- 子代理耗时 ≥ 600 秒且未返回有效报告
- 子代理返回空输出或构建报告失败

## 核心约束

1. **单脚本完成**：所有数据读取、分析、报告生成、文件保存必须在单个 `execute_code` 调用内完成
   - 原因：`execute_code` 每次调用是独立 Python 进程，变量不保留
   - 反面案例：在第一个 `execute_code` 中定义 `r1`，第二个 `execute_code` 中访问 `r1` → `NameError`

2. **数据层并行化**：即使在直接执行模式下，仍应在脚本内用 `ThreadPoolExecutor` 并行读取多只股票数据

3. **报告注明**：必须在报告中注明降级原因和数据来源

## 完整代码模板

```python
import pandas as pd
import os
from concurrent.futures import ThreadPoolExecutor

# === 配置 ===
DATA_ROOT = "/Users/penghongming/quant-data/tushare/股票数据"
TODAY = "2026-05-27"

# 实时行情（从东财 push2 API 或腾讯 API 获取）
REALTIME = {
    "603305.SH": {
        "price": 16.71, "change_pct": 0.06, "open": 16.56,
        "high": 16.74, "low": 16.31, "pre_close": 16.70,
        "volume": 175193, "amount": 289099599, "volume_ratio": 1.41,
    },
    "000555.SZ": {
        "price": 13.41, "change_pct": -3.46, "open": 13.84,
        "high": 13.85, "low": 13.33, "pre_close": 13.89,
        "volume": 168406, "amount": 227852993, "volume_ratio": 1.52,
    }
}

# === 数据读取函数 ===
def read_daily(code):
    return pd.read_parquet(os.path.join(DATA_ROOT, "daily", f"{code}.parquet")).sort_values('trade_date')

def read_factor(code):
    return pd.read_parquet(os.path.join(DATA_ROOT, "stk_factor_pro", f"{code}.parquet")).sort_values('trade_date')

def read_moneyflow(code):
    path = os.path.join(DATA_ROOT, "moneyflow_data/individual/tushare", f"{code}.parquet")
    return pd.read_parquet(path).sort_values('trade_date') if os.path.exists(path) else None

def read_cyq(code):
    path = os.path.join(DATA_ROOT, "cyq_chips", f"{code}.parquet")
    return pd.read_parquet(path).sort_values('trade_date') if os.path.exists(path) else None

def read_auction(code):
    path = os.path.join(DATA_ROOT, "stk_auction_c", f"{code}.parquet")
    return pd.read_parquet(path).sort_values('trade_date') if os.path.exists(path) else None

# === 单股票分析函数 ===
def analyze_stock(ts_code, name, rt):
    df_d = read_daily(ts_code)
    df_f = read_factor(ts_code)
    df_mf = read_moneyflow(ts_code)
    df_cyq = read_cyq(ts_code)
    df_ac = read_auction(ts_code)
    
    fac = df_f.iloc[-1]
    
    # 聚合所有分析逻辑...
    # - 技术指标 (MA5/10/20/30, MACD, KDJ, RSI, BOLL)
    # - 近10日走势
    # - 近5日资金流向
    # - 筹码分布
    # - 集合竞价
    # - 下午推演（盘中分析）
    
    # 生成 Markdown 报告并保存
    report = build_report(ts_code, name, rt, df_d, df_f, df_mf, df_cyq, df_ac, fac)
    
    save_dir = "/Users/penghongming/agent-skills/custom/stock-deep-analysis/references/pending-validations/2026-05-26"
    os.makedirs(save_dir, exist_ok=True)
    path = os.path.join(save_dir, f"stock-analysis-{ts_code.split('.')[0]}-2026-05-27.md")
    with open(path, 'w', encoding='utf-8') as f:
        f.write(report)
    
    return path

# === 并行执行 ===
stocks = [
    ("603305.SH", "旭升集团", REALTIME["603305.SH"]),
    ("000555.SZ", "神州信息", REALTIME["000555.SZ"]),
]

with ThreadPoolExecutor(max_workers=2) as ex:
    results = list(ex.map(lambda s: analyze_stock(*s), stocks))

print(f"报告已保存: {results}")
```

## 东方财富 push2 API 获取实时行情

```bash
# 单股
curl -s "https://push2.eastmoney.com/api/qt/stock/get?secid=1.603305&fields=f43,f44,f45,f46,f47,f48,f50,f51,f57,f58,f60,f170"

# 多股票（分别请求，不支持批量）
# 需要在 Python 中用 ThreadPoolExecutor 并行
```

**Python 解析示例**：
```python
import requests

def fetch_eastmoney_realtime(market, code):
    """market: 1=沪市, 0=深市"""
    url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=f43,f44,f45,f46,f47,f48,f50,f51,f57,f58,f60,f170"
    resp = requests.get(url, timeout=10)
    data = resp.json().get("data", {})
    return {
        "price": float(data.get("f43", 0)) / 100,
        "high": float(data.get("f44", 0)) / 100,
        "low": float(data.get("f45", 0)) / 100,
        "open": float(data.get("f46", 0)) / 100,
        "volume": float(data.get("f47", 0)),
        "amount": float(data.get("f48", 0)),
        "volume_ratio": float(data.get("f50", 0)),
        "amplitude": float(data.get("f51", 0)),
        "code": data.get("f57"),
        "name": data.get("f58"),
        "pre_close": float(data.get("f60", 0)) / 100,
        "change_pct": float(data.get("f170", 0)),
    }
```

## 常见陷阱

| 陷阱 | 现象 | 解决 |
|------|------|------|
| 多个 `execute_code` 变量不共享 | 第二个脚本报 `NameError` | 单脚本完成全部逻辑 |
| pandas 未安装 | `ModuleNotFoundError: No module named 'pandas'` | 用 `pyarrow.parquet` 直读，或先 `pip install pandas pyarrow` |
| 资金流向路径不存在 | `FileNotFoundError` | 先检查 `os.path.exists()`，不存在返回 None |
| 筹码数据异常 | `cyq_chips` 仅含单行无效数据 | 读取后检查 `len(df)` 和 `df['percent'].sum()` |
| 东财 API 空响应 | 返回 HTTP 200 但 body 为空 | 降级至腾讯 API |

## 与子代理模式的对比

| 维度 | 子代理模式 | 直接执行模式 |
|------|-----------|------------|
| 并行度 | 多个子代理完全并行 | 单个脚本内用 ThreadPoolExecutor |
| 分析深度 | 高（每个代理独立推演） | 中（模板化填充） |
| 耗时 | ~12-27 分钟（可能超时） | ~2-5 分钟 |
| 可靠性 | 中（受子代理稳定性影响） | 高（无网络/进程依赖） |
| 适用场景 | 深度研究、单只精选 | 快速盘中响应、多只并行 |
