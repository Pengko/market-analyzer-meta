# API-Only Analysis Workflow (零本地数据时的纯API分析路径)

> 当目标股票在本地数据仓库中**完全无数据文件**（cyq_perf、stk_factor_pro、moneyflow、margin_detail 全部 missing）时，使用以下纯 API 路径完成完整深度分析。

## 触发条件

执行 `ls` 扫描确认以下文件全部不存在：
- `cyq_perf/{code}.parquet`
- `stk_factor_pro/{code}.parquet`
- `moneyflow_data/individual/ths/{code}.parquet`
- `margin_detail/{code}.parquet`

**与现有降级路径的区别：**
- `direct-execution-fallback-pattern.md` → 解决 delegate_task 超时
- `pandas 缺失降级` → 解决环境依赖问题
- **本文件** → 解决"目标股票根本没有本地缓存数据"的问题

## 数据源清单

| 数据维度 | API 源 | 格式 | 备注 |
|----------|--------|------|------|
| 实时行情 | 腾讯 `qt.gtimg.cn/q=sz{code}` | GB2312文本, `~`分隔 | 字段3=现价,4=昨收,32=涨跌%,37=成交额(万),38=换手率,47=涨停价,48=跌停价 |
| 日K线(120天) | 腾讯 `web.ifzq.gtimg.cn/appstock/app/fqkline/get` | JSON | `param={code},day,,,120,qfqday&_var=kline_day`, **键名是 `day` 不是 `qfqday`** |
| 5分钟K线 | 新浪 `money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData` | JSON | `symbol={code}&scale=5&ma=no&datalen=48` |
| 60分钟K线 | 新浪同上 | JSON | `scale=240&datalen=30` |
| 板块对标 | 腾讯 `qt.gtimg.cn` 批量查询 | GB2312文本 | 手动选3-5只同行业对标股 |
| 大盘指数 | 腾讯 `qt.gtimg.cn/q=sh000001,sz399001,sz399006` | GB2312文本 | 与个股行情同一API |
| 资金流向(盘中累计) | 东方财富 `push2.eastmoney.com/api/qt/stock/fflow/kline/get` | JSON | `secid={1.代码或0.代码}&klt=1&lmt=0` |
| 资金流向(每日) | 东方财富 `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get` | JSON | `secid={1.代码或0.代码}&lmt=10` |
| 筹码估算(VWAP) | 新浪日K线(同上) × 换手率加权 | 自算 | 10日VWAP近似筹码集中区 |
| 新闻消息 | TrendRadar MCP / 浏览器搜索 | JSON/HTML | 部分股票可能搜索结果为空 |

## 关键注意点（实战踩坑）

1. **腾讯日K线键名是 `day` 不是 `qfqday`** — 即使请求参数写了 `qfqday`, 返回JSON中键名是 `day`。如用 `d['data'][code]['qfqday']` 会抛 `KeyError`
2. **新浪5分钟K线包含跨日数据** — 需过滤只取当天数据 (按日期前缀 `2026-06-12` 筛选)
3. **腾讯API编码是GB2312** — 必须 `.decode('gb2312')`, 不能用 utf-8
4. **技术指标全手动计算** — 不依赖本地stk_factor_pro, 从日K线收盘价数组直接算MA/EMA/MACD/RSI/BOLL
5. **筹码维度用VWAP估算** — cyq_perf不存在时, 用近10日成交量加权均价(VWAP)近似替代筹码集中区, 必须标注为估算。详见上方"筹码估算方法"
6. **资金流向必须通过东方财富API获取** — ⚠️ 此为强制维度, 不可跳过。使用 `push2.eastmoney.com/api/qt/stock/fflow/kline/get` (盘中累计) + `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get` (每日)。详见上方"东方财富资金流向API"
7. **融资融券维度跳过** — 无本地数据且无便捷API, 直接标注"融资融券数据缺失"
8. **TrendRadar对部分小盘股搜索返回空** — 4次不同关键词搜索均为空时, 标注"消息面缺失,更像纯资金/板块联动驱动"

## 东方财富资金流向 API（2026-06-12 实战验证）

**⚠️ 重要：资金流向是强制分析维度，即使无本地数据也必须通过 API 获取。遗漏资金流会导致结论方向性错误。**

### API 1：盘中分时累计资金流

```
GET https://push2.eastmoney.com/api/qt/stock/fflow/kline/get
    ?secid={market}.{code}
    &fields1=f1,f2,f3,f7
    &fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63
    &klt=1&lmt=0
```

- `market`: 上证=1, 深证=0 (如 `secid=1.600172` 或 `secid=0.002583`)
- `klt=1`: 1分钟级(累计), `lmt=0`: 不限条数
- 返回 `data.klines[]`，每条格式: `datetime,f52,f53,f54,f55,f56`

**⚠️ 字段映射（已验证）：**
| 字段 | 含义 | 单位 |
|------|------|------|
| f52 | 主力净流入 | 元(×10000=万元) |
| f53 | 小单净流入(<20万) | 元 |
| f54 | 中单净流入(20-100万) | 元 |
| f55 | 大单净流入(100-500万) | 元 |
| f56 | 超大单净流入(>500万) | 元 |

**验证公式：主力(f52) = 大单(f55) + 超大单(f56)**（误差<100万即为正确）

**分时段增量计算**：取两个时间点的累计值做差，得到该时段增量。关键时段：
- 09:31→09:45: 第一波冲高（可能是涨停首攻）
- 09:45→09:55: 炸板/回落时段（看超大单是否撤退）
- 09:55→10:30: 震荡调整时段
- 10:30→11:30: 午前走稳时段

### API 2：每日资金流

```
GET https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get
    ?secid={market}.{code}
    &fields1=f1,f2,f3,f7
    &fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65
    &lmt=10
```

字段映射与盘中 API 一致（f52=主力, f53=小单, f54=中单, f55=大单, f56=超大单）。

**分析要点：**
- 对比近5日主力/超大单/小单的趋势方向
- 识别"超大单从出货转为回补"等关键转折
- 主力连续净卖出后的首日转正是重要信号
- 注意区分"超大单主导"vs"散户追高"——只看内外盘/涨跌停会误判

### 分析模板

```python
import urllib.request, json

def get_money_flow_intraday(code):
    """盘中分时累计资金流"""
    market = 1 if code.startswith('6') else 0
    url = f"https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?secid={market}.{code}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63&klt=1&lmt=0"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/'})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode('utf-8'))
    klines = data.get('data', {}).get('klines', [])
    results = []
    for line in klines:
        parts = line.split(',')
        results.append({
            'time': parts[0][-5:],
            'main': float(parts[1])/10000,    # 主力(万)
            'small': float(parts[2])/10000,   # 小单(万)
            'mid': float(parts[3])/10000,     # 中单(万)
            'big': float(parts[4])/10000,     # 大单(万)
            'super': float(parts[5])/10000,   # 超大单(万)
        })
    return results

def get_money_flow_daily(code, days=10):
    """每日资金流"""
    market = 1 if code.startswith('6') else 0
    url = f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={market}.{code}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65&lmt={days}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://data.eastmoney.com/'})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode('utf-8'))
    klines = data.get('data', {}).get('klines', [])
    results = []
    for line in klines:
        parts = line.split(',')
        results.append({
            'date': parts[0][:10],
            'main': float(parts[1])/10000,
            'small': float(parts[2])/10000,
            'mid': float(parts[3])/10000,
            'big': float(parts[4])/10000,
            'super': float(parts[5])/10000,
        })
    return results

def segment_analysis(intraday_data, segments):
    """分时段增量分析"""
    def find(data, t):
        for d in data:
            if d['time'] == t: return d
        return None
    for label, t1, t2 in segments:
        d1, d2 = find(intraday_data, t1), find(intraday_data, t2)
        if d1 and d2:
            print(f"  {label}: 超大单:{d2['super']-d1['super']:+.0f}万 大单:{d2['big']-d1['big']:+.0f}万 -> 主力:{d2['main']-d1['main']:+.0f}万")
```

## 筹码估算方法（无 cyq_perf 时）

当本地 `cyq_perf` 缺失时，用 **日K线成交量加权均价 (VWAP)** 近似替代筹码分布：

### 方法：近10日 VWAP = 筹码集中区

```python
# 从新浪日K线(scale=240)获取近10日数据
# 每日 VWAP ≈ 成交额 / 成交量(股)
# 10日加权均价 = sum(每日成交额) / sum(每日成交量)
```

**分析逻辑：**
- 10日 VWAP >> 当前价 → 大量近期筹码被套，上方套牢盘密集（压力大）
- 10日 VWAP ≈ 当前价 → 筹码集中在当前价位，支撑压力均衡
- 10日 VWAP << 当前价 → 近期持仓获利，筹码稳定（支撑强）

**按日拆分套牢区：**
- 逐日列出每日均价（成交额/成交量）
- 当日均价 > 当前价 → 该日筹码被套
- 被套天数越多、成交量越大 → 套牢压力越重
- 标注"高位套牢区"（均价偏离当前价 >5%）和"近期成本区"（偏离 <2%）

**⚠️ 必须标注**：`筹码数据为VWAP估算，非真实筹码分布，仅供参考`

## 手动技术指标计算代码模板

```python
def calc_ma(closes, n):
    return sum(closes[-n:]) / n if len(closes) >= n else None

def calc_ema(arr, n):
    k = 2.0 / (n + 1)
    e = [arr[0]]
    for i in range(1, len(arr)):
        e.append(arr[i] * k + e[-1] * (1 - k))
    return e

def calc_macd(closes, fast=12, slow=26, signal=9):
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    dif = [a - b for a, b in zip(ema_fast, ema_slow)]
    dea = calc_ema(dif, signal)
    macd = [2 * (a - b) for a, b in zip(dif, dea)]
    return dif[-1], dea[-1], macd[-1]

def calc_rsi(closes, n):
    gains, losses = [], []
    for i in range(len(closes)-n, len(closes)):
        chg = closes[i] - closes[i-1]
        gains.append(max(0, chg))
        losses.append(max(0, -chg))
    ag = sum(gains) / n
    al = sum(losses) / n
    return 100 - 100 / (1 + ag / al) if al > 0 else 100

def calc_boll(closes, n=20):
    mid = calc_ma(closes, n)
    std = (sum((c - mid)**2 for c in closes[-n:]) / n) ** 0.5
    return mid + 2*std, mid, mid - 2*std  # upper, mid, lower
```

## 板块对标股手动选择参考

本地概念成分表缺失时，基于行业知识手动选择：

| 行业方向 | 推荐对标 | 选股逻辑 |
|----------|---------|---------|
| 通信设备/专网 | 中兴通讯、烽火通信、海格通信 | 龙头+中军+高弹性 |
| 化工/聚氨酯 | 万华化学、华鲁恒升、鲁西化工 | 龙头+周期中军 |
| LED/封装 | 三安光电、华灿光电、通富微电 | 龙头+高弹性 |
| 消费电子 | 立讯精密、歌尔股份、蓝思科技 | 龙头+供应链 |

## 报告标注规范

使用API-only路径时, 报告开头必须写明:

```
数据来源: 纯API获取(腾讯行情+新浪K线), 本地数据仓库无该股票缓存
数据完整度: ~85% (缺失: 融资融券、精确筹码分布。资金流向已通过东方财富API获取)
降级说明: 技术指标由日K线手动计算, 资金流向通过东方财富API获取, 筹码为VWAP估算
```

## 实战验证

- **2026-06-12 海能达(002583)**: 全部本地文件缺失, 使用本路径完成完整深度分析
- 分析耗时: ~15分钟 (含12次API调用 + 技术指标计算 + 报告输出)
- 报告质量: 与有本地数据的分析相比, 仅缺失精确筹码结构和融资融券维度
- **2026-06-12 黄河旋风(600172)**: 全部本地文件缺失, 使用本路径(含东方财富资金流API)
  - 初版分析未含资金流 → 结论"散户追高被套"(错误)
  - 补充东方财富资金流API后 → 结论修正为"超大单主导涨停+试探性回补"(正确)
  - **教训：遗漏资金流会导致方向性错误，此维度为强制项**
