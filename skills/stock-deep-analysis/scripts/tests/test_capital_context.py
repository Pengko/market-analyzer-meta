"""Tests for capital_context.py"""
import unittest
from unittest.mock import patch

from capital_context import is_event_theme, summarize_capital_freshness


class TestIsEventTheme(unittest.TestCase):
    """is_event_theme: checks if a theme name matches known event patterns."""

    def test_known_theme_exact(self):
        self.assertTrue(is_event_theme("回购"))

    def test_known_theme_exact2(self):
        self.assertTrue(is_event_theme("增持"))

    def test_known_theme_full(self):
        self.assertTrue(is_event_theme("回购增持再贷款"))

    def test_known_theme_margin(self):
        self.assertTrue(is_event_theme("融资融券"))

    def test_known_theme_merger(self):
        self.assertTrue(is_event_theme("并购重组"))

    def test_keyword_in_text(self):
        self.assertTrue(is_event_theme("某公司回购计划"))

    def test_keyword_chizeng(self):
        self.assertTrue(is_event_theme("大股东增持"))

    def test_keyword_binggou(self):
        self.assertTrue(is_event_theme("重大并购"))

    def test_keyword_chongzu(self):
        self.assertTrue(is_event_theme("资产重组"))

    def test_empty_string(self):
        self.assertFalse(is_event_theme(""))

    def test_none_returns_false(self):
        self.assertFalse(is_event_theme(None))

    def test_unrelated_theme(self):
        self.assertFalse(is_event_theme("新能源"))

    def test_whitespace_stripped(self):
        self.assertTrue(is_event_theme("  回购  "))


class TestSummarizeCapitalFreshness(unittest.TestCase):
    """summarize_capital_freshness: summarizes next-day capital signals."""

    def test_unavailable_status(self):
        result = summarize_capital_freshness({"status": "missing"})
        self.assertEqual(result["status"], "unavailable")

    def test_unavailable_with_reason(self):
        result = summarize_capital_freshness({
            "status": "missing",
            "reason": "custom reason",
        })
        self.assertEqual(result["summary"], "custom reason")

    def test_available_empty_signals(self):
        data = {
            "status": "available",
            "result": {
                "features": {},
                "signals": [],
            },
        }
        result = summarize_capital_freshness(data)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["label"], "中性待确认")

    def test_positive_only(self):
        data = {
            "status": "available",
            "result": {
                "features": {},
                "signals": ["新增主导资金介入明显"],
            },
        }
        result = summarize_capital_freshness(data)
        self.assertEqual(result["label"], "偏新资金介入")

    def test_negative_only(self):
        data = {
            "status": "available",
            "result": {
                "features": {},
                "signals": ["主力派发明显"],
            },
        }
        result = summarize_capital_freshness(data)
        self.assertEqual(result["label"], "偏派发分歧")

    def test_mixed_signals(self):
        data = {
            "status": "available",
            "result": {
                "features": {},
                "signals": ["新资金介入", "主力派发"],
            },
        }
        result = summarize_capital_freshness(data)
        self.assertEqual(result["label"], "新老资金换手")

    def test_bullish_candle_in_summary(self):
        data = {
            "status": "available",
            "result": {
                "features": {"is_bullish_candle": True},
                "signals": [],
            },
        }
        result = summarize_capital_freshness(data)
        self.assertIn("阳线", result["summary"])

    def test_leaderboard_in_summary(self):
        data = {
            "status": "available",
            "result": {
                "features": {
                    "leaderboard_context": {
                        "is_listed": True,
                        "top_list_net_rate": 12.5,
                    },
                },
                "signals": [],
            },
        }
        result = summarize_capital_freshness(data)
        self.assertIn("龙虎榜", result["summary"])

    def test_amount_ratio_in_summary(self):
        data = {
            "status": "available",
            "result": {
                "features": {"amount_ratio_vs_prev1": 1.5},
                "signals": [],
            },
        }
        result = summarize_capital_freshness(data)
        self.assertIn("成交额比前一日", result["summary"])

    def test_signals_truncated_to_2(self):
        data = {
            "status": "available",
            "result": {
                "features": {},
                "signals": [
                    "新增主导资金介入",
                    "新资金关注",
                    "量价协同较强",
                ],
            },
        }
        result = summarize_capital_freshness(data)
        self.assertEqual(len(result["signals"]), 2)


if __name__ == "__main__":
    unittest.main()
