# 飞书表格渲染问题与 Gateway 修复记录

> 日期：2026-05-31
> 关联：`skills/stock-deep-analysis/references/feishu-formatting-guide.md`

## 问题背景

通过 Hermes Gateway 的 streaming card（飞书 interactive card）发送 stock-deep-analysis 报告时，报告中的 markdown 表格在飞书客户端不能正确渲染，显示为原始 markdown 纯文本。

## 根因分析

1. **飞书 lark_md 不支持表格**
   - 飞书 interactive card 的 `lark_md` 元素不支持 markdown 表格语法
   - 服务器端会将 `lark_md` 自动降级为 `text` 元素
   - 验证：直接调用飞书 OpenAPI 发送原始 markdown 表格，返回数据中 `lark_md` 被降级为 `text`

2. **Gateway `_render_tables_for_card` bug**
   - 文件：`hermes-agent/gateway/platforms/feishu.py`
   - 函数计算了 `col_widths` 但从未使用来做对齐
   - 输出是无对齐的加粗文本，而不是表格

3. **Skill 错误 workaround**
   - 文件：`references/pitfalls-session-learnings.md` 第36行
   - 内容："Skip `render_feishu()` and send standard Markdown tables directly"
   - 影响：此错误指令导致 agent 直接以原始 markdown 表格发送
   - 状态：已修正，移除跳过指令，添加严禁跳过说明

## 修复方案

### Gateway 层修复

重写 `_render_tables_for_card()` 函数（文件：`hermes-agent/gateway/platforms/feishu.py`）：

- 自动检测 markdown 表格（以 `|` 开头且下一行是分隔线）
- 将 markdown 表格转换为 Unicode 制表线 ASCII 艺术代码块
- 使用 `┌─┬─┐` `│ │ │` `├┼┤` `└┴┘` 等制表符号
- 正确处理 CJK 双宽字符宽度计算和对齐

**关键代码片段**：
```python
def _render_tables_for_card(markdown: str) -> str:
    lines = markdown.split("\n")
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and i + 1 < len(lines) and _is_table_separator(lines[i + 1].strip()):
            table_lines = [line]
            i += 1
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i])
                i += 1
            ascii_lines = _table_to_ascii(table_lines)
            result.append("```")
            result.extend(ascii_lines)
            result.append("```")
        else:
            result.append(line)
            i += 1
    return "\n".join(result)
```

### Skill 层修复

- 更新 `scripts/render/__init__.py`，添加 `render_feishu` 导出
- 修正 `references/pitfalls-session-learnings.md`：移除 "Skip render_feishu" 错误指令

## 验证

### 本地测试

```python
# 输入
| 指标 | 数值 | 涨幅 |
|------|------|------|
| 上证指数 | 3350.12 | +0.45% |
| 深证成指 | 10820.55 | +0.82% |

# 输出
```
┌────────┬────────┬──────┤
│指标      │      数值│    涨幅│
├────────┼────────┼──────┤
│上证指数  │   3350.12│  +0.45%│
│深证成指  │  10820.55│  +0.82%│
└────────┴────────┴──────┘
```
```

### 飞书 API 直接测试

通过飞书 OpenAPI 发送两条 interactive card 测试消息：
- 测试A（原始 markdown 表格）：lark_md 降级为 text，原样显示
- 测试B（ASCII 艺术代码块）：lark_md 降级为 text，但等宽字体下保持表格形状

结论：ASCII 艺术代码块是唯一可行方案。

## 注意事项

- Gateway 修改后必须重启进程才能加载新代码
- macOS 上命令：`launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway`
- 若 OpenClaw cloud 部署也遇到同样问题，需在其对应 gateway 上同步表格渲染修复
- 以后所有通过 streaming card 发送的报告，表格将自动渲染为 ASCII 艺术
