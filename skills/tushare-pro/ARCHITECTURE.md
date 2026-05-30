# Architecture

本项目当前采用“入口脚本 + 共享核心层”的结构：

## 入口层

- `update_daily.py`
  - 面向日常增量更新
  - 负责 CLI、分组选择、缺失交易日检测、接口调度
- `auto_fill_data.py`
  - 面向批量体检、去重、历史缺口补齐
  - 负责全局扫描、补全策略选择和最终报告

## 共享核心层

共享逻辑已经抽到 `core/`：

- `core/registry.py`
  - 单一接口注册表
  - 统一维护 interface 配置和 group 配置
  - 提供 auto-fill 使用的子集视图

- `core/calendar.py`
  - 交易日历获取
  - 时间同步检查
  - 交易日范围计算

- `core/health.py`
  - 本地最新日期检查
  - 缺失交易日计算
  - 数据完整性扫描

- `core/files.py`
  - 大文件尾读最新日期
  - 追加写入
  - 快速单日期合并
  - CSV 去重

- `core/logging_utils.py`
  - 统一日志输出

## 当前边界

这轮重构优先解决的是：

- 接口注册表重复
- 文件合并/去重逻辑重复
- 交易日历与健康检查逻辑重复
- `update_daily.py` / `auto_fill_data.py` 的共享依赖散落

仍然保留在入口脚本中的内容：

- 某些接口的特殊抓取逻辑
  - `cyq_chips`
  - `cyq_perf`
  - `theme_data`
  - `margin_detail`
  - 若干指数接口
- 兼容现有命令行参数和运行习惯

## 后续建议

如果继续往“健康项目”推进，下一步应做的是：

1. 把特殊接口更新器逐步搬到 `core/updaters/`
2. 把 CLI 参数解析抽到独立模块
3. 给 registry 引入 dataclass，而不是继续用裸字典
4. 给核心文件操作和缺失检测补更多单测
5. 用一个统一 runner 同时服务 `update_daily` 和 `auto_fill_data`
