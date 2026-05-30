# 从 quick_analyze.py 生成标准盘后深度分析报告

## 适用场景

- 用户要求盘后深度分析多只股票
- `build_stock_report.py` 的默认输出格式不满足需求
- 需要基于 `quick_analyze.py` JSON 数据快速生成符合技能规范的报告

## 关键认知

### 1. 没有 `full_analyze.py`

技能目录下**不存在** `full_analyze.py` 。主要入口为 `quick_analyze.py` 和 `build_stock_report.py` 。

### 2. quick_analyze.py 输出格式特殊

输出第一行是标题 `[快速分析] XXX @ YYYY-MM-DD`，**不是纯 JSON**。解析时必须跳过第一行。

### 3. 核心数据字段速查

| 字段路径 | 类型 | 说明 |
|----------|------|------|
| `snapshot.current` | float | 当日收盘价 |
| `snapshot.change_pct` | float | 当日涨跌幅(%) |
| `snapshot.volume` | float | 当日成交量 |
| `klines[]` | array[10] | 近10日日线 |
| `minute_intent[]` | array[16] | 16个15分钟时段 |
| `moneyflow.rows[]` | array[5] | 近5日资金流向 |
| `factors.latest` | dict | 技术因子快照（延迟1天） |
| `chips.rows[]` | array | 筹码分布（延迟1天） |
| `data_status` | dict | 各维度状态 |

#### 重要认知：minute_intent[].volume 为累计值

`quick_analyze.py` 的 `minute_intent` 中 `volume` 字段表示**累计到该时段的成交量**，而非该时段内的区间成交量。

例如：09:30-09:45 显示 volume=79.4万手，表示从开盘到09:45累计成交了79.4万手。如果下一时段 09:45-10:00 显示 volume=85.2万手，则该时段区间成交量仅为 85.2 - 79.4 = 5.8万手。

**报告中必须标注 `(累计)`**，禁止直接将累计值当作区间量能使用。

## 报告生成脚本

已提供自动化脚本：`scripts/generate_postmarket_report.py`

用法：
```bash
python3 scripts/generate_postmarket_report.py \
    --input /tmp/000555_quick.json \
    --output references/pending-validations/2026-05-26/待验证-000555.SZ-神州信息-盘后.md \
    --name "神州信息"
```

该脚本自动处理：跳过标题行、生成标准 Markdown 结构（含表格）、标注 minute_intent 累计量能、处理 N/A 和缺失数据、自动计算数据完整度和简单决策引擎。

## 已知 Python 陷阱

### 陷阱1: f-string 内部嵌套引号

```python
# 错误 - 内部双引号与外部双引号冲突
f"接近程度: {"接近" if x else "远离"}"

# 正确 - 使用单引号或提前计算
proximity = "接近" if x else "远离"
f"接近程度: {proximity}"
```

### 陷阱2: 对可能为字符串的值使用数字格式化

```python
# 错误 - 当 fac_latest.get() 返回 'N/A' 时会抛出 ValueError
f"KDJ K={fac_latest.get('kdj_k_bfq','N/A'):.2f}"

# 正确 - 先尝试转换为 float
fmt_num(fac_latest.get('kdj_k_bfq'))

def fmt_num(val, dec=2):
    try: return f"{float(val):.{dec}f}"
    except: return str(val)
```

### 陷阱3: 中文文本 typo

- "姓续" → "继续" (已在 2026-05-26 修复)

## 存档路径

- 本文档: `references/from-quick-analyze-to-postmarket-report.md`
- 自动化脚本: `scripts/generate_postmarket_report.py`
