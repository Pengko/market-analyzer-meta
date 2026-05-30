#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT_SCRIPTS = Path(__file__).resolve().parents[1]
OPENCLAW_NEWS_SCRIPTS = (
    Path.home() / ".openclaw" / "skills" / "custom" / "market-news-intelligence" / "scripts"
)
if str(ROOT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(ROOT_SCRIPTS))
if str(OPENCLAW_NEWS_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(OPENCLAW_NEWS_SCRIPTS))

from runtime import news_runtime  # type: ignore
import run_news_pipeline  # type: ignore


class NewsFallbackSemanticsTests(unittest.TestCase):
    def test_run_news_pipeline_falls_back_to_local_capture(self) -> None:
        args = SimpleNamespace(
            executor="hermes",
            dry_run=False,
            symbol="600103.SH",
            trade_date="20260415",
            url=[],
            preset=["eastmoney"],
            keyword=[],
            center="stock",
            center_keyword=[],
            related_keyword=[],
            sector_keyword=[],
            market_keyword=[],
            stock_name="青山纸业",
            limit=4,
            per_page_limit=4,
            deep_open_limit=0,
            wait_ms=4000,
            detail_wait_ms=2500,
            timeout_ms=45000,
            headless=False,
            raw_output=None,
            output=None,
            agent="news-agent",
            session_id="news-agent:test",
            message_id="",
            request_id="",
            timeout=60,
        )
        raw_path = Path(tempfile.mkdtemp()) / "browser_news.json"
        payload = {"articles": [{"title": "x", "source": "test", "published_at": "2026-04-15 10:00", "content": "x", "url": "https://example.com"}]}

        with patch.object(
            run_news_pipeline, "run_capture_hermes", side_effect=RuntimeError("hermes failed")
        ), patch.object(
            run_news_pipeline, "run_capture", return_value=(payload, raw_path)
        ):
            raw, path, meta = run_news_pipeline._run_capture_with_fallback(args, "hermes")

        self.assertEqual(raw, payload)
        self.assertEqual(path, raw_path)
        self.assertTrue(meta["fallback_used"])
        self.assertEqual(meta["capture_executor"], "local")
        self.assertIn("hermes failed", meta["fallback_reason"])

    def test_auto_resolve_news_json_path_uses_latest_valid_cached_news(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            pipeline_root = tmp / "news_pipeline"
            browser_root = tmp / "browser_news"
            pipeline_root.mkdir(parents=True, exist_ok=True)
            browser_root.mkdir(parents=True, exist_ok=True)

            cached = pipeline_root / "news_pipeline_600103_2026-04-13.json"
            cached.write_text(
                json.dumps(
                    {
                        "news_sentiment": {
                            "status": "available",
                            "summary": "旧但有效的结构化新闻",
                            "main_sources": ["东方财富"],
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(news_runtime, "NEWS_PIPELINE_ROOT", pipeline_root), patch.object(
                news_runtime, "NEWS_BROWSER_ROOT", browser_root
            ), patch.object(
                news_runtime, "_load_stock_name", return_value="青山纸业"
            ), patch.object(
                news_runtime, "load_stock_basic_index", return_value={"600103.SH": {"industry": "AI硬件"}}
            ), patch.object(
                news_runtime.subprocess,
                "run",
                side_effect=[
                    SimpleNamespace(returncode=1, stdout="", stderr="hermes failed"),
                    SimpleNamespace(returncode=1, stdout="", stderr="local failed"),
                ],
            ):
                path, meta = news_runtime.auto_resolve_news_json_path(
                    "600103.SH", "2026-04-15"
                )

        self.assertEqual(path, str(cached))
        self.assertEqual(meta["status"], "generated")
        self.assertEqual(meta["source"], "latest_valid_cached_news")
        self.assertEqual(meta["requested_executor"], "hermes")


if __name__ == "__main__":
    unittest.main()
