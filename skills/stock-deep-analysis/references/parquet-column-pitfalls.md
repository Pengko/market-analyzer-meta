## Parquet 列名陷阱与数据读取约束（2026-05-29 实战验证）

本文档记录在直接使用 `pyarrow.parquet` 读取本地 parquet 数据时遇到的实际列名陷阱。

---

### 陷阱 1：`kdj_j_bfq` 列不存在（stk_factor_pro）

**问题**：常见代码试图读取 `kdj_j_bfq`，但该列在 parquet 中不存在。

**实际列名**：`stk_factor_pro/{code}.parquet` 中 KDJ J 值字段名为 `kdj_bfq`，不是 `kdj_j_bfq`。

```python
# ❌ 错误（KeyError）
factor[['kdj_k_bfq', 'kdj_d_bfq', 'kdj_j_bfq']]

# ✅ 正确
available = [c for c in factor.columns if 'kdj' in c]
factor[['kdj_k_bfq', 'kdj_d_bfq', 'kdj_bfq']]  # kdj_bfq = J值
```

---

### 陷阱 2：moneyflow_data/individual/ths 列名与 quick_analyze 输出不一致

**问题**：不仅列名不同，而且单位可能不同，禁止混用。

| 维度 | quick_analyze 输出字段 | ths parquet 实际字段 | 说明 |
|------|----------------------|---------------------|------|
| 日期列 | `date` | `trade_date` | 必须用 `trade_date` 排序 |
| 净流入 | `net_mf_amount` | `net_amount` | 字段名不同 |
| 超大单买入 | — | `buy_lg_amount` | quick_analyze 未输出 |
| 超大单买入占比 | — | `buy_lg_amount_rate` | quick_analyze 未输出 |
| 中单买入 | — | `buy_md_amount` | quick_analyze 未输出 |
| 小单买入 | — | `buy_sm_amount` | quick_analyze 未输出 |

**严重陷阱：ths parquet 只有 BUY 侧列，无 SELL 侧列，也无 ELG（超大单）分类**

以下列名**不存在**于 `moneyflow_data/individual/ths/{code}.parquet` 中，读取会直接抛 `KeyError`：
- `sell_sm_amount` / `sell_lg_amount` / `sell_md_amount` — 全部不存在
- `buy_elg_amount` / `sell_elg_amount` — 不存在（ths 数据无"超大单"分类，只有 lg/md/sm 三档）
- `net_mf_amount` — 不存在（该名称是东财接口字段名）

**ths 实际可用列完整清单**：
```python
# 可用列（共 12 列）：
trade_date, ts_code, name, pct_change, latest,
net_amount, net_d5_amount,
buy_lg_amount, buy_lg_amount_rate,
buy_md_amount, buy_md_amount_rate,
buy_sm_amount, buy_sm_amount_rate
```

**这意味着**：
1. 无法从 ths parquet 直接计算"大单净买入"（因无 sell_lg）
2. 无法计算"超大单"（elg）任何指标（该分类不存在）
3. 若要分析 sell 侧或 elg 级别，必须改用 `moneyflow_data/individual/tushare/`（按日期全市场表）或东财 API

**单位差异警告**：ths parquet 的 `net_amount` 与同花顺接口原始单位一致，而 quick_analyze 的 `net_mf_amount` 来自东财接口，二者数值量级可能不同。禁止将 ths parquet 的 `net_amount` 直接与 quick_analyze 的 `net_mf_amount` 横向对比。

**正确读取方式**：
```python
import pyarrow.parquet as pq

mf = pq.read_table(f'{root}/moneyflow_data/individual/ths/{code}.parquet').to_pandas()
mf = mf.sort_values('trade_date')  # 不是 'date'
# 可用列：trade_date, ts_code, name, pct_change, latest,
#         net_amount, net_d5_amount,
#         buy_lg_amount, buy_lg_amount_rate,
#         buy_md_amount, buy_md_amount_rate,
#         buy_sm_amount, buy_sm_amount_rate
```

---

### 陷阱 3：execute_code 中 f-string 嵌套转义

**问题**：当使用 `execute_code` 写入包含 f-string 的 Python 代码时，大括号 `{}` 会被外层 f-string 解析，导致语法错误。

**解决**：在 `execute_code` 内写变量时，将变量传入再使用字符串拼接，或避免 f-string：

```python
# ❌ 错误（外层 f-string 会解析内层 {} 造成语法错误）
code_str = f"""
price = {price}
print(f"Price: {price}")  # 这里的 {price} 会被外层解析
"""

# ✅ 正确：用单引号包裹，避免双层 f-string
execute_code(code='''
price = 4.28
print(f"Price: {price}")
''')
```

---

### 陷阱 4：stk_factor_pro 列名带 `_bfq` 后缀

**问题**：常见代码试图读取 `ma5`、`ma10`、`ma20`、`ma30`、`rsi_6`、`rsi_12` 等列名，但这些列在 parquet 中不存在。

**实际列名**：`stk_factor_pro/{code}.parquet` 中均线和 RSI 字段均带 `_bfq` 后缀：

| 常见错误列名 | 实际列名 | 说明 |
|-------------|---------|------|
| `ma5` | `ma_bfq_5` | 5日均线（不复权） |
| `ma10` | `ma_bfq_10` | 10日均线 |
| `ma20` | `ma_bfq_20` | 20日均线 |
| `ma30` | `ma_bfq_30` | 30日均线 |
| `rsi_6` | `rsi_bfq_6` | 6日RSI |
| `rsi_12` | `rsi_bfq_12` | 12日RSI |
| `kdj_j_bfq` | `kdj_bfq` | KDJ J值（已在陷阱1记录） |

**正确读取方式**：
```python
import pyarrow.parquet as pq

factor = pq.read_table(f'{root}/stk_factor_pro/{code}.parquet').to_pandas()
factor = factor.sort_values('trade_date')
# 可用均线列：ma_bfq_5, ma_bfq_10, ma_bfq_20, ma_bfq_30, ma_bfq_60, ma_bfq_90, ma_bfq_250
# 可用RSI列：rsi_bfq_6, rsi_bfq_12, rsi_bfq_24
# KDJ列：kdj_k_bfq, kdj_d_bfq, kdj_bfq (J值)
```

---

### 陷阱 5：parquet 文件名带交易所后缀（.SH / .SZ）

**问题**：用 `{code}.parquet`（如 `600703.parquet`）读取文件会 FileNotFoundError。

**实际文件名**：`{code}.{exchange}.parquet`，如 `600703.SH.parquet`、`000725.SZ.parquet`。

**正确路径**：
```python
# ❌ 错误（FileNotFoundError）
path = f'{root}/daily/600703.parquet'

# ✅ 正确
path = f'{root}/daily/600703.SH.parquet'
# 通用写法
path = f'{root}/daily/{ts_code}.parquet'  # ts_code 已包含 .SH/.SZ
```

**适用范围**：所有以 `{code}` 为键的 parquet 目录（daily、stk_factor_pro、moneyflow、cyq_perf、margin_detail 等）均使用带交易所后缀的文件名。

---

### 陷阱 6：概念成分表是年度合并文件，不是按股票拆分

**问题**：尝试读取 `dc_concept_cons/600703.SH.parquet` 会 FileNotFoundError。

**实际结构**：概念成分表按年份存储，一个文件包含该年度所有股票的概念归属。

```
theme_data/
├── dc_concept_cons/
│   ├── 2026.parquet        # 2026年全部概念成分（含所有股票）
│   └── 2025.parquet
├── kpl_concept_cons/
│   ├── 2026.parquet
│   └── 2025.parquet
└── dc_concept/
    └── 2026.parquet        # 概念列表（不含成分股）
```

**正确读取方式**：
```python
import pyarrow.parquet as pq

# 读取年度文件，再按 ts_code 过滤
dc = pq.read_table(f'{root}/theme_data/dc_concept_cons/2026.parquet').to_pandas()
sanan_dc = dc[dc['ts_code'] == '600703.SH']

# dc_concept_cons 列名：ts_code, trade_date, name, theme_code, industry_code, industry, reason, hot_num
# kpl_concept_cons 列名：ts_code, name, con_name, con_code, trade_date, desc, hot_num
```

---

### 处理原则

1. **先检查列名**：读取 parquet 前，先用 `df.columns.tolist()` 确认实际列名
2. **禁止硬编码**：禁止凭记忆硬编码列名，尤其是 `_bfq` / `_afq` 后缀和 `date` / `trade_date` 差异
3. **单位分离**：当需要与 quick_analyze 输出对比时，先确认两方字段的映射关系和单位是否一致
4. **字段映射表**：每次新增数据源时，在本文档更新映射表，确保后续 agent 可查
5. **文件名带交易所后缀**：parquet 文件名必须包含 `.SH` / `.SZ`，不能只用纯数字代码
6. **概念成分表按年存储**：读取时先加载年度文件，再按 `ts_code` 过滤目标股票
