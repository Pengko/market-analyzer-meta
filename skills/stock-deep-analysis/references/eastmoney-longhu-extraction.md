# 东方财富龙虎榜详细席位数据提取方法

> 适用场景：用户提供了龙虎榜截图但 vision/OCR 不可用，或需要补充抓取某只个股的详细席位买卖明细。

## 1. 概览数据 API（已验证）

### 每日龙虎榜个股概览
```
https://datacenter-web.eastmoney.com/api/data/v1/get
  ?reportName=RPT_DAILYBILLBOARD_PROFILE
  &columns=ALL
  &filter=(SECURITY_CODE="{code}")
```

返回字段示例（京东方A 2026-05-29）：
- `TRADE_DATE`: 交易日期
- `CHANGE_RATE`: 涨跌幅
- `BILLBOARD_NET_AMT`: 龙虎榜净额（元）
- `ONLIST_NUM`: 上榜次数

### 机构交易汇总
```
https://datacenter-web.eastmoney.com/api/data/v1/get
  ?reportName=RPT_ORGANIZATION_TRADE_DETAILSNEW
  &columns=ALL
  &filter=(SECURITY_CODE="{code}")
```

返回字段示例：
- `TRADE_DATE`: 交易日期
- `BUY_TIMES` / `SELL_TIMES`: 买卖次数
- `BUY_AMT` / `SELL_AMT`: 买卖金额（元）
- `NET_BUY_AMT`: 净买入（元）
- `EXPLANATION`: 上榜原因

### 机构席位统计
```
https://datacenter-web.eastmoney.com/api/data/v1/get
  ?reportName=RPT_ORGANIZATION_SEATNEW
  &columns=ALL
  &filter=(SECURITY_CODE="{code}")
```

返回字段示例：
- `BUY_AMT` / `SELL_AMT`: 机构买卖金额
- `NET_BUY_AMT`: 机构净买入
- `BUY_TIMES` / `SELL_TIMES`: 机构买卖次数

**⚠️ 注意**：以上 API 返回的是**汇总统计数据**，不是逐席位明细。要获取每个营业部的具体买入/卖出金额，必须使用浏览器方式（见第2节）。

## 2. 详细席位明细 — 浏览器提取法（推荐）

当需要具体席位（如"深股通专用买入多少、卖出多少"）时，浏览器提取是唯一可靠方式。

### 2.1 明细页面 URL 模式
```
https://data.eastmoney.com/stock/lhb,{YYYY-MM-DD},{CODE}.html
```

示例（京东方A 2026-05-29）：
```
https://data.eastmoney.com/stock/lhb,2026-05-29,000725.html
```

### 2.2 提取步骤

1. **导航到明细页**
   ```
   browser_navigate(url="https://data.eastmoney.com/stock/lhb,2026-05-29,000725.html")
   ```

2. **用 JavaScript 提取表格数据**
   ```javascript
   const tables = document.querySelectorAll('table');
   let result = [];
   for (let t of tables) {
     const rows = t.querySelectorAll('tr');
     for (let r of rows) {
       const cells = r.querySelectorAll('td, th');
       if (cells.length > 0) {
         result.push(Array.from(cells).map(c => c.textContent.trim()).join(' | '));
       }
     }
   }
   result.join('\n')
   ```

3. **页面上的表格结构**
   - 表1：个股基本信息（代码、名称、最新价、涨跌幅、换手率等）
   - 表2：**买入金额最大的前5名**（席位名称、买入金额、卖出金额、净额、占总成交比例）
   - 表3：**卖出金额最大的前5名**（同上）
   - 表尾：合计行（总买入、总卖出、总净额）

### 2.3 实际输出示例

**买入前5席位：**

| 排名 | 席位 | 买入(万) | 卖出(万) | 净额(万) |
|:----:|------|:-------:|:-------:|:-------:|
| 1 | 深股通专用 | 82,035 | 66,122 | +15,913 |
| 2 | 机构专用 | 50,076 | 5,356 | +44,719 |
| 3 | 华泰证券深圳彩田路 | 33,337 | 160 | +33,176 |
| 4 | 机构专用 | 25,549 | 26,218 | -668 |
| 5 | 机构专用 | 23,396 | 29,179 | -5,782 |

**卖出前5席位：**

| 排名 | 席位 | 买入(万) | 卖出(万) | 净额(万) |
|:----:|------|:-------:|:-------:|:-------:|
| 1 | 深股通专用 | 82,035 | 66,122 | +15,913 |
| 2 | 国泰海通上海分公司 | 1 | 36,124 | -36,123 |
| 3 | 机构专用 | 23,396 | 29,179 | -5,782 |
| 4 | 机构专用 | 25,549 | 26,218 | -668 |
| 5 | 国泰海通杭州富春路 | 6,586 | 17,800 | -11,214 |

**合计：**
- 总买入：220,979.39万
- 总卖出：180,958.07万
- 净买入：+40,021.32万

## 3. 从概览页获取明细页 URL

如果不知道明细页 URL，可以先访问概览页，再用 JavaScript 提取：

```
https://data.eastmoney.com/stock/tradedetail.html?code={code}
```

提取明细链接的 JavaScript：
```javascript
const rows = document.querySelectorAll('table tbody tr');
let detailUrl = null;
for (let r of rows) {
  const cells = r.querySelectorAll('td');
  if (cells.length > 5 && cells[1]?.textContent?.trim() === '000725') {
    const links = r.querySelectorAll('a');
    for (let a of links) {
      if (a.textContent.includes('明细')) {
        detailUrl = a.href;
        break;
      }
    }
    break;
  }
}
detailUrl
```

返回示例：
```
https://data.eastmoney.com/stock/lhb,2026-05-29,000725.html
```

## 4. 分析要点

拿到详细席位数据后，重点分析：

1. **北向资金态度**：深股通/沪股通专用席位的净买卖方向
2. **机构分歧**：多个"机构专用"席位之间的买卖方向是否一致
3. **游资动向**：知名营业部（如华泰彩田路、国泰海通杭州等）的单向买卖行为
4. **多空对比**：买方前5 vs 卖方前5的净额对比
5. **合计净额**：即使跌停，龙虎榜净额仍可能为正（说明有大资金抄底）

## 5. 常见陷阱

- **API 端点试错陷阱**：东方财富 datacenter API 的 `reportName` 参数不公开枚举，盲目猜测会返回 `"报表配置不存在"`。正确做法是从目标网页源代码中 `grep 'reportName='` 提取实际使用的端点名。
- **日期过滤陷阱**：部分 API（如 `RPT_ORGANIZATION_TRADE_DETAILSNEW`）不支持 `(TRADE_DATE="...")` 格式的日期过滤，需去掉日期条件或改用范围过滤。
- **列名陷阱**：不同 API 返回的买卖金额列名不同，可能是 `BUY_AMT`/`SELL_AMT`、`BILLBOARD_BUY_AMT`/`BILLBOARD_SELL_AMT` 或 `NET_BUY_AMT`，需先读一条样本确认列名。
- **汇总 vs 明细混淆**：`RPT_BILLBOARD_TRADEALLNEW` 返回的是该股一段时间内的龙虎榜汇总统计，不是逐日逐席位明细。要逐席位明细必须用浏览器提取法。
