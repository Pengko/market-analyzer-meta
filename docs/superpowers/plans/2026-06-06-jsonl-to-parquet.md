# JSONL to Parquet Migration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成从 jsonl 到 parquet 的存储迁移，清理残留代码，更新验证脚本的归档逻辑。

**Architecture:** 
- `decision_engine.py` 已部分修改（输出 parquet），需清理残留 jsonl 函数
- `validate_pending_reports.py` 的归档逻辑需从 jsonl 改为 parquet
- `parquet_io.py` 已创建完整的读写工具

**Tech Stack:** Python 3.9+, pandas, pyarrow

**Frozen Baseline:** 
- 当前分支: `大盘分析开发`
- 已完成: `parquet_io.py` 创建, `decision_engine.py` 核心逻辑已改为输出 parquet
- 待完成: 清理 + 归档逻辑更新

---

## File Structure

| 文件 | 操作 | 说明 |
|------|------|------|
| `scripts/data/parquet_io.py` | 已创建 | Parquet 读写工具 |
| `scripts/decision/decision_engine.py` | 修改 | 清理 jsonl 函数 |
| `scripts/validate_pending_reports.py` | 修改 | 更新归档逻辑 |

---

### Task 1: 清理 decision_engine.py 中的 jsonl 残留函数

**Files:**
- Modify: `scripts/decision/decision_engine.py:703-736`

- [ ] **Step 1: 删除未使用的 jsonl 函数**

删除以下三个函数（它们不再被调用）：

```python
# 删除 append_checkpoint_jsonl (行 703-704)
# 删除 write_validation_payload (行 715-736)
```

同时删除 `import json` 如果不再需要（检查是否其他地方还用到 json）。

- [ ] **Step 2: 验证删除后无语法错误**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python -c "from decision.decision_engine import persist_pending_validation"
```

Expected: 无报错

---

### Task 2: 更新 validate_pending_reports.py 的归档逻辑

**Files:**
- Modify: `scripts/validate_pending_reports.py:569-595`

- [ ] **Step 1: 更新 archive_validated_pending 函数**

将归档逻辑从 jsonl 改为 parquet：

```python
def archive_validated_pending(report: dict[str, Any]) -> list[Path]:
    archived: list[Path] = []
    target_date = report.get("target_date")
    if not target_date:
        return archived
    pending_date_dir = PENDING_DIR / target_date
    validated_date_dir = VALIDATIONS_DIR / target_date
    validated_date_dir.mkdir(parents=True, exist_ok=True)
    for item in report.get("validations") or []:
        symbol = item.get("symbol")
        if not symbol:
            continue
        # 归档 md 文件
        pending_md_candidates = sorted(pending_date_dir.glob(f"待验证-{symbol}*.md"))
        for pending_md in pending_md_candidates:
            suffix = pending_md.name.removeprefix(f"待验证-{symbol}")
            validated_md = validated_date_dir / f"已验证-{symbol}{suffix}"
            shutil.move(str(pending_md), str(validated_md))
            archived.append(validated_md)
        # 归档 parquet 文件
        pending_parquet_candidates = sorted(pending_date_dir.glob(f"待验证-{symbol}*.parquet"))
        for pending_parquet in pending_parquet_candidates:
            suffix = pending_parquet.name.removeprefix(f"待验证-{symbol}")
            validated_parquet = validated_date_dir / f"已验证-{symbol}{suffix}"
            shutil.move(str(pending_parquet), str(validated_parquet))
            archived.append(validated_parquet)
    return archived
```

- [ ] **Step 2: 验证修改后无语法错误**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python -c "from validate_pending_reports import archive_validated_pending"
```

Expected: 无报错

---

### Task 3: 端到端验证

- [ ] **Step 1: 运行 build_stock_report.py 测试新流程**

```bash
cd /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts
python build_stock_report.py 000725.SZ 2026-06-04
```

Expected: 
- 生成 `.md` 文件
- 生成 `.parquet` 文件（与 .md 同名，不同扩展名）
- 无 jsonl 文件生成

- [ ] **Step 2: 验证 parquet 文件可读**

```bash
python -c "
import pandas as pd
from pathlib import Path
import glob
files = glob.glob('/Users/penghongming/quant-data/市场分析/reports/2026-06-04/待验证-000725*.parquet')
if files:
    df = pd.read_parquet(files[0])
    print('Columns:', list(df.columns))
    print('Shape:', df.shape)
    print(df[['symbol', 'trade_date', 'checkpoint', 'decision']].to_string())
else:
    print('No parquet files found')
"
```

Expected: 输出包含 symbol, trade_date, checkpoint, decision 等字段

---

### Task 4: 清理 json 导入（可选）

**Files:**
- Modify: `scripts/decision/decision_engine.py`

- [ ] **Step 1: 检查 json 是否仍被使用**

```bash
grep -n "json\." /Users/penghongming/agent-skills/custom/market-analyzer-meta/skills/stock-deep-analysis/scripts/decision/decision_engine.py
```

如果 `json` 模块不再被使用，删除 `import json`。

- [ ] **Step 2: 验证无语法错误**

```bash
python -c "import decision.decision_engine"
```

---

## Acceptance Criteria

1. ✅ `decision_engine.py` 无 jsonl 相关函数
2. ✅ `validate_pending_reports.py` 归档逻辑使用 parquet
3. ✅ 运行 `build_stock_report.py` 生成 `.md` + `.parquet`，无 `.jsonl`
4. ✅ 生成的 parquet 文件可正常读取
5. ✅ 无语法错误
