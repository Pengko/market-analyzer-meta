import tempfile
import unittest
from datetime import datetime as real_datetime
from pathlib import Path
from unittest import mock

import pandas as pd

import update_weekly_monthly as updater
from core import autofill_runtime


class FakeWeeklyMonthlyPro:
    def __init__(self, frames_by_date, weekly_frames_by_date=None, monthly_frames_by_date=None):
        self.frames_by_date = frames_by_date
        self.weekly_frames_by_date = weekly_frames_by_date or {}
        self.monthly_frames_by_date = monthly_frames_by_date or {}
        self.calls = []
        self.named_calls = []

    def stk_weekly_monthly(self, trade_date=None, freq=None):
        self.calls.append((str(trade_date), str(freq)))
        self.named_calls.append(("stk_weekly_monthly", {"trade_date": str(trade_date), "freq": str(freq)}))
        frame = self.frames_by_date.get(str(trade_date))
        return None if frame is None else frame.copy()

    def _resolve_fallback_frame(self, mapping, trade_date=None, limit=None, offset=None):
        source = mapping.get(str(trade_date))
        if source is None:
            return pd.DataFrame()
        if isinstance(source, list):
            page_limit = int(limit or 1)
            page_index = int(offset or 0) // page_limit
            if page_index >= len(source):
                return pd.DataFrame()
            frame = source[page_index]
            return frame.copy()
        return source.copy()

    def weekly(self, trade_date=None, limit=None, offset=None):
        self.named_calls.append(
            ("weekly", {"trade_date": str(trade_date), "limit": limit, "offset": offset})
        )
        return self._resolve_fallback_frame(self.weekly_frames_by_date, trade_date, limit, offset)

    def monthly(self, trade_date=None, limit=None, offset=None):
        self.named_calls.append(
            ("monthly", {"trade_date": str(trade_date), "limit": limit, "offset": offset})
        )
        return self._resolve_fallback_frame(self.monthly_frames_by_date, trade_date, limit, offset)


class FakeRetryPro:
    def __init__(self, primary_side_effects=None, weekly_side_effects=None, monthly_side_effects=None):
        self.primary_side_effects = list(primary_side_effects or [])
        self.weekly_side_effects = list(weekly_side_effects or [])
        self.monthly_side_effects = list(monthly_side_effects or [])
        self.named_calls = []

    def _pop_effect(self, bucket):
        if not bucket:
            return pd.DataFrame()
        effect = bucket.pop(0)
        if isinstance(effect, Exception):
            raise effect
        return effect

    def stk_weekly_monthly(self, trade_date=None, freq=None):
        self.named_calls.append(("stk_weekly_monthly", {"trade_date": str(trade_date), "freq": str(freq)}))
        return self._pop_effect(self.primary_side_effects)

    def weekly(self, trade_date=None, limit=None, offset=None):
        self.named_calls.append(
            ("weekly", {"trade_date": str(trade_date), "limit": limit, "offset": offset})
        )
        return self._pop_effect(self.weekly_side_effects)

    def monthly(self, trade_date=None, limit=None, offset=None):
        self.named_calls.append(
            ("monthly", {"trade_date": str(trade_date), "limit": limit, "offset": offset})
        )
        return self._pop_effect(self.monthly_side_effects)


class WeeklyMonthlyUpdaterTests(unittest.TestCase):
    def test_get_target_period_dates_includes_live_week_and_month_snapshots(self):
        class FakeDateTime:
            @staticmethod
            def now():
                return real_datetime(2026, 5, 15, 15, 0, 0)

        week_map = {
            20260508: 20260508,
            20260514: 20260515,
            20260515: 20260515,
        }
        month_map = {
            20260430: 20260430,
            20260514: 20260529,
            20260515: 20260529,
        }
        with mock.patch.object(updater, "datetime", FakeDateTime):
            weekly_targets, weekly_volatile = updater.get_target_period_dates(week_map, n=3, fetch_all=False)
            monthly_targets, monthly_volatile = updater.get_target_period_dates(month_map, n=3, fetch_all=False)

        self.assertEqual(weekly_targets, ["20260508", "20260515"])
        self.assertEqual(weekly_volatile, {"20260515"})
        self.assertEqual(monthly_targets, ["20260430", "20260515"])
        self.assertEqual(monthly_volatile, {"20260515"})

    def test_run_selected_interface_supports_weekly_only(self):
        with mock.patch.object(updater, "update_weekly", return_value={"ok": True}) as weekly_mock, \
            mock.patch.object(updater, "update_monthly") as monthly_mock:
            result = updater.run_selected_interface("weekly", n_periods=5, verbose=False)

        weekly_mock.assert_called_once_with(5, False, bypass_whitelist=False, fetch_all=False, target_dates=None)
        monthly_mock.assert_not_called()
        self.assertEqual(result, {"weekly": {"ok": True}})

    def test_parse_args_supports_single_interface_backfill(self):
        args = updater.parse_args(["--interface", "monthly", "--periods", "6", "--quiet"])
        self.assertEqual(args.interface, "monthly")
        self.assertEqual(args.periods, 6)
        self.assertTrue(args.quiet)

    def test_parse_args_supports_ignore_whitelist(self):
        args = updater.parse_args(["--interface", "weekly", "--ignore-whitelist"])
        self.assertEqual(args.interface, "weekly")
        self.assertTrue(args.ignore_whitelist)

    def test_parse_args_supports_all(self):
        args = updater.parse_args(["--interface", "both", "--all"])
        self.assertEqual(args.interface, "both")
        self.assertTrue(args.all)

    def test_parse_args_supports_trade_dates(self):
        args = updater.parse_args(["--interface", "weekly", "--trade-dates", "20250516,20250523,20250530"])
        self.assertEqual(args.interface, "weekly")
        self.assertEqual(args.trade_dates, "20250516,20250523,20250530")

    def test_run_selected_interface_forwards_bypass_whitelist(self):
        with mock.patch.object(updater, "update_weekly", return_value={"ok": True}) as weekly_mock:
            result = updater.run_selected_interface(
                "weekly",
                n_periods=4,
                verbose=False,
                bypass_whitelist=True,
            )

        weekly_mock.assert_called_once_with(4, False, bypass_whitelist=True, fetch_all=False, target_dates=None)
        self.assertEqual(result, {"weekly": {"ok": True}})

    def test_run_selected_interface_forwards_fetch_all(self):
        with mock.patch.object(updater, "update_monthly", return_value={"ok": True}) as monthly_mock:
            result = updater.run_selected_interface(
                "monthly",
                n_periods=4,
                verbose=False,
                fetch_all=True,
            )

        monthly_mock.assert_called_once_with(4, False, bypass_whitelist=False, fetch_all=True, target_dates=None)
        self.assertEqual(result, {"monthly": {"ok": True}})

    def test_run_selected_interface_forwards_target_dates(self):
        with mock.patch.object(updater, "update_weekly", return_value={"ok": True}) as weekly_mock:
            result = updater.run_selected_interface(
                "weekly",
                n_periods=4,
                verbose=False,
                target_dates=["20250516", "20250523", "20250530"],
            )

        weekly_mock.assert_called_once_with(
            4,
            False,
            bypass_whitelist=False,
            fetch_all=False,
            target_dates=["20250516", "20250523", "20250530"],
        )
        self.assertEqual(result, {"weekly": {"ok": True}})

    def test_update_period_fetches_market_by_period_and_marks_whitelist(self):
        frames = {
            "20260410": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260410", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10, "amount": 20},
                    {"ts_code": "000002.SZ", "trade_date": "20260410", "open": 3, "high": 4, "low": 3, "close": 4, "vol": 30, "amount": 40},
                ]
            ),
            "20260417": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260417", "open": 5, "high": 6, "low": 5, "close": 6, "vol": 50, "amount": 60},
                ]
            ),
        }
        fake_pro = FakeWeeklyMonthlyPro(frames)
        period_map = {20260410: 20260410, 20260417: 20260417}

        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            data_dir = Path(tmpdir) / "weekly"
            data_dir.mkdir(parents=True)

            with mock.patch.object(updater, "pro", fake_pro), \
                mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                result = updater.update_period(
                    "weekly",
                    data_dir,
                    "weekly",
                    period_map,
                    n_periods=2,
                    verbose=False,
                )
                payload = autofill_runtime._load_interface_whitelist()

            self.assertEqual(fake_pro.calls, [("20260410", "week"), ("20260417", "week")])
            self.assertEqual(result["fetched_periods"], 2)
            self.assertEqual(result["failed_periods"], [])
            self.assertTrue((data_dir / "weekly_000001.SZ.csv").exists())
            self.assertTrue((data_dir / "weekly_000002.SZ.csv").exists())
            self.assertEqual(payload["weekly"]["validated_mode"], "stk_weekly_monthly_period_batch")
            self.assertEqual(
                payload["weekly"]["validated_intervals"],
                [
                    {"start": "20260410", "end": "20260410"},
                    {"start": "20260417", "end": "20260417"},
                ],
            )

    def test_update_period_skips_whitelisted_periods(self):
        period_map = {20260410: 20260410, 20260417: 20260417}
        fake_pro = FakeWeeklyMonthlyPro({})

        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            data_dir = Path(tmpdir) / "monthly"
            data_dir.mkdir(parents=True)

            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                autofill_runtime._mark_interface_whitelisted(
                    "monthly",
                    latest_date="20260417",
                    mode="full",
                    calendar_dates=["20260410", "20260417"],
                )

                with mock.patch.object(updater, "pro", fake_pro):
                    result = updater.update_period(
                        "monthly",
                        data_dir,
                        "monthly",
                        period_map,
                        n_periods=2,
                        verbose=False,
                    )

            self.assertEqual(fake_pro.calls, [])
            self.assertEqual(result["fetched_periods"], 0)
            self.assertEqual(result["skipped_whitelist_periods"], 2)

    def test_update_period_can_bypass_whitelist_and_force_refetch(self):
        period_map = {20260410: 20260410, 20260417: 20260417}
        frames = {
            "20260410": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260410", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10, "amount": 20},
                ]
            ),
            "20260417": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260417", "open": 3, "high": 4, "low": 3, "close": 4, "vol": 30, "amount": 40},
                ]
            ),
        }
        fake_pro = FakeWeeklyMonthlyPro(frames)

        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            data_dir = Path(tmpdir) / "weekly"
            data_dir.mkdir(parents=True)

            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                autofill_runtime._mark_interface_whitelisted(
                    "weekly",
                    latest_date="20260417",
                    mode="full",
                    calendar_dates=["20260410", "20260417"],
                )
                with mock.patch.object(updater, "pro", fake_pro):
                    result = updater.update_period(
                        "weekly",
                        data_dir,
                        "weekly",
                        period_map,
                        n_periods=2,
                        verbose=False,
                        bypass_whitelist=True,
                    )

            self.assertEqual(fake_pro.calls, [("20260410", "week"), ("20260417", "week")])
            self.assertEqual(result["fetched_periods"], 2)
            self.assertEqual(result["skipped_whitelist_periods"], 0)

    def test_update_period_fetch_all_ignores_period_limit(self):
        period_map = {20260403: 20260403, 20260410: 20260410, 20260417: 20260417}
        frames = {
            "20260403": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260403", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10, "amount": 20},
                ]
            ),
            "20260410": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260410", "open": 3, "high": 4, "low": 3, "close": 4, "vol": 30, "amount": 40},
                ]
            ),
            "20260417": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260417", "open": 5, "high": 6, "low": 5, "close": 6, "vol": 50, "amount": 60},
                ]
            ),
        }
        fake_pro = FakeWeeklyMonthlyPro(frames)

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "weekly"
            data_dir.mkdir(parents=True)
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"

            with mock.patch.object(updater, "pro", fake_pro), \
                mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                result = updater.update_period(
                    "weekly",
                    data_dir,
                    "weekly",
                    period_map,
                    n_periods=1,
                    verbose=False,
                    fetch_all=True,
                )

        self.assertEqual(fake_pro.calls, [("20260403", "week"), ("20260410", "week"), ("20260417", "week")])
        self.assertEqual(result["requested_periods"], 3)
        self.assertEqual(result["fetched_periods"], 3)

    def test_update_period_can_fetch_specific_target_dates(self):
        frames = {
            "20250516": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20250516", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10, "amount": 20},
                ]
            ),
            "20250530": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20250530", "open": 3, "high": 4, "low": 3, "close": 4, "vol": 30, "amount": 40},
                ]
            ),
        }
        fake_pro = FakeWeeklyMonthlyPro(frames)
        period_map = {20250516: 20250516, 20250523: 20250523, 20250530: 20250530}

        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            data_dir = Path(tmpdir) / "weekly"
            data_dir.mkdir(parents=True)

            with mock.patch.object(updater, "pro", fake_pro), \
                mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                result = updater.update_period(
                    "weekly",
                    data_dir,
                    "weekly",
                    period_map,
                    n_periods=1,
                    verbose=False,
                    target_dates=["20250530", "20250516"],
                )

        self.assertEqual(fake_pro.calls, [("20250516", "week"), ("20250530", "week")])
        self.assertEqual(result["requested_periods"], 2)
        self.assertEqual(result["fetched_periods"], 2)

    def test_update_period_does_not_whitelist_live_snapshot_date(self):
        class FakeDateTime:
            @staticmethod
            def now():
                return real_datetime(2026, 5, 15, 15, 0, 0)

        period_map = {
            20260430: 20260430,
            20260514: 20260529,
            20260515: 20260529,
        }
        frames = {
            "20260430": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260430", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10, "amount": 20},
                ]
            ),
            "20260515": pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260515", "open": 3, "high": 4, "low": 3, "close": 4, "vol": 30, "amount": 40},
                ]
            ),
        }
        fake_pro = FakeWeeklyMonthlyPro(frames)

        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            data_dir = Path(tmpdir) / "monthly"
            data_dir.mkdir(parents=True)

            with mock.patch.object(updater, "pro", fake_pro), \
                mock.patch.object(updater, "datetime", FakeDateTime), \
                mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                result = updater.update_period(
                    "monthly",
                    data_dir,
                    "monthly",
                    period_map,
                    n_periods=3,
                    verbose=False,
                )
                payload = autofill_runtime._load_interface_whitelist()

        self.assertEqual(result["fetched_periods"], 2)
        self.assertEqual(
            payload["monthly"]["validated_intervals"],
            [{"start": "20260430", "end": "20260430"}],
        )

    def test_update_period_falls_back_to_weekly_when_stk_weekly_monthly_empty(self):
        period_map = {20260410: 20260410}
        fake_pro = FakeWeeklyMonthlyPro(
            {"20260410": pd.DataFrame()},
            weekly_frames_by_date={
                "20260410": pd.DataFrame(
                    [
                        {"ts_code": "000001.SZ", "trade_date": "20260410", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10, "amount": 20},
                    ]
                )
            },
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "weekly"
            data_dir.mkdir(parents=True)
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"

            with mock.patch.object(updater, "pro", fake_pro), \
                mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                result = updater.update_period(
                    "weekly",
                    data_dir,
                    "weekly",
                    period_map,
                    n_periods=1,
                    verbose=False,
                    bypass_whitelist=True,
                )

                self.assertTrue((data_dir / "weekly_000001.SZ.csv").exists())

        call_names = [name for name, _ in fake_pro.named_calls]
        self.assertEqual(call_names, ["stk_weekly_monthly", "weekly"])
        self.assertEqual(result["fetched_periods"], 1)

    def test_update_period_falls_back_to_monthly_with_pagination(self):
        period_map = {20260430: 20260430}
        first_page = pd.DataFrame(
            [{"ts_code": f"{i:06d}.SZ", "trade_date": "20260430", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10, "amount": 20} for i in range(2)]
        )
        second_page = pd.DataFrame(
            [{"ts_code": f"{2 + i:06d}.SZ", "trade_date": "20260430", "open": 3, "high": 4, "low": 3, "close": 4, "vol": 30, "amount": 40} for i in range(1)]
        )
        fake_pro = FakeWeeklyMonthlyPro(
            {"20260430": pd.DataFrame()},
            monthly_frames_by_date={"20260430": [first_page, second_page]},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "monthly"
            data_dir.mkdir(parents=True)
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"

            with mock.patch.object(updater, "pro", fake_pro), \
                mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path), \
                mock.patch.dict(updater.OFFICIAL_PERIOD_PAGE_LIMITS, {"monthly": 2}, clear=False):
                result = updater.update_period(
                    "monthly",
                    data_dir,
                    "monthly",
                    period_map,
                    n_periods=1,
                    verbose=False,
                    bypass_whitelist=True,
                )

        monthly_calls = [payload for name, payload in fake_pro.named_calls if name == "monthly"]
        self.assertEqual(len(monthly_calls), 2)
        self.assertEqual(monthly_calls[0]["limit"], 2)
        self.assertEqual(monthly_calls[0]["offset"], 0)
        self.assertEqual(monthly_calls[1]["offset"], 2)
        self.assertEqual(result["fetched_periods"], 1)
        self.assertEqual(result["written_codes"], 3)

    def test_primary_timeout_retries_then_succeeds(self):
        frame = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "20260410", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10, "amount": 20},
            ]
        )
        fake_pro = FakeRetryPro(
            primary_side_effects=[
                Exception("Read timed out"),
                Exception("Read timed out"),
                frame,
            ]
        )

        with mock.patch.object(updater, "pro", fake_pro), \
            mock.patch.object(updater.time, "sleep", return_value=None):
            result_frame, source = updater._fetch_market_period_data("weekly", "20260410")

        primary_calls = [name for name, _ in fake_pro.named_calls if name == "stk_weekly_monthly"]
        self.assertEqual(len(primary_calls), 3)
        self.assertEqual(source, "stk_weekly_monthly")
        self.assertEqual(len(result_frame), 1)

    def test_primary_non_timeout_error_does_not_retry(self):
        fake_pro = FakeRetryPro(primary_side_effects=[Exception("invalid token confirm")])

        with mock.patch.object(updater, "pro", fake_pro), \
            mock.patch.object(updater.time, "sleep", return_value=None):
            with self.assertRaises(Exception):
                updater._fetch_market_period_data("weekly", "20260410")

        primary_calls = [name for name, _ in fake_pro.named_calls if name == "stk_weekly_monthly"]
        self.assertEqual(len(primary_calls), 1)

    def test_fallback_timeout_retries_then_succeeds(self):
        frame = pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "trade_date": "20260430", "open": 1, "high": 2, "low": 1, "close": 2, "vol": 10, "amount": 20},
            ]
        )
        fake_pro = FakeRetryPro(
            primary_side_effects=[pd.DataFrame()],
            monthly_side_effects=[
                Exception("Read timed out"),
                frame,
            ],
        )

        with mock.patch.object(updater, "pro", fake_pro), \
            mock.patch.object(updater.time, "sleep", return_value=None):
            result_frame, source = updater._fetch_market_period_data("monthly", "20260430")

        monthly_calls = [name for name, _ in fake_pro.named_calls if name == "monthly"]
        self.assertEqual(len(monthly_calls), 2)
        self.assertEqual(source, "monthly")
        self.assertEqual(len(result_frame), 1)


if __name__ == "__main__":
    unittest.main()
