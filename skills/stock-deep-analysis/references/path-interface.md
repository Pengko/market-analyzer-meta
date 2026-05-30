# 本地路径接口文档

## 目标

这份文档定义 `stock-deep-analysis` 当前统一的本地路径接口。

目标只有两个：

1. 路径真值只保留一处，方便后续整体迁移。
2. 业务代码只调用接口，不再自己拼绝对路径或猜目录结构。


## 总体分层

当前本地路径接口分 3 层：

1. 配置真值层
   文件：[`references/config/skill-config.yaml`](/Users/penghongming/agent-skills/custom/stock-deep-analysis/references/config/skill-config.yaml)

2. 配置读取层
   目录：[`scripts/data/`](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/data)

3. 公共导出层
   文件：[`scripts/common.py`](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/common.py)


## 一、配置真值层

权威路径配置文件：
[`skill-config.yaml`](/Users/penghongming/agent-skills/custom/stock-deep-analysis/references/config/skill-config.yaml)

这里是“路径真值”的唯一来源，优先维护这里，不要在业务代码里重复写路径。

当前核心配置包括：

- `paths.stock_data_root`
  本地股票数据根目录
- `paths.news_data_root`
  本地消息面数据根目录
- `paths.index_data_root`
  本地指数数据根目录
- `paths.financial_data_root`
  本地财务数据根目录
- `paths.references_dir`
  技能文档/参考资料目录
- `paths.validations_dir`
  验证归档目录
- `paths.sqlite_db`
  SQLite 数仓路径
- `paths.trade_cal`
  交易日历候选文件列表

当前股票数据子目录统一定义在：

- `paths.subdirs.daily`
- `paths.subdirs.daily_basic`
- `paths.subdirs.stk_factor`
- `paths.subdirs.moneyflow_data`
- `paths.subdirs.moneyflow_individual`
- `paths.subdirs.moneyflow_individual_tushare`
- `paths.subdirs.moneyflow_individual_dc`
- `paths.subdirs.moneyflow_individual_ths`
- `paths.subdirs.weekly`
- `paths.subdirs.monthly`
- `paths.subdirs.minute`
- `paths.subdirs.hm_list`
- `paths.subdirs.trade_cal_dir`
- `paths.subdirs.financial_income`
- `paths.subdirs.financial_balancesheet`
- `paths.subdirs.financial_cashflow`
- `paths.subdirs.financial_disclosure_date`
- `paths.subdirs.financial_express`
- `paths.subdirs.financial_fina_audit`
- `paths.subdirs.financial_fina_indicator`
- `paths.subdirs.financial_fina_mainbz`
- `paths.subdirs.financial_forecast`


## 二、配置读取层

主要文件：

- [`config_loader.py`](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/data/config_loader.py)
- [`data_access.py`](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/data/data_access.py)

### `config_loader.py`

职责：

- 读取 `skill-config.yaml`
- 展开 `~`
- 展开环境变量
- 提供统一访问接口

推荐使用方式：

```python
from data.config_loader import cfg

stock_root = cfg.paths("stock_data_root")
minute_root = cfg.paths("minute")
sqlite_db = cfg.paths("sqlite_db")
timeout = cfg.network("timeout_seconds", default=30)
```

核心接口：

- `cfg.paths(key)`
  读取路径配置
- `cfg.get(*keys, default=...)`
  通用多层读取
- `cfg.indicator(...)`
- `cfg.network(...)`
- `cfg.fetcher(...)`
- `cfg.mobile(...)`

说明：

- `cfg.paths("stock_data_root")` 读取的是直接路径
- `cfg.paths("minute")` 这种会从 `paths.subdirs.minute` 自动拼到 `stock_data_root`

### `data_access.py`

职责：

- 封装本地数据读取
- 兼容不同目录结构
- 兼容 `csv / parquet`
- 提供上层业务统一读法

当前已经统一支持：

- 平铺结构
- 年份分区结构
- `csv`
- `parquet`
- parquet 不可读时自动回退同名 CSV

推荐优先使用的读取接口：

- `load_yearly_or_flat_rows(root_dir, filename)`
  统一读取年份分区或平铺数据
- `load_daily_row(full_symbol, trade_date_compact)`
- `load_daily_basic_row(full_symbol, trade_date_compact)`
- `read_top_list(trade_date_compact)`
- `read_top_inst(trade_date_compact)`


## 三、公共导出层

文件：
[`common.py`](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/common.py)

职责：

- 把常用路径导出成稳定常量
- 给业务代码提供统一入口

当前核心常量：

- `SKILL_ROOT`
- `SCRIPTS_ROOT`
- `REFERENCES_ROOT`
- `LOGS_ROOT`
- `TUSHARE_ROOT`
- `STOCK_DATA_ROOT`
- `NEWS_DATA_ROOT`
- `INDEX_DATA_ROOT`
- `FINANCIAL_DATA_ROOT`
- `MINUTE_DATA_ROOT`

推荐使用方式：

```python
from common import STOCK_DATA_ROOT, MINUTE_DATA_ROOT, INDEX_DATA_ROOT

daily_root = STOCK_DATA_ROOT / "daily"
minute_root = MINUTE_DATA_ROOT
index_root = INDEX_DATA_ROOT
```


## 四、推荐调用规则

### 业务代码

优先顺序：

1. 先用 `common.py` 导出的常量
2. 再用 `cfg.paths(...)`
3. 最后才在 `data_access.py` 里封装具体读取

推荐：

```python
from common import MINUTE_DATA_ROOT
target = MINUTE_DATA_ROOT / "2026" / "04" / "23" / "600103.SH" / "1m.csv"
```

也可以：

```python
from data.config_loader import cfg
moneyflow_root = cfg.paths("moneyflow_individual_tushare")
```

不推荐：

```python
Path.home() / "quant-data" / "tushare" / "股票数据" / "分钟数据"
```

### 测试代码

默认也走同一套接口。

如需隔离测试目录，优先用环境变量覆盖：

- `STOCK_DATA_ROOT`
- `MINUTE_DATA_ROOT`

### Shell / Node 脚本

统一原则：

- 优先通过环境变量接收路径
- 不在脚本里写死绝对路径
- 输出路径与 Python 主链保持同一目录结构


## 五、当前权威目录口径

### 股票数据根

[`股票数据`](/Users/penghongming/quant-data/tushare/股票数据)

### 指数数据根

[`指数数据`](/Users/penghongming/quant-data/tushare/指数数据)

### 财务数据根

[`财务数据`](/Users/penghongming/quant-data/tushare/财务数据)

### 核心业务目录

- 日线：`daily/YYYY/daily_{ts_code}.csv`
- 日基本面：`daily_basic/YYYY/daily_basic_{ts_code}.csv`
- 技术因子：`stk_factor_pro/YYYY/stk_factor_pro_{ts_code}.csv`
- 竞价开盘：`stk_auction_o/YYYY/stk_auction_o_{ts_code}.csv`
- 竞价收盘：`stk_auction_c/YYYY/stk_auction_c_{ts_code}.csv`
- 筹码分布：`cyq_chips/YYYY/cyq_chips_{ts_code}.csv`
- 筹码绩效：`cyq_perf/YYYY/cyq_perf_{ts_code}.csv`
- 资金流：`moneyflow_data/individual/tushare/YYYY/MM/DD/moneyflow_{YYYYMMDD}.csv`
- 龙虎榜明细：`top_list/YYYY/top_list_{YYYYMMDD}.csv`
- 龙虎榜机构明细：`top_inst/YYYY/top_inst_{YYYYMMDD}.csv`
- 游资榜单：`hm_list/hm_list.csv`
- 分钟线：`分钟数据/YYYY/MM/DD/{ts_code}/1m.csv`
- 指数日线：`指数数据/index_daily/YYYY/index_daily_{index_code}.csv`
- 利润表：`财务数据/income/income_{YYYY}.csv`
- 资产负债表：`财务数据/balancesheet/balancesheet_{YYYY}.csv`
- 现金流量表：`财务数据/cashflow/cashflow_{YYYY}.csv`
- 财报披露日期：`财务数据/disclosure_date/disclosure_date_{YYYY}.csv`
- 业绩快报：`财务数据/express/express_{YYYY}.csv`
- 审计意见：`财务数据/fina_audit/fina_audit_{YYYY}.csv`
- 财务指标：`财务数据/fina_indicator/fina_indicator_{YYYY}.csv`
- 主营业务构成：`财务数据/fina_mainbz/{YYYY}.csv`
- 业绩预告：`财务数据/forecast/forecast_{YYYY}.csv`


## 六、已兼容的历史结构

当前主链已经兼容以下历史结构，但都不再是推荐写法：

- `YYYY/MM/DD/{pure}_1m.csv`
- `{code}/{date}/minute_kline.csv`
- 旧 JSON 风格分钟文件伪装成 `.csv`
- 部分平铺的 `root/filename.csv`
- 年份目录下同时存在 `.parquet` 和 `.csv`


## 七、禁止写法

以下写法应避免继续新增：

- 在业务代码里直接写 `/Users/penghongming/...`
- 手工拼 `Path.home() / "quant-data" / ...`
- 在多个脚本里各自重复维护同一条子目录规则
- 只认 parquet，不回退同名 CSV
- 分钟线只认扁平 `{pure}_1m.csv`，不认 `{symbol}/1m.csv`


## 八、后续维护原则

如果后续要改本地数据根目录，按下面顺序处理：

1. 先改 [`skill-config.yaml`](/Users/penghongming/agent-skills/custom/stock-deep-analysis/references/config/skill-config.yaml)
2. 再确认 `cfg.paths(...)` 是否仍能正确解析
3. 再检查 `common.py` 导出的常量是否仍满足主链
4. 最后跑主链校验脚本，确认新鲜度、分钟、资金流、竞价等都能读通

如果后续新增新的数据目录，也按下面规则接入：

1. 先在 `skill-config.yaml` 增加路径或子目录配置
2. 再在 `common.py` 视需要导出常量
3. 再在 `data_access.py` 增加统一读取函数
4. 最后让业务层调用接口，不直接拥有路径知识
