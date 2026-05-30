# Tushare Pro 脚本修复记录

## 修复历史

---

### 2026-04-10: Token 认证失败 & 代理配置问题

#### 问题描述
运行 `update_daily.py` 时出现大量 "无效的 token" 错误，所有接口调用失败。

#### 根本原因

**1. Token 不匹配**
- 环境变量 `TUSHARE_TOKEN` 设置为 `6e6e69e277...`
- 但代理服务器 `http://lianghua.nanyangqiankun.top` 只认 token `68e53ce462...`
- `config.py` 使用 `os.getenv('TUSHARE_TOKEN', default)` 优先读取环境变量，导致使用了错误的 token

**2. 导入顺序问题**
- `update_daily.py` 先 `import tushare as ts`，后导入 `tushare_client`
- `tushare_client.py` 需要在导入 tushare **之前** 清除代理环境变量（HTTP_PROXY 等）
- 如果 tushare 先被导入，它会读取环境变量中的代理设置（`127.0.0.1:7890`），导致请求路由到本地 Clash（未运行时超时）

**3. 缺少 import os**
- 修改 `config.py` 时添加了 `os.getenv` 调用，但忘记添加 `import os`

#### 修复内容

**config.py:**
```python
# 添加 import os
import os

# 保留环境变量读取逻辑，但添加注释说明
TOKEN = os.getenv('TUSHARE_TOKEN', "68e53ce462eb8689c5e8ed6422bcd9c2be589df0c179564a4e827b9472d0")
```

**update_daily.py:**
```python
# 调整导入顺序：tushare_client 必须在 tushare 之前导入
from tushare_client import create_pro_api
import tushare as ts
```

**环境变量:**
```bash
# 更新 ~/.zshrc 中的 TUSHARE_TOKEN
export TUSHARE_TOKEN="68e53ce462eb8689c5e8ed6422bcd9c2be589df0c179564a4e827b9472d0"
```

#### 测试验证
```bash
# 测试代理连接
python3 -c "
import sys; sys.path.insert(0, 'utils')
from config import TOKEN, PROXY_URL
import tushare as ts
pro = ts.pro_api(token=TOKEN, timeout=30)
pro._DataApi__http_url = PROXY_URL
df = pro.stock_basic(limit=3)
print(f'✅ 成功, {len(df)} 条数据')
"
```

#### 教训
1. **代理服务器和 token 是绑定的** - 换了代理必须确认 token 是否匹配
2. **导入顺序很重要** - 环境变量清除必须在库导入之前
3. **环境变量优先级** - `os.getenv` 会覆盖默认值，要确保环境变量值正确

---

### 2026-04-09: 代理服务器宕机

#### 问题描述
代理服务器 `http://lianghua.nanyangqiankun.top` 完全无法访问（ping 100% 丢包）。

#### 解决方案
- 等待代理服务器恢复（约 1 天后自动恢复）
- 临时切换到 Tushare 官方 API 的方案（未实施）

---

### 2026-04-09: Rate Limit 限制

#### 问题描述
`stk_mins` 和 `rt_min` 接口有严格的调用限制：
- `stk_mins`: 2次/天，每次只能查 1 只股票
- `rt_min`: 10次/天

#### 解决方案
在 `update_daily.py` 中注释掉这两个接口，避免意外触发限制：
```python
# 已注释（Rate Limit 限制）
# 'rt_min': {'type': 'standalone', 'func': 'update_rt_min', 'group': 'realtime'},
# 'stk_mins': {'type': 'standalone', 'func': 'update_stk_mins', 'group': 'mins'},
```

---

---

### 2026-04-10: API Rate Limit 达到上限

#### 问题描述
运行 `update_daily.py` 时提示 "当前接口达到请求上限，请稍后重试"。

#### 根本原因
代理服务器 `http://lianghua.nanyangqiankun.top` 的每日 API 配额已用完。

#### 解决方案

**方案 1**: 等待代理恢复（推荐）
- 代理服务器通常每天重置配额
- 明天再运行更新脚本

**方案 2**: 切换到官方 API
```bash
# 切换到官方 API（需要自己有 Tushare 账号和积分）
cd ~/.openclaw/skills/custom/tushare_pro
./switch_api.sh official

# 恢复代理模式
./switch_api.sh proxy
```

#### 快速切换脚本
```bash
./switch_api.sh [proxy|official]
```

---

## 故障排查速查表

| 错误信息 | 可能原因 | 解决方案 |
|---------|---------|---------|
| 无效的 token | Token 与代理服务器不匹配 | 检查 `config.py` 和 env 中的 token 是否为代理专用 |
| Connection timeout | 路由到本地代理 (127.0.0.1:7890) | 确保 `tushare_client.py` 在 `import tushare` 之前清除代理环境变量 |
| NameError: name 'os' is not defined | 缺少 import | 在文件开头添加 `import os` |
| 代理服务器无响应 | 代理宕机 | 检查 `ping lianghua.nanyangqiankun.top`，等待恢复或切换官方 API |

---

## 测试脚本

### 测试代理连接
```bash
cd ~/.openclaw/skills/custom/tushare_pro
python3 tests/test_tushare_proxy.py
```

### 测试官方 API
```bash
cd ~/.openclaw/skills/custom/tushare_pro
python3 tests/test_official_api.py
```

### 快速验证
```bash
python3 -c "
import sys; sys.path.insert(0, 'utils')
from config import TOKEN, PROXY_URL
import tushare as ts
pro = ts.pro_api(token=TOKEN, timeout=30)
pro._DataApi__http_url = PROXY_URL
df = pro.daily(trade_date='20260408', limit=3)
print(f'✅ 代理正常，获取 {len(df)} 条数据')
"
```
