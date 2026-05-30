"""
Infoway WebSocket 客户端

基于 Infoway WebSocket API 订阅 A 股分钟数据
文档: https://docs.infoway.io/api-reference/websocket-api

设计方案: Infoway分钟数据接入设计方案.md
"""

import asyncio
import json
import os
import signal
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

import websockets

# 添加父目录到路径（用于导入 infoway_minute_writer）
sys.path.insert(0, str(Path(__file__).parent))
from infoway_minute_writer import persist_infoway_bar, persist_infoway_bars


@dataclass
class InfowayConfig:
    """Infoway 配置"""
    api_key: str
    endpoint: str = "wss://data.infoway.io/ws"
    reconnect_interval: float = 5.0
    heartbeat_interval: float = 30.0
    max_reconnect_attempts: int = 10


class InfowayWebSocketClient:
    """
    Infoway WebSocket 客户端
    
    特性:
    - 自动重连机制
    - 心跳保活
    - 多股票订阅
    - 实时持久化
    """
    
    def __init__(self, config: InfowayConfig, trade_date: Optional[str] = None):
        self.config = config
        self.trade_date = trade_date or datetime.now().strftime("%Y-%m-%d")
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.subscribed_symbols: set[str] = set()
        self.is_running = False
        self.reconnect_count = 0
        self.last_heartbeat = 0
        self.message_handler: Optional[Callable[[Dict], Coroutine]] = None
        
    async def connect(self) -> bool:
        """建立 WebSocket 连接
        
        Infoway WebSocket 端点格式: wss://data.infoway.io/ws?business=stock&apikey=API_KEY
        """
        try:
            # 在 URL 中传递 apikey（Infoway 要求的方式）
            endpoint = f"{self.config.endpoint}?business=stock&apikey={self.config.api_key}"
            
            self.ws = await websockets.connect(
                endpoint,
                ping_interval=None,  # 我们自己管理心跳
            )
            
            self.reconnect_count = 0
            print(f"[✓] WebSocket 连接成功: {self.config.endpoint}")
            return True
            
        except Exception as e:
            print(f"[✗] WebSocket 连接失败: {e}")
            return False
    
    async def subscribe(self, symbols: List[str], kline_type: int = 1) -> bool:
        """
        订阅股票分钟数据
        
        Infoway 协议号 10006: K线订阅请求
        
        Args:
            symbols: 股票代码列表，如 ["002594.SZ", "000001.SZ"]
            kline_type: K线类型，1=分钟，默认1
        """
        if not self.ws:
            print("[✗] 未连接到 WebSocket")
            return False
        
        # Infoway 协议格式（协议号 10006）
        subscribe_msg = {
            "code": 10006,
            "trace": str(uuid.uuid4()).replace("-", ""),
            "data": {
                "arr": [
                    {
                        "type": kline_type,
                        "codes": ",".join(symbols)
                    }
                ]
            }
        }
        
        try:
            await self.ws.send(json.dumps(subscribe_msg))
            self.subscribed_symbols.update(symbols)
            print(f"[→] 发送订阅请求: {', '.join(symbols)}")
            return True
        except Exception as e:
            print(f"[✗] 订阅失败: {e}")
            return False
    
    async def unsubscribe(self, symbols: List[str]) -> bool:
        """取消订阅"""
        if not self.ws:
            return False
        
        # Infoway 协议号 10014: 取消订阅请求
        unsubscribe_msg = {
            "code": 10014,
            "trace": str(uuid.uuid4()).replace("-", ""),
            "data": {
                "arr": [
                    {
                        "type": 1,
                        "codes": ",".join(symbols)
                    }
                ]
            }
        }
        
        try:
            await self.ws.send(json.dumps(unsubscribe_msg))
            self.subscribed_symbols.difference_update(symbols)
            print(f"[✓] 取消订阅: {', '.join(symbols)}")
            return True
        except Exception as e:
            print(f"[✗] 取消订阅失败: {e}")
            return False
    
    async def send_heartbeat(self) -> bool:
        """发送心跳包
        
        Infoway 协议号 10010: 心跳请求
        """
        if not self.ws:
            return False
        
        try:
            heartbeat_msg = {
                "code": 10010,
                "trace": str(uuid.uuid4()).replace("-", "")
            }
            await self.ws.send(json.dumps(heartbeat_msg))
            self.last_heartbeat = time.time()
            return True
        except Exception as e:
            print(f"[!] 心跳发送失败: {e}")
            return False
    
    async def receive_loop(self):
        """消息接收主循环"""
        while self.is_running and self.ws:
            try:
                # 设置超时以便检查心跳
                message = await asyncio.wait_for(
                    self.ws.recv(), 
                    timeout=self.config.heartbeat_interval + 10
                )
                
                # 解析消息
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError as e:
                    print(f"[!] JSON解析失败: {e}")
                    
            except asyncio.TimeoutError:
                # 检查是否需要发送心跳
                if time.time() - self.last_heartbeat > self.config.heartbeat_interval:
                    await self.send_heartbeat()
            except websockets.exceptions.ConnectionClosed:
                print("[!] 连接已关闭，准备重连...")
                break
            except Exception as e:
                print(f"[!] 接收消息异常: {e}")
    
    async def _handle_message(self, data: dict):
        """
        处理接收到的消息
        
        Infoway 协议:
        - 10007: 订阅确认
        - 10008: K线数据推送
        - 10011: 心跳响应
        - 200: 连接成功
        """
        # 获取协议号
        code = data.get("code")
        
        if code == 10011:
            # 心跳响应（协议号 10011）
            pass
            
        elif code == 10008:
            # K线数据推送（协议号 10008）
            await self._handle_kline(data)
            
        elif code == 10007:
            # 订阅确认（协议号 10007）
            trace = data.get("trace", "")
            msg = data.get("msg", "ok")
            print(f"[✓] 服务器确认订阅: {msg}")
            
        elif code == 200:
            # 连接成功
            pass
            
        elif code and code >= 400:
            # 错误消息
            print(f"[✗] 服务器错误 (code={code}): {data.get('msg', 'Unknown error')}")
            
        else:
            # 其他消息
            if data:
                print(f"[i] 收到消息(code={code}): {json.dumps(data, ensure_ascii=False)[:200]}")
        
        # 调用用户自定义的处理器
        if self.message_handler:
            await self.message_handler(data)
    
    async def _handle_kline(self, data: dict):
        """
        处理 K 线数据（协议号 10008）
        
        Infoway K线数据格式:
        {
            "code": 10008,
            "data": {
                "s": "600103.SH",
                "c": "10.50",
                "h": "10.55",
                "l": "10.48",
                "o": "10.50",
                "pca": "0.05",
                "pfr": "0.48%",
                "t": 1747550640,
                "ty": 1,
                "v": "12345.67",
                "vw": "129532.50"
            }
        }
        """
        kline_data = data.get("data", {})
        symbol = kline_data.get("s")
        
        if not symbol or not kline_data:
            return
        
        # 持久化到文件
        try:
            persist_infoway_bar(symbol, kline_data, self.trade_date)
        except Exception as e:
            print(f"[!] 数据持久化失败({symbol}): {e}")
    
    async def run(self, symbols: List[str] | None = None):
        """
        启动客户端
        
        Args:
            symbols: 要订阅的股票列表，如果为空则使用已订阅的股票
        """
        self.is_running = True
        
        while self.is_running:
            # 尝试连接
            if not await self.connect():
                self.reconnect_count += 1
                if self.reconnect_count > self.config.max_reconnect_attempts:
                    print(f"[✗] 最大重连次数({self.config.max_reconnect_attempts})已达，停止重连")
                    break
                
                wait_time = min(self.config.reconnect_interval * self.reconnect_count, 60)
                print(f"[*] {wait_time}秒后重试({self.reconnect_count}/{self.config.max_reconnect_attempts})...")
                await asyncio.sleep(wait_time)
                continue
            
            # 订阅股票
            if symbols:
                await self.subscribe(symbols)
            elif self.subscribed_symbols:
                await self.subscribe(list(self.subscribed_symbols))
            
            # 进入接收循环
            await self.receive_loop()
            
            # 如果是正常退出，不重连
            if not self.is_running:
                break
            
            # 重连等待
            self.reconnect_count += 1
            if self.reconnect_count > self.config.max_reconnect_attempts:
                print(f"[✗] 最大重连次数已达，停止")
                break
            
            wait_time = min(self.config.reconnect_interval * self.reconnect_count, 60)
            print(f"[*] {wait_time}秒后重试...")
            await asyncio.sleep(wait_time)
    
    async def stop(self):
        """停止客户端"""
        print("[*] 正在停止客户端...")
        self.is_running = False
        
        if self.ws:
            await self.ws.close()
            self.ws = None
        
        print("[✓] 客户端已停止")


def create_client(
    api_key: Optional[str] = None,
    trade_date: Optional[str] = None
) -> InfowayWebSocketClient:
    """
    工厂函数：创建 WebSocket 客户端
    
    Args:
        api_key: API Key，默认从环境变量 INFOWAY_API_KEY 读取
        trade_date: 交易日期，默认今天
    """
    api_key = api_key or os.getenv("INFOWAY_API_KEY")
    
    if not api_key:
        raise ValueError(
            "INFOWAY_API_KEY 未设置。\n"
            "请设置环境变量: export INFOWAY_API_KEY=your_api_key\n"
            "或在配置文件中指定: ~/.config/stock-deep-analysis/config.yaml"
        )
    
    config = InfowayConfig(api_key=api_key)
    return InfowayWebSocketClient(config, trade_date)


async def main():
    """命令行示例"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Infoway WebSocket 客户端")
    parser.add_argument(
        "symbols",
        nargs="+",
        help="要订阅的股票代码，如 002594.SZ 000001.SZ"
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="交易日期 (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("INFOWAY_API_KEY"),
        help="Infoway API Key"
    )
    
    args = parser.parse_args()
    
    # 创建客户端
    client = create_client(args.api_key, args.date)
    
    # 设置信号处理
    def signal_handler(sig, frame):
        print("\n[!] 收到中断信号，正在退出...")
        asyncio.create_task(client.stop())
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 启动
    print(f"[*] 启动 Infoway 客户端")
    print(f"[*] 订阅股票: {', '.join(args.symbols)}")
    print(f"[*] 交易日期: {args.date}")
    print("[*] 按 Ctrl+C 停止\n")
    
    await client.run(args.symbols)


if __name__ == "__main__":
    asyncio.run(main())
