# Hermes 变更日志

## [2026-05-24] CardKit 流式卡片修复

**Record Type**: implemented_change
**Scope**: gateway/platforms/feishu.py
**Status**: 已部署

### 问题
- `create_streaming_card` 被硬编码为 `return None`，导致 CardKit 流式卡片路径完全禁用
- 所有流式输出 fallback 到 `patch_message` 路径，表格内容被 `_build_outbound_payload` 降级为纯文本消息
- 用户看到的是文本消息而非卡片，表格渲染异常

### 修复 v1（第一次尝试）
- 重新实现 `create_streaming_card`：调用 CardKit API (`POST /open-apis/cardkit/v1/cards`) 创建流式卡片，从 `response.raw.content` 解析 `card_id`
- 重新实现 `streaming_update_card`：调用 CardKit API (`PUT /open-apis/cardkit/v1/cards/{card_id}/elements/md_content/content`) 更新 markdown 元素内容
- 重新实现 `disable_streaming_card`：调用 CardKit API (`PATCH /open-apis/cardkit/v1/cards/{card_id}/settings`) 禁用流式模式
- **问题**：请求体格式错误（缺少 `type`/`data` 封装，`sequence` 缺失），API 返回 400 Bad Request

### 修复 v2（通过 API 测试验证）
- `create_streaming_card`：请求体必须包含 `type: "card_json"` 和 `data: "<json-string>"`（data 为 JSON 字符串而非对象）
- `streaming_update_card`：请求体必须包含 `sequence` 字段
- `disable_streaming_card`：请求体必须包含 `sequence` 和 `settings: "<json-string>"`

### 验证
- 直接 curl 测试 CardKit API：创建卡片、更新内容、禁用流式均返回 `code: 0`
- Gateway 已重启（PID: 78010）
- 等待用户测试确认卡片和表格渲染正常

---
