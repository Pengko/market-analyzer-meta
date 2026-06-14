"""Smoke test: build_payload 完成核心管线不抛异常。

Mock 策略:
- _phase2_parallel: 返回最小化模拟结果，避免并行 Agent 的文件/网络依赖
- llm_judge: 返回固定 JSON，避免 LLM 网络调用
- 其余函数（freshness、mixed_trade_date_context 等）使用真实数据
"""
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 确保 scripts/ 在 sys.path 中
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
# build_peer_linkage 内部 `from scripts.data.dataslicer import slice_all`
# 需要 scripts/ 的父目录在 sys.path 中
PARENT_DIR = SCRIPTS_DIR.parent
if str(PARENT_DIR) not in sys.path:
    sys.path.insert(0, str(PARENT_DIR))

SYMBOL = "000725.SZ"
TRADE_DATE = "2026-05-26"  # 数据库中最新可用日期


def _mock_parallel_results(*args, **kwargs):
    """返回最小化的并行结果，覆盖所有 Agent 返回键。"""
    return {
        "kline_sync": {"kline_sync": {"status": "ok"}, "factor_sync": {"status": "ok"}},
        "news": {
            "resolved_news_json_path": None,
            "narrative_context": {},
            "manual_news_raw": {},
            "news_pipeline_meta": {},
        },
        "intraday": {"intraday": {"status": "unavailable", "reason": "mocked"}},
        "sector": {
            "market_context": {"status": "ok", "market_bias": "mocked"},
            "sector_context": {"status": "ok", "summary": "mocked"},
        },
        "stock_dims": {
            "financing_context": {"status": "ok"},
            "auction_intent": {"status": "ok"},
            "trend_structure": {"status": "ok", "score": 0},
            "chip_structure": {"status": "ok", "score": 0},
            "volatility_context": {"status": "ok", "score": 0},
            "fundamental": {"status": "ok"},
        },
        "dragon_tiger": {"status": "ok", "signal": None, "overall_score": None},
        "intraday_linkage": {"status": "ok", "linkage_label": "mocked"},
        "fundamental_deep": {"status": "ok", "financial_health": "mocked"},
    }


def _mock_llm_judge(*args, **kwargs):
    """固定 LLM 返回值，避免网络调用。"""
    return {
        "decision": "仅适合观察",
        "bullish_dimensions": [],
        "bearish_dimensions": [],
        "conflicts": [],
        "preconditions": [],
        "invalidations": [],
        "key_levels": {},
        "reasoning": "mocked",
    }


def _mock_peer_linkage(*args, **kwargs):
    """固定 peer_linkage 返回值，避免 DataSlicer 的重文件 I/O。"""
    return {
        "status": "available",
        "primary_sector": "mocked",
        "alignment": "单一板块主导",
        "target_position": "中位",
        "target_pct_chg": 0.0,
        "concept_count": 1,
        "peer_count": 1,
        "peers": [],
        "summary": "mocked",
        "source": "mocked",
        "confidence": "中",
    }


class TestBuildPayloadSmoke(unittest.TestCase):
    """build_payload 核心管线冒烟测试。"""

    def test_build_payload_completes(self):
        """build_payload 应在合理时间内完成，返回包含必要字段的 payload。"""
        from llm.llm_client import llm_judge as real_llm_judge

        t0 = time.time()
        with (
            patch("build_stock_report._phase2_parallel", side_effect=_mock_parallel_results),
            patch("llm.llm_client.llm_judge", side_effect=_mock_llm_judge),
            patch("build_stock_report.build_peer_linkage", side_effect=_mock_peer_linkage),
        ):
            from build_stock_report import build_payload
            payload = build_payload(SYMBOL, TRADE_DATE, checkpoint="close")
        elapsed = time.time() - t0

        # 超时保护：120 秒
        self.assertLess(elapsed, 120.0, f"build_payload 耗时 {elapsed:.1f}s，超过 120s 阈值")

        # 必要字段存在
        required_fields = [
            "symbol", "stock_name", "trade_date", "checkpoint",
            "market_context", "sector_context", "chip_structure",
            "final_decision", "freshness", "decision_layer",
        ]
        for field in required_fields:
            self.assertIn(field, payload, f"Missing required field: {field}")

        # symbol 解析正确
        self.assertEqual(payload["symbol"], SYMBOL)

        # chip_structure 结构
        chip = payload.get("chip_structure", {})
        self.assertIn(chip.get("status"), ["ok", "available", "manual_pending", "missing"])

        # final_decision 包含 data_completeness
        fd = payload.get("final_decision", {})
        self.assertIn("data_completeness", fd)
        self.assertIsInstance(fd["data_completeness"], int)

        # dimension_results 存在且包含 peer_linkage
        dim = payload.get("dimension_results", {})
        self.assertIn("peer_linkage", dim)

        # decision_layer 结构完整
        dl = payload.get("decision_layer", {})
        self.assertIn("fused_signals", dl)
        self.assertIn("bull_report", dl)
        self.assertIn("bear_report", dl)
        self.assertIn("judge_verdict", dl)
        self.assertIn("portfolio_decision", dl)

    def test_build_payload_returns_dict(self):
        """build_payload 返回值应为 dict 类型。"""
        with (
            patch("build_stock_report._phase2_parallel", side_effect=_mock_parallel_results),
            patch("llm.llm_client.llm_judge", side_effect=_mock_llm_judge),
            patch("build_stock_report.build_peer_linkage", side_effect=_mock_peer_linkage),
        ):
            from build_stock_report import build_payload
            payload = build_payload(SYMBOL, TRADE_DATE, checkpoint="close")

        self.assertIsInstance(payload, dict)

    def test_build_payload_checkpoint_recorded(self):
        """checkpoint 应正确记录到 payload 中。"""
        with (
            patch("build_stock_report._phase2_parallel", side_effect=_mock_parallel_results),
            patch("llm.llm_client.llm_judge", side_effect=_mock_llm_judge),
            patch("build_stock_report.build_peer_linkage", side_effect=_mock_peer_linkage),
        ):
            from build_stock_report import build_payload
            payload = build_payload(SYMBOL, TRADE_DATE, checkpoint="close")

        self.assertEqual(payload["checkpoint"], "close")


if __name__ == "__main__":
    unittest.main()
