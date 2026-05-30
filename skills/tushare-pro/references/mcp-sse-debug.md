# MCP SSE 服务器连接调试

Tushare MCP 服务器使用 SSE (Server-Sent Events) 传输协议。

## 服务器信息

- URL: `http://124.220.22.110:8020/mcp?token=6be0552842c69a4c84636359df4028459ce14d13d092cdce491ce77d361ab5a6`
- 协议: SSE (Server-Sent Events)
- 服务名: `tushare-mcp-static`
- 工具总数: 258 个

## 关键：Accept Header 必须同时包含两个 MIME 类型

直接用 curl 或 requests 连接 SSE MCP 服务器时，**Accept header 必须同时包含 `application/json` 和 `text/event-stream`**，否则返回 406：

```
{"code":-32600,"message":"Not Acceptable: Client must accept both application/json and text/event-stream"}
```

### 正确的连接方式

**Python (aiohttp):**
```python
headers = {
    "Accept": "application/json, text/event-stream",  # 两者必须同时存在
    "Content-Type": "application/json",
}
```

**curl:**
```bash
curl -s "http://124.220.22.110:8020/mcp?token=..." \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### 错误的连接方式

```python
# 错误 1：只发 application/json
headers = {"Accept": "application/json", "Content-Type": "application/json"}
# → 406

# 错误 2：只发 text/event-stream
headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
# → 406

# 错误 3：分开两个 header
headers = {
    "Accept": "application/json",
    "Accept": "text/event-stream",  # 后者覆盖前者
    "Content-Type": "application/json",
}
# → 406
```

## 响应格式

SSE 响应为多行文本，每条消息格式：

```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{...}}
```

解析时需提取 `data:` 后面的 JSON 内容。

## Hermes 集成

在 Hermes Agent 中，可通过以下方式使用：

1. **native-mcp skill**: 在 `~/.hermes/config.yaml` 中配置 mcpServers
2. **mcporter CLI**: 通过 `mcporter` 命令行工具连接
3. **直接 HTTP 调用**: 如上所述，用 Python requests/aiohttp 发送 SSE 请求
