# 飞书汇报格式指南

> 存档日期：2026-05-26
> 适用场景：通过 `send_message` 将分析报告发送至飞书群聊/私聊时

## 核心约束

飞书群聊/私聊的**标准文本消息**不支持 Markdown 表格语法（`|` 分隔线不会渲染为表格，而是原样显示为纯文本）。

这会影响 `stock-deep-analysis` 技能中强制要求使用表格的四个模块：
1. 最近10日走势
2. 分时主力意图分析
3. 近5日资金流向
4. 消息面（公司公告 + 综合判断）

## 已验证的方案

### 方案 1：等宽代码块 + Unicode 制表线（推荐）

使用三个反引号包裹，内部用等宽字体对齐的文本 + Unicode 制表线：

```
日期       收盘价   涨跌幅    换手率   成交量    形态/信号
────────── ──────── ───────── ──────── ───────── ───────────
2026-05-22 12.34   +5.67%    8.92%    45.2万手  放量突破
2026-05-21 11.68   +2.10%    4.55%    23.1万手  缩量整理
```

**要点**：
- 使用 `─`（U+2500）画横线，`│`（U+2502）画竖线（可选，简单对齐可不用竖线）
- 中文字符占 2 个等宽单元，英文字母/数字占 1 个，需仔细对齐
- 数字右对齐，文字左对齐
- 涨跌幅保留正负号（+/-）
- 代码块内不要套代码块（即外层用 ```` ` ``` ` ```` 避免冲突）

**示例效果**（飞书客户端渲染）：
飞书会将代码块渲染为灰色背景的等宽字体区域，列对齐可见，阅读体验接近表格。

### 方案 2：要点列表（宽表格降级）

当表格列数过多（>5列）或单元格内容过长时，代码块内对齐困难，改用结构化列表：

```
【最近10日走势】
• 2026-05-22 | 收 12.34 | +5.67% | 换 8.92% | 量 45.2万手 | 放量突破
• 2026-05-21 | 收 11.68 | +2.10% | 换 4.55% | 量 23.1万手 | 缩量整理
```

### 方案 3：CardKit 交互卡片（如需真正表格）

若必须使用真正表格（如表头固定、列宽自适应），需通过飞书 CardKit API 发送 `interactive` 卡片，而非 `send_message` 文本消息。

**限制**：
- 需要 `cardkit:card:write` 权限
- 飞书客户端需 7.20+
- Hermes 当前 `send_message` 工具不支持直接发送 CardKit 卡片
- 实现复杂度高，不推荐常规分析汇报使用

## 不适用方案（已验证失败）

| 方案 | 结果 |
|------|------|
| Markdown 标准表格 `\| 列1 \| 列2 \|` | ❌ 飞书按纯文本显示，`\|` 和 `-` 全部可见，无表格边框 |
| HTML `<table>` 标签 | ❌ 飞书文本消息不解析 HTML |
| 飞书特有的 `<at>` 等扩展语法 | ❌ 与表格无关 |

## 代码实现参考

在 `parallel/agents.py` 或报告渲染层中，可将 Markdown 表格自动转换为代码块格式：

```python
def markdown_table_to_code_block(table_md: str) -> str:
    """将标准 Markdown 表格转换为飞书可用的等宽代码块"""
    lines = [l.strip() for l in table_md.strip().split("\n") if l.strip()]
    # 移除 Markdown 表格的分隔线（第2行通常是 |---|---|）
    filtered = [l for l in lines if not set(l.strip()).issubset({"|", "-", ":", " "})]
    # 提取单元格内容
    rows = []
    for line in filtered:
        cells = [c.strip() for c in line.split("|") if c.strip()]
        rows.append(cells)
    # 计算每列最大宽度（中文字符算2宽）
    def width(s):
        return sum(2 if ord(c) > 127 else 1 for c in s)
    col_widths = [max(width(r[i]) for r in rows if i < len(r)) for i in range(max(len(r) for r in rows))]
    # 构建等宽行
    def pad(s, w):
        return s + " " * (w - width(s))
    formatted = []
    for i, row in enumerate(rows):
        formatted.append(" ".join(pad(cell, col_widths[j]) for j, cell in enumerate(row)))
        if i == 0:  # 表头后加制表线
            formatted.append(" ".join("─" * w for w in col_widths))
    return "```\n" + "\n".join(formatted) + "\n```"
```

## 使用约定

1. **本地保存的报告**：继续使用标准 Markdown 表格（`references/pending-validations/` 目录下的 `.md` 文件）
2. **飞书发送的汇报**：将表格转换为等宽代码块后再 `send_message`
3. **混合场景**：若同一份报告既要本地保存又要飞书发送，保存原始 Markdown 版，发送前动态转换
