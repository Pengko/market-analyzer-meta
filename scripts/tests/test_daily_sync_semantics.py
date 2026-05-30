#!/usr/bin/env python3
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data import data_access


class DailySyncSemanticsTest(unittest.TestCase):
    def _write_csv(self, path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def test_sync_latest_daily_kline_skips_non_latest_trade_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            trade_cal_dir = root / 'trade_cal'
            self._write_csv(
                trade_cal_dir / 'trade_cal_all.csv',
                ['exchange', 'cal_date', 'is_open', 'pretrade_date'],
                [
                    {'exchange': 'SSE', 'cal_date': '20260415', 'is_open': '1', 'pretrade_date': '20260414'},
                    {'exchange': 'SSE', 'cal_date': '20260416', 'is_open': '1', 'pretrade_date': '20260415'},
                ],
            )
            with patch.object(data_access, 'STOCK_DATA_ROOT', root), patch.object(data_access, 'TRADE_CAL_DIR_CANDIDATES', [trade_cal_dir]):
                meta = data_access.sync_latest_daily_kline_via_browser('600103.SH', '2026-04-15', reference_date_text='2026-04-16')
            self.assertEqual(meta['status'], 'skipped_not_latest_trade_date')
            self.assertEqual(meta['latest_open_trade_date'], '2026-04-16')


if __name__ == '__main__':
    unittest.main()
