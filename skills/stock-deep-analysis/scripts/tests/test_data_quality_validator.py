"""数据质量校验器测试。

验证 validate_stock_data 对已知数据的检测结果。
使用真实 parquet 数据文件，固定日期确保可重复。
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SYMBOL = "000725.SZ"
TRADE_DATE = "20260526"  # 数据库最新可用日期


class TestDataQualityValidator(unittest.TestCase):
    """validate_stock_data 检测结果验证。"""

    def test_000725_cyq_chips_invalid(self):
        """000725.SZ 的 cyq_chips 应被检测为 invalid（全部 percent=0.01 占位值）。"""
        from data.validate_data_quality import validate_stock_data
        result = validate_stock_data(SYMBOL, TRADE_DATE)

        self.assertIn(result["status"], ("ok", "warnings", "critical"))
        cyq_check = next(
            (c for c in result["checks"] if c["data_type"] == "cyq_chips"), None
        )
        self.assertIsNotNone(cyq_check, "cyq_chips check not found in results")
        self.assertEqual(cyq_check["status"], "invalid")
        self.assertIn("percent", cyq_check["message"])

    def test_000725_cyq_perf_ok(self):
        """000725.SZ 的 cyq_perf 应检测通过（数据正常）。"""
        from data.validate_data_quality import validate_stock_data
        result = validate_stock_data(SYMBOL, TRADE_DATE)

        perf_check = next(
            (c for c in result["checks"] if c["data_type"] == "cyq_perf"), None
        )
        self.assertIsNotNone(perf_check, "cyq_perf check not found in results")
        self.assertEqual(perf_check["status"], "ok")

    def test_000725_daily_ok(self):
        """000725.SZ 的 daily 数据应检测通过。"""
        from data.validate_data_quality import validate_stock_data
        result = validate_stock_data(SYMBOL, TRADE_DATE)

        daily_check = next(
            (c for c in result["checks"] if c["data_type"] == "daily"), None
        )
        self.assertIsNotNone(daily_check, "daily check not found in results")
        self.assertEqual(daily_check["status"], "ok")

    def test_nonexistent_stock_returns_critical(self):
        """不存在的股票应返回 critical 状态（多个维度 missing）。"""
        from data.validate_data_quality import validate_stock_data
        result = validate_stock_data("999999.SZ", TRADE_DATE)

        self.assertEqual(result["status"], "critical")
        missing_count = sum(
            1 for c in result["checks"] if c["status"] == "missing"
        )
        self.assertGreaterEqual(missing_count, 2, "Expected at least 2 missing checks for nonexistent stock")

    def test_result_structure(self):
        """返回结构应包含 status、checks、summary。"""
        from data.validate_data_quality import validate_stock_data
        result = validate_stock_data(SYMBOL, TRADE_DATE)

        self.assertIn("status", result)
        self.assertIn("checks", result)
        self.assertIn("summary", result)
        self.assertIsInstance(result["checks"], list)
        self.assertGreater(len(result["checks"]), 0)
        for check in result["checks"]:
            self.assertIn("data_type", check)
            self.assertIn("status", check)
            self.assertIn("message", check)


if __name__ == "__main__":
    unittest.main()
