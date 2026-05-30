#!/usr/bin/env python3
from __future__ import annotations

import csv
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import sys

SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from runtime import runtime_fetch
from fetchers import hermes_browser_fetch


def _write_complete_minute_csv(path: Path, trade_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    cumulative_volume = 0.0
    cumulative_amount = 0.0
    minute_points = []
    minute_points.extend(f"09:{m:02d}" for m in range(30, 60))
    minute_points.extend(f"10:{m:02d}" for m in range(0, 60))
    minute_points.extend(f"11:{m:02d}" for m in range(0, 31))
    minute_points.extend(f"13:{m:02d}" for m in range(1, 60))
    minute_points.extend(f"14:{m:02d}" for m in range(0, 60))
    minute_points.append("15:00")

    price = 10.0
    for idx, hhmm in enumerate(minute_points):
        cumulative_volume += 1000 + idx
        cumulative_amount += (1000 + idx) * price
        rows.append(
            {
                "datetime": f"{trade_date} {hhmm}",
                "open": price,
                "close": price,
                "high": price,
                "low": price,
                "volume": 1000 + idx,
                "amount": (1000 + idx) * price,
                "avg": price,
            }
        )

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["datetime", "open", "close", "high", "low", "volume", "amount", "avg"],
        )
        writer.writeheader()
        writer.writerows(rows)


class MinuteRefreshSemanticsTest(unittest.TestCase):
    def test_auto_fetch_minute_data_forces_refresh_on_current_trade_day(self) -> None:
        fake_now = datetime(2026, 4, 15, 14, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        with tempfile.TemporaryDirectory() as tmpdir:
            minute_path = Path(tmpdir) / "minute_kline.csv"
            minute_path.write_text("placeholder", encoding="utf-8")
            with patch.object(runtime_fetch, "resolve_minute_path", return_value=minute_path):
                with patch.object(
                    runtime_fetch,
                    "auto_fetch_minute_via_browser",
                    return_value={"status": "pending_started"},
                ) as mocked_fetch:
                    meta = runtime_fetch.auto_fetch_minute_data("600103.SH", "2026-04-15", fake_now)
        self.assertIsNotNone(meta)
        self.assertTrue(meta.get("force_refresh"))
        self.assertEqual(meta.get("reason"), "current_trade_day_realtime_refresh")
        mocked_fetch.assert_called_once()

    def test_auto_fetch_minute_data_reuses_existing_after_close(self) -> None:
        fake_now = datetime(2026, 4, 15, 15, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        with tempfile.TemporaryDirectory() as tmpdir:
            minute_path = Path(tmpdir) / "minute_kline.csv"
            minute_path.write_text("placeholder", encoding="utf-8")
            with patch.object(runtime_fetch, "resolve_minute_path", return_value=minute_path):
                with patch.object(runtime_fetch, "auto_fetch_minute_via_browser") as mocked_fetch:
                    meta = runtime_fetch.auto_fetch_minute_data("600103.SH", "2026-04-15", fake_now)
        self.assertIsNone(meta)
        mocked_fetch.assert_not_called()

    def test_run_hermes_falls_back_to_local_minute_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            minute_path = Path(tmpdir) / "minute_kline.csv"
            args = Namespace(
                task_kind="minute",
                dry_run=False,
                timeout=10,
                symbol="600519.SH",
                trade_date="20260415",
                hermes_executor_dir=str(SCRIPTS_ROOT.parents[2] / "hermes-executor"),
                hermes_docker_path=str(Path.home() / "hermes-docker"),
                executor="hermes",
                agent="stock-agent",
                session_id="stock-agent:default",
                message_id="",
                request_id="",
                skills=[],
                save_to_memory=False,
                stock_name="",
                preset=[],
                klt=5,
                artifact_dir="/tmp/hermes-browser-fetch",
            )

            class FakeHermesClient:
                def __init__(self, _docker_path: str):
                    pass

                def execute_from_payload(self, _payload):
                    return {
                        "success": False,
                        "returncode": 124,
                        "stdout": "",
                        "stderr": "timeout",
                    }

            def fake_local_run(_args: Namespace):
                _write_complete_minute_csv(minute_path, "2026-04-15")
                return {"success": True, "returncode": 0, "stdout": "ok", "stderr": "", "executor": "local"}

            with patch.object(hermes_browser_fetch, "_load_hermes_client", return_value=FakeHermesClient):
                with patch.object(hermes_browser_fetch, "_minute_output_path", return_value=minute_path):
                    with patch.object(hermes_browser_fetch, "_run_local", side_effect=fake_local_run):
                        with patch.object(hermes_browser_fetch, "_should_force_realtime_refresh", return_value=True):
                            result = hermes_browser_fetch._run_hermes(args)

        self.assertTrue(result["success"])
        self.assertEqual(result["fallback_used"], "local_minute_fetch")
        self.assertTrue(result["minute_complete"])


if __name__ == "__main__":
    unittest.main()
