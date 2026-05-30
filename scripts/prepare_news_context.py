#!/usr/bin/env python3
"""
shim — 指向 message-intelligence skill 的 normalize/news_sentiment.py
保持对 stock-deep-analysis 中 load_news_payload / prepare logic 的向后兼容
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

from data.config_loader import cfg

_MSG_INTEL = cfg.get("paths", "external", "message_intelligence")
if not _MSG_INTEL:
    _MSG_INTEL = str(Path.home() / "agent-skills" / "custom" / "message-intelligence")
MSG_INTEL_DIR = Path(_MSG_INTEL)

SENTIMENT_SCRIPT = MSG_INTEL_DIR / "normalize" / "news_sentiment.py"

if str(MSG_INTEL_DIR) not in sys.path:
    sys.path.insert(0, str(MSG_INTEL_DIR))

if not SENTIMENT_SCRIPT.exists():
    raise ImportError(f"message-intelligence normalize/news_sentiment.py not found: {SENTIMENT_SCRIPT}")
SPEC = importlib.util.spec_from_file_location("_mi_prepare_context", SENTIMENT_SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"failed to load {SENTIMENT_SCRIPT}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

for name in dir(MODULE):
    if name.startswith("__") and name not in {"__doc__", "__all__"}:
        continue
    globals()[name] = getattr(MODULE, name)

if __name__ == "__main__":
    # 命令行入口：接收 --news-json XX --trade-date YY → 输出归一化结果
    import json
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--news-json", required=True)
    parser.add_argument("--trade-date")
    args = parser.parse_args()

    raw = load_news_payload(args.news_json)
    ns = normalize_news_sentiment(raw, trade_date_text=args.trade_date)

    # 增强 narrative context
    from normalize.narrative_context import narrative_context_from_news
    nc = narrative_context_from_news(ns)

    result = {
        "news_sentiment": ns,
        "narrative_context": nc,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
