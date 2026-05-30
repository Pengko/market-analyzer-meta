# 实时大盘分析数据获取指南

本参考文档记录使用 Tushare Pro 进行实时/准实时市场全景分析时的数据获取策略、常见陷阱与最佳实践。

## 数据获取优先级（fallback chain）

当本地数据可能滞后（非最新交易日）时，按以下优先级获取实时数据：

1. **本地校验**（最快）
   - 读取 `~/quant-data/tushare/股票数据/` 下的最新文件
   - 若最新 trade_date 等于最新交易日 → 可直接使用本地数据
   - 若滞后 1 日以上 → 进入远程获取

2. **Tushare Pro 实时接口**（推荐）
   - 指数行情：`pro.index_daily(ts_code='000001.SH', trade_date='YYYYMMDD')`
   - 个股全市场：`pro.daily(trade_date='YYYYMMDD')`（用于涨跌家数统计）
   - 涨跌停：`pro.limit_list_d(trade_date='YYYYMMDD')`
   - 资金流向：`pro.moneyflow_hsgt(trade_date='YYYYMMDD')`（沪深港通）
   - 行业/概念资金：`pro.moneyflow_ind_ths` / `pro.moneyflow_cnt_ths`
   - 融资融券：`pro.margin(trade_date='YYYYMMDD')`
   - 技术形态：`pro.limit_step(trade_date='YYYYMMDD')`（涨停阶梯）

3. **腾讯/新浪等免费 API**（备选，网络依赖）
   - `qt.gtimg.cn` 秒级行情
   - 东方财富 `datacenter-web.eastmoney.com` API（JSONP，需处理 callback）
   - ⚠️  mainland 环境可能需要代理；被墙或限流时立即 fallback

4. **浏览器抓取**（最后手段）
   - 仅当以上全部不可用时使用
   - 用户明确禁止用浏览器补抓龙虎榜数据（本地无 = 未上榜）

## 常见陷阱

### 陷阱1：DataFrame object dtype 导致 format 失败

Tushare Pro 返回的 DataFrame 中，数值列常被解析为 `object` dtype（尤其是包含空字符串的接口）。

```python
# ❌ 会报错：ValueError: Invalid format specifier '.0f ' for object of type 'str'
f"{row['close']:.0f}"

# ✅ 先转换类型
val = float(row['close']) if pd.notna(row['close']) else 0
f"{val:.0f}"

# ✅ 或批量转换
df['close'] = pd.to_numeric(df['close'], errors='coerce')
```

**触发接口**：`moneyflow_ind_ths`、`moneyflow_cnt_ths`、`daily` 等返回空值较多的接口。

### 陷阱2：北向资金字段含义混淆

`pro.moneyflow_hsgt(trade_date='YYYYMMDD')` 返回的字段：

| 字段 | 含义 | 单位 |
|------|------|------|
| `north_money` | 当日总成交额 | 百万元（100万元 = 1亿） |
| `north_buy` | 买入成交额 | 百万元 |
| `north_sell` | 卖出成交额 | 百万元 |

**常见误解**：把 `north_money` 当成净流入。实际净流入 = `north_buy - north_sell`。

### 陷阱3：概念/行业资金接口需先查字段名

同花顺行业/概念资金流向接口返回的列名不固定，第一次调用前应先 inspect：

```python
df = pro.moneyflow_ind_ths(trade_date='20260522')
print(df.columns.tolist())  # ['trade_date', 'ts_code', 'name', 'close', ...]
```

再针对性取字段，避免硬编码列名导致 KeyError。

## 市场广度计算模式

使用 `pro.daily(trade_date='YYYYMMDD')` 获取全市场当日数据，然后：

```python
df = pro.daily(trade_date='20260522')
up = df[df['pct_chg'] > 0]
down = df[df['pct_chg'] < 0]
flat = df[df['pct_chg'] == 0]
limit_up = df[df['pct_chg'] >= 9.9]    # 近似涨停
limit_down = df[df['pct_chg'] <= -9.9] # 近似跌停
```

⚠️ 科创板/创业板涨跌幅为 20%，北交所为 30%，如需精确判断需结合 `market` 字段。

## 报告阶段化输出模式

用户偏好分阶段报告（阶段1/4、阶段2/4...），每阶段带明确进度指示：

```markdown
📊 **阶段1/4：当前市场全景**
✅ 数据获取完成
📈 ...分析内容...

---
📊 **阶段2/4：资金面与情绪面**
⏳ 正在获取...
```

这种模式的优势：
- 降低长分析等待焦虑
- 每阶段可独立审阅
- 用户可随时喊停或要求"重新分析"（意味着进入下一轮，增加数据维度）

## 时段规则（从 stock-deep-analysis 继承）

| 时段 | 允许分析范围 | 禁止 |
|------|-------------|------|
| 盘前 (09:30前) | T-1 收盘数据回顾 | 推演当日 |
| 盘中/午间 (09:30-15:00) | 上午走势 + 下午推演 | 推演 T+1 及后续 |
| 盘后 (15:00后) | 全天回顾 + T+1 推演 | — |

盘后分析完成后，报告应保存到 `references/pending-validations/{T日日期}/`（大盘分析通常不需要 pending-validation）。
