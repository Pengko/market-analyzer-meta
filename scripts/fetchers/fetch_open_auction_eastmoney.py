#!/usr/bin/env python3

from pathlib import Path
import runpy
import sys

from data.config_loader import cfg

_SCRIPT = cfg.get('paths', 'external', 'fetch_eastmoney_auction')
if not _SCRIPT:
    _SCRIPT = str(Path.home() / '.openclaw' / 'skills' / 'custom' / 'stock-deep-analysis' / 'scripts' / 'fetch_eastmoney_auction.py')
SCRIPT = Path(_SCRIPT)

sys.argv = [str(SCRIPT), "--type", "open", *sys.argv[1:]]
runpy.run_path(str(SCRIPT), run_name="__main__")
