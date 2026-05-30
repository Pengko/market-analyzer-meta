"""
YAML 配置中心加载器

使用方式:
    from data.config_loader import get_config, cfg

    # 获取配置值
    stock_root = cfg.paths("stock_data_root")
    rsi_periods = cfg.indicator("rsi", "periods")

    # 通用查询（支持多层嵌套键）
    timeout = cfg.get("network", "browser", "timeout_ms", default=30000)

配置文件位置: references/config/skill-config.yaml
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

_SKILL_ROOT = Path(__file__).parent.parent.parent
_DEFAULT_CONFIG_PATH = _SKILL_ROOT / "references" / "config" / "skill-config.yaml"


def _expand_env(value: Any) -> Any:
    """展开字符串中的环境变量 ${VAR} 或 $VAR。"""
    if isinstance(value, str):
        pattern = re.compile(r"\$\{(\w+)\}|\$(\w+)")

        def replacer(m: re.Match) -> str:
            var = m.group(1) or m.group(2)
            return os.environ.get(var, "")

        return pattern.sub(replacer, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def _expand_user(value: Any) -> Any:
    """展开 ~ 为用户主目录。"""
    if isinstance(value, str):
        return os.path.expanduser(value)
    if isinstance(value, dict):
        return {k: _expand_user(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_user(v) for v in value]
    return value


def _process_value(value: Any) -> Any:
    """先展开环境变量，再展开 ~ 路径。"""
    return _expand_user(_expand_env(value))


class Config:
    """配置容器，支持多层嵌套键查询。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _DEFAULT_CONFIG_PATH
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        with open(self._path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
        return _process_value(raw)

    def get(self, *keys: str, default: Any = None) -> Any:
        """
        通用配置查询，支持多层键。

        Example:
            cfg.get("network", "browser", "timeout_ms", default=30000)
        """
        d = self._data
        for key in keys:
            if not isinstance(d, dict):
                return default
            d = d.get(key, default)
            if d is None:
                return default
        return d

    def paths(self, key: str) -> Path:
        """
        获取路径配置，支持直接路径和子目录组装。

        Args:
            key: 路径键名，如 "stock_data_root"，或子目录名如 "daily"
        """
        # 1. 先尝试直接读取 paths 下的路径
        val = self.get("paths", key)
        if val is not None:
            return Path(val)

        # 2. 尝试从子目录组装
        subdirs = self.get("paths", "subdirs", default={})
        if key in subdirs:
            if key.startswith("financial_"):
                financial_root = self.get("paths", "financial_data_root")
                if financial_root:
                    return Path(financial_root) / subdirs[key]
            stock_root = self.get("paths", "stock_data_root")
            if stock_root:
                return Path(stock_root) / subdirs[key]

        raise KeyError(
            f"Path config not found: paths.{key} (checked paths and subdirs)"
        )

    def indicator(self, *keys: str, default: Any = None) -> Any:
        """获取技术指标参数。"""
        return self.get("indicators", *keys, default=default)

    def decision(self, *keys: str, default: Any = None) -> Any:
        """获取决策引擎参数。"""
        return self.get("decision", *keys, default=default)

    def network(self, *keys: str, default: Any = None) -> Any:
        """获取网络/浏览器参数。"""
        return self.get("network", *keys, default=default)

    def report(self, *keys: str, default: Any = None) -> Any:
        """获取报告参数。"""
        return self.get("report", *keys, default=default)

    def news(self, *keys: str, default: Any = None) -> Any:
        """获取新闻参数。"""
        return self.get("news", *keys, default=default)

    def mobile(self, *keys: str, default: Any = None) -> Any:
        """获取移动端参数。"""
        return self.get("mobile", *keys, default=default)

    def fetcher(self, *keys: str, default: Any = None) -> Any:
        """获取数据抓取参数。"""
        return self.get("fetchers", *keys, default=default)

    def reload(self) -> None:
        """重新加载配置文件。"""
        self._data = self._load()


# 全局单例（懒加载）
_config_instance: Config | None = None


def get_config() -> Config:
    """获取全局配置实例（首次调用时加载）。"""
    global _config_instance
    if _config_instance is None:
        _config_instance = Config()
    return _config_instance


def reload_config() -> Config:
    """重新加载并返回配置实例。"""
    global _config_instance
    _config_instance = Config()
    return _config_instance


# 便捷全局对象（推荐使用）
cfg = get_config()
