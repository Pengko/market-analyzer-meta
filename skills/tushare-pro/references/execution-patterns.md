# A股数据分析执行模式与陷阱

本文档记录在A股数据分析工作流中，`execute_code`、`terminal`、`write_file` 三种工具的最佳组合模式、已知陷阱与解决方案。

## 工具选型决策树

```
需要 tushare API 调用？
├── 是 → 必须用 terminal() 执行 Python 脚本
│         （execute_code 隔离环境无 tushare）
└── 否 → 数据已在本地 CSV/SQLite？
          ├── 是 → execute_code 或 terminal() 均可
          │         （execute_code 有 pandas，适合纯分析任务）
          └── 否 → 根据复杂度选择
```

## 陷阱1：execute_code 隔离环境缺少依赖

**表现**：
```
ModuleNotFoundError: No module named 'tushare'
```

**根因**：`execute_code` 运行在独立沙盒环境中，虽然有 pandas、numpy 等基础库，但**没有安装 tushare**。即使主环境（`terminal()`）已经安装了 tushare，execute_code 也无法访问。

**解决**：
- 需要 tushare API 时，**始终用 `terminal()`**执行 Python 脚本
- execute_code 只用于：纯数据分析（CSV已读入 DataFrame）、图表绘制、文本处理

## 陷阱2：terminal heredoc 被误判为背景进程

**表现**：
执行 `terminal(command="python3 << 'PYEOF'\n...\nPYEOF")` 时，系统提示已启动背景进程。这是因为 `PYEOF` 定界符中包含 `&` 字符，被工具解析器误判为 shell 后台运行标识。

**解决**：
**永远不用 heredoc 传递 Python 脚本。**改用两步法：

```python
# 步骤1：写入文件
write_file(path="/tmp/analysis_script.py", content="...")

# 步骤2：执行
terminal(command="python3 /tmp/analysis_script.py")
```

这种方式更可靠，避免了特殊字符解析问题，也方便调试。

## 陷阱3：按年份分区的数据文件

**表现**：读取 `daily/daily_000001.SZ.csv` 失败，文件不存在。

**根因**：`daily`、`daily_basic`、`stk_factor_pro`、`cyq_chips` 等接口的数据文件是按年份分目录存储的：
```
daily/
  2013/daily_000001.SZ.csv
  2024/daily_000001.SZ.csv
  2025/daily_000001.SZ.csv
  2026/daily_000001.SZ.csv
```

**解决**：使用 `rglob` 搜索所有年份：

```python
from pathlib import Path
import pandas as pd

# 正确：合并所有年份
files = sorted(Path('/Users/penghongming/quant-data/tushare/股票数据/daily').rglob('daily_000001.SZ.csv'))
df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)

# 错误：直接读单一路径
df = pd.read_csv('daily/daily_000001.SZ.csv')  # 文件不存在！
```

## 最佳实践：多步骤分析流程

对于需要结合 API + 本地数据 + 图表的复杂分析（如股东户数变化分析）：

```
步骤 1: write_file 写入完整脚本
步骤 2: terminal(python3 /tmp/script.py) 获取结果
步骤 3: 如需要，execute_code 读取生成的 CSV 做二次分析/绘图
```

这种模式的好处：
- 脚本可复用、可调试
- 避免 heredoc 陷阱
- terminal 有完整依赖，execute_code 处理纯计算
- 如果脚本出错，可以直接修改文件重试

## 常用分析脚本模板

### 模板A：单股票全量数据合并
```python
from pathlib import Path
import pandas as pd

def load_stock_data(data_type, ts_code):
    base = Path('/Users/penghongming/quant-data/tushare/股票数据') / data_type
    files = sorted(base.rglob(f'{data_type}_{ts_code}.csv'))
    return pd.concat([pd.read_csv(f) for f in files], ignore_index=True)

# 使用
df = load_stock_data('daily', '000001.SZ')
```

### 模板B：API 数据获取 + 本地缓存
```python
import pandas as pd
import os

# 设置环境
os.environ['TUSHARE_API_URL'] = 'http://lianghua.nanyangqiankun.top'
os.environ['TUSHARE_TOKEN'] = 'your_token'

import tushare as ts
pro = ts.pro_api()

# 获取数据
df = pro.stk_holdernumber(ts_code='603305.SH', limit=5000)

# 本地缓存（避免重复调用 API）
cache_path = '/tmp/stk_holdernumber_603305.csv'
df.to_csv(cache_path, index=False)
print(f"缓存已保存到: {cache_path}")
```

### 模板C：多接口并行获取
当需要同时获取多个接口数据时，先分别写入文件，再统一执行：

```python
# 在一个脚本中获取多个接口数据，然后分别导出
df_holder = pro.stk_holdernumber(ts_code='603305.SH', limit=5000)
df_basic = pro.daily_basic(ts_code='603305.SH', trade_date='20260522')
df_money = pro.moneyflow(ts_code='603305.SH', start_date='20250101', end_date='20260522')

df_holder.to_csv('/tmp/holder.csv', index=False)
df_basic.to_csv('/tmp/basic.csv', index=False)
df_money.to_csv('/tmp/money.csv', index=False)
```
