"""Tests for time_util.py"""
import unittest
from datetime import datetime, time

from time_util import (
    scenario_from_now,
    resolve_checkpoint,
    normalize_trade_date_text,
    parse_date_candidates,
    normalize_trade_date_for_session,
)


class TestScenarioFromNow(unittest.TestCase):
    """scenario_from_now: maps datetime to trading session label."""

    def test_pre_market(self):
        now = datetime(2025, 6, 15, 9, 14, 59)
        self.assertEqual(scenario_from_now(now), "盘前")

    def test_pre_market_exact_boundary(self):
        now = datetime(2025, 6, 15, 0, 0, 0)
        self.assertEqual(scenario_from_now(now), "盘前")

    def test_morning_session(self):
        now = datetime(2025, 6, 15, 9, 15, 0)
        self.assertEqual(scenario_from_now(now), "上午盘中")

    def test_morning_session_end(self):
        now = datetime(2025, 6, 15, 11, 30, 0)
        self.assertEqual(scenario_from_now(now), "上午盘中")

    def test_noon_break(self):
        now = datetime(2025, 6, 15, 11, 30, 1)
        self.assertEqual(scenario_from_now(now), "午间休盘")

    def test_noon_break_until_1300(self):
        now = datetime(2025, 6, 15, 12, 59, 59)
        self.assertEqual(scenario_from_now(now), "午间休盘")

    def test_afternoon_session(self):
        now = datetime(2025, 6, 15, 13, 0, 0)
        self.assertEqual(scenario_from_now(now), "下午盘中")

    def test_afternoon_session_end(self):
        now = datetime(2025, 6, 15, 15, 0, 0)
        self.assertEqual(scenario_from_now(now), "下午盘中")

    def test_post_market(self):
        now = datetime(2025, 6, 15, 15, 0, 1)
        self.assertEqual(scenario_from_now(now), "盘后")

    def test_late_night(self):
        now = datetime(2025, 6, 15, 23, 59, 59)
        self.assertEqual(scenario_from_now(now), "盘后")


class TestResolveCheckpoint(unittest.TestCase):
    """resolve_checkpoint: maps (now, trade_date, arg) to checkpoint string."""

    def test_pre_open_always_close(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        self.assertEqual(resolve_checkpoint(now, "2025-06-15", "pre_open"), "close")

    def test_noon_explicit(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        self.assertEqual(resolve_checkpoint(now, "2025-06-15", "noon"), "noon")

    def test_close_explicit(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        self.assertEqual(resolve_checkpoint(now, "2025-06-15", "close"), "close")

    def test_auto_pre_market(self):
        now = datetime(2025, 6, 15, 8, 0, 0)
        self.assertEqual(resolve_checkpoint(now, "2025-06-15", "auto"), "close")

    def test_auto_morning(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        self.assertEqual(resolve_checkpoint(now, "2025-06-15", "auto"), "open")

    def test_auto_noon(self):
        now = datetime(2025, 6, 15, 12, 0, 0)
        self.assertEqual(resolve_checkpoint(now, "2025-06-15", "auto"), "noon")

    def test_auto_afternoon(self):
        now = datetime(2025, 6, 15, 14, 0, 0)
        self.assertEqual(resolve_checkpoint(now, "2025-06-15", "auto"), "afternoon")

    def test_auto_post_market(self):
        now = datetime(2025, 6, 15, 16, 0, 0)
        self.assertEqual(resolve_checkpoint(now, "2025-06-15", "auto"), "close")

    def test_auto_past_trade_date_returns_next_close(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        self.assertEqual(
            resolve_checkpoint(now, "2025-06-10", "auto"), "next_close"
        )


class TestNormalizeTradeDateText(unittest.TestCase):
    """normalize_trade_date_text: normalizes various date formats."""

    def test_hyphenated_date(self):
        self.assertEqual(normalize_trade_date_text("2025-06-15"), "2025-06-15")

    def test_compact_date(self):
        self.assertEqual(normalize_trade_date_text("20250615"), "2025-06-15")

    def test_none_returns_none(self):
        self.assertIsNone(normalize_trade_date_text(None))

    def test_empty_string_returns_none(self):
        self.assertIsNone(normalize_trade_date_text(""))

    def test_whitespace_returns_none(self):
        self.assertIsNone(normalize_trade_date_text("   "))

    def test_invalid_format_returns_none(self):
        self.assertIsNone(normalize_trade_date_text("not-a-date"))

    def test_date_with_time_suffix(self):
        self.assertEqual(normalize_trade_date_text("2025-06-15 extra"), "2025-06-15")

    def test_numeric_input(self):
        self.assertEqual(normalize_trade_date_text(20250615), "2025-06-15")


class TestParseDateCandidates(unittest.TestCase):
    """parse_date_candidates: parses a list of date strings."""

    def test_mixed_formats(self):
        result = parse_date_candidates(["2025-06-15", None, "", "invalid", "2025-12-31"])
        self.assertEqual(result, ["2025-06-15", "2025-12-31"])

    def test_all_invalid(self):
        self.assertEqual(parse_date_candidates(["foo", "bar"]), [])

    def test_empty_input(self):
        self.assertEqual(parse_date_candidates([]), [])

    def test_truncates_long_strings(self):
        result = parse_date_candidates(["2025-06-15T10:00:00"])
        self.assertEqual(result, ["2025-06-15"])

    def test_single_valid(self):
        result = parse_date_candidates(["2025-06-15"])
        self.assertEqual(result, ["2025-06-15"])


class TestNormalizeTradeDateForSession(unittest.TestCase):
    """normalize_trade_date_for_session: pre-open date rollback logic."""

    def _mock_fn(self, date_text):
        return "2025-06-13"

    def test_non_adjusting_session(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        result, meta = normalize_trade_date_for_session(
            now, "2025-06-15", "auto"
        )
        self.assertEqual(result, "2025-06-15")
        self.assertFalse(meta["adjusted"])

    def test_pre_open_checkpoint_no_adjust_non_today(self):
        now = datetime(2025, 6, 15, 10, 0, 0)
        result, meta = normalize_trade_date_for_session(
            now, "2025-06-14", "pre_open",
            latest_open_trade_date_on_or_before_fn=self._mock_fn,
        )
        self.assertEqual(result, "2025-06-14")
        self.assertFalse(meta["adjusted"])

    def test_auto_pre_market_today_adjusts(self):
        now = datetime(2025, 6, 15, 8, 0, 0)
        result, meta = normalize_trade_date_for_session(
            now, "2025-06-15", "auto",
            latest_open_trade_date_on_or_before_fn=self._mock_fn,
        )
        self.assertEqual(result, "2025-06-13")
        self.assertTrue(meta["adjusted"])

    def test_auto_pre_market_non_today_no_adjust(self):
        now = datetime(2025, 6, 15, 8, 0, 0)
        result, meta = normalize_trade_date_for_session(
            now, "2025-06-14", "auto",
            latest_open_trade_date_on_or_before_fn=self._mock_fn,
        )
        self.assertEqual(result, "2025-06-14")
        self.assertFalse(meta["adjusted"])

    def test_auto_pre_market_missing_fn_raises(self):
        now = datetime(2025, 6, 15, 8, 0, 0)
        with self.assertRaises(RuntimeError):
            normalize_trade_date_for_session(
                now, "2025-06-15", "auto", latest_open_trade_date_on_or_before_fn=None,
            )

    def test_auto_pre_market_fn_returns_none(self):
        now = datetime(2025, 6, 15, 8, 0, 0)
        result, meta = normalize_trade_date_for_session(
            now, "2025-06-15", "auto",
            latest_open_trade_date_on_or_before_fn=lambda d: None,
        )
        self.assertEqual(result, "2025-06-15")
        self.assertFalse(meta["adjusted"])


if __name__ == "__main__":
    unittest.main()
