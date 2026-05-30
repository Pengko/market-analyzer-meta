"""筹码分析测试。

验证 analyze_chip_structure 基于 cyq_perf 的分析逻辑。
使用真实数据，固定日期确保可重复。
"""
import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

SYMBOL = "000725.SZ"
TRADE_DATE = "2026-05-26"  # 数据库最新可用日期


class TestChipAnalysis(unittest.TestCase):
    """analyze_chip_structure 分析结果验证。"""

    def test_analyze_chip_structure_000725(self):
        """000725.SZ 筹码分析应返回 available 状态和有效 details。"""
        from analysis.stock_trend_analyzer import analyze_chip_structure
        result = analyze_chip_structure(SYMBOL, TRADE_DATE)

        self.assertEqual(result["status"], "available")
        self.assertIn("winner_rate", result)
        self.assertIn("details", result)

        details = result["details"]
        self.assertIn("cost_5pct", details)
        self.assertIn("cost_95pct", details)
        self.assertIn("cost_concentration", details)

    def test_analyze_chip_structure_score_range(self):
        """筹码评分应在 [-3, 3] 范围内。"""
        from analysis.stock_trend_analyzer import analyze_chip_structure
        result = analyze_chip_structure(SYMBOL, TRADE_DATE)

        score = result.get("score", 0)
        self.assertGreaterEqual(score, -3, f"Score {score} below minimum -3")
        self.assertLessEqual(score, 3, f"Score {score} above maximum 3")

    def test_winner_rate_reasonable(self):
        """winner_rate 应在 0-100 范围内。"""
        from analysis.stock_trend_analyzer import analyze_chip_structure
        result = analyze_chip_structure(SYMBOL, TRADE_DATE)

        wr = result.get("winner_rate")
        if wr is not None:
            self.assertGreaterEqual(wr, 0, "winner_rate < 0")
            self.assertLessEqual(wr, 100, "winner_rate > 100")

    def test_cost_percentiles_ordered(self):
        """cost_5pct <= cost_50pct <= cost_95pct 应成立。"""
        from analysis.stock_trend_analyzer import analyze_chip_structure
        result = analyze_chip_structure(SYMBOL, TRADE_DATE)

        details = result.get("details", {})
        c5 = details.get("cost_5pct")
        c50 = details.get("cost_50pct")
        c95 = details.get("cost_95pct")
        if c5 is not None and c50 is not None and c95 is not None:
            self.assertLessEqual(c5, c50, "cost_5pct > cost_50pct")
            self.assertLessEqual(c50, c95, "cost_50pct > cost_95pct")

    def test_missing_data_returns_manual_pending(self):
        """不存在的数据应返回 manual_pending。"""
        from analysis.stock_trend_analyzer import analyze_chip_structure
        result = analyze_chip_structure("999999.SZ", TRADE_DATE)

        self.assertEqual(result["status"], "manual_pending")


if __name__ == "__main__":
    unittest.main()
