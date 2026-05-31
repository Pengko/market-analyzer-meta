# check_data_freshness.py 已知 Bug 与降级指南

## Bug 表现

```
ValueError: unconverted data remains: :00
```

## 触发条件

脚本对 `YYYYMMDD` 格式的 `--trade-date` 参数解析时可能失败，原因通常是日期字符串带有额外的时间后缀（如 `:00`）或脚本内部的日期解析逻辑对纯数字日期处理不当。

该 bug 在 2026-05-31 分析京东方A（000725）时复现确认。

## 即时降级步骤

当遇到此错误时，**不要放弃数据新鲜度检查**，按以下步骤手动完成：

### 1. 扫描本地数据目录

检查各数据维度的本地 parquet 文件最后修改时间：

```bash
# 日线
ls -la ${STOCK_DATA_ROOT}/daily/{code}.parquet

# 技术因子
ls -la ${STOCK_DATA_ROOT}/stk_factor_pro/{code}.parquet

# 资金流向
ls -la ${STOCK_DATA_ROOT}/moneyflow_data/individual/ths/{code}.parquet

# 筹码（cyq_perf 为主，cyq_chips 已废弃）
ls -la ${STOCK_DATA_ROOT}/cyq_perf/{code}.parquet

# 融资融券
ls -la ${STOCK_DATA_ROOT}/margin_detail/{code}.parquet

# 分钟线（年/月/日/个股 层级）
ls -la ${STOCK_DATA_ROOT}/minute_kline/{code}/
```

### 2. 通过腾讯 API 验证最新行情

获取当日实时行情，确认最新交易日期：

```bash
# 个股
 curl -s "http://qt.gtimg.cn/q=sz{code}" | iconv -f gb2312 -t utf-8

# 大盘指数（上证/深成/创业板）
curl -s "http://qt.gtimg.cn/q=sh000001,sz399001,sz399006" | iconv -f gb2312 -t utf-8
```

字段解析（逗号分隔）：
- 字段3：最新价（当日收盘）
- 字段4：昨收价
- 字段5：开盘价
- 字段6：最高价
- 字段7：最低价
- 字段32：涨跌幅（%）
- 字段37：成交额（万元）
- 字段38：换手率（%）

### 3. 通过腾讯 K 线 API 获取近期日线

获取近 60 日复权日线，用于确认本地数据与网络数据的最新日期差异：

```bash
curl -s "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sz{code},day,,,60,qfq"
```

### 4. 生成数据新鲜度汇总表

在报告中以表格形式显式输出各数据维度的状态：

| 数据维度 | 最新日期 | 状态 | 说明 |
|----------|----------|------|------|
| 日线（daily） | YYYY-MM-DD | available / stale_Xd / missing | 本地最新日期与网络对比 |
| 技术因子（stk_factor_pro） | YYYY-MM-DD | stale_Xd | KDJ 等指标需降级参考 |
| 资金流向（moneyflow） | YYYY-MM-DD | stale_Xd | T日资金意图以分时成交量替代 |
| 筹码（cyq_perf） | YYYY-MM-DD | stale_Xd | 跌停后筹码结构已剧变需动态推演 |
| 融资融券（margin） | YYYY-MM-DD | stale_Xd | 融资余额高位可能剧烈变化 |
| 分钟线（minute_kline） | MISSING | missing | 本地无存储需注明 |
| 指数（index_daily） | MISSING | missing | 本地无目录已用API补全 |

状态定义：
- `available`：本地数据最新日期 == 分析日（T-1）
- `stale_Xd`：本地数据最新日期比分析日延迟 X 天
- `missing`：本地无该维度数据

### 5. 降级声明

在报告开头的"场景与数据"模块中必须写明：

> **降级说明**：`check_data_freshness.py` 因日期格式 bug（`ValueError: unconverted data remains: :00`）执行失败，已降级为手动目录扫描 + 腾讯行情API + K线API直接获取。T日个股及大盘数据均来自网络渠道，本地数据只做历史结构同步确认。

## 注意事项

1. **禁止跳过数据新鲜度检查**。即使 `check_data_freshness.py` 失败，也必须通过手动方式完成数据新鲜度确认，不能让分析在"数据黑箱"状态下进行。
2. **网络渠道优先原则**：当本地数据 stale 或 missing 时，浏览器/API 获取的当日事实优先于本地过期数据。
3. **所有降级必须在报告中显式标注**，不能静默使用网络数据而不说明来源。
4. **分钟线缺失时**：若本地无 minute_kline 存储，必须标记为 `missing`，禁止用日线冒充分钟线分析分时主力意图。

## 修复方向

长期修复应修改 `check_data_freshness.py` 的日期解析逻辑：
- 在 `pd.to_datetime()` 或 `datetime.strptime()` 前清理日期字符串中的时间后缀
- 或增加对 `YYYY-MM-DD HH:MM:SS` 和 `YYYYMMDD` 两种格式的兼容性处理
