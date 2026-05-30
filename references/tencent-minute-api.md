# 腾讯 API 实时分钟数据技术参考

## 接口端点

```
GET https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={tencent_code}
```

`股票代码格式`:
- 上海: `sh600519`
- 深圳: `sz000001`
- 北交: `bjxxxxxx`

## 请求示例

```bash
curl -s "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh600519"
```

## 响应格式

```json
{
  "code": 0,
  "msg": "",
  "data": {
    "sh600519": {
      "data": {
        "data": [
          "0930 1287.00 463 59588100.00",
          "0931 1281.20 1785 229381825.00",
          "0932 1277.20 3269 419308851.00"
        ]
      }
    }
  }
}
```

## 字段解析

每条记录格式: `"{HHMM} {price} {cum_vol} {cum_amount}"`

| 字段 | 含义 | 单位 |
|------|------|------|
| HHMM | 时间 (时分) | - |
| price | 该分钟价格 | 元 |
| cum_vol | 累计成交量 | 手 (1手=100股) |
| cum_amount | 累计成交额 | 元 |

## 转换为 OHLCV

1. **分钟增量计算** (diff 计算)
   ```python
   df["volume"] = df["cum_vol"].diff().fillna(df["cum_vol"].iloc[0])
   df["amount"] = df["cum_amount"].diff().fillna(df["cum_amount"].iloc[0])
   ```

2. **单位转换**
   ```python
   df["volume"] = (df["volume"] * 100).astype(int)  # 手 → 股
   df["amount"] = df["amount"].astype(int)           # 已是元，只要整数化
   ```

3. **OHLC 近似**
   腾讯只返回每分钟一个价格点，因此:
   ```python
   df["open"]  = df["price"].shift(1).fillna(df["price"])  # 上一分钟收盘
   df["close"] = df["price"]                                # 当前分钟价格
   df["high"]  = df["price"]                                # 单点价格
   df["low"]   = df["price"]                                # 单点价格
   df["avg"]   = df["price"]                                # 单点价格
   ```

## 保存路径

```
~/quant-data/tushare/股票数据/分钟数据/YYYY/MM/DD/{ts_code}/1m.csv
```

例如: `~/quant-data/tushare/股票数据/分钟数据/2026/05/25/600519.SH/1m.csv`

## 与本地格式一致性

保存的 CSV 与本地其他分钟数据格式完全一致:
```csv
datetime,open,close,high,low,volume,amount,avg
2026-05-25 09:30:00,1287.0,1287.0,1287.0,1287.0,46300,59588100,1287.0
```

## 限制与适用场景

| 项目 | 说明 |
|------|------|
| 数据范围 | 仅当日实时分时数据 |
| 历史数据 | 不支持（需用 Tushare `stk_mins`） |
| 限额 | 无限额，免费 |
| 最佳场景 | 盘中实时分析、Tushare 分钟限额用完时的 fallback |

## 已知问题

- 历史分钟 K 线（如 `m1`/`m5` 粗度）不支持，只能获取当日分时数据
- `fkline` 接口支持 `day`/`week`/`month`，但不支持分钟粗度
