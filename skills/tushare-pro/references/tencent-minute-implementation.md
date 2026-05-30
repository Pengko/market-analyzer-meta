# 腾讯 API 实时分钟数据 — 实现参考

> 对应代码：`core/tencent_min_fetcher.py`（2026-05-25 实现）

## 快速调用

```bash
# 单股
python3 core/tencent_min_fetcher.py --symbol 600519.SH

# 多股（并发）
python3 core/tencent_min_fetcher.py --symbols "600519.SH,000001.SZ,002594.SZ" --workers 4

# 通过 auto_fill_data 批量（全股票池）
python3 auto_fill_data.py --tencent-min --tencent-min-workers 8
```

## 核心实现

### 1. API 请求与解析

```python
import httpx
import json

async def fetch_tencent_min(symbol: str) -> pd.DataFrame:
    """
    symbol: tushare 格式 (600519.SH → sh600519)
    返回: DataFrame[datetime,open,close,high,low,volume,amount,avg]
    """
    tencent_code = symbol.replace('.SH', '').replace('.SZ', '').replace('.BJ', '')
    tencent_code = ('sh' if symbol.endswith('.SH') else
                    'sz' if symbol.endswith('.SZ') else 'bj') + tencent_code

    url = f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={tencent_code}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()

    raw = data["data"][tencent_code]["data"]["data"]
    records = []
    for line in raw:
        parts = line.split()
        hhmm = parts[0]
        price = float(parts[1])
        cum_vol = int(parts[2])      # 手
        cum_amount = float(parts[3])  # 元
        records.append({"hhmm": hhmm, "price": price,
                        "cum_vol": cum_vol, "cum_amount": cum_amount})

    df = pd.DataFrame(records)

    # 增量计算
    df["volume"] = df["cum_vol"].diff().fillna(df["cum_vol"].iloc[0])
    df["amount"] = df["cum_amount"].diff().fillna(df["cum_amount"].iloc[0])

    # 单位转换
    df["volume"] = (df["volume"] * 100).astype(int)  # 手→股
    df["amount"] = df["amount"].astype(int)

    # OHLC 近似（单点价格）
    df["open"]  = df["price"].shift(1).fillna(df["price"])
    df["close"] = df["price"]
    df["high"]  = df["price"]
    df["low"]   = df["price"]
    df["avg"]   = df["price"]

    # 时间戳
    today = datetime.now().strftime("%Y-%m-%d")
    df["datetime"] = pd.to_datetime(today + " " + df["hhmm"].str[:2] + ":"
                                      + df["hhmm"].str[2:] + ":00")

    return df[["datetime","open","close","high","low","volume","amount","avg"]]
```

### 2. 批量并发获取

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def batch_fetch(symbols: list[str], workers: int = 4) -> dict[str, pd.DataFrame]:
    """批量获取，带进度条和错误重试"""
    semaphore = asyncio.Semaphore(workers)

    async def _fetch_one(sym):
        async with semaphore:
            try:
                return await fetch_tencent_min(sym)
            except Exception as e:
                print(f"[ERR] {sym}: {e}")
                return None

    tasks = [_fetch_one(s) for s in symbols]
    results = await asyncio.gather(*tasks)
    return {s: r for s, r in zip(symbols, results) if r is not None}
```

### 3. 保存路径

```python
from pathlib import Path

def save_min_data(df: pd.DataFrame, symbol: str, base_dir: Path):
    """保存到 ~/quant-data/tushare/股票数据/分钟数据/YYYY/MM/DD/{symbol}/1m.csv"""
    today = datetime.now()
    save_dir = base_dir / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}" / symbol
    save_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(save_dir / "1m.csv", index=False)
```

## 集成到 auto_fill_data.py

在 `auto_fill_data.py` 的 `AutoFillRuntime` 中添加 `run_tencent_min_fetch()` 方法：

```python
# 在 __init__ 之后添加调用
# 位置：所有 Tushare 接口处理完成后，作为分钟数据的补充来源

async def run_tencent_min_fetch(self):
    """获取当日腾讯实时分钟数据（Tushare stk_mins 限额用完时的 fallback）"""
    from core.tencent_min_fetcher import TencentMinFetcher

    fetcher = TencentMinFetcher(data_dir=self.data_dir)

    # 获取股票池（排除 ST）
    stock_basic = self.pro.stock_basic(exchange='', list_status='L',
                                        fields='ts_code,name')
    stock_basic = stock_basic[~stock_basic['name'].str.contains('ST|退', na=False)]
    symbols = stock_basic['ts_code'].tolist()

    # 批量获取（默认 500 只，可配置）
    batch_size = getattr(self, 'tencent_min_batch_size', 500)
    workers = getattr(self, 'tencent_min_workers', 4)
    symbols = symbols[:batch_size]

    results = await fetcher.batch_fetch(symbols, workers=workers)
    saved = sum(1 for r in results.values() if r is not None)
    print(f"腾讯分钟: {saved}/{len(symbols)} 成功")
```

CLI 参数：
```python
parser.add_argument('--tencent-min', action='store_true',
                    help='获取腾讯实时分钟数据')
parser.add_argument('--tencent-min-batch-size', type=int, default=500)
parser.add_argument('--tencent-min-workers', type=int, default=4)
parser.add_argument('--tencent-min-symbols', type=str,
                    help='指定股票列表，逗号分隔')
```

## 关键陷阱

### 陷阱 1: core/calendar.py 覆盖标准库

`core/calendar.py` 与 Python 标准库 `calendar` 同名。当从 `core/` 目录直接运行脚本时：

```bash
# 危险：会导致 import 冲突
cd core && python3 tencent_min_fetcher.py
```

**连锁反应**：
```
import urllib.request → import http.client → import email.parser
→ import email.utils → import calendar → **命中 core/calendar.py**
→ 循环导入错误 / AttributeError
```

**修复**（在 `core/` 下的可执行脚本开头）：
```python
import sys
# 移除 core/ 目录，避免覆盖标准库
core_dir = Path(__file__).parent.resolve()
if str(core_dir) in sys.path:
    sys.path.remove(str(core_dir))
# 然后再导入标准库模块
import calendar  # 现在会正确命中 Python 标准库
```

### 陷阱 2: 并发请求过多被限流

腾讯 API 虽无限额，但并发过高（>20）可能触发 IP 限流。

**建议**：workers 控制在 4-8，配合 `asyncio.Semaphore` 限制并发。

### 陷阱 3: 非交易日报错

非交易日调用会返回空数据或结构异常。建议在调用前检查 `trade_cal`：

```python
from utils.tushare_client import TushareClient
client = TushareClient()
is_trade_day = client.pro.trade_cal(start_date=today, end_date=today,
                                     is_open='1').shape[0] > 0
if not is_trade_day:
    print("非交易日，跳过")
    return
```

## 与 Tushare stk_mins 的区别

| 特性 | Tushare stk_mins | 腾讯 minute/query |
|------|------------------|-------------------|
| 数据范围 | 历史分钟（多天） | 仅当日实时 |
| OHLC 精度 | 真实 OHLC | 单点价格近似 |
| 限额 | 2次/天 | 无限额 |
| 最佳场景 | 盘后历史分析 | 盘中实时/Tushare 限额用完 |
| 保存格式 | 一致 | 一致 |

## 调用链

```
stock-deep-analysis 需要分钟数据
    → 检查本地 parquet: ~/quant-data/.../分钟数据/YYYY/MM/DD/{symbol}/1m.csv
    → 本地无数据 → Tushare stk_mins（限额 2 次/天）
    → Tushare 超限 → 腾讯 minute/query（无限额 fallback）
    → 腾讯也无数据 → 浏览器补抓（最终 fallback）
```
