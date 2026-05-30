# `stk_weekly_monthly` 单独补全调用文档

本文档用于单独补全 `stk_weekly_monthly` 对应的本地 `weekly` / `monthly` 数据。

## 一、作用说明

- 上游接口：`stk_weekly_monthly`
- 本地落盘：
  - `weekly` -> `/Users/penghongming/quant-data/tushare/股票数据/weekly`
  - `monthly` -> `/Users/penghongming/quant-data/tushare/股票数据/monthly`
- 脚本入口：
  - `/Users/penghongming/agent-skills/custom/tushare_pro/update_weekly_monthly.py`

## 二、基础命令

先进入项目目录：

```bash
cd /Users/penghongming/agent-skills/custom/tushare_pro
```

### 1）只补全周线

```bash
python3 update_weekly_monthly.py --interface weekly --periods 6
```

### 2）只补全月线

```bash
python3 update_weekly_monthly.py --interface monthly --periods 6
```

### 3）周线和月线一起补全

```bash
python3 update_weekly_monthly.py --interface both --periods 6
```

## 三、参数说明

### `--interface`

可选值：

- `weekly`：只补全周线
- `monthly`：只补全月线
- `both`：同时补全周线和月线

默认值：

```bash
--interface both
```

### `--periods`

表示“补全最近多少个周期”：

- 对 `weekly` 来说，是最近 N 个周周期
- 对 `monthly` 来说，是最近 N 个月周期

例如：

```bash
python3 update_weekly_monthly.py --interface weekly --periods 12
```

表示补最近 12 个周周期。

### `--quiet`

安静模式，减少过程日志输出。

示例：

```bash
python3 update_weekly_monthly.py --interface monthly --periods 6 --quiet
```

说明：

- 普通模式：会打印每个周期的拉取过程
- 安静模式：只保留很少的输出，适合后台执行或你自己盯总结果时使用

### `--ignore-whitelist`

忽略当前白名单，强制重新检查并重拉目标周期。

适用场景：

- 你怀疑白名单已经标记了，但本地数据其实没补完整
- 你想无视历史状态，重新补最近几个周/月周期

示例：

```bash
python3 update_weekly_monthly.py --interface both --periods 6 --ignore-whitelist
```

### `--all`

拉取截至今天的全部历史周期，忽略 `--periods`。

适用场景：

- 你要全量重扫历史周线
- 你要全量重扫历史月线
- 你要配合 `--ignore-whitelist` 做一次完整重补

示例：

```bash
python3 update_weekly_monthly.py --interface both --all
```

### `--trade-dates`

只补指定周期日期，不走“最近 N 个周期”的自动选择。

多个日期用英文逗号分隔，例如：

```bash
python3 update_weekly_monthly.py --interface weekly --trade-dates 20250516,20250523,20250530
```

适用场景：

- 你明确知道超时的是哪几条周期
- 你只想补失败的几周/几个月
- 你不想把最近一整段周期重新拉一遍

说明：

- 可配合 `--ignore-whitelist` 使用，强制重拉这几条
- 不可与 `--all` 同时使用

## 四、推荐用法

### 日常补最近 3 个周期

```bash
python3 update_weekly_monthly.py --interface both --periods 3
```

### 手动修复最近 12 个周周期

```bash
python3 update_weekly_monthly.py --interface weekly --periods 12
```

### 无视白名单，强制重拉最近 6 个周期

```bash
python3 update_weekly_monthly.py --interface both --periods 6 --ignore-whitelist
```

### 只重补 3 条超时周线

```bash
python3 update_weekly_monthly.py --interface weekly --trade-dates 20250516,20250523,20250530 --ignore-whitelist
```

### 全量历史周期重拉

```bash
python3 update_weekly_monthly.py --interface both --all --ignore-whitelist
```

### 手动修复最近 12 个月周期

```bash
python3 update_weekly_monthly.py --interface monthly --periods 12
```

## 五、执行逻辑说明

脚本执行时会：

1. 读取最近 N 个目标周期
2. 先检查白名单
3. 只请求白名单未覆盖的周期
4. 第一轮先请求 `stk_weekly_monthly`
5. 若某周期返回无数据，第二轮自动回退到官方 `weekly` / `monthly`
6. 把返回结果按股票分别写入 `weekly` 或 `monthly`
7. 成功周期写回白名单，避免下次重复拉取

当传入 `--all` 时：

- 不再只看最近 N 个周期
- 会拉取截至今天的全部历史周期
- 如果再配合 `--ignore-whitelist`，则会无视白名单全量重查

当前活跃周/月规则：

- 若今天是交易日，会额外把“当前交易日对应的活跃周/月快照”纳入本轮请求
- 当前活跃周/月不走白名单，每次运行都会重抓
- 只有历史稳定周期才会写入白名单

## 六、结果文件说明

生成后的文件示例：

### 周线

```text
/Users/penghongming/quant-data/tushare/股票数据/weekly/weekly_000001.SZ.csv
```

### 月线

```text
/Users/penghongming/quant-data/tushare/股票数据/monthly/monthly_000001.SZ.csv
```

## 七、常见场景

### 场景 1：只想补周线

```bash
cd /Users/penghongming/agent-skills/custom/tushare_pro && python3 update_weekly_monthly.py --interface weekly --periods 6
```

### 场景 2：只想补月线

```bash
cd /Users/penghongming/agent-skills/custom/tushare_pro && python3 update_weekly_monthly.py --interface monthly --periods 6
```

### 场景 3：一起补，少打印日志

```bash
cd /Users/penghongming/agent-skills/custom/tushare_pro && python3 update_weekly_monthly.py --interface both --periods 6 --quiet
```

### 场景 4：白名单已标记，但我要强制重查

```bash
cd /Users/penghongming/agent-skills/custom/tushare_pro && python3 update_weekly_monthly.py --interface both --periods 6 --ignore-whitelist
```

### 场景 5：全历史重拉

```bash
cd /Users/penghongming/agent-skills/custom/tushare_pro && python3 update_weekly_monthly.py --interface both --all --ignore-whitelist
```

## 八、注意事项

- 这个脚本调用的是 `stk_weekly_monthly`，不是分别调用 `weekly` / `monthly` 官方单接口
- 本地保存目录仍然是 `weekly` 和 `monthly`
- 如果白名单已覆盖目标周期，脚本会自动跳过
- 如果你想强制重拉某些周期，需要先清理对应白名单记录
