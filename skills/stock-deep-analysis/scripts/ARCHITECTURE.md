# 架构说明

## 目标

把 `stock-deep-analysis` 拆成清晰的 6 层：

1. `common`
- 通用路径、符号归一化、共享常量

2. `data`
- 只负责本地数据读取与交易日定位
- 不做分析判断

3. `analysis`
- 只做分析结论生成
- 包括大盘、板块、个股趋势结构

4. `runtime`
- 只做运行时抓取与质量判定
- 包括分钟补抓、网络时间、消息自动联动

5. `decision`
- 只做综合裁决、联动解释、验证跟踪

6. `render`
- 只做 Markdown / JSON 展示层格式化

7. `orchestration`
- `build_stock_report.py`
- 只负责串联各层并产出主报告

## 目录

```text
scripts/
  analysis/
    market_analyzer.py
    sector_analyzer.py
    stock_trend_analyzer.py
  data/
    data_access.py
  decision/
    decision_engine.py
  render/
    report_renderer.py
  runtime/
    runtime_fetch.py
    runtime_quality.py
    news_runtime.py
  build_stock_report.py
  common.py
```

## 兼容策略

为避免一次性打断现有脚本与导入路径，根目录保留了兼容入口文件：

- `market_analyzer.py`
- `sector_analyzer.py`
- `stock_trend_analyzer.py`
- `data_access.py`
- `decision_engine.py`
- `report_renderer.py`
- `runtime_fetch.py`
- `runtime_quality.py`
- `news_runtime.py`

这些文件当前只做转发导入，真实实现已经迁移到子目录。

## 后续方向

1. 逐步减少根目录兼容入口的直接引用
2. 新代码优先从子目录模块导入
3. 等外部依赖路径稳定后，再决定是否移除兼容入口

## 兼容层

- 根目录仍保留兼容入口文件，详见：[COMPATIBILITY.md](/Users/penghongming/agent-skills/custom/stock-deep-analysis/scripts/COMPATIBILITY.md)
