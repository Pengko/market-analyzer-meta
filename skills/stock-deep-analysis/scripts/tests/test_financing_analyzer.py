"""Tests for financing_analyzer.py"""
import unittest
from unittest.mock import patch

from financing_analyzer import safe_float, resolve_symbol


class TestSafeFloat(unittest.TestCase):
    """safe_float: converts values to float or None."""

    def test_normal_string(self):
        self.assertEqual(safe_float("3.14"), 3.14)

    def test_integer_string(self):
        self.assertEqual(safe_float("100"), 100.0)

    def test_none_returns_none(self):
        self.assertIsNone(safe_float(None))

    def test_empty_returns_none(self):
        self.assertIsNone(safe_float(""))

    def test_invalid_string_returns_none(self):
        self.assertIsNone(safe_float("abc"))

    def test_whitespace_stripped(self):
        self.assertEqual(safe_float("  2.5  "), 2.5)

    def test_negative_number(self):
        self.assertEqual(safe_float("-1.23"), -1.23)

    def test_scientific_notation(self):
        self.assertEqual(safe_float("1e3"), 1000.0)


class TestResolveSymbol(unittest.TestCase):
    """resolve_symbol: returns code directly or looks up from parquet."""

    def test_pure_digits_returns_directly(self):
        self.assertEqual(resolve_symbol("600000"), "600000")

    def test_digits_with_suffix(self):
        self.assertEqual(resolve_symbol("600000.SH"), "600000.SH")

    def test_digits_with_sz(self):
        self.assertEqual(resolve_symbol("000001.SZ"), "000001.SZ")

    def test_non_digit_code_returns_directly(self):
        result = resolve_symbol("600000.SH")
        self.assertEqual(result, "600000.SH")

    @patch("financing_analyzer._read_single_parquet")
    def test_exact_name_match(self, mock_read):
        mock_read.return_value = [
            {"name": "浦发银行", "ts_code": "600000.SH"},
            {"name": "平安银行", "ts_code": "000001.SZ"},
        ]
        self.assertEqual(resolve_symbol("浦发银行"), "600000.SH")

    @patch("financing_analyzer._read_single_parquet")
    def test_fuzzy_name_match(self, mock_read):
        mock_read.return_value = [
            {"name": "中国平安", "ts_code": "601318.SH"},
        ]
        self.assertEqual(resolve_symbol("平安"), "601318.SH")

    @patch("financing_analyzer._read_single_parquet")
    def test_no_match_returns_original(self, mock_read):
        mock_read.return_value = [
            {"name": "浦发银行", "ts_code": "600000.SH"},
        ]
        self.assertEqual(resolve_symbol("不存在"), "不存在")

    @patch("financing_analyzer._read_single_parquet")
    def test_parquet_read_error_returns_original(self, mock_read):
        mock_read.side_effect = Exception("file not found")
        self.assertEqual(resolve_symbol("浦发银行"), "浦发银行")


if __name__ == "__main__":
    unittest.main()
