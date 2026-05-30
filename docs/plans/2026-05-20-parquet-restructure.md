# Parquet 存储结构优化 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将散落在 CSV 同目录的 `.parquet` 文件集中到独立目录树，消除双格式冗余，降低目录噪音，同时保留 CSV 存储结构不变。

**Architecture:** 新增一个 centralized parquet 层 `~/quant-data/tushare/_parquet/`，按数据类型(interface)一级子目录 + 按年二级子目录组织，每个数据类型的 parquet 合并为一个文件（而非逐股票/逐日散文件）。这样 parquet 从"CSV 的影子"变成"独立优化的列存层"。

**Tech Stack:** Python, pandas/pyarrow, parquet

**Frozen Baseline:**
- CSV 路径、文件名、格式完全不变
- 写入/更新流程只改 `core/files.py` 的 `write_multi_format_bundle` 一处
- 下游消费端（stock-deep-analysis `data_access.py`）适配新路径
- 旧散落的 `.parquet` 文件通过迁移脚本清理，不清除不影响运行

---

### 现状全景

**写入端**（谁来写 parquet）：

| 调用方 | 文件 | 调用次数 |
|--------|------|----------|
| `core/files.py` | `write_multi_format_bundle()` — 唯一实际写入函数 | 1 个函数 |
| `core/autofill_runtime.py` | 各处调 `shared_write_multi_format_bundle()` | ~20 处 |
| `core/theme_fillers.py` | 通过 `_write_theme_bundle()` 调 `write_multi_format_bundle` | ~10 处 |
| `scripts/fill_financial_recent_year.py` | 调 `af.shared_write_multi_format_bundle()` | 1 处 |
| `scripts/migrate_flat_to_ymd.py` | 调 `write_multi_format_bundle` | 1 处 |

**当前写入逻辑**（`core/files.py:166-196`）：
```
parquet_path = csv_path.with_suffix(".parquet")
# → CSV 同目录同文件名，仅后缀不同
# → daily/2025/daily_000001.SZ.parquet 紧挨着 daily/2025/daily_000001.SZ.csv
```

**消费端**（谁来读 parquet）：

| 调用方 | 文件 | 读取模式 |
|--------|------|----------|
| `stock-deep-analysis/data_access.py` | `_resolve_yearly_path()`, `load_yearly_or_flat_rows()`, `read_top_list()`, `read_top_inst()`, `load_dc_concept_constituents_local()` | CSV 同目录 `.parquet` 后缀替换 |
| `stock-deep-analysis/scan_data_inventory.py` | 数据清单扫描 | glob `**/*.parquet` |
| `stock-deep-analysis/check_data_freshness.py` | 新鲜度检查 | 后缀替换 |
| `stock-deep-analysis/dragon_tiger_agent.py` | 龙虎榜分析 | `fp.suffix == ".parquet"` |
| `stock-deep-analysis/dragon_tiger_analyzer.py` | 龙虎榜分析 | `for ext in (".parquet", ".csv")` |
| `stock-deep-analysis/pre_collect_data.py` | 预采集 | 显式 parquet 路径 |

---

### 新存储结构设计

```
~/quant-data/tushare/
├── 股票数据/              ← CSV 完全不变
│   ├── daily/YYYY/daily_{ts_code}.csv
│   ├── daily_basic/YYYY/daily_basic_{ts_code}.csv
│   └── ...
├── _parquet/              ← NEW: 独立 parquet 存储
│   ├── daily/
│   │   ├── 2025.parquet
│   │   └── 2026.parquet
│   ├── daily_basic/
│   │   ├── 2025.parquet
│   │   └── 2026.parquet
│   ├── moneyflow/
│   │   ├── 2025.parquet
│   │   └── 2026.parquet
│   ├── weekly/            ← 周线不分年（文件少）
│   │   └── weekly.parquet
│   ├── monthly/
│   │   └── monthly.parquet
│   ├── stk_factor_pro/
│   │   ├── 2025.parquet
│   │   └── 2026.parquet
│   ├── trade_cal/
│   │   └── trade_cal_all.parquet
│   ├── stock_basic/
│   │   └── stock_basic.parquet
│   ├── top_list/
│   │   ├── 2025.parquet
│   │   └── 2026.parquet
│   ├── top_inst/
│   │   ├── 2025.parquet
│   │   └── 2026.parquet
│   ├── limit_list_d/
│   │   ├── 2025.parquet
│   │   └── 2026.parquet
│   ├── cyq_chips/
│   │   ├── 2025.parquet
│   │   └── 2026.parquet
│   ├── cyq_perf/
│   │   ├── 2025.parquet
│   │   └── 2026.parquet
│   ├── stk_auction_c/
│   │   ├── 2025.parquet
│   │   └── 2026.parquet
│   └── stk_auction_o/
│       ├── 2025.parquet
│       └── 2026.parquet
```

**原则：**
- 按数据类型（interface name）一级目录
- 数据量大的按年分文件（daily/daily_basic/moneyflow 等），量小的单文件（trade_cal/stock_basic）
- 每个 parquet 文件内部按 `trade_date` 排序，包含该类型该年份的全量数据
- theme_data 类数据（dc_concept/kpl_concept_cons/ths_daily 等）保留原有按日/按概念子目录结构，parquet 仅做格式转换，不合并

---

### Task 1: 修改 `core/files.py` — 新增 centralized parquet 写入函数

**Files:**
- Modify: `core/files.py:166-196`
- Create: `core/parquet_paths.py`

**背景：** 当前 `write_multi_format_bundle` 在 CSV 同目录写 `.parquet`。改为写入 centralized `_parquet/` 目录树。

**设计：** 新增配置模块 `core/parquet_paths.py`，维护接口名 → parquet 目标路径的映射规则。`write_multi_format_bundle` 根据 `interface_name` 查表决定 parquet 写入位置，不再写到 CSV 旁边。

- [ ] **Step 1: 创建 `core/parquet_paths.py` — 路径规则模块**

```python
"""Parquet 存储路径规则。将接口名映射到 _parquet/ 目录下的目标路径。"""

from pathlib import Path
from typing import Optional

# 集中式 parquet 根目录
PARQUET_ROOT = Path("/Users/penghongming/quant-data/tushare/_parquet")

# 需要按年分文件的接口（数据量大）
YEARLY_PARTITIONED_INTERFACES = {
    "daily", "daily_basic", "moneyflow", "stk_factor_pro",
    "top_list", "top_inst", "limit_list_d", "limit_list_ths",
    "cyq_chips", "cyq_perf", "stk_auction_c", "stk_auction_o",
    "stk_nineturn", "stk_shock", "limit_step", "margin_detail",
    "hm_detail", "pledge_detail", "block_trade",
    "weekly", "monthly",
}

# 单文件接口（全量快照类）
FLAT_INTERFACES = {
    "trade_cal": "trade_cal_all.parquet",
    "stock_basic": "stock_basic_all.parquet",
    "index_basic": "index_basic_all.parquet",
    "ths_index": "ths_index_all.parquet",
    "dc_index": "dc_index_all.parquet",
}

# theme 类接口（保留子目录结构，只做格式转换）
THEME_INTERFACES = {
    "kpl_concept_cons", "kpl_list", "dc_concept",
    "dc_concept_cons", "ths_daily", "dc_daily",
    "ths_member", "dc_member",
}


def parquet_path_for_interface(
    interface_name: str,
    csv_path: Path,
    trade_year: Optional[str] = None,
) -> Path:
    """返回该接口本次写入的 parquet 目标路径。"""
    if interface_name in FLAT_INTERFACES:
        return PARQUET_ROOT / interface_name / FLAT_INTERFACES[interface_name]
    if interface_name in THEME_INTERFACES:
        rel = csv_path.relative_to(csv_path.anchor)  # 保留原相对路径结构
        return PARQUET_ROOT / interface_name / rel.parent / f"{csv_path.stem}.parquet"
    if interface_name in YEARLY_PARTITIONED_INTERFACES:
        year = trade_year or "unknown"
        return PARQUET_ROOT / interface_name / f"{year}.parquet"
    # fallback: 按年文件
    year = trade_year or "unknown"
    return PARQUET_ROOT / interface_name / f"{year}.parquet"
```

- [ ] **Step 2: 修改 `write_multi_format_bundle` 写入新路径**

```python
def write_multi_format_bundle(csv_path, frame, interface_name=None, write_parquet=True):
    """Write optional parquet to centralized _parquet/ tree."""
    csv_path = Path(csv_path)
    result = {"csv": str(csv_path), "parquet": None}
    if frame is None:
        return result
    # 清理遗留 sidecar 逻辑不变
    for legacy_sidecar in (
        csv_path.with_name(f"{csv_path.stem}.agent.jsonl"),
        csv_path.with_name(f"{csv_path.stem}.agent.meta.json"),
    ):
        try:
            if legacy_sidecar.exists():
                legacy_sidecar.unlink()
        except Exception:
            pass
    if write_parquet:
        try:
            parquet_frame = frame.copy()
            for col in parquet_frame.columns:
                if str(col).endswith("_date") or str(col) in {"trade_date", "cal_date"}:
                    parquet_frame[col] = parquet_frame[col].astype(str).str.replace("-", "", regex=False)
            # 确定年份
            trade_year = None
            if "trade_date" in parquet_frame.columns and not parquet_frame["trade_date"].empty:
                trade_year = str(parquet_frame["trade_date"].iloc[0])[:4]
            # 计算目标路径
            from core.parquet_paths import parquet_path_for_interface
            parquet_path = parquet_path_for_interface(interface_name or "unknown", csv_path, trade_year)
            parquet_path.parent.mkdir(parents=True, exist_ok=True)
            # 追加模式：读已有 parquet，合并，去重，写回
            if parquet_path.exists():
                existing = pd.read_parquet(parquet_path)
                dedup_cols = [c for c in ["trade_date", "ts_code"] if c in existing.columns and c in parquet_frame.columns]
                combined = pd.concat([existing, parquet_frame], ignore_index=True)
                if dedup_cols:
                    combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
                combined.to_parquet(parquet_path, index=False)
            else:
                parquet_frame.to_parquet(parquet_path, index=False)
            result["parquet"] = str(parquet_path)
        except Exception as exc:
            result["parquet"] = None
    return result
```

- [ ] **Step 3: 验证 `_parquet/` 目录结构**

Run: `python3 -c "from core.parquet_paths import parquet_path_for_interface; print(parquet_path_for_interface('daily', Path('daily/2025/daily_000001.SZ.csv'), '2025'))"`

Expected: `~/quant-data/tushare/_parquet/daily/2025.parquet`

- [ ] **Step 4: 运行主脚本验证写入**

Run: `python3 auto_fill_data.py --mode latest --latest-trade-days 1 --interfaces daily --skip-sqlite-sync`

Expected: CSV 仍在 `股票数据/daily/2026/`，parquet 写到 `_parquet/daily/2026.parquet`

---

### Task 2: 修改 `core/theme_fillers.py` — theme 类适配新写入

**Files:**
- Modify: `core/theme_fillers.py` — `_write_theme_bundle()` 函数

**背景：** `_write_theme_bundle` 内部调 `write_multi_format_bundle`，传入了 `interface_name`。在 Task 1 之后会自动走新路径。但 theme 类接口的子目录结构需要确认路径映射正确。

- [ ] **Step 1: 验证 theme 接口的 parquet 路径**

检查 `parquet_path_for_interface("dc_concept", Path("theme_data/dc_concept/dc_concept_20260427.csv"))` 返回正确的子目录结构。

- [ ] **Step 2: 运行 theme 补全验证**

Run: `python3 auto_fill_data.py --mode latest --latest-trade-days 1 --interfaces dc_concept --skip-sqlite-sync`

Expected: CSV 不变，parquet 写入 `_parquet/dc_concept/`

---

### Task 3: 更新下游 reader `data_access.py`

**Files:**
- Modify: `~/agent-skills/custom/stock-deep-analysis/scripts/data/data_access.py`

**背景：** 所有 parquet 读取路径现在需要指向 `_parquet/` 目录树，而不是 CSV 同目录。

- [ ] **Step 1: 在 `data_access.py` 顶部添加 parquet 根路径常量**

```python
PARQUET_ROOT = STOCK_DATA_ROOT.parent / "_parquet"
```

- [ ] **Step 2: 修改 `_resolve_yearly_path` — 优先检查 `_parquet/` 再 fallback 到 CSV**

逻辑改为：给定 filename `daily_000001.SZ.csv` 和 trade_date，先在 `_parquet/daily/{year}.parquet` 中查该股票该日期的行，命中则直接返回；未命中则回退到 CSV。

- [ ] **Step 3: 修改 `_read_all_yearly_rows` — 同上的 parquet-first 逻辑**

- [ ] **Step 4: 修改 `read_top_list` 和 `read_top_inst` — 从 `_parquet/top_list/{year}.parquet` 读取**

- [ ] **Step 5: 修改 `load_dc_concept_constituents_local` — 检查 `_parquet/dc_concept_cons/`**

- [ ] **Step 6: 验证读取回退**

Run: `python3 -c "from scripts.data.data_access import load_daily_row; print(load_daily_row('000001.SZ', '20260427'))"`

Expected: 优先从 `_parquet/daily/2026.parquet` 读到数据，CSV 路径为 fallback。

---

### Task 4: 迁移脚本 — 将现有散落 parquet 归集到 `_parquet/`

**Files:**
- Create: `scripts/migrate_existing_parquet.py`

**背景：** 现有文件系统上已有大量 `.parquet` 文件散落在 CSV 旁边。需要一次性归集到新结构。

- [ ] **Step 1: 创建迁移脚本**

```python
"""将现有散落的 .parquet 文件归集到 _parquet/ 目录。"""
import shutil
from pathlib import Path
import pandas as pd

STOCK_DATA = Path("/Users/penghongming/quant-data/tushare/股票数据")
PARQUET_ROOT = Path("/Users/penghongming/quant-data/tushare/_parquet")

def collect_parquet_files():
    for parquet_file in sorted(STOCK_DATA.rglob("*.parquet")):
        rel = parquet_file.relative_to(STOCK_DATA)
        parts = list(rel.parts)
        # 去掉年份目录层级，归类到接口名下
        interface_dir = parts[0]  # e.g. daily, daily_basic
        filename = parquet_file.name
        target_dir = PARQUET_ROOT / interface_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename
        if target.exists():
            # 合并
            existing = pd.read_parquet(target)
            new = pd.read_parquet(parquet_file)
            combined = pd.concat([existing, new], ignore_index=True)
            combined = combined.drop_duplicates(keep="last")
            combined.to_parquet(target, index=False)
        else:
            shutil.copy2(str(parquet_file), str(target))
        print(f"  {parquet_file} -> {target}")

collect_parquet_files()
```

- [ ] **Step 2: 运行迁移**

Run: `python3 scripts/migrate_existing_parquet.py`

Expected: 所有现有 parquet 文件被归集到 `_parquet/`，原始文件保留不动。

- [ ] **Step 3: 数据完整性校验**

Run: `python3 -c "
from pathlib import Path
import pandas as pd
old = pd.read_parquet('/Users/penghongming/quant-data/tushare/股票数据/daily/2026/daily_000001.SZ.parquet')
new = pd.read_parquet('/Users/penghongming/quant-data/tushare/_parquet/daily/daily_000001.SZ.parquet')
print(f'Old: {len(old)} rows, New: {len(new)} rows')
"`

Expected: 行数一致或 new 更多（因为合并了更多数据）。

---

### Task 5: 更新 `scan_data_inventory.py` 数据清单路径

**Files:**
- Modify: `~/agent-skills/custom/stock-deep-analysis/scripts/scan_data_inventory.py`

**背景：** 数据清单扫描路径包括 `**/*.parquet`，现在 parquet 已迁移，需要更新 path 指向 `_parquet/`。

- [ ] **Step 1: 将 `scan_data_inventory.py` 中的 parquet glob 路径改为指向 `_parquet/`**

例如：
```python
{"name": "daily", "paths": [f"{PARQUET_ROOT}/daily/*.parquet"], ...}
```

- [ ] **Step 2: 运行扫描验证**

Run: `python3 scripts/scan_data_inventory.py` (from stock-deep-analysis checkout)

Expected: 扫描正常完成，无文件缺失报错。

---

### Task 6: 清理旧散落 parquet（可选，确认后再执行）

**Files:**
- Create: `scripts/cleanup_legacy_parquet.sh`

- [ ] **Step 1: 创建清理脚本**

```bash
#!/bin/bash
# 在确认迁移完成且下游正常运行后执行
find /Users/penghongming/quant-data/tushare/股票数据 -name "*.parquet" -type f
echo "---"
echo "以上文件将被删除。按 Ctrl+C 取消，或按 Enter 继续。"
read
find /Users/penghongming/quant-data/tushare/股票数据 -name "*.parquet" -type f -delete
echo "清理完成。"
```

- [ ] **Step 2: 仅在确认后执行**

---

### 涉及修改的全部文件清单

| 文件 | 操作 | 优先级 |
|------|------|--------|
| `core/parquet_paths.py` | **新建** | P0 |
| `core/files.py:166-196` | **修改** `write_multi_format_bundle` | P0 |
| `stock-deep-analysis/scripts/data/data_access.py` | **修改** 多处 reader 函数 | P0 |
| `scripts/migrate_existing_parquet.py` | **新建** 迁移脚本 | P1 |
| `stock-deep-analysis/scripts/scan_data_inventory.py` | **修改** glob 路径 | P1 |
| `stock-deep-analysis/scripts/check_data_freshness.py` | **修改** parquet 路径检测 | P2 |
| `stock-deep-analysis/scripts/dragon_tiger_agent.py` | **修改** parquet 读取路径 | P2 |
| `stock-deep-analysis/scripts/dragon_tiger_analyzer.py` | **修改** parquet 读取路径 | P2 |
| `stock-deep-analysis/scripts/fetchers/pre_collect_data.py` | **修改** 显式 parquet 路径 | P2 |
| `scripts/cleanup_legacy_parquet.sh` | **新建** 清理脚本 | P3 |

---

### 风险与注意事项

1. **大 parquet 文件内存**: daily 全市场某一年数据写入一个 parquet 可能内存占用较高。如果出现 OOM，可改为按季度分片 (`2026Q1.parquet`, `2026Q2.parquet`)。
2. **并发写入冲突**: 当前 `auto_fill_data.py` 是单线程顺序执行，不会出现并发问题。但如果未来改为并行补全，parquet 追加模式的 `read-merge-write` 不是原子操作，需要加文件锁。
3. **theme 数据特殊性**: dc_member/ths_member 是按板块代码分文件的，`parquet_path_for_interface` 需要正确保留子目录结构。
4. **回退机制**: `data_access.py` 必须保持 CSV fallback，确保即使 parquet 缺失也能从 CSV 读取。不要在旧 parquet 删除前删除 CSV。
5. **旧文件兼容**: 迁移脚本不删除旧 parquet，所有旧文件保留。清理步骤务必在充分验证后手动执行。
