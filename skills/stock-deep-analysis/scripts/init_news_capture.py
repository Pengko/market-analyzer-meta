#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


from data.config_loader import cfg

_NEW_SCRIPT = cfg.get('paths', 'external', 'news_init')
if not _NEW_SCRIPT:
    _NEW_SCRIPT = str(Path.home() / '.openclaw' / 'skills' / 'custom' / 'market-news-intelligence' / 'scripts' / 'init_news_capture.py')
NEW_SCRIPT = Path(_NEW_SCRIPT)
if str(NEW_SCRIPT.parent) not in sys.path:
    sys.path.insert(0, str(NEW_SCRIPT.parent))
SPEC = importlib.util.spec_from_file_location("market_news_intelligence_init_news_capture", NEW_SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"failed to load {NEW_SCRIPT}")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

for name in dir(MODULE):
    if name.startswith("__") and name not in {"__doc__", "__all__"}:
        continue
    globals()[name] = getattr(MODULE, name)

if __name__ == "__main__":
    MODULE.main()
