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

### 处理原则

1. **先检查列名**：读取 parquet 前，先用 `df.columns.tolist()` 确认实际列名
2. **禁止硬编码**：禁止凭记忆硬编码列名，尤其是 `_bfq` / `_afq` 后缀和 `date` / `trade_date` 差异
3. **单位分离**：当需要与 quick_analyze 输出对比时，先确认两方字段的映射关系和单位是否一致
4. **字段映射表**：每次新增数据源时，在本文档更新映射表，确保后续 agent 可查
