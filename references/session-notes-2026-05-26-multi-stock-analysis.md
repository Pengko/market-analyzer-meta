# 2026-05-26 多股票并行分析实战记录

## 背景

用户要求同时深度分析三只股票：华映科技（000536.SZ）、神州信息（000555.SZ）、旭升集团（603305.SH）。

## 采用的工作流

### 1. 并行数据获取（execute_code + pyarrow）

当 `quick_analyze.py` 运行较慢时，采用 `execute_code` 调用 Python 脚本，用 `ThreadPoolExecutor` 并行获取多只股票数据：

```python
from concurrent.futures import ThreadPoolExecutor
import pyarrow.parquet as pq
import requests

def fetch_tencent_snapshot(symbol):
    url = f"http://qt.gtimg.cn/q={symbol}"
    resp = requests.get(url, timeout=10)
    # 解析腾讯API返回的csv格式
    ...

def load_local_data(symbol, root_dir):
    daily = pq.read_table(f"{root_dir}/daily/{symbol}.parquet").to_pandas()
    factor = pq.read_table(f"{root_dir}/stk_factor_pro/{symbol}.parquet").to_pandas()
    money = pq.read_table(f"{root_dir}/moneyflow_data/individual/ths/{symbol}.parquet").to_pandas()
    return {"daily": daily, "factor": factor, "money": money}

with ThreadPoolExecutor(max_workers=4) as ex:
    tencent_results = list(ex.map(fetch_tencent_snapshot, symbols))
    local_results = list(ex.map(lambda s: load_local_data(s, root), symbols))
```

**关键经验**：
- `pyarrow.parquet` 比 pandas 更可能已安装（特别是 macOS 环境）
- 实测数据层耗时约 10-20 秒，远快于串行执行

### 2. 批量腾讯 API 查询板块对标股

板块对标股行情使用批量查询，大幅减少请求次数：

```bash
curl -s "http://qt.gtimg.cn/q=sz000725,sz000100,sz000536,sh600707" | iconv -f gb2312 -t utf-8
```

**实用技巧**：
- 用逗号分隔多个股票代码，一次返回全部行情
- 返回格式：分号分隔各股的字符串，每个字符串内逗号分隔字段
- 典型字段：3=最新价、4=昨收、32=涨跌幅、37=成交额(万元)

### 3. 新闻获取降级路径：10jqka F10 浏览器导航

当 `fetch_browser_news.py` 失败时，直接使用浏览器工具导航到同花顺 F10 页面是可靠降级：

```
basic.10jqka.com.cn/{code}/news.html      # 新闻列表
basic.10jqka.com.cn/{code}/concept.html   # 概念分类
basic.10jqka.com.cn/{code}/position.html  # 资金分析
```

**实践要点**：
- 先用 `browser_navigate` 访问页面
- 用 `browser_vision` 提取文本内容（比 snapshot 更适合复杂布局）
- 提示词示例：`"从这个页面中提取所有新闻标题、日期和来源"

### 4. cyq_chips 严重异常案例（华映科技）

本次分析中，华映科技（000536.SZ）的 `cyq_chips` 数据出现极端异常：
- 文件路径：`cyq_chips/000536.SZ.parquet`
- 实际内容：仅含单行记录，`price=9.0`, `percent=0.01`
- 当日实际股价：4.6元附近
- 偏离度：价格偏离超过 90%

**处理**：
1. 立即标记为 `invalid`
2. 在报告中明确声明：`cyq_chips 数据严重异常，仅含占位记录，未纳入核心分析`
3. 使用近期成交量和 VWAP 推断筹码成本区

### 5. 板块分化的实际表现

本次分析发现板块内部显著分化：
- 面板板块：京东方A +9.49%（龙头），华映科技 +4.78%（跟风），深天马A +2.61%（弱跟）
- 量子计算：润和软件 +2.46%（龙头），神州信息 -2.53%（最弱）
- 机器人：三花智控 +0.98%（抗跌），旭升集团 -2.22%（跟润）

**启示**：
- 模式：资金优先拥抱龙头，小盘跟风股持续性存疑
- 分析时必须明确目标股在板块中的角色（龙头/中军/跟风）

## 实用代码片段

### 从腾讯行情快照提取多股票数据

```python
import requests

def parse_tencent_batch(symbols):
    url = f"http://qt.gtimg.cn/q={','.join(symbols)}"
    resp = requests.get(url, timeout=10)
    text = resp.text
    results = {}
    for item in text.split(';'):
        if not item.strip() or 'v_' not in item:
            continue
        # 解析格式: v_sz000536="1~华映科技~..."
        parts = item.split('"')
        if len(parts) < 2:
            continue
        code_key = parts[0].split('=')[0].replace('v_', '')
        fields = parts[1].split('~')
        if len(fields) >= 45:
            results[code_key] = {
                'name': fields[1],
                'price': float(fields[3]),
                'pre_close': float(fields[4]),
                'open': float(fields[5]),
                'high': float(fields[33]),
                'low': float(fields[34]),
                'change_pct': float(fields[32]),
                'amount_wan': float(fields[37]),  # 万元
                'turnover': float(fields[38]),
                'volume': float(fields[36]),
            }
    return results
```

### 并行读取多只股票本地数据

```python
from concurrent.futures import ThreadPoolExecutor
import pyarrow.parquet as pq

def load_stock_data(symbol, root_dir):
    result = {'symbol': symbol}
    try:
        daily = pq.read_table(f"{root_dir}/daily/{symbol}.parquet").to_pandas()
        result['daily'] = daily.tail(10)  # 近10日
    except Exception:
        result['daily'] = None
    try:
        factor = pq.read_table(f"{root_dir}/stk_factor_pro/{symbol}.parquet").to_pandas()
        result['factor'] = factor.tail(1)
    except Exception:
        result['factor'] = None
    try:
        money = pq.read_table(f"{root_dir}/moneyflow_data/individual/ths/{symbol}.parquet").to_pandas()
        result['money'] = money.tail(3)
    except Exception:
        result['money'] = None
    return result

with ThreadPoolExecutor(max_workers=4) as ex:
    results = list(ex.map(lambda s: load_stock_data(s, root), symbols))
```

## 常见陷阱

1. **fetch_browser_news.py 可能无输出**
   - 当脚本返回 exit code 2 且 stdout 空时，不要重试，直接降级到浏览器导航
   
2. **分钟线 VWAP 单位陷阱**
   - CSV 中 volume 字段单位是**手**，计算 VWAP 时必须 `* 100`
   - 验证：用平均成交价对比，二者应接近

3. **多股票分析时禁止串行**
   - 数据获取层必须并行化，否则总耗时随股票数量线性增长
   - AI推演层可以逐只进行，但基于已有结构化数据（无需额外IO）

## 文件路径快查

| 数据类型 | 路径模板 | 备注 |
|----------|----------|------|
| 日线 | `{root}/daily/{code}.parquet` | T-1 及以前 |
| 因子 | `{root}/stk_factor_pro/{code}.parquet` | 261字段 |
| 资金流向 | `{root}/moneyflow_data/individual/ths/{code}.parquet` | 通常滞后1日 |
| 筹码 | `{root}/cyq_chips/{code}.parquet` | 可能严重异常，读取必验 |
| 分钟线 | `{root}/分钟数据/YYYY/MM/DD/{code}.{EXCHANGE}/1m.csv` | 仍为CSV |
| 龙虎榜 | `{root}/top_list/YYYY.parquet` | 年份全市场表 |
| 大盘指数 | `{root}/index_daily/{index_code}.parquet` | sh000001, sz399001 |
