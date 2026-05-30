# Tushare Pro Data Pipeline

面向 A 股数据采集与本地落盘的数据工程项目，当前围绕统一主入口组织：

- `auto_fill_data.py`: 日常更新、体检、去重和缺口补齐

核心目标是：

- 统一 Tushare Pro / 自定义代理端点的调用方式
- 将股票和指数数据按固定目录结构持续落盘
- 在接口限额、断点和缺口存在时，提供统一的补齐逻辑
- 为后续量化分析、题材研究和 agent 工作流准备本地数据底座

## 仓库内容

- `auto_fill_data.py`: 按数据缺口自动补齐到最新交易日
- `update_weekly_monthly.py`: 周线/月线聚合与 API 校验
- `aggregate_weekly_monthly.py`: 周线/月线基础聚合逻辑
- `core/`: 共享核心层，包含注册表、交易日历、健康检查、文件工具
- `utils/`: 配置、路径、客户端封装、公共工具
- `tests/`: 最小单测与重构验证
- `docs/`: 修复记录与补充说明
  - `docs/STK_WEEKLY_MONTHLY_USAGE.md`: `stk_weekly_monthly` 单独补全调用文档

## 环境要求

- Python 3.10+
- 可用的 `TUSHARE_TOKEN`
- 可选的自定义 API 端点

安装依赖：

```bash
pip install -r requirements.txt
```

## 配置

当前仓库主链统一通过 `/Users/penghongming/agent-skills/custom/tushare_pro/utils/tushare_bootstrap.py` 初始化：

- `token = 6be0552842c69a4c84636359df4028459ce14d13d092cdce491ce77d361ab5a6`
- `http_url = http://124.220.22.110:8020/`

标准调用方式等价于：

```python
import tushare as ts
pro = ts.pro_api('6be0552842c69a4c84636359df4028459ce14d13d092cdce491ce77d361ab5a6')
pro._DataApi__http_url = "http://124.220.22.110:8020/"
```

说明：

- 主链默认直接使用 `utils/tushare_bootstrap.py` 里的统一初始化参数
- 后续如需切换 token / relay URL，请直接修改 `utils/tushare_bootstrap.py`
- 如果出现 “Token 不对”，请先确认代码里是否真的执行了：
  - `pro._DataApi__http_url = "http://124.220.22.110:8020/"`

## 快速开始

按最近交易日执行日常更新：

```bash
python3 auto_fill_data.py --mode latest --latest-trade-days 1
```

按自动模式执行主链：

```bash
python3 auto_fill_data.py
```

强制全量闭环：

```bash
python3 auto_fill_data.py --mode full
```

指定接口执行最近窗口补齐 + 体检：

```bash
python3 auto_fill_data.py --mode latest --interfaces daily,dc_index
```

指定接口执行全量闭环补缺 + 体检：

```bash
python3 auto_fill_data.py --mode full --interfaces ths_index,ths_member,ths_daily,dc_daily,dc_index,dc_member
```

指定接口忽略白名单并强制全历史重拉（先重拉，再体检修复）：

```bash
python3 auto_fill_data.py --mode full --interfaces index_daily,index_weekly,index_monthly --ignore-whitelist --force-refetch --history-start-date 20200101
```

说明：

- `ths_index`、`ths_member`、`dc_index`、`dc_member` 这 4 个 theme 快照/成员接口，在主脚本 `auto` / `latest` 模式下默认只追 **最新 1 个交易日覆盖**
- `ths_member`、`dc_member` 在主脚本 `full` 模式下也只追 **最新 1 个交易日覆盖**
- 如果需要补历史缺口，请使用 `--mode full`

自动补齐缺失数据：

```bash
python3 auto_fill_data.py
```

单独补全 `stk_weekly_monthly` 对应的周/月线：

```bash
python3 update_weekly_monthly.py --interface both --periods 6
```

无视白名单，强制重新检查并补全：

```bash
python3 update_weekly_monthly.py --interface both --periods 6 --ignore-whitelist
```

全历史周期重拉：

```bash
python3 update_weekly_monthly.py --interface both --all --ignore-whitelist
```

详细调用说明见：

- `docs/STK_WEEKLY_MONTHLY_USAGE.md`

## 常见数据类别

已覆盖的主数据类型包括：

- 股票日线与日频指标：`daily`, `daily_basic`, `moneyflow`
- 涨跌停与龙虎榜：`limit_list_d`, `top_list`, `top_inst`
- 集合竞价：`stk_auction_o`, `stk_auction_c`
- 主题与板块：`kpl_concept_cons`, `kpl_list`, `dc_concept`
- 筹码与因子：`cyq_chips`, `cyq_perf`, `stk_factor_pro`
- 指数数据：`index_daily`, `index_weight`, `sw_daily`

## 目录结构

```text
tushare_pro/
├── README.md
├── ARCHITECTURE.md
├── SKILL.md
├── requirements.txt
├── auto_fill_data.py
├── update_weekly_monthly.py
├── aggregate_weekly_monthly.py
├── core/
├── utils/
├── tests/
└── docs/
```

## 数据落盘

默认数据目录由 `utils/paths.py` 和相关配置脚本控制。常见输出会按股票、日期或主题维度拆分，便于：

- 断点续跑
- 局部修复
- 逐类重建
- 下游 agent 和分析脚本直接消费

## 使用建议

- 先运行 `python3 auto_fill_data.py --mode latest --latest-trade-days 1` 验证日常链路
- 初次部署时优先跑核心行情和指数，再补主题、筹码、分钟数据
- 遇到缺口先用 `auto_fill_data.py`
- 公开部署时不要把真实 token 写回代码文件

## 说明

- 共享逻辑已经集中到 `core/`，主维护链路统一收敛到 `auto_fill_data.py`
- 仓库内忽略了本地日志、缓存和样本数据目录，避免把运行产物直接提交到版本控制
