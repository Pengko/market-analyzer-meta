# stock-deep-analysis 架构优化方案

## 问题

目前 `build_payload()` 全串行执行，核心流程耗时分布：

| 步骤 | 典型耗时 | 性质 |
|---|---|---|
| 消息面（TrendRadar + Browser） | ~90s | I/O，可并行 |
| 分钟线补全（浏览器） | ~30s | I/O，可并行 |
| 大盘+板块+个股维度 | ~10s | 纯计算，可并行 |
| 龙虎榜数据分析 | ~5s | 纯计算，可并行 |
| **总耗时** | **~2min - 44min**（浏览器超时放大） | |

## 优化目标

全流程从 44 分钟降到 90~120 秒。

## 架构方案

### 三阶段模型

```
Phase 1 (串行, ~5s)
  符号归一化 + 交易日校准 + kline同步 + factor同步
  → 产出基础上下文

Phase 2 (并行, 最快维度 ~5s, 最慢维度 ~90s)
  ┌ Agent-A: 消息面采集 ─────────────────────┐
  │  趋势雷达(3s) → 不足则浏览器抓取(90s)      │
  ├ Agent-B: 分钟线获取 ──────────────────────┤
  │  本地(1s) → 不足则浏览器补全(30s)          │
  ├ Agent-C: 大盘+板块+题材分析 ───────────────┤
  │  市场环境 + 板块表现 + 移动端题材发现       │
  ├ Agent-D: 个股维度计算 ─────────────────────┘
  │  next_day + capital + trend + chip + 
  │  volatility + financing + auction
  └ Agent-E: 龙虎榜分析（时段感知）─────────────┘
      (盘后:完整分析 | 盘中:快查 | 盘前:跳过)

Phase 3 (串行, <1s)
  合并 payload → enrich_news_sentiment →
  context_propagation → final_decision → 持久化 → 出报告
```

### 并行通信协议

每个 Agent 把结果写到 `_tmp/{agent_name}_{trade_date_compact}.json`，主流程轮询或 `concurrent.futures` 等待。

```
TMP_ROOT = config.paths("tmp_dir", default=Path("_tmp"))
TMP_ROOT / f"news_{trade_date_compact}.json"
TMP_ROOT / f"intraday_{trade_date_compact}.json"
TMP_ROOT / f"sector_{trade_date_compact}.json"
TMP_ROOT / f"stock_dims_{trade_date_compact}.json"
TMP_ROOT / f"dragon_tiger_{trade_date_compact}.json"
```

超时兜底：消息面 Agent 超时 → 填充 missing；分钟线超时 → 用本地已有数据。不阻塞全流程。

### 龙虎榜时段感知优化

当前 `should_launch_agent()` 只看连续上榜天数。改为三档：

```
盘后 (15:00 - 次日 09:15): 完整 DragonTiger 分析
  连续上榜追踪、席位延续/离场/新进、
  游资标签匹配、资金趋势、评分（15%权重）
  最有价值时段，全量触发

盘中 (09:30 - 15:00): 轻量快查
  快查：顶级游资首次介入 + 机构逆势净买
  两个信号够了，不做完整席位追踪
  不纳入 final_decision 评分，只作为 notes 信息
  如果昨天没有上榜记录 → 完全跳过（零开销）

盘前 (09:15 之前): 跳过
  前天榜单，隔了一个交易日已无参考价值
```

快查逻辑替代 `preprocess_dragon_tiger()/aggregate_exalters_for_agent()`：

```python
def quick_check_dragon_tiger(full_symbol: str, trade_date_text: str) -> dict:
    """盘中快查：只看顶级游资首次介入 + 机构逆势净买"""
    top_list = load_top_list(full_symbol, latest_trade_date_before(trade_date_text))
    inst = load_top_inst(full_symbol, latest_trade_date_before(trade_date_text))
    
    signals = {}
    # 信号1: 顶级游资首次介入（检查近5天是否有相同席位出现）
    top_seats = find_top_level_seats(top_list)
    if top_seats and not appeared_in_last_n_days(top_seats, full_symbol, 5):
        signals["顶级游资首次介入"] = True
    
    # 信号2: 机构逆势净买（买入占比高 + 当天跌）
    if inst and inst.get("net_buy", 0) > 0 and inst.get("pct_chg", 0) < 0:
        signals["机构逆势净买"] = inst["net_buy"]
    
    return signals
```

权重调整：盘中 DragonTiger 在 `build_final_decision()` 里权重降为 0（不纳入信号分），只作为报告中的"龙虎榜快查"信息区块。盘后才恢复完整的增量评分逻辑。

## 实施顺序

1. **Phase 2 并行化**：把 build_payload() 中第 765-813 行按依赖关系拆成并行组
2. **龙虎榜时段感知**：修改 should_launch_agent + build_final_decision 中的 DragonTiger 分支
3. **超时兜底 + 降级**：每个并行 Agent 独立超时，不阻塞整条链路
4. **性能基线**：优化前后打时间戳，确认加速比
