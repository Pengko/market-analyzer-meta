"""对标股角色测试。

验证 build_peer_linkage 返回正确角色信息。
使用真实数据，通过 DataSlicer 读取 parquet 文件。
"""
import sys
import time
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
# build_peer_linkage 内部 `from scripts.data.dataslicer import slice_all`
# 需要 scripts/ 的父目录在 sys.path 中
PARENT_DIR = SCRIPTS_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

SYMBOL = "000725.SZ"
TRADE_DATE = "2026-05-26"  # 数据库最新可用日期


class TestPeerLinkageRole(unittest.TestCase):
    """build_peer_linkage 角色验证。"""

    def test_peer_linkage_returns_valid_status(self):
        """peer linkage 应返回 available 或 manual_pending 状态。"""
        from decision.decision_engine import build_peer_linkage
        t0 = time.time()
        result = build_peer_linkage(SYMBOL, TRADE_DATE)
        elapsed = time.time() - t0

        self.assertLess(elapsed, 120.0, f"build_peer_linkage 耗时 {elapsed:.1f}s")
        self.assertIn(result["status"], ["available", "manual_pending"])

    def test_peer_linkage_has_target_position(self):
        """available 时应包含 target_position 字段。"""
        from decision.decision_engine import build_peer_linkage
        result = build_peer_linkage(SYMBOL, TRADE_DATE)

        if result["status"] == "available":
            self.assertIn("target_position", result)
            self.assertIn(
                result["target_position"], ["领先", "中位", "掉队"],
                f"Invalid target_position: {result['target_position']}"
            )

    def test_peer_linkage_has_peers(self):
        """available 时应包含 peers 列表。"""
        from decision.decision_engine import build_peer_linkage
        result = build_peer_linkage(SYMBOL, TRADE_DATE)

        if result["status"] == "available":
            self.assertIn("peers", result)
            self.assertIsInstance(result["peers"], list)
            self.assertGreater(len(result["peers"]), 0, "peers list is empty")
            # 每个 peer 应有 role 字段
            for peer in result["peers"]:
                self.assertIn("role", peer)
                self.assertIn("symbol", peer)
                self.assertIn("daily_corr", peer)

    def test_peer_linkage_has_primary_sector(self):
        """available 时应包含 primary_sector。"""
        from decision.decision_engine import build_peer_linkage
        result = build_peer_linkage(SYMBOL, TRADE_DATE)

        if result["status"] == "available":
            self.assertIn("primary_sector", result)
            self.assertIsInstance(result["primary_sector"], str)
            self.assertGreater(len(result["primary_sector"]), 0)

    def test_nonexistent_stock_manual_pending(self):
        """不存在的股票应返回 manual_pending。"""
        from decision.decision_engine import build_peer_linkage
        result = build_peer_linkage("999999.SZ", TRADE_DATE)

        self.assertEqual(result["status"], "manual_pending")


if __name__ == "__main__":
    unittest.main()
