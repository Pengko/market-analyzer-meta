# 个股资金流分析框架

> 2026-06-12 实战教训：遗漏资金流导致将"超大单主导涨停"误判为"散户追高被套"，结论方向性错误。资金流分析是强制模块。

## 一、为什么资金流分析必须做

资金流回答的核心问题：**今天的价格波动是谁在买、谁在卖？**

仅看涨跌幅和内外盘无法区分"散户追高"和"主力扫货"。内外盘只反映主动买卖方向，不反映资金体量。必须用超大单/大单/中单/小单的分层数据才能判断真实意图。

**典型案例（600172 黄河旋风 2026-06-12）：**
- 表面：涨停炸板，内盘53.7% > 外盘46.3%，看似抛压重
- 资金流真相：超大单上午净买入+1.92亿（主导涨停），散户反而净卖出-1.49亿
- 结论完全反转：不是"散户追高被套"，而是"超大单主导+试探性回补"

## 二、数据获取

### 优先级1：本地 parquet（延迟1日）

```
${STOCK_DATA_ROOT}/moneyflow_data/individual/ths/{code}.parquet
```

- 字段：`trade_date`, `buy_elg_amount`(超大单买入), `sell_elg_amount`(超大单卖出), `buy_lg_amount`(大单买入), `sell_lg_amount`(大单卖出), `buy_md_amount`(中单买入), `sell_md_amount`(中单卖出), `buy_sm_amount`(小单买入), `sell_sm_amount`(小单卖出), `net_amount`(主力净流入)
- ⚠️ 常延迟1日，标记为 `stale_1d`，不作为当日判断主依据

### 优先级2：东方财富实时API（当日数据）

#### A. 每日资金流（近N日）

```
GET https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={mkt}.{code}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65&lmt={days}
```

- `secid`：沪市 `1.{code}`，深市 `0.{code}`
- `lmt`：返回天数（如 `lmt=5` 返回近5日）
- **字段映射（已验证）**：
  - f51 = 日期
  - f52 = 主力净流入
  - f53 = 小单净流入
  - f54 = 中单净流入
  - f55 = 大单净流入（100-500万）
  - f56 = 超大单净流入（>500万）
- **⚠️ 映射验证公式**：`f52 == f55 + f56`（主力 = 大单 + 超大单），必须验算

#### B. 分时资金流（日内逐分钟累计）

```
GET https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?secid={mkt}.{code}&fields1=f1,f2,f3,f7&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63&klt=1&lmt=0
```

- `klt=1` 表示1分钟粒度
- `lmt=0` 表示返回全部（当日从开盘到当前）
- 返回的是**累计值**（非增量），需要做差计算分段增量
- 字段映射同上：f52=主力, f53=小单, f54=中单, f55=大单, f56=超大单

### 优先级3：浏览器F10页面

当API不可用时，通过浏览器访问东方财富/同花顺个股资金流页面获取。

## 三、分析方法

### 3.1 分时段增量分析（核心）

资金流累计值 → 按关键时段做差 → 得到每个时段的增量

**标准时段划分：**

| 时段 | 时间区间 | 分析重点 |
|------|---------|---------|
| 开盘冲击 | 09:31→09:45 | 开盘15分钟主力态度，抢筹还是出逃 |
| 首次分歧 | 09:45→09:55 | 涨停/冲高后主力是否撤退 |
| 震荡消化 | 09:55→10:30 | 分歧后主力是继续卖还是回补 |
| 午前确认 | 10:30→11:30 | 上午收盘前主力最终态度 |
| 午后延续 | 13:00→14:00 | 午后是否延续上午趋势 |
| 尾盘定调 | 14:30→15:00 | 尾盘主力最终方向 |

**计算方法：**

```python
# 假设 all_data 已按时间排序，每个元素含 t(时间), zl(主力), cd(超大单), dd(大单), zd(中单), xd(小单)
def find(data, t):
    for d in data:
        if d['t'] == t: return d
    return None

# 某时段增量 = 时段末累计 - 时段初累计
d_start = find(all_data, "09:31")
d_end = find(all_data, "09:45")
增量_主力 = d_end['zl'] - d_start['zl']
增量_超大单 = d_end['cd'] - d_start['cd']
# ... 同理
```

### 3.2 超大单/大单行为差异分析

超大单（>500万）和大单（100-500万）的行为差异是判断主力意图的关键：

| 场景 | 超大单 | 大单 | 判断 |
|------|--------|------|------|
| 主力扫货 | 大幅流入 | 同向流入或小幅流出 | 强势，主力共识明确 |
| 试探性买入 | 流入 | 流出 | 分歧，需观察持续性 |
| 大单出逃 | 流入 | 大幅流出 | 大单先跑，超大单可能在掩护 |
| 全面撤退 | 流出 | 流出 | 主力一致看空 |
| 超大单独买 | 大幅流入 | 大幅流出 | 特殊：可能是大资金左侧布局 |

**关键指标：超大单占主力比例**
- 超大单/主力 > 80%：超大单主导，通常是机构行为
- 超大单/主力 50-80%：混合行为
- 超大单/主力 < 50%：大单主导，可能是游资行为

### 3.3 近5日趋势对比

将当日资金流放在近5日序列中观察趋势转变：

| 信号 | 含义 | 可靠度 |
|------|------|--------|
| 连续5日主力净卖出 → 今日转正 | 可能是出货途中的反弹 | 低（需2-3日确认） |
| 连续5日主力净卖出 → 今日超大单大幅转正 | 超大单态度转变 | 中（比单纯转正更可靠） |
| 连续3日主力净买入 → 今日继续 | 趋势延续 | 高 |
| 主力净流入但散户也在买入 | 追高风险 | 中 |

### 3.4 与涨停/炸板的关联分析

当个股出现涨停或炸板时，必须叠加资金流分析：

**涨停未炸板：**
- 看封板时段超大单流入强度
- 看封板后超大单是否持续加仓（锁仓）还是撤退

**涨停炸板：**
- 核心问题：炸板时段谁在卖？谁在买？
  - 超大单卖出 + 散户买入 → 主力出货，散户接盘（最危险）
  - 超大单小幅卖出 + 散户卖出 → 短线获利盘消化（相对安全）
  - 超大单买入 + 散户卖出 → 主力洗盘后回补（偏积极）
- 看炸板后超大单是否回流（回流 = 可能只是试探性抛售）

**涨停炸板分时段诊断模板：**

```
炸板诊断：
  冲板阶段(XX:XX-XX:XX)  超大单:+XXXX万  散户:XXXX万  → [谁主导冲板]
  炸板阶段(XX:XX-XX:XX)  超大单:XXXX万  散户:XXXX万  → [谁在砸板/谁在接]
  震荡阶段(XX:XX-XX:XX)  超大单:XXXX万  散户:XXXX万  → [主力是否回流]
  结论: [超大单主导冲板+炸板后回流/超大单出货+散户接盘/...]
```

## 四、输出格式

### 标准资金流分析表

```
### 大单资金流向

#### 今日分时段资金流（增量）

| 时段 | 超大单(>500万) | 大单(100-500万) | 主力合计 | 中单 | 小单 |
|------|---------------|----------------|---------|------|------|
| 冲高阶段 | +XXXX万 | XXXX万 | +XXXX万 | XXXX万 | XXXX万 |
| 回落阶段 | XXXX万 | XXXX万 | XXXX万 | XXXX万 | XXXX万 |
| 震荡阶段 | XXXX万 | XXXX万 | XXXX万 | XXXX万 | XXXX万 |
| 确认阶段 | XXXX万 | XXXX万 | XXXX万 | XXXX万 | XXXX万 |

上午/全天累计: 主力+XXXX万, 超大单+XXXX万, 散户-XXXX万

#### 近5日每日资金流趋势

| 日期 | 主力 | 超大单 | 大单 | 小单 | 信号 |
|------|------|--------|------|------|------|
| MM-DD | +XXXX万 | +XXXX万 | XXXX万 | XXXX万 | [转正/延续/恶化] |

#### 资金流判断

- 主力态度: [回补/出货/试探/观望]
- 超大单行为: [主导买入/部分获利/全面撤退]
- 与价格关系: [量价配合/量价背离/缩量反弹]
- 结论: [资金面对短线偏多/偏空/中性]
```

## 五、常见误判与避坑

1. **只看全天累计不看分段**：全天净流入可能是上午大买+下午大卖的平均值，掩盖了盘中反转
2. **混淆内外盘与资金流**：内盘>外盘不代表主力卖出，只代表主动卖出更多
3. **东方财富字段映射搞错**：f53不是大单是小单，f55才是大单，必须用 f52=f55+f56 验算
4. **单日资金流当趋势**：单日回补可能是出货途中的反弹，需连续2-3日确认
5. **忽略超大单与大单的分歧**：超大单买+大单卖 = 机构vs游资分歧，比单一方向更有信息量
6. **无本地数据时跳过**：必须用东方财富API补数据，不能因为本地parquet缺失就放弃资金流维度

## 六、代码模板

```python
import urllib.request, json

def get_moneyflow_daily(code, market, days=5):
    """获取近N日每日资金流"""
    secid = f"{market}.{code}"
    url = (f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?"
           f"secid={secid}&fields1=f1,f2,f3,f7"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"
           f"&lmt={days}")
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://data.eastmoney.com/'
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode('utf-8'))
    
    results = []
    for line in data.get('data', {}).get('klines', []):
        p = line.split(',')
        # 验证: f52(主力) == f55(大单) + f56(超大单)
        主力 = float(p[1])/10000
        小单 = float(p[2])/10000
        中单 = float(p[3])/10000
        大单 = float(p[4])/10000
        超大单 = float(p[5])/10000
        assert abs(主力 - (大单 + 超大单)) < 1, \
            f"字段映射错误! 主力={主力} vs 大单+超大单={大单+超大单}"
        results.append({
            'date': p[0][:10], '主力': 主力, '小单': 小单,
            '中单': 中单, '大单': 大单, '超大单': 超大单
        })
    return results

def get_moneyflow_intraday(code, market):
    """获取当日分时资金流(累计值)"""
    secid = f"{market}.{code}"
    url = (f"https://push2.eastmoney.com/api/qt/stock/fflow/kline/get?"
           f"secid={secid}&fields1=f1,f2,f3,f7"
           f"&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63"
           f"&klt=1&lmt=0")
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://data.eastmoney.com/'
    })
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode('utf-8'))
    
    all_data = []
    for line in data.get('data', {}).get('klines', []):
        p = line.split(',')
        t = p[0][-5:]  # HH:MM
        all_data.append({
            't': t,
            'zl': float(p[1])/10000,
            'xd': float(p[2])/10000,
            'zd': float(p[3])/10000,
            'dd': float(p[4])/10000,
            'cd': float(p[5])/10000
        })
    return all_data

def calc_segment_flow(all_data, t1, t2):
    """计算两个时间点之间的增量资金流"""
    d1 = d2 = None
    for d in all_data:
        if d['t'] == t1: d1 = d
        if d['t'] == t2: d2 = d
    if not d1 or not d2:
        return None
    return {
        '主力': d2['zl'] - d1['zl'],
        '超大单': d2['cd'] - d1['cd'],
        '大单': d2['dd'] - d1['dd'],
        '中单': d2['zd'] - d1['zd'],
        '小单': d2['xd'] - d1['xd'],
    }

# 使用示例:
# daily = get_moneyflow_daily("600172", 1, 5)
# intraday = get_moneyflow_intraday("600172", 1)
# segment = calc_segment_flow(intraday, "09:31", "09:45")
```

## 七、与其他模块的关联

- **与缺口分析联动**：涨停缺口的封板资金质量 → 看超大单封板强度
- **与分时主力意图联动（Step 6）**：分时图的量价关系 → 用资金流验证"谁在买"
- **与筹码分析联动（Step 8）**：资金流入方向 → 判断筹码是在集中还是分散
- **与涨停炸板分析联动**：炸板时的资金分层 → 判断是主力出货还是洗盘
- **与交易决策推演联动（Step 14）**：资金面是多空因素对比的核心输入
