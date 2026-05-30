# 统一 parquet-only 改造记录（2026-05-26）

## 背景

本地数据仓库中，同一数据类型同时存在 CSV 和 parquet 两种格式。当读取脚本使用 `pd.read_csv()` 或 `read_csv_robust()` 时，存在以下隐患：

1. **数据不一致**：CSV 和 parquet 的更新不同步。parquet 通常比 CSV 新（由 Tushare 同步脚本写入），但旧 CSV 文件仍然存在，脚本如果先读到 CSV 就会使用过时数据。
2. **字段缺失**：旧版 CSV 可能缺少关键字段。典型案例：`stk_factor_pro` 的扁平旧 CSV（`stk_factor_pro_{code}.csv`）缺少 `kdj_k_bfq`、`macd_bfq`、`boll_*_bfq`、`cci_bfq` 等关键技术指标字段，而新版 parquet 文件字段完整。
3. **编码问题**：CSV 存在 BOM 头、GBK/UTF-8 混用等编码问题，parquet 无此问题。
4. **性能差异**：parquet 读取速度远快于 CSV，且自带列式存储和压缩。

## 改造范围（13个文件，分两轮完成）

### 第一轮（2026-05-26 初版，7个文件）

| 文件 | 修改内容 |
|------|----------|
| `scripts/quick_analyze.py` | 删除 `read_csv_robust()`；`fetch_local_daily()` 从 CSV 改为 parquet；`fetch_local_factors()` 从 `load_yearly_or_flat_rows()`（CSV 优先）改为直接 `pd.read_parquet()`；`fetch_local_chips()` 改为 parquet；`fetch_local_basic()` 改为 parquet |
| `scripts/build_stock_report.py` | `fetch_local_daily()` 改为 parquet；`load_yearly_or_flat_rows()` 内部改为 parquet 优先（`get_local_latest_date` 同步更新） |
| `scripts/data/data_access.py` | `get_local_latest_date()` 改为 parquet 优先（`*.parquet` → `*.csv`）；`load_yearly_or_flat_rows()` 改为 parquet 优先；`load_dc_concepts_local()` / `load_dc_concept_constituents_local()` 改为 parquet-only |
| `scripts/get_quote_tencent.py` | 新增 `get_daily_parquet()` 用于读取本地 parquet 日线 |
| `scripts/discover_ths_mobile_stock_concepts.py` | 从 CSV 读取改为 parquet 读取（`dc_concept_cons/{name}.parquet`） |
| `scripts/discover_ths_mobile_subthemes.py` | 同上，parquet 读取 |
| `scripts/discover_ths_mobile_theme_leaders.py` | 同上，parquet 读取 |

### 第二轮（2026-05-26 补全，6个文件）

用户明确指令"只读parquet"后，对残余 CSV 读取进行彻底清理：

| 文件 | 修改内容 |
|------|----------|
| `scripts/build_stock_report.py` | 删除 `daily_row` 的 CSV fallback 逻辑（原注释"parquet 可能落后于 CSV，尝试 CSV fallback"）；`_resolve_symbol()` 改为读取 `stock_basic_all.parquet`（原读 CSV）；`analyze_financing_context()` 的 `margin_detail` 改为调用 `_read_stock_parquet` |
| `scripts/signals/core/score_next_day_bias.py` | 所有数据加载（daily/moneyflow/factor/top_list/top_inst/hm_detail）改为 parquet 路径 |
| `scripts/validate_pending_reports.py` | `load_daily_rows()` 从 CSV DictReader 改为 `_read_stock_parquet("daily", symbol)` |
| `scripts/optimize_strategy.py` | `run_backtest_on_validated_samples()` 从 CSV 读取改为 `_read_stock_parquet("daily", symbol)` |
| `scripts/render/report_renderer.py` | `_load_stock_name_fallback()` 改为读取 `stock_basic_all.parquet`（原读 CSV） |
| `scripts/data/data_access.py` | `load_dc_concepts_local()` / `load_dc_concept_constituents_local()` 彻底删除 CSV fallback，改为 parquet-only |

## 验证结果

- 改造前：`quick_analyze.py --symbol 600103.SH --date 2026-05-23` 输出的 `kdj_k`、`macd`、`boll`、`cci`、`rsi` 等字段全部为 `"N/A"`
- 改造后：同一命令输出的 `kdj_k`="38.75"、`macd`="-0.03"、`boll`="2.40/2.48/2.57"、`cci`="-17.08"、`rsi_6`="43.75"，字段完整且数值合理
- 补全后验证：`600519.SH @ 2026-05-25` 的 `kdj_k_bfq=4.91981`、`macd_bfq=-12.212`、`cci_bfq=-140.41795`，所有字段正常

## 仍有 CSV 读取的文件（保留原因）

以下文件仍读 CSV，但属于合理保留：

| 文件 | CSV 用途 | 保留原因 |
|------|---------|---------|
| `scripts/data/data_access.py` 中的 `_read_csv_rows` | `trade_cal_all.csv` | 静态日历无 parquet 源 |
| `fetchers/infoway_minute_writer.py` / `fetch_minute_data.py` | 分钟线 CSV | 用户要求的 `年/月/日/个股/1m.csv` 统一目录结构 |
| `analysis/sector_analyzer.py` / `market_analyzer.py` / `stock_trend_analyzer.py` | 第三方数据（东财、开屏料等） | 需确认 parquet 源存在后再改 |

## 后续规则

- 所有本地数据读取，**parquet 是唯一权威格式**，CSV 仅作为历史备份存在，不再被任何分析脚本读取
- 新增数据类型时，必须同时生成 parquet，且脚本只读 parquet
- `read_csv_robust()` 等 CSV 专用读取函数已从 `quick_analyze.py` 中删除，不再维护
- 数据源表格中的 CSV 标注已全部更新为 parquet
- 当用户明确说"只读parquet"时，意味着**彻底清理所有 CSV fallback**，不留降级逻辑，即使 parquet 缺失也应标注 `missing` 而非回退 CSV
