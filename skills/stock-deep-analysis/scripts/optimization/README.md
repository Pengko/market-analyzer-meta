# 策略因子优化模块

> 独立运行的策略因子优化系统，供其他agent不间断优化使用

## 模块职责

1. **验证准确性**：对比预测与实际走势
2. **分析命中率**：统计T+1/T+2预测准确率
3. **识别模式**：找出强项和弱项预测模式
4. **生成优化建议**：提供具体的改进方向
5. **输出优化配置**：生成可直接使用的优化参数

## 输入数据

### 必需数据
1. **验证报告**：`references/validations/validation-report-YYYY-MM-DD.md`
2. **待验证报告**：`references/pending-validations/YYYY-MM-DD/`
3. **实际行情**：`~/.openclaw/data/tushare/股票数据/daily/`

### 可选数据
1. **历史验证报告**：用于趋势分析
2. **技术因子数据**：用于因子组合分析

## 输出数据

### 优化报告
- **每日**：`references/strategy-analysis/strategy-optimization-YYYY-MM-DD.md`
- **每周**：`references/strategy-analysis/weekly-strategy-optimization-YYYY-WXX.md`
- **每月**：`references/strategy-analysis/monthly-strategy-optimization-YYYY-MM.md`

### 优化配置
- **因子权重**：`references/strategy-analysis/factor-weights-YYYY-MM-DD.json`
- **预测模型参数**：`references/strategy-analysis/prediction-model-YYYY-MM-DD.json`

## 运行方式

### 每日优化
```bash
cd /Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts
python3 optimize_strategy.py
```

### 每周优化
```bash
python3 optimization/weekly_optimizer.py
```

### 每月优化
```bash
python3 optimization/monthly_optimizer.py
```

## 优化流程

```
1. 扫描验证报告 → 获取命中率统计
2. 分析预测模式 → 识别强项/弱项
3. 回测历史数据 → 验证因子组合效果
4. 生成优化建议 → 提供改进方向
5. 输出优化配置 → 供其他agent使用
```

## 使用约定

1. **独立运行**：其他agent应独立运行此模块，不依赖主分析流程
2. **定期执行**：每日/每周/每月定期运行，形成持续优化
3. **结果共享**：优化结果保存到指定目录，供所有agent使用
4. **配置更新**：优化配置可直接用于调整技术因子权重

## 文件结构

```
optimization/
├── README.md              # 本文件
├── weekly_optimizer.py    # 每周优化脚本
├── monthly_optimizer.py   # 每月优化脚本
├── factor_analyzer.py     # 因子分析模块
└── config_generator.py    # 配置生成模块
```

## 注意事项

1. **数据依赖**：需要验证报告和实际行情数据
2. **时效性**：优化结果基于历史数据，需定期更新
3. **可解释性**：优化建议应可解释，避免黑箱调整
4. **向后兼容**：优化配置应兼容现有分析流程