# A股K线图表生成指南

本参考文档记录使用 matplotlib 生成专业级A股K线图表的完整代码模板、中文字体配置、常见陷阱与最佳实践。

## 快速开始

### 完整代码模板（复制即用）

```python
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # 无头模式，适合后台生成
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# ========== macOS中文字体配置（必填）==========
plt.rcParams['font.sans-serif'] = ['Heiti TC', 'Songti SC', 'Arial Unicode MS', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

# ========== 读取数据 ==========
# df 应包含: trade_date, open, high, low, close, amount, ma5, ma10, ma20, ma30
df['date'] = pd.to_datetime(df['trade_date'], format='%Y%m%d')

# ========== 创建图表 ==========
fig, axes = plt.subplots(2, 1, figsize=(14, 10),
                         gridspec_kw={'height_ratios': [3, 1]}, sharex=True)
fig.suptitle('上证指数 K线图', fontsize=16, fontweight='bold')

# ========== 上半部分: K线 + 均线 ==========
ax1 = axes[0]

# 绘制K线
for _, row in df.iterrows():
    color = '#e74c3c' if row['close'] >= row['open'] else '#2ecc71'
    ax1.plot([row['date'], row['date']], [row['low'], row['high']], color=color, linewidth=1)
    ax1.plot([row['date'], row['date']], [row['open'], row['close']], color=color, linewidth=4)

# 绘制均线
ax1.plot(df['date'], df['ma5'], label='MA5', color='#3498db', linewidth=1.2, alpha=0.8)
ax1.plot(df['date'], df['ma10'], label='MA10', color='#f39c12', linewidth=1.2, alpha=0.8)
ax1.plot(df['date'], df['ma20'], label='MA20', color='#9b59b6', linewidth=1.2, alpha=0.8)
ax1.plot(df['date'], df['ma30'], label='MA30', color='#1abc9c', linewidth=1.5, alpha=0.9)

# 标注关键点位（支撑/压力/历史高低点）
ax1.axhline(y=4258.86, color='red', linestyle='--', alpha=0.5, label='高点')
ax1.axhline(y=4061.15, color='green', linestyle='--', alpha=0.5, label='低点')

# 标注特定日期
ax1.annotate('5/21 -2.04%', xy=(df.iloc[-2]['date'], df.iloc[-2]['close']),
             xytext=(df.iloc[-2]['date'], df.iloc[-2]['close']-55),
             fontsize=9, color='green', fontweight='bold',
             arrowprops=dict(arrowstyle='->', color='green', lw=1))

ax1.set_ylabel('点位')
ax1.legend(loc='upper left', fontsize=8)
ax1.grid(True, alpha=0.3)

# ========== 下半部分: 成交量 ==========
ax2 = axes[1]
colors_vol = ['#e74c3c' if c >= o else '#2ecc71'
              for c, o in zip(df['close'], df['open'])]
ax2.bar(df['date'], df['amount']/1e8, color=colors_vol, width=0.8)
ax2.set_ylabel('成交额(亿)')
ax2.grid(True, alpha=0.3)

# 日期格式化
ax2.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
ax2.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
plt.xticks(rotation=45)
plt.tight_layout()

# 保存
plt.savefig('/tmp/sh_index_chart.png', dpi=150, bbox_inches='tight', facecolor='white')
```

## 环境差异说明

| 执行方式 | 优缺点 | 适用场景 |
|:---|:---|:---|
| **`terminal()`** | 可访问用户主环境（含venv、tushare等全部依赖） | **推荐**。数据获取+图表生成一体化任务 |
| **`execute_code`** | 沙盒环境，无tushare等依赖，需手动安装 | 仅当数据已在沙盒可访问时使用 |

**推荐模式**：先用 `terminal()` 获取数据并导出CSV，再用 `execute_code` 或继续 `terminal()` 绘图。

## 中文字体配置（按操作系统）

### macOS
```python
# 优先级：Arial Unicode MS 在多数 macOS 系统上可用性最高；Heiti TC 可能不是默认安装字体
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'Hiragino Sans GB', 'Heiti TC', 'Songti SC', 'WenQuanYi Micro Hei', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题
```

**实战检查**：如果仍出现 `Glyph ... missing from font(s) DejaVu Sans`，说明上述字体均未找到，需用以下命令查看系统安装的中文字体列表：
```python
import matplotlib.font_manager as fm
fonts = [f.name for f in fm.fontManager.ttflist if any(k in f.name.lower() for k in ['hei', 'song', 'unicode', 'cjk', 'micro'])]
print(sorted(set(fonts)))
```

### Linux
```python
plt.rcParams['font.sans-serif'] = ['WenQuanYi Micro Hei', 'Noto Sans CJK SC', 'sans-serif']
plt.rcParams['axes.unicode_minus'] = False
```

### 查找系统字体
```python
import matplotlib.font_manager as fm
fonts = [f.name for f in fm.fontManager.ttflist if 'Hei' in f.name or 'Song' in f.name]
print(set(fonts))
```

## 历史量化类比方法

通过筛选历史相似K线组合来预测后续走势：

```python
df = df.sort_values('trade_date').reset_index(drop=True)

# 筛选条件：前日大跌且当日反弹
matches = []
for i in range(1, len(df)):
    if df.loc[i-1, 'pct_chg'] < -1.5 and df.loc[i, 'pct_chg'] > 0.5:
        matches.append(i)

# 计算每次相似走势后的N日累计涨跌
for i in matches:
    date = df.loc[i, 'trade_date']
    close = df.loc[i, 'close']
    cum3 = (df.loc[i+3, 'close'] / close - 1) * 100 if i + 3 < len(df) else 'N/A'
    cum5 = (df.loc[i+5, 'close'] / close - 1) * 100 if i + 5 < len(df) else 'N/A'
    print(f'{date}: 后3日{cum3:.2f}% 后5日{cum5:.2f}%')
```

**应用场景**：
- 大跌后反弹的持续性判断
- 突破/跌破关键位后的回撤概率
- 量价背离模式验证

## 斐波那契回撤位绘制

```python
low = 4061.15   # 前期低点
high = 4258.86  # 前期高点
fib_levels = [0.236, 0.382, 0.5, 0.618, 0.786]

for f in fib_levels:
    level = high - (high - low) * f
    ax1.axhline(y=level, color='gray', linestyle=':', alpha=0.4)
    ax1.text(df['date'].iloc[-1], level, f'{f*100:.1f}%', fontsize=8, va='center')
```

## 常见陷阱

| 陷阱 | 表现 | 解决 |
|:---|:---|:---|
| 中文显示为口 | `UserWarning: Glyph ... missing from font(s)` | 设置 `plt.rcParams['font.sans-serif']` |
| 负号显示为方块 | `−1.5` 变成 `□` | 设置 `plt.rcParams['axes.unicode_minus'] = False` |
| 沙盒缺tushare | `ModuleNotFoundError: No module named 'tushare'` | 改用 `terminal()` 执行 |
| K线颜色反了 | 涨为绿色、跌为红色 | A股习惯是红涨绿跌，注意 `color = '#e74c3c' if close >= open else '#2ecc71'` |
| 图表标题被截断 | `bbox_inches='tight'` 不足 | 加 `plt.tight_layout()` 在 `savefig` 之前 |

## 进阶技巧

### 添加涨跌幅标签
```python
for _, row in df.iterrows():
    color = '#e74c3c' if row['pct_chg'] > 0 else '#2ecc71'
    ax1.text(row['date'], row['high'] + 10, f"{row['pct_chg']:.1f}%",
             ha='center', fontsize=7, color=color)
```

### 多指数对比图
```python
fig, axes = plt.subplots(3, 1, figsize=(14, 14), sharex=True)
# 分别绘制上证、深证、创业板的走势
```

### 保存到references目录
```python
import os
output_dir = os.path.expanduser("~/.hermes/hermes-agent/references")
os.makedirs(output_dir, exist_ok=True)
plt.savefig(os.path.join(output_dir, f"chart_{datetime.now().strftime('%Y%m%d')}.png"),
            dpi=150, bbox_inches='tight', facecolor='white')
```
