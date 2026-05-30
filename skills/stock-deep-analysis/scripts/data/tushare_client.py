"""
Tushare Pro 初始化模块。

统一入口，其他模块直接引用:
    from data.tushare_client import pro

通过 HTTP 中转 (124.220.22.110:8020) 替代官方 tushare 服务,
接口名称和官方 tushare 完全一致。
"""

import tushare as ts

pro = ts.pro_api("6be0552842c69a4c84636359df4028459ce14d13d092cdce491ce77d361ab5a6")
pro._DataApi__http_url = "http://124.220.22.110:8020/"
