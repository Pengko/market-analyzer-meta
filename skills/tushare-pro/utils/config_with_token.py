#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主要作用:
- 提供一个带示例字段的配置模板
- 用法上等同于 `config_template.py`，更偏向“填写式”示例
"""

import os
from typing import Optional


class TushareConfig:
    """Tushare Pro 配置类"""
    
    # ==================== Token 配置 ====================
    # 注意：请勿将真实token提交到版本控制系统
    # 优先使用环境变量 TUSHARE_TOKEN，其次使用此配置
    
    # Tushare Pro Token（从 https://tushare.pro 注册获取）
    TOKEN: str = "your_tushare_token"
    
    # 自定义API端点（可选）
    API_URL: Optional[str] = None
    
    # ==================== 缓存配置 ====================
    
    # 缓存目录（默认：当前目录下的 tushare_cache）
    CACHE_DIR: str = "./tushare_cache"
    
    # 缓存有效期（天）
    CACHE_DAYS: int = 30
    
    # 缓存格式：auto/parquet/csv/json
    # auto：自动检测（优先parquet，其次csv）
    CACHE_FORMAT: str = "auto"
    
    # ==================== 数据获取配置 ====================
    
    # 默认日期范围（天）
    DEFAULT_DAYS_BACK: int = 30
    
    # 默认股票代码（用于演示和测试）
    DEFAULT_STOCKS: list = [
        '000001.SZ',  # 平安银行
        '600036.SH',  # 招商银行
        '300059.SZ',  # 东方财富
        '000858.SZ',  # 五粮液
        '600519.SH',  # 贵州茅台
    ]
    
    # ==================== 代理配置（可选） ====================
    
    # HTTP代理（如需科学上网）
    HTTP_PROXY: Optional[str] = None
    HTTPS_PROXY: Optional[str] = None
    
    # ==================== 获取配置方法 ====================
    
    @classmethod
    def get_token(cls) -> str:
        """获取Token（优先环境变量）"""
        return os.getenv('TUSHARE_TOKEN', cls.TOKEN)
    
    @classmethod
    def get_api_url(cls) -> Optional[str]:
        """获取API URL（优先环境变量）"""
        return os.getenv('TUSHARE_API_URL', cls.API_URL)
    
    @classmethod
    def get_cache_dir(cls) -> str:
        """获取缓存目录（优先环境变量）"""
        return os.getenv('TUSHARE_CACHE_DIR', cls.CACHE_DIR)
    
    @classmethod
    def setup_proxy(cls):
        """设置代理（如需要）"""
        if cls.HTTP_PROXY:
            os.environ['HTTP_PROXY'] = cls.HTTP_PROXY
        if cls.HTTPS_PROXY:
            os.environ['HTTPS_PROXY'] = cls.HTTPS_PROXY
    
    @classmethod
    def validate(cls):
        """验证配置"""
        token = cls.get_token()
        if not token or token == "your_token_here":
            raise ValueError(
                "请配置Tushare Token：\n"
                "1. 修改 config.py 中的 TOKEN 值\n"
                "2. 或设置环境变量 TUSHARE_TOKEN\n"
                "3. 注册地址：https://tushare.pro"
            )
        return True


# 创建配置实例
config = TushareConfig()

if __name__ == "__main__":
    # 配置验证测试
    try:
        config.validate()
        print("✅ 配置验证通过")
        print(f"   Token: {config.get_token()[:8]}...{config.get_token()[-8:]}")
        print(f"   API URL: {config.get_api_url()}")
        print(f"   缓存目录: {config.get_cache_dir()}")
    except ValueError as e:
        print(f"❌ 配置错误: {e}")
        print("\n📝 配置步骤:")
        print("1. 复制此文件为 local_config.py")
        print("2. 修改 local_config.py 中的配置项")
        print("3. 或设置环境变量:")
        print("   export TUSHARE_TOKEN='your_token_here'")
        print("   export TUSHARE_API_URL='http://your-api.com'")
