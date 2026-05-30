## 测试记录保存

为便于后续回归、复盘和优化，这个技能的测试历史默认保存在技能目录内。

- 测试说明、回归结果、命中率记录：`/Users/penghongming/agent-skills/custom/stock-deep-analysis/references/`
- 测试脚本与辅助脚本：`/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/`

### 强制保存步骤（Hermes执行时必做）

**分析报告生成后，必须立即执行以下三步，不能等用户询问：**

1. **保存报告到pending-validations/**：统一写入 `references/pending-validations/YYYY-MM-DD/` 目录
   - 命名格式：`待验证-{股票代码}.{市场前缀}-{股票名称}-{分析类型}.md`
     - 市场前缀：上海 `.SH`、深圳 `.SZ`，**必须用点号**，不能用连字符 `-SH`/`-SZ`
   - 分析类型示例：`盘后`、`午间推演`、`盘中推演`、`盘前分析`
   - 路径：`references/pending-validations/YYYY-MM-DD/待验证-{股票代码}.{市场前缀}-{股票名称}-{分析类型}.md`
   - **禁止**使用非标准名称（如"持仓视角标准版"），统一用分析类型命名
   - **去重覆盖规则（强制执行）**：
     - 每个股票每个分析类型在同一天只保留一份最新报告
     - **保存前必须先清理**：扫描目标目录 `references/pending-validations/YYYY-MM-DD/`，删除所有匹配 `待验证-{股票代码}.*-{分析类型}*.md` 和 `待验证-{股票代码}.*-{分析类型}*.json` 的旧文件
     - 盘前分析和盘后分析是不同类型，各自的最新版都应保留
   - `references/` 根目录不允许再直接落个股分析报告

2. **生成validation-meta.json**：同时生成同名的JSON元数据文件，供AI验证agent使用
   - 命名格式：`待验证-{股票代码}.{市场前缀}-{股票名称}-{分析类型}-meta.json`
   - 模板：`references/validation-meta-template.json`
   - 必填字段：`predictions`（预测结论+置信度）、`validation_fields`（待验证项）、`context_propagation`（传导评分）
   - 与人类可读的md报告一一对应，AI验证agent直接读取JSON

3. **更新测试对象池**：在 `references/test-pools/测试对象池.md` 中更新该股票的"最近场景"和分析要点

这三步是**强制操作**，不是可选操作。即使报告已在对话中输出，也必须同步保存到文件。

**已知陷阱：历史复盘报告不会自动保存**

- **根因**：`decision_engine.py` 的 `persist_pending_validation()` 只在 `record_status == 'pending_validation'` 时保存报告文件
- **什么时候触发**：`checkpoint='full'` + `build_validation_tracking()` 设置 `record_status='historical_replay'` → **不会保存**
- **结果是**：用 `build_payload('某某股票', 'YYYY-MM-DD', checkpoint='full')` 分析历史日期时，报告在内存中生成（终端可见），但磁盘上 `references/pending-validations/` 目录下无文件
- **排查方法**：如果用户问"报告在哪"但目录下找不到对应文件，先检查 `decision_engine.py:persist_pending_validation()` 的 `record_status` 判断逻辑，而非怀疑代码有 bug
- **手动导出方法**：
  ```python
  from build_stock_report import build_payload, render_status_text
  from render.report_renderer import render_pending_validation_markdown

  payload = build_payload('彩虹股份', '2026-04-28', checkpoint='full')
  report_md = render_pending_validation_markdown(payload)
  # 手动写入目标目录
  import os
  out = f'references/pending-validations/2026-04-28/待验证-600707.SH-彩虹股份-收盘.md'
  os.makedirs(os.path.dirname(out), exist_ok=True)
  with open(out, 'w', encoding='utf-8') as f:
      f.write(report_md)
  ```

### 策略因子优化（独立模块）

**策略因子优化**已独立为专用模块，供其他agent不间断优化使用。

#### 独立优化模块
- **位置**：`scripts/optimization/`
- **用途**：专门负责策略因子优化，不依赖主分析流程
- **运行者**：其他agent（如Codex、Claude Code等）独立运行

#### 优化模块组成
1. **weekly_optimizer.py**：每周优化脚本
   - 汇总一周验证报告
   - 分析周度命中率趋势
   - 生成周度优化建议

2. **monthly_optimizer.py**：每月优化脚本
   - 汇总一月验证报告
   - 进行全面策略有效性评估
   - 生成月度优化报告

3. **config_generator.py**：配置生成模块
   - 根据优化结果生成配置文件
   - 输出因子权重和预测模型参数
   - 供其他agent直接使用

#### 优化流程（独立运行）
```
其他agent运行优化模块：
1. 扫描验证报告 → 获取命中率统计
2. 分析预测模式 → 识别强项/弱项
3. 生成优化建议 → 提供改进方向
4. 输出优化配置 → 供所有agent使用
```

#### 使用约定
1. **独立运行**：其他agent应独立运行优化模块，不依赖主分析流程
2. **定期执行**：每日/每周/每月定期运行，形成持续优化
3. **结果共享**：优化结果保存到`references/strategy-analysis/`目录
4. **配置更新**：优化配置可直接用于调整技术因子权重

#### 对主分析流程的影响
- **主流程简化**：我使用skill时无需运行优化流程
- **配置直接使用**：直接读取优化配置，调整技术因子权重
- **持续改进**：其他agent持续优化，我享受优化成果

#### 重要原则
- **技术因子**：用于实时分析决策（均线、RSI、波动率等）
- **策略因子**：用于事后分析优化（命中率统计、模型优化）
- **两者互补**：形成"分析→验证→优化"闭环
- **分工明确**：我负责分析，其他agent负责优化

约定：

- 每轮测试尽量单独写成一份带日期的记录文件
- 文件名应包含测试日期、样本范围或主题，例如：
  - `test-2026-04-08-multi-stock-am-vs-pm.md`
  - `test-2026-04-07-to-2026-04-08-next-day-bias.md`
- 如果结论、命中率或回归口径发生变化，应更新或新增对应测试记录，避免只留在终端输出里
- 正式行情、分钟线、竞价、资金流等业务数据仍保存在 `${STOCK_DATA_ROOT}/`
- `references/` 保存的是"怎么测、测了什么、结果如何"的技能历史，不是业务主数据
- `references/` 目录结构规范：
  - `references/pending-validations/YYYY-MM-DD/` —保存待验证报告
  - `references/validations/` —验证完成后的已验证报告
  - `references/test-pools/` —测试对象池
  - `references/portfolio/` —个人持仓数据（`portfolio.yaml`）
  - `references/strategy-analysis/` —策略回测/优化结果
  - `references/` 根目录 —只保留说明文档、模板、框架文档，不允许直接落个股报告
- 个人持仓数据保存在 `references/portfolio/portfolio.yaml`，由 `scripts/data/portfolio_loader.py` 自动加载，分析时无需用户重复输入成本/仓位
- 测试对象池单独维护在 `references/test-pools/测试对象池.md`
- 凡是本轮被正式分析、被拿来做对标、或被补抓分钟线用于验证的股票，都应考虑加入测试对象池，并标注角色与形态
- 测试对象池默认分成：
  - `主分析标的`
  - `对标股`
  - `特殊形态样本`
  - `待补充分时/待回测样本`

