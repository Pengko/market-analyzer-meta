# 目录结构说明

## 核心文件（根目录）

| 文件 | 说明 |
|------|------|
| `README.md` | 快速开始指南 |
| `ARCHITECTURE.md` | 当前项目结构与后续演进说明 |
| `SKILL.md` | 详细技能文档 |
| `auto_fill_data.py` | 日常更新、自动体检、去重和补齐主入口 |
| `update_weekly_monthly.py` | 周线/月线更新器 |
| `aggregate_weekly_monthly.py` | 周线/月线聚合基础逻辑 |
| `batch_update_today.sh` | 批量更新脚本 |
| `setup_env.sh` | 环境变量示例设置脚本 |

## 子目录

| 目录 | 说明 |
|------|------|
| `core/` | 共享核心层（registry、calendar、health、files） |
| `utils/` | 工具模块（config, tushare_client, paths 等） |
| `tests/` | 最小测试集 |
| `docs/` | 补充说明文档 |

## 快速使用

```bash
# 每日更新
python3 auto_fill_data.py --mode latest --latest-trade-days 1

# 自动补齐缺口
python3 auto_fill_data.py

# 强制全量闭环
python3 auto_fill_data.py --mode full

# 指定接口补最近窗口并体检
python3 auto_fill_data.py --mode latest --interfaces daily,dc_index

# 指定接口做全量补缺并体检
python3 auto_fill_data.py --mode full --interfaces ths_index,ths_member,ths_daily,dc_daily,dc_index,dc_member
```

补充说明：

- `ths_index`、`ths_member`、`dc_index`、`dc_member` 在 `auto` / `latest` 模式下只追最新覆盖
- `ths_member`、`dc_member` 在 `full` 模式下也只追最新覆盖
- 如需处理历史缺口，请改用 `--mode full`

## 数据存储

数据存储在: `~/quant-data/tushare/股票数据/`

```
股票数据/           # 股票数据主目录
├── daily/              # 日线数据
├── daily_basic/        # 每日指标
├── moneyflow_data/     # 资金流向
├── kpl_concept_cons/   # 概念数据
└── ...

指数数据/           # 指数数据
消息面数据/         # 消息监听与主题事件链数据
```
