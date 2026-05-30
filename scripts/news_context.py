#!/usr/bin/env python3
"""
shim — 指向 message-intelligence skill 的 normalize/news_sentiment.py
保持对 stock-deep-analysis 外部调用的向后兼容
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from data.config_loader import cfg

# 从 config 读取路径，fallback 到本项目
_MSG_INTEL = cfg.get("paths", "external", "message_intelligence")
if not _MSG_INTEL:
    _MSG_INTEL = str(Path.home() / "agent-skills" / "custom" / "message-intelligence")
MSG_INTEL_DIR = Path(_MSG_INTEL)

SENTIMENT_SCRIPT = MSG_INTEL_DIR / "normalize" / "news_sentiment.py"
NARRATIVE_SCRIPT = MSG_INTEL_DIR / "normalize" / "narrative_context.py"

# 将 MSG_INTEL_DIR 加入 sys.path，确保内部互相 import 能解析
if str(MSG_INTEL_DIR) not in sys.path:
    sys.path.insert(0, str(MSG_INTEL_DIR))

# 加载 normalize/news_sentiment.py
if not SENTIMENT_SCRIPT.exists():
    raise ImportError(f"message-intelligence normalize/news_sentiment.py not found: {SENTIMENT_SCRIPT}")
SPEC_NS = importlib.util.spec_from_file_location("_mi_news_sentiment", SENTIMENT_SCRIPT)
if SPEC_NS is None or SPEC_NS.loader is None:
    raise ImportError(f"failed to load {SENTIMENT_SCRIPT}")
MODULE_NS = importlib.util.module_from_spec(SPEC_NS)
SPEC_NS.loader.exec_module(MODULE_NS)

# 加载 normalize/narrative_context.py
if NARRATIVE_SCRIPT.exists():
    SPEC_NC = importlib.util.spec_from_file_location("_mi_narrative_context", NARRATIVE_SCRIPT)
    if SPEC_NC and SPEC_NC.loader:
        MODULE_NC = importlib.util.module_from_spec(SPEC_NC)
        SPEC_NC.loader.exec_module(MODULE_NC)
        # 合并 narrative 的函数
        for name in ("narrative_context_from_news",):
            if hasattr(MODULE_NC, name):
                globals()[name] = getattr(MODULE_NC, name)

# 从 news_sentiment 模块导出所有公开函数
for name in dir(MODULE_NS):
    if name.startswith("__") and name not in {"__doc__", "__all__"}:
        continue
    if name in globals():
        continue  # 跳过已经手动映射的
    globals()[name] = getattr(MODULE_NS, name)
