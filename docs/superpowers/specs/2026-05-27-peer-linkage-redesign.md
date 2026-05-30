# 对标股优化 — 走势驱动的板块发现与个股对标

## 概述

将 `build_peer_linkage()` 从静态行业分类改为**双重时序相关性驱动**：日K (N天涨跌幅) + 日内 (分钟滑动corr)，自动发现"最相关板块"并选出该板块内的对标股。

## 架构

Phase 3 串行步骤（不改 agent 结构），替换现有 `build_peer_linkage()`：

```
Phase 3:
  ...
  build_peer_linkage(full_symbol, trade_date_text)
      │
      ├─ get_stock_concepts() → 概念列表
      │
      ├─ 对每个概念:
      │   ├─ dc_concept_cons → 成分股 + 板块指数
      │   ├─ 日K相关: stock.daily.pct_chg[20d] ⬌ sector_index.daily.pct_chg[20d] → Pearson r
      │   └─ 日内相关: stock.分钟 ⬌ sector指数.分钟 → sliding_corr avg_r
      │
      ├─ 综合评分选出 primary_sector + intraday_sector
      │   └─ 输出 alignment (一致/轮动偏离/独立走势/无明确对标)
      │
      └─ 在 primary_sector 成分股中:
          ├─ 日K相关 + 日内相关 加权排名
          ├─ 角色分类: 龙头(相关+涨幅)、中军(相关+市值)、高弹(相关+振幅)、纯相关
          └─ 输出 top 3-5 对标股
```

## 双重评分

| 层级 | 计算 | 权重 |
|------|------|------|
| 日K相关性 | stock.pct_chg[20d] ⬌ sector/peer.pct_chg[20d] Pearson r | 60% |
| 日内跟随 | stock.分钟 ⬌ index/peer.分钟 sliding_corr avg_r | 40% |

日内数据缺失时退到 100% 日K。

## 板块发现

| 日K vs 日内顶级板块 | alignment | 含义 |
|-------------------|-----------|------|
| 同一板块 | 一致 | 稳健跟随 |
| 不同板块 | 轮动偏离 | 今日切换 |
| 日K有、日内全低 | 独立走势 | 走自己 |
| 全低 | 无明确对标 | 孤立 |

## 对标股角色

| 角色 | 依据 |
|------|------|
| 龙头 | 日内相关度最高 + 当天涨幅领先板块均值 |
| 中军 | 日内相关度 ≥0.5 + 市值最大 |
| 高弹性 | 日内相关度 ≥0.5 + 振幅最大 |
| 纯相关 | 日K/日内相关度高，但量价不突出 |

## 数据依赖

| 数据 | 来源 | 覆盖 |
|------|------|------|
| 概念→成分股 | dc_concept_cons parquet | ~500 概念 |
| 概念→板块指数 | sector_index_codes.json (sz399xxx 代理) | 取决于映射 |
| 个股日K | daily/{symbol}.parquet | 全量 |
| 板块指数日K | index_daily/{code}.parquet | 有指数数据 |
| 个股分钟 | 分钟数据/{symbol}/1min.csv | 部分交易日 |
| 板块指数分钟 | Tencent API | 实时/当天 |

## 改造文件

| 文件 | 改动 |
|------|------|
| `scripts/decision/decision_engine.py` | 重写 `build_peer_linkage()` |
| `scripts/analysis/sector_analyzer.py` | 构造板块→成分股反向索引 (概念名→ts_code列表) |
| `scripts/data/data_provider.py` | 如缺板块日K/分钟读取函数则补 |

## 输出格式

```python
{
  "status": "available",
  "primary_sector": "LED概念",
  "intraday_sector": "AI硬件",
  "alignment": "轮动偏离",
  "target_pct_chg": 2.3,
  "concept_count": 2,
  "peers": [
    {
      "symbol": "000100.SZ", "name": "TCL科技",
      "daily_corr": 0.82, "intraday_corr": 0.71,
      "role": "龙头", "pct_chg": 3.5, "amount_yi": 12.3,
      "inspiration": "龙头日内高相关，板块核心参考"
    },
    ...
  ]
}
```

## 兼容性

- `dimension_results["peer_linkage"]` 字段不变
- `extract_decision_context()` 从新格式消费 peer position
- `analyze_t_plus_two_bias()` 对齐判断需微调 (从 `target_position` 改为读 `alignment`)
- Markdown 报告渲染需适配角色字段

## 不做

- 不新增 Phase 2 Agent (Phase 3 串行即可)
- 不改 context_propagation_rules
- 不做日内分钟数据的批量预加载
