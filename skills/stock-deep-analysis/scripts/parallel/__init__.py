"""
parallel — 并行分析 Agent 调度模块。

架构:
|  5个并行 Agent → ThreadPoolExecutor → _tmp JSON 文件通信 → Phase 3 合并

集成入口:
  build_stock_report.build_payload() 自动调用了三阶段流程。

阶段:
  Phase 1 (串行, ~1s): 输入归一化（不含browser同步）
   Phase 2 (并行, ~1-90s): 8个 Agent 并发执行
  Phase 3 (串行, <10s): 合并结果 + 决策引擎 + 持久化

Agent:
  - kline_sync: 浏览器日K同步+因子重建 (最慢, ~50s) — 并行后不阻塞其他Agent
  - news: TrendRadar → Browser fallback (盘中仅TrendRadar) (~1-90s)
  - intraday: 分钟线读取 (~0.1s)
  - sector: 大盘+板块+题材 (~0.8s)
  - stock_dims: 融资融券+竞价+趋势+筹码+波动率+基本面 (~0.8s)
   - dragon_tiger: 龙虎榜时段感知（盘前skip/盘中quick/盘后full）(~0.0s)
   - intraday_linkage: 分钟级联动分析 (大盘/板块/个股) (~30s, LLM调用)
   - fundamental_deep: 基本面深度背调 (4级降级) (~30s, LLM调用)
"""

from .runner import ParallelAgent, run_parallel, clear_tmp
from .agents import (
    run_news_agent,
    run_intraday_agent,
    run_sector_agent,
    run_stock_dims_agent,
    run_dragon_tiger_agent,
    run_kline_sync_agent,
    run_intraday_linkage_agent,
    run_fundamental_agent,
)

__all__ = [
    "ParallelAgent",
    "run_parallel",
    "clear_tmp",
    "run_news_agent",
    "run_intraday_agent",
    "run_sector_agent",
    "run_stock_dims_agent",
    "run_dragon_tiger_agent",
    "run_kline_sync_agent",
    "run_intraday_linkage_agent",
    "run_fundamental_agent",
]
