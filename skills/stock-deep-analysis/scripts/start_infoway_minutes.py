#!/usr/bin/env python3
"""
Infoway 分钟数据采集启动脚本

使用 Infoway WebSocket 实时订阅 A 股分钟数据，持久化到标准目录结构
支持多股票订阅、自动重连、心跳保活

用法:
    # 基本用法 - 订阅单个股票
    python3 start_infoway_minutes.py 002594.SZ
    
    # 订阅多个股票
    python3 start_infoway_minutes.py 002594.SZ 000001.SZ 300750.SZ
    
    # 指定交易日期
    python3 start_infoway_minutes.py 002594.SZ --date 2026-04-22
    
    # 使用环境变量中的 API Key
    export INFOWAY_API_KEY=your_key
    python3 start_infoway_minutes.py 002594.SZ
    
    # 指定 API Key
    python3 start_infoway_minutes.py 002594.SZ --api-key your_key

设计方案: Infoway分钟数据接入设计方案.md
"""

import argparse
import asyncio
import os
import signal
import sys
from datetime import datetime
from pathlib import Path

from data.config_loader import cfg

# 添加导入路径
SCRIPT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_ROOT / "fetchers"))

from infoway_ws_client import create_client, InfowayWebSocketClient


# 全局客户端实例（用于信号处理）
_client_instance: InfowayWebSocketClient | None = None


def signal_handler(signum, frame):
    """处理中断信号"""
    signame = signal.Signals(signum).name
    print(f"\n[!] 收到 {signame}，正在清理...")
    
    global _client_instance
    if _client_instance:
        # 创建任务来停止客户端
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_client_instance.stop())
            else:
                loop.run_until_complete(_client_instance.stop())
        except Exception:
            pass
    
    print("[✓] 已退出")
    sys.exit(0)


async def main():
    parser = argparse.ArgumentParser(
        description="Infoway 分钟数据采集工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python3 %(prog)s 002594.SZ
  python3 %(prog)s 002594.SZ 000001.SZ --date 2026-04-22
  export INFOWAY_API_KEY=xxx && python3 %(prog)s 002594.SZ
        """
    )
    
    parser.add_argument(
        "symbols",
        nargs="+",
        help="要订阅的股票代码，如 002594.SZ 000001.SZ"
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="交易日期 (YYYY-MM-DD)，默认今天"
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("INFOWAY_API_KEY"),
        help="Infoway API Key，默认从环境变量 INFOWAY_API_KEY 读取"
    )
    parser.add_argument(
        "--output-dir",
        default=str(cfg.paths("minute")),
        help="分钟数据输出目录"
    )
    parser.add_argument(
        "--heartbeat",
        type=int,
        default=30,
        help="心跳间隔（秒），默认30"
    )
    parser.add_argument(
        "--reconnect",
        type=int,
        default=5,
        help="重连间隔（秒），默认5"
    )
    
    args = parser.parse_args()
    
    # 校验 API Key
    if not args.api_key:
        print("错误: INFOWAY_API_KEY 未设置")
        print("请设置环境变量: export INFOWAY_API_KEY=your_api_key")
        print("或使用 --api-key 参数指定")
        sys.exit(1)
    
    # 校验日期格式
    try:
        datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print(f"错误: 日期格式无效: {args.date}")
        print("请使用 YYYY-MM-DD 格式")
        sys.exit(1)
    
    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 让写盘链路与当前 CLI 参数共用同一套分钟数据根目录
    os.environ["MINUTE_DATA_ROOT"] = str(Path(args.output_dir).expanduser())
    
    # 创建客户端
    global _client_instance
    _client_instance = create_client(args.api_key, args.date)
    
    # 覆盖默认配置
    _client_instance.config.heartbeat_interval = args.heartbeat
    _client_instance.config.reconnect_interval = args.reconnect
    
    # 输出信息
    print("=" * 60)
    print("Infoway 分钟数据采集工具")
    print("=" * 60)
    print(f"订阅股票: {', '.join(args.symbols)}")
    print(f"交易日期: {args.date}")
    print(f"输出目录: {args.output_dir}")
    print(f"心跳间隔: {args.heartbeat}秒")
    print(f"重连间隔: {args.reconnect}秒")
    print("-" * 60)
    print("按 Ctrl+C 停止\n")
    
    # 启动客户端
    try:
        await _client_instance.run(args.symbols)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\n错误: {e}")
        sys.exit(1)
    finally:
        if _client_instance:
            await _client_instance.stop()


if __name__ == "__main__":
    asyncio.run(main())
