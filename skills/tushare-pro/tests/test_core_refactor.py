#!/usr/bin/env python3
"""
主要作用:
- 为本轮重构新增的共享核心层提供最小回归测试
- 重点覆盖注册表装配、文件合并和去重等基础能力
"""

import tempfile
import unittest
import json
from pathlib import Path
from unittest import mock

import pandas as pd

import auto_fill_data
import fetch_theme_data_full
from core import autofill_workflow
from core import autofill_runtime
from core import calendar as core_calendar
from core import health as core_health
from core import theme_fillers
from core.files import append_to_csv, deduplicate_file, fast_merge_to_file, get_latest_date_fast
from core.health import check_interface_by_date, get_local_latest_date, scan_incomplete_records
from core.registry import (
    AUTO_FILL_STOCK_BY_STOCK_NAMES,
    INTERFACE_CONFIG,
    build_auto_fill_registry,
    list_auto_fill_interface_names,
)
from utils import tushare_client
from utils.tushare_client import classify_api_error, diagnose_api_connection


class CoreRefactorTests(unittest.TestCase):
    def test_theme_full_fetch_runs_whitelist_inspection_after_successful_fill(self):
        config = {"path": "theme_data/ths_daily", "root": "stock"}
        fill_fn = mock.Mock(return_value={"ok": True, "covered_target_date": True})
        inspect_result = {"complete": True}

        with mock.patch.object(fetch_theme_data_full.runtime, "_repair_single_interface", return_value=inspect_result) as repair_mock:
            result = fetch_theme_data_full.fetch_theme_interface(
                "ths_daily",
                config,
                ["20260427"],
                fill_fn,
            )

        self.assertTrue(result["complete"])
        fill_fn.assert_called_once_with(config, ["20260427"])
        repair_mock.assert_called_once_with(
            "ths_daily",
            config,
            ["20260427"],
            "20260427",
            code_type=None,
            execution_mode="full",
            bypass_whitelist=True,
            inspect_only=True,
        )

    def test_theme_full_fetch_skips_whitelist_inspection_when_fill_failed(self):
        config = {"path": "theme_data/dc_daily", "root": "stock"}
        fill_fn = mock.Mock(return_value={"ok": False, "covered_target_date": False})

        with mock.patch.object(fetch_theme_data_full.runtime, "_repair_single_interface") as repair_mock:
            result = fetch_theme_data_full.fetch_theme_interface(
                "dc_daily",
                config,
                ["20260427"],
                fill_fn,
            )

        self.assertFalse(result["complete"])
        fill_fn.assert_called_once_with(config, ["20260427"])
        repair_mock.assert_not_called()

    def test_theme_full_fetch_second_round_only_processes_pending_interfaces(self):
        task_a = {
            "name": "ths_index",
            "config": {"path": "theme_data/ths_index", "root": "stock"},
            "trade_dates": ["20260518"],
            "fill_fn": mock.Mock(),
        }
        task_b = {
            "name": "dc_daily",
            "config": {"path": "theme_data/dc_daily", "root": "stock"},
            "trade_dates": ["20260516", "20260519"],
            "fill_fn": mock.Mock(),
        }

        with mock.patch.object(
            fetch_theme_data_full,
            "fetch_theme_interface",
            side_effect=[
                {"ok": True, "complete": True, "inspect_result": {"complete": True}},
                {"ok": False, "complete": False, "inspect_result": None},
                {"ok": True, "complete": True, "inspect_result": {"complete": True}},
            ],
        ) as fetch_mock, mock.patch.object(fetch_theme_data_full, "repair_theme_interface") as repair_mock:
            result = fetch_theme_data_full.run_theme_rounds([task_a, task_b], max_rounds=2)

        self.assertTrue(result)
        self.assertEqual(fetch_mock.call_count, 3)
        fetch_mock.assert_any_call("ths_index", task_a["config"], ["20260518"], task_a["fill_fn"])
        fetch_mock.assert_any_call("dc_daily", task_b["config"], ["20260516", "20260519"], task_b["fill_fn"])
        repair_mock.assert_not_called()

    def test_health_normalize_date_value_accepts_iso_datetime(self):
        self.assertEqual(autofill_runtime._normalize_date_series(pd.Series(["2026-04-22 00:00:00"])).iloc[0], "20260422")
        self.assertEqual(core_health._normalize_date_value("2026-04-22 00:00:00"), "20260422")

    def test_is_likely_network_error_uses_api_error_classifier(self):
        self.assertTrue(autofill_runtime._is_likely_network_error(Exception("Read timed out")))
        self.assertTrue(autofill_runtime._is_likely_network_error(Exception("Max retries exceeded with url")))
        self.assertFalse(autofill_runtime._is_likely_network_error(Exception("您的token不对，请确认。")))

    def test_dc_concept_writes_daily_theme_files_without_aggregate_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            stock_dir.mkdir()
            pro_api = mock.Mock()
            pro_api.dc_concept.side_effect = [
                pd.DataFrame(
                    [
                        {"trade_date": "20260424", "name": "锂矿概念", "theme_code": "A"},
                        {"trade_date": "20260424", "name": "AI漫剧", "theme_code": "B"},
                    ]
                ),
                pd.DataFrame(),
            ]
            logs = []
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda msg, level="INFO": logs.append((level, msg)),
            )
            theme_fillers.fill_dc_concept_theme({"path": "theme_data/dc_concept"}, ["20260424"])

            out_dir = stock_dir / "theme_data" / "dc_concept"
            self.assertTrue((out_dir / "dc_concept_20260424.csv").exists())
            self.assertTrue((out_dir / "锂矿概念" / "锂矿概念_20260424.csv").exists())
            self.assertTrue((out_dir / "AI漫剧" / "AI漫剧_20260424.csv").exists())
            self.assertFalse((out_dir / "锂矿概念" / "锂矿概念.csv").exists())
            self.assertFalse((out_dir / "AI漫剧" / "AI漫剧.csv").exists())

    def test_dc_concept_theme_returns_success_when_target_day_fetched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            stock_dir.mkdir()
            pro_api = mock.Mock()
            pro_api.dc_concept.side_effect = [
                pd.DataFrame(),
                pd.DataFrame(),
                pd.DataFrame(
                    [
                        {
                            "trade_date": "20260428",
                            "name": "锂矿概念",
                            "theme_code": "A",
                            "pct_change": 1.0,
                            "hot": 1,
                            "sort": 1,
                            "strength": 1.0,
                            "z_t_num": 1,
                            "main_change": 0.1,
                            "lead_stock": "A",
                            "lead_stock_code": "000001.SZ",
                            "lead_stock_pct_change": 1.2,
                        }
                    ]
                ),
                pd.DataFrame(),
            ]
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )
            result = theme_fillers.fill_dc_concept_theme(
                {"path": "theme_data/dc_concept"},
                ["20260427", "20260428"],
            )

        self.assertEqual(result, {"ok": True, "covered_target_date": True})

    def test_kpl_concept_cons_theme_returns_success_when_target_day_fetched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            stock_dir.mkdir()
            pro_api = mock.Mock()
            pro_api.kpl_concept_cons.side_effect = [
                pd.DataFrame(
                    [
                        {
                            "ts_code": "000001.SZ",
                            "name": "平安银行",
                            "con_name": "金融",
                            "con_code": "C1",
                            "trade_date": "20260428",
                            "desc": "",
                            "hot_num": 1,
                        }
                    ]
                ),
                pd.DataFrame(),
            ]
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )
            result = theme_fillers.fill_kpl_concept_cons_theme(
                {"path": "theme_data/kpl_concept_cons"},
                ["20260428"],
            )

        self.assertEqual(result, {"ok": True, "covered_target_date": True})

    def test_ths_index_theme_writes_snapshot_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            stock_dir.mkdir()
            pro_api = mock.Mock()
            pro_api.ths_index.return_value = pd.DataFrame(
                [
                    {"ts_code": "885800.TI", "name": "OLED概念", "count": 92, "exchange": "A", "list_date": "20200101", "type": "N"},
                    {"ts_code": "885978.TI", "name": "人形机器人", "count": 50, "exchange": "A", "list_date": "20240101", "type": "TH"},
                ]
            )
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )

            result = theme_fillers.fill_ths_index_theme(
                {"path": "theme_data/ths_index"},
                ["20260516"],
            )

            out_dir = stock_dir / "theme_data" / "ths_index"
            self.assertTrue((out_dir / "ths_index_all.csv").exists())
            self.assertFalse((out_dir / "by_type").exists())
            self.assertEqual(result, {"ok": True, "covered_target_date": True})

    def test_ths_member_theme_writes_index_and_stock_views(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            (stock_dir / "theme_data" / "ths_index").mkdir(parents=True)
            pd.DataFrame(
                [
                    {"ts_code": "885800.TI", "name": "OLED概念", "type": "N"},
                ]
            ).to_csv(stock_dir / "theme_data" / "ths_index" / "ths_index_all.csv", index=False)
            pro_api = mock.Mock()
            pro_api.ths_member.return_value = pd.DataFrame(
                [
                    {"ts_code": "885800.TI", "con_code": "000001.SZ", "con_name": "平安银行"},
                    {"ts_code": "885800.TI", "con_code": "000002.SZ", "con_name": "万科A"},
                ]
            )
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )

            result = theme_fillers.fill_ths_member_theme(
                {"path": "theme_data/ths_member"},
                ["20260516"],
            )

            out_dir = stock_dir / "theme_data" / "ths_member"
            self.assertTrue((out_dir / "885800.TI_OLED概念.csv").exists())
            self.assertFalse((out_dir / "by_index").exists())
            self.assertFalse((out_dir / "by_stock").exists())
            self.assertEqual(result, {"ok": True, "covered_target_date": True})

    def test_dc_daily_theme_writes_daily_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            stock_dir.mkdir()
            pro_api = mock.Mock()
            pro_api.dc_daily.side_effect = [
                pd.DataFrame(
                    [
                        {"ts_code": "BK1063.DC", "trade_date": "20250513", "close": 1, "open": 1, "high": 1, "low": 1, "pct_change": 1, "vol": 1, "amount": 1},
                    ]
                ),
                pd.DataFrame(),
            ]
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )

            result = theme_fillers.fill_dc_daily_theme(
                {"path": "theme_data/dc_daily"},
                ["20250513"],
            )

            out_dir = stock_dir / "theme_data" / "dc_daily"
            self.assertTrue((out_dir / "dc_daily_20250513.csv").exists())
            self.assertFalse((out_dir / "by_date").exists())
            self.assertEqual(result, {"ok": True, "covered_target_date": True})

    def test_ths_daily_theme_writes_daily_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            stock_dir.mkdir()
            pro_api = mock.Mock()
            pro_api.ths_daily.return_value = pd.DataFrame(
                [
                    {"ts_code": "885800.TI", "trade_date": "20250513", "close": 1},
                    {"ts_code": "885978.TI", "trade_date": "20250513", "close": 2},
                ]
            )
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )

            result = theme_fillers.fill_ths_daily_theme(
                {"path": "theme_data/ths_daily"},
                ["20250513"],
            )

            out_dir = stock_dir / "theme_data" / "ths_daily"
            self.assertTrue((out_dir / "ths_daily_20250513.csv").exists())
            self.assertEqual(result, {"ok": True, "covered_target_date": True})

    def test_dc_index_theme_writes_latest_snapshot_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            stock_dir.mkdir()
            pro_api = mock.Mock()
            pro_api.dc_index.side_effect = [
                pd.DataFrame(
                    [
                        {"ts_code": "BK0475.DC", "name": "人形机器人", "trade_date": "20250513"},
                        {"ts_code": "BK1036.DC", "name": "AI眼镜", "trade_date": "20250513"},
                    ]
                ),
            ]
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )

            result = theme_fillers.fill_dc_index_theme(
                {"path": "theme_data/dc_index"},
                ["20250513"],
            )

            out_dir = stock_dir / "theme_data" / "dc_index"
            self.assertTrue((out_dir / "dc_index_all.csv").exists())
            self.assertEqual(result, {"ok": True, "covered_target_date": True})

    def test_dc_member_theme_writes_board_files_like_ths_member(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            (stock_dir / "theme_data" / "dc_index").mkdir(parents=True)
            pd.DataFrame(
                [
                    {"ts_code": "BK0475.DC", "name": "人形机器人", "trade_date": "20250513"},
                    {"ts_code": "BK1036.DC", "name": "AI眼镜", "trade_date": "20250513"},
                ]
            ).to_csv(stock_dir / "theme_data" / "dc_index" / "dc_index_all.csv", index=False)
            pro_api = mock.Mock()
            pro_api.dc_member.return_value = pd.DataFrame(
                [
                    {"ts_code": "BK0475.DC", "con_code": "300024.SZ", "trade_date": "20250513", "name": "三花智控"},
                    {"ts_code": "BK1036.DC", "con_code": "300458.SZ", "trade_date": "20250513", "name": "全志科技"},
                ]
            )
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )

            result = theme_fillers.fill_dc_member_theme(
                {"path": "theme_data/dc_member"},
                ["20250513"],
            )

            out_dir = stock_dir / "theme_data" / "dc_member"
            self.assertTrue((out_dir / "BK0475.DC_人形机器人.csv").exists())
            self.assertTrue((out_dir / "BK1036.DC_AI眼镜.csv").exists())
            self.assertEqual(result, {"ok": True, "covered_target_date": True})

    def test_dc_index_theme_falls_back_to_latest_available_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            stock_dir.mkdir()
            pro_api = mock.Mock()
            pro_api.dc_index.side_effect = [
                pd.DataFrame(),
                pd.DataFrame(
                    [
                        {"ts_code": "BK0475.DC", "name": "人形机器人", "trade_date": "20250512"},
                    ]
                ),
            ]
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )

            result = theme_fillers.fill_dc_index_theme(
                {"path": "theme_data/dc_index"},
                ["20250512", "20250513"],
            )

            out_file = stock_dir / "theme_data" / "dc_index" / "dc_index_all.csv"
            self.assertTrue(out_file.exists())
            frame = pd.read_csv(out_file)
            self.assertEqual(str(frame["trade_date"].iloc[0]), "20250512")
            self.assertEqual(result, {"ok": True, "covered_target_date": False})

    def test_append_to_csv_handles_zero_byte_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "daily_000001.SZ.csv"
            filepath.write_bytes(b"")
            frame = pd.DataFrame(
                [{"ts_code": "000001.SZ", "trade_date": "20260411", "close": 10.2}]
            )

            append_to_csv(filepath, frame)

            loaded = pd.read_csv(filepath)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(str(loaded["trade_date"].iloc[0]), "20260411")

    def test_fast_merge_to_file_appends_newer_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "daily_000001.SZ.csv"
            initial = pd.DataFrame(
                [{"ts_code": "000001.SZ", "trade_date": "20260410", "close": 1.0}]
            )
            initial.to_csv(filepath, index=False)

            newer = pd.DataFrame(
                [{"ts_code": "000001.SZ", "trade_date": "20260411", "close": 2.0}]
            )
            action = fast_merge_to_file(filepath, newer, date_col="trade_date")

            self.assertEqual(action, "appended")
            merged = pd.read_csv(filepath)
            self.assertEqual(len(merged), 2)
            self.assertEqual(str(merged["trade_date"].iloc[-1]), "20260411")

    def test_get_latest_date_fast_reads_tail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "moneyflow_000001.SZ.csv"
            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260410"},
                    {"ts_code": "000001.SZ", "trade_date": "20260411"},
                ]
            ).to_csv(filepath, index=False)
            self.assertEqual(get_latest_date_fast(filepath), "20260411")

    def test_deduplicate_file_keeps_last(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = Path(tmpdir) / "index_weight_20260411.csv"
            pd.DataFrame(
                [
                    {"index_code": "000300.SH", "con_code": "600000.SH", "trade_date": "20260411", "weight": 1},
                    {"index_code": "000300.SH", "con_code": "600000.SH", "trade_date": "20260411", "weight": 2},
                ]
            ).to_csv(filepath, index=False)

            removed = deduplicate_file(
                filepath, ["index_code", "con_code", "trade_date"], keep="last"
            )

            self.assertEqual(removed, 1)
            deduped = pd.read_csv(filepath)
            self.assertEqual(len(deduped), 1)
            self.assertEqual(int(deduped["weight"].iloc[0]), 2)

    def test_build_auto_fill_registry_returns_expected_buckets(self):
        registry = build_auto_fill_registry()
        self.assertIn("stock", registry)
        self.assertIn("index", registry)
        self.assertIn("daily", registry["stock"]["by_date"])
        self.assertIn("moneyflow_ths", registry["stock"]["by_date"])
        self.assertIn("cyq_chips", registry["stock"]["by_stock"])
        self.assertIn("index_weight", registry["index"]["by_date"])
        self.assertIn("index_daily", registry["index"]["by_index"])
        self.assertNotIn("rt_idx_k", registry["index"]["by_date"])
        self.assertIn("ths_index", registry["stock"]["by_date"])
        self.assertIn("ths_member", registry["stock"]["by_date"])
        self.assertIn("ths_daily", registry["stock"]["by_date"])
        self.assertIn("dc_daily", registry["stock"]["by_date"])
        self.assertIn("dc_index", registry["stock"]["by_date"])
        self.assertIn("dc_member", registry["stock"]["by_date"])

    def test_canonical_auto_fill_interface_count_is_stable(self):
        names = list_auto_fill_interface_names()
        self.assertEqual(len(names), 37)
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("daily", names)
        self.assertNotIn("income", names)
        self.assertNotIn("cashflow", names)
        self.assertNotIn("balancesheet", names)
        self.assertNotIn("fina_indicator", names)
        self.assertNotIn("pledge_detail", names)
        self.assertIn("pledge_stat", names)
        self.assertNotIn("fina_mainbz", names)
        self.assertIn("moneyflow_ths", names)
        self.assertIn("index_monthly", names)
        self.assertIn("stk_mins", names)
        self.assertIn("ths_index", names)
        self.assertIn("ths_member", names)
        self.assertIn("ths_daily", names)
        self.assertIn("dc_daily", names)
        self.assertIn("dc_index", names)
        self.assertIn("dc_member", names)
        self.assertNotIn("stk_high_shock", names)
        self.assertNotIn("trade_cal", names)

    def test_workflow_tasks_use_canonical_interface_list(self):
        registry = build_auto_fill_registry()
        with mock.patch.object(autofill_workflow, "STOCK_INTERFACE_CONFIG", registry["stock"]), \
            mock.patch.object(autofill_workflow, "INDEX_INTERFACE_CONFIG", registry["index"]):
            task_names = [name for name, _, _ in autofill_workflow._build_interface_tasks()]

        self.assertEqual(task_names, list_auto_fill_interface_names(registry))
        self.assertEqual(len(task_names), 37)

    def test_workflow_tasks_respect_selected_interfaces_filter(self):
        registry = build_auto_fill_registry()
        with mock.patch.object(autofill_workflow, "STOCK_INTERFACE_CONFIG", registry["stock"]), \
            mock.patch.object(autofill_workflow, "INDEX_INTERFACE_CONFIG", registry["index"]), \
            mock.patch.object(autofill_workflow, "SELECTED_INTERFACES", ["daily", "dc_index"]):
            task_names = [name for name, _, _ in autofill_workflow._build_interface_tasks()]

        self.assertEqual(task_names, ["daily", "dc_index"])

    def test_theme_snapshot_interfaces_only_use_latest_day_in_latest_mode(self):
        registry = build_auto_fill_registry()
        names = ["ths_index", "ths_member", "dc_index", "dc_member"]
        for name in names:
            if name in registry["stock"]["by_date"]:
                config = registry["stock"]["by_date"][name]
            else:
                self.fail(f"{name} not found in stock by_date registry")
            self.assertEqual(config.get("latest_trade_days_override"), 1)

    def test_dc_member_only_uses_latest_day_in_full_mode(self):
        registry = build_auto_fill_registry()
        config = registry["stock"]["by_date"]["dc_member"]
        self.assertEqual(config.get("full_trade_days_override"), 1)

        calendar = ["20260422", "20260423", "20260518"]
        resolved = autofill_workflow._resolve_full_interface_trade_calendar(config, calendar)
        self.assertEqual(resolved, ["20260518"])

    def test_ths_member_only_uses_latest_day_in_full_mode(self):
        registry = build_auto_fill_registry()
        config = registry["stock"]["by_date"]["ths_member"]
        self.assertEqual(config.get("full_trade_days_override"), 1)

        calendar = ["20260422", "20260423", "20260518"]
        resolved = autofill_workflow._resolve_full_interface_trade_calendar(config, calendar)
        self.assertEqual(resolved, ["20260518"])

    def test_fill_dc_member_prefers_trade_date_only_fetch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            stock_dir.mkdir()
            pro_api = mock.Mock()
            pro_api.dc_member.side_effect = [
                pd.DataFrame(
                    [
                        {"trade_date": "20260518", "ts_code": "BK0001.DC", "con_code": "000001.SZ", "name": "平安银行"},
                        {"trade_date": "20260518", "ts_code": "BK0001.DC", "con_code": "000002.SZ", "name": "万科A"},
                    ]
                )
            ]
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )

            result = theme_fillers.fill_dc_member_theme(
                {"path": "theme_data/dc_member", "page_limit": 5000, "max_pages": 2},
                ["20260518"],
            )

            self.assertEqual(result, {"ok": True, "covered_target_date": True})
            pro_api.dc_member.assert_called_once_with(trade_date="20260518", limit=5000, offset=0)

    def test_fill_dc_member_supports_multi_page_trade_date_fetch(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            (stock_dir / "theme_data" / "dc_index").mkdir(parents=True)
            pd.DataFrame(
                [
                    {"ts_code": "BK0001.DC", "name": "板块一", "trade_date": "20260518"},
                    {"ts_code": "BK0002.DC", "name": "板块二", "trade_date": "20260518"},
                ]
            ).to_csv(stock_dir / "theme_data" / "dc_index" / "dc_index_all.csv", index=False)
            pro_api = mock.Mock()
            pro_api.dc_member.side_effect = [
                pd.DataFrame(
                    [
                        {"trade_date": "20260518", "ts_code": "BK0001.DC", "con_code": "000001.SZ", "name": "平安银行"},
                    ]
                    * 5000
                ),
                pd.DataFrame(
                    [
                        {"trade_date": "20260518", "ts_code": "BK0002.DC", "con_code": "000002.SZ", "name": "万科A"},
                    ]
                ),
            ]
            theme_fillers.initialize_theme_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                get_root_dir_fn=lambda config: stock_dir,
                log_fn=lambda *args, **kwargs: None,
            )

            result = theme_fillers.fill_dc_member_theme(
                {"path": "theme_data/dc_member", "page_limit": 5000, "max_pages": 20},
                ["20260518"],
            )

            self.assertEqual(result, {"ok": True, "covered_target_date": True})
            self.assertEqual(pro_api.dc_member.call_args_list[0], mock.call(trade_date="20260518", limit=5000, offset=0))
            self.assertEqual(pro_api.dc_member.call_args_list[1], mock.call(trade_date="20260518", limit=5000, offset=5000))
            out_dir = stock_dir / "theme_data" / "dc_member"
            self.assertTrue((out_dir / "BK0001.DC_板块一.csv").exists())
            self.assertTrue((out_dir / "BK0002.DC_板块二.csv").exists())

    def test_theme_full_fetch_respects_full_trade_days_override(self):
        pro = object()
        config = {"full_trade_days_override": 1}
        with mock.patch.object(
            fetch_theme_data_full,
            "get_trade_dates",
            return_value=["20260515", "20260516", "20260518"],
        ) as trade_dates_mock:
            resolved = fetch_theme_data_full.resolve_interface_trade_dates(
                "dc_member",
                config,
                pro,
                "20200101",
                "20260518",
            )

        trade_dates_mock.assert_called_once_with(pro, start_date="20200101", end_date="20260518")
        self.assertEqual(resolved, ["20260518"])

    def test_theme_full_fetch_daily_interface_without_override_keeps_full_range(self):
        pro = object()
        config = {}
        with mock.patch.object(
            fetch_theme_data_full,
            "get_trade_dates",
            return_value=["20260515", "20260516", "20260518"],
        ):
            resolved = fetch_theme_data_full.resolve_interface_trade_dates(
                "dc_daily",
                config,
                pro,
                "20200101",
                "20260518",
            )

        self.assertEqual(resolved, ["20260515", "20260516", "20260518"])

    def test_deduplicate_interface_supports_ths_index_without_date_col(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            target_dir = stock_dir / "theme_data" / "ths_index"
            target_dir.mkdir(parents=True)
            csv_path = target_dir / "ths_index_all.csv"
            pd.DataFrame(
                [
                    {"ts_code": "885001.TI", "name": "概念A"},
                    {"ts_code": "885001.TI", "name": "概念A"},
                    {"ts_code": "885002.TI", "name": "概念B"},
                ]
            ).to_csv(csv_path, index=False)

            autofill_runtime.initialize_runtime(
                pro_api=mock.Mock(),
                data_dir=stock_dir,
                index_dir=stock_dir,
                financial_dir=stock_dir,
                log_fn=lambda *args, **kwargs: None,
                theme_handlers={},
            )

            removed = autofill_runtime.deduplicate_interface(
                "ths_index",
                {
                    "path": "theme_data/ths_index",
                    "root": "stock",
                    "prefix": "ths_index_",
                    "fixed_file_name": "ths_index_all.csv",
                },
            )

            self.assertEqual(removed, 1)
            frame = pd.read_csv(csv_path)
            self.assertEqual(len(frame), 2)

    def test_deduplicate_interface_supports_ths_member_without_explicit_dedup_cols(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            target_dir = stock_dir / "theme_data" / "ths_member"
            target_dir.mkdir(parents=True)
            csv_path = target_dir / "885001.TI_概念A.csv"
            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "con_code": "885001.TI", "con_name": "概念A"},
                    {"ts_code": "000001.SZ", "con_code": "885001.TI", "con_name": "概念A"},
                    {"ts_code": "000002.SZ", "con_code": "885001.TI", "con_name": "概念A"},
                ]
            ).to_csv(csv_path, index=False)

            autofill_runtime.initialize_runtime(
                pro_api=mock.Mock(),
                data_dir=stock_dir,
                index_dir=stock_dir,
                financial_dir=stock_dir,
                log_fn=lambda *args, **kwargs: None,
                theme_handlers={},
            )

            removed = autofill_runtime.deduplicate_interface(
                "ths_member",
                {
                    "path": "theme_data/ths_member",
                    "root": "stock",
                    "prefix": "",
                    "date_col": "in_date",
                    "dedup_cols": ["ts_code", "con_code"],
                },
            )

            self.assertEqual(removed, 1)
            frame = pd.read_csv(csv_path)
            self.assertEqual(len(frame), 2)

    def test_forecast_uses_announcement_date_daily_fetch(self):
        registry = build_auto_fill_registry()
        self.assertIn("forecast", registry["stock"]["by_date"])
        self.assertNotIn("forecast", registry["stock"]["by_stock"])
        config = registry["stock"]["by_date"]["forecast"]
        self.assertEqual(config.get("date_col"), "ann_date")
        self.assertEqual(config.get("save_granularity"), "year")
        self.assertFalse(config.get("use_pagination"))
        self.assertTrue(config.get("force_by_date"))
        self.assertTrue(config.get("use_date_range_fetch"))
        self.assertTrue(config.get("date_fetch_with_stock_pool"))
        self.assertEqual(config.get("code_param"), "ts_code")

    def test_trade_calendar_uses_only_is_open_dates(self):
        frame = pd.DataFrame(
            [
                {"cal_date": "20260424", "is_open": "1"},
                {"cal_date": "20260425", "is_open": "0"},
                {"cal_date": "20260427", "is_open": 1},
            ]
        )

        dates = core_calendar._extract_trade_dates(
            frame,
            start_date="20260424",
            end_date="20260427",
        )

        self.assertEqual(dates, ["20260424", "20260427"])

    def test_moneyflow_related_interfaces_have_consistent_health_and_paging_config(self):
        names = [
            "moneyflow_ths",
            "moneyflow_hsgt",
            "moneyflow_ind_ths",
            "moneyflow_cnt_ths",
            "moneyflow_mkt_dc",
            "repurchase",
        ]
        for name in names:
            cfg = INTERFACE_CONFIG[name]
            self.assertTrue(cfg.get("use_pagination"))
            self.assertGreaterEqual(int(cfg.get("page_limit", 0)), 1)
            self.assertGreaterEqual(int(cfg.get("max_pages", 0)), 1)
            self.assertGreaterEqual(int(cfg.get("health_recent_trade_days", 0)), 1)

    def test_individual_moneyflow_interfaces_use_year_date_layout(self):
        self.assertEqual(INTERFACE_CONFIG["moneyflow"].get("save_granularity"), "year_date")
        self.assertEqual(INTERFACE_CONFIG["moneyflow_ths"].get("save_granularity"), "year_date")
        self.assertTrue(INTERFACE_CONFIG["moneyflow"].get("force_by_date"))
        self.assertTrue(INTERFACE_CONFIG["moneyflow_ths"].get("force_by_date"))
        self.assertNotIn("moneyflow_dc", INTERFACE_CONFIG)

    def test_hm_detail_uses_year_date_layout(self):
        self.assertEqual(INTERFACE_CONFIG["hm_detail"].get("save_granularity"), "year_date")
        self.assertTrue(INTERFACE_CONFIG["hm_detail"].get("force_by_date"))

    def test_build_latest_mode_calendar_uses_recent_trade_window(self):
        trade_calendar = [
            "20260410",
            "20260411",
            "20260414",
            "20260415",
            "20260416",
            "20260417",
            "20260420",
            "20260421",
            "20260422",
            "20260423",
            "20260424",
        ]
        scoped = autofill_workflow.build_latest_mode_calendar(
            trade_calendar,
            "20260423",
            window_trade_days=10,
        )
        self.assertEqual(
            scoped,
            [
                "20260410",
                "20260411",
                "20260414",
                "20260415",
                "20260416",
                "20260417",
                "20260420",
                "20260421",
                "20260422",
                "20260423",
            ],
        )

    def test_build_latest_mode_calendar_clamps_to_available_dates(self):
        trade_calendar = ["20260421", "20260422", "20260423"]
        scoped = autofill_workflow.build_latest_mode_calendar(
            trade_calendar,
            "20260423",
            window_trade_days=10,
        )
        self.assertEqual(scoped, ["20260421", "20260422", "20260423"])

    def test_batched_closure_inspects_all_before_repairing_pending(self):
        tasks = [
            ("daily", {"path": "daily"}, None),
            ("moneyflow", {"path": "moneyflow"}, "stock"),
        ]
        events = []
        inspect_counts = {"moneyflow": 0}

        def repair_single(name, config, calendar, latest_date, **kwargs):
            if kwargs.get("inspect_only"):
                events.append(("inspect", name))
                inspect_counts[name] = inspect_counts.get(name, 0) + 1
                if name == "moneyflow" and inspect_counts[name] > 1:
                    return {
                        "complete": True,
                        "missing_dates": [],
                        "health_report": {"dates": [], "codes_by_date": {}, "empty_codes": []},
                    }
                return {
                    "complete": name == "daily",
                    "missing_dates": ["20260424"] if name != "daily" else [],
                    "health_report": {"dates": ["20260424"], "codes_by_date": {}, "empty_codes": []}
                    if name != "daily"
                    else {"dates": [], "codes_by_date": {}, "empty_codes": []},
                }
            events.append(("repair", name, kwargs.get("repair_only"), kwargs.get("initial_health_result") is not None))
            return True

        with mock.patch.object(autofill_workflow, "LOG"), \
            mock.patch.object(autofill_workflow, "REPAIR_SINGLE_INTERFACE", side_effect=repair_single):
            ok = autofill_workflow._run_batched_interface_closure(
                tasks,
                ["20260424"],
                "20260424",
                "full",
                False,
            )

        self.assertTrue(ok)
        self.assertEqual(
            events,
            [
                ("inspect", "daily"),
                ("inspect", "moneyflow"),
                ("repair", "moneyflow", True, True),
                ("inspect", "moneyflow"),
            ],
        )

    def test_batched_closure_repairs_all_pending_before_rechecking_any(self):
        tasks = [
            ("top_inst", {"path": "top_inst"}, None),
            ("repurchase", {"path": "repurchase"}, None),
        ]
        events = []
        inspect_counts = {}

        def repair_single(name, config, calendar, latest_date, **kwargs):
            if kwargs.get("inspect_only"):
                events.append(("inspect", name))
                inspect_counts[name] = inspect_counts.get(name, 0) + 1
                return {
                    "complete": inspect_counts[name] > 1,
                    "missing_dates": [] if inspect_counts[name] > 1 else ["20260424"],
                    "health_report": {"dates": [], "codes_by_date": {}, "empty_codes": []}
                    if inspect_counts[name] > 1
                    else {"dates": ["20260424"], "codes_by_date": {}, "empty_codes": []},
                }
            events.append(("repair", name, kwargs.get("repair_only")))
            return True

        with mock.patch.object(autofill_workflow, "LOG"), \
            mock.patch.object(autofill_workflow, "REPAIR_SINGLE_INTERFACE", side_effect=repair_single):
            ok = autofill_workflow._run_batched_interface_closure(
                tasks,
                ["20260424"],
                "20260424",
                "full",
                False,
            )

        self.assertTrue(ok)
        self.assertEqual(
            events,
            [
                ("inspect", "top_inst"),
                ("inspect", "repurchase"),
                ("repair", "top_inst", True),
                ("repair", "repurchase", True),
                ("inspect", "top_inst"),
                ("inspect", "repurchase"),
            ],
        )

    def test_direct_latest_fill_runs_all_interfaces_before_post_check(self):
        tasks = [
            ("daily", {"path": "daily"}, None),
            ("moneyflow", {"path": "moneyflow"}, "stock"),
        ]
        events = []

        def fake_dispatch(name, config, calendar, code_type=None, code_list=None):
            events.append(("fill", name, tuple(calendar), code_type))
            return True

        with mock.patch.object(autofill_workflow, "LOG"), \
            mock.patch.object(autofill_workflow, "DISPATCH_FILL", side_effect=fake_dispatch), \
            mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", return_value=None), \
            mock.patch.object(autofill_workflow, "GET_REPORT_TARGET_DATE", return_value="20260424"):
            autofill_workflow._run_direct_latest_fill(tasks, ["20260423", "20260424"])

        self.assertEqual(
            events,
            [
                ("fill", "daily", ("20260423", "20260424"), None),
                ("fill", "moneyflow", ("20260423", "20260424"), "stock"),
            ],
        )

    def test_latest_mode_fetches_before_closure_scan(self):
        logs = []
        events = []
        registry = {
            "stock": {"by_date": {"daily": {"path": "daily"}}, "by_stock": {}, "by_name": {}},
            "index": {"by_date": {}, "by_index": {}, "by_name": {}},
        }

        def fake_log(message, level="INFO"):
            logs.append((message, level))

        def fake_dispatch(name, config, calendar, code_type=None, code_list=None):
            events.append(("fill", name, tuple(calendar), code_type))
            return True

        def fake_closure(tasks, calendar, latest_date, execution_mode, explicit_full_mode):
            events.append(("closure", [name for name, _, _ in tasks], tuple(calendar), execution_mode))
            return True

        autofill_workflow.initialize_workflow(
            data_dir=Path("/tmp"),
            index_dir=Path("/tmp"),
            financial_dir=Path("/tmp"),
            stock_interface_config=registry["stock"],
            index_interface_config=registry["index"],
            log_fn=fake_log,
            get_trade_dates_fn=lambda: ["20260423", "20260424"],
            get_local_latest_date_fn=lambda name, config: "20260424",
            get_report_target_date_fn=lambda config, calendar: "20260424",
            resolve_run_target_trade_date_fn=lambda calendar: "20260424",
            repair_single_interface_fn=lambda *args, **kwargs: True,
            dispatch_fill_fn=fake_dispatch,
            get_interface_whitelist_record_fn=lambda name: None,
            calendar_window_covered_by_whitelist_fn=lambda record, calendar: False,
            preflight_api_health_check_fn=lambda: True,
            fill_dc_concept_cons_theme_fn=lambda *args, **kwargs: True,
            weekly_monthly_updater=mock.Mock(update_weekly_monthly=lambda *args, **kwargs: True),
            mode="latest",
            lag_trigger_trade_days=1,
            latest_mode_trade_days=1,
        )

        with mock.patch.object(
            autofill_workflow,
            "_run_latest_interface_light_cycle",
            side_effect=lambda tasks, calendar, latest_date: events.append(
                ("light_cycle", [name for name, _, _ in tasks], tuple(calendar), latest_date)
            ) or True,
        ), \
            mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", return_value=None):
            ok = autofill_workflow.run_autofill_workflow()

        self.assertTrue(ok)
        self.assertEqual(
            events,
            [
                ("light_cycle", ["daily"], ("20260424",), "20260424"),
            ],
        )
        self.assertTrue(any("先直接拉取最近 1 个交易日" in message for message, _ in logs))

    def test_interface_whitelist_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                autofill_runtime._mark_interface_whitelisted(
                    "daily",
                    latest_date="20260423",
                    mode="full",
                    calendar_dates=["20260421", "20260422", "20260423"],
                )
                self.assertTrue(autofill_runtime._is_interface_whitelisted("daily"))
                payload = autofill_runtime._load_interface_whitelist()
                self.assertEqual(payload["daily"]["latest_date"], "20260423")
                self.assertEqual(payload["daily"]["validated_mode"], "full")
                self.assertEqual(payload["daily"]["validated_start_date"], "20260421")
                self.assertEqual(payload["daily"]["validated_end_date"], "20260423")
                self.assertEqual(
                    payload["daily"]["validated_intervals"],
                    [
                        {"start": "20260421", "end": "20260423"},
                    ],
                )
                autofill_runtime._remove_interface_whitelist("daily")
                self.assertFalse(autofill_runtime._is_interface_whitelisted("daily"))

    def test_clean_calendar_intervals_split_on_bad_dates(self):
        intervals = autofill_runtime._clean_calendar_intervals_for_whitelist(
            ["20260421", "20260422", "20260423", "20260424"],
            ["20260423"],
            {"dates": [], "codes_by_date": {}, "empty_codes": []},
        )
        self.assertEqual(
            intervals,
            [
                {"start": "20260421", "end": "20260422"},
                {"start": "20260424", "end": "20260424"},
            ],
        )

    def test_financial_year_files_whitelist_local_contiguous_date_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            financial_dir = Path(tmpdir) / "financial"
            data_dir = financial_dir / "income"
            data_dir.mkdir(parents=True)
            pd.DataFrame(
                {
                    "ann_date": ["20260421", "20260422", "20260424"],
                    "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                    "revenue": [1, 2, 3],
                }
            ).to_csv(data_dir / "income_2026.csv", index=False)

            config = {
                "path": "income",
                "prefix": "income_",
                "date_col": "ann_date",
                "root": "financial",
                "save_granularity": "year",
            }
            with mock.patch.object(autofill_runtime, "FINANCIAL_DIR", financial_dir):
                intervals = autofill_runtime._local_year_file_date_intervals_for_whitelist(
                    "income",
                    config,
                    ["20260421", "20260422", "20260423", "20260424"],
                    health_report={"dates": [], "codes_by_date": {}, "empty_codes": []},
                )

        self.assertEqual(
            intervals,
            [
                {"start": "20260421", "end": "20260422"},
                {"start": "20260424", "end": "20260424"},
            ],
        )

    def test_interface_whitelist_status_uses_runtime_path_and_canonical_names(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                autofill_runtime._mark_interface_whitelisted(
                    "daily",
                    latest_date="20260424",
                    mode="full",
                    calendar_dates=["20260424"],
                )
                status = autofill_runtime.get_interface_whitelist_status(["daily", "moneyflow"])

        self.assertEqual(status["whitelist_path"], str(whitelist_path))
        self.assertEqual(status["total_count"], 2)
        self.assertEqual(status["whitelisted_count"], 1)
        self.assertEqual(status["not_whitelisted_count"], 1)
        self.assertEqual(status["whitelisted"], ["daily"])
        self.assertEqual(status["not_whitelisted"], ["moneyflow"])

    def test_get_local_latest_date_prefers_whitelist_latest_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            config = {
                "path": "daily",
                "prefix": "daily_",
                "date_col": "trade_date",
                "root": "stock",
            }
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path), \
                mock.patch.object(autofill_runtime, "DATA_DIR", Path(tmpdir)), \
                mock.patch.object(autofill_runtime, "INDEX_DIR", Path(tmpdir)), \
                mock.patch.object(autofill_runtime, "FINANCIAL_DIR", Path(tmpdir)), \
                mock.patch("core.autofill_runtime.shared_get_local_latest_date", return_value="20260420") as shared_latest:
                autofill_runtime._mark_interface_whitelisted(
                    "daily",
                    latest_date="20260424",
                    mode="full",
                    calendar_dates=["20260424"],
                )
                latest = autofill_runtime.get_local_latest_date("daily", config)

        self.assertEqual(latest, "20260424")
        shared_latest.assert_called_once()

    def test_get_local_latest_date_prefers_max_of_whitelist_latest_and_validated_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            whitelist_path.write_text(
                json.dumps(
                    {
                        "daily": {
                            "enabled": True,
                            "latest_date": "20260424",
                            "validated_end_date": "20260428",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            config = {
                "path": "daily",
                "prefix": "daily_",
                "date_col": "trade_date",
                "root": "stock",
            }
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                latest = autofill_runtime.get_local_latest_date("daily", config)

        self.assertEqual(latest, "20260428")

    def test_get_local_latest_date_prefers_max_of_whitelist_and_actual_local(self):
        config = {
            "path": "dc_concept",
            "prefix": "dc_concept_",
            "date_col": "trade_date",
            "root": "stock",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            whitelist_path.write_text(
                json.dumps(
                    {
                        "dc_concept": {
                            "enabled": True,
                            "latest_date": "20260424",
                            "validated_end_date": "20260427",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path), \
                mock.patch("core.autofill_runtime.shared_get_local_latest_date", return_value="20260428"):
                latest = autofill_runtime.get_local_latest_date("dc_concept", config)

        self.assertEqual(latest, "20260428")

    def test_get_local_latest_date_scans_year_date_layout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = Path(tmpdir) / "stock"
            target_dir = data_root / "moneyflow_data" / "individual" / "ths" / "2026"
            target_dir.mkdir(parents=True)
            pd.DataFrame(
                {
                    "trade_date": ["20260428"],
                    "ts_code": ["000001.SZ"],
                    "net_amount": [1],
                }
            ).to_csv(target_dir / "moneyflow_ths_20260428.csv", index=False)
            config = {
                "path": "moneyflow_data/individual/ths",
                "prefix": "moneyflow_ths_",
                "date_col": "trade_date",
                "root": "stock",
                "type": "by_date",
                "save_granularity": "year_date",
            }
            with mock.patch.object(autofill_runtime, "DATA_DIR", data_root), \
                mock.patch.object(autofill_runtime, "INDEX_DIR", Path(tmpdir)), \
                mock.patch.object(autofill_runtime, "FINANCIAL_DIR", Path(tmpdir)), \
                mock.patch.object(autofill_runtime, "WHITELIST_PATH", Path(tmpdir) / "missing_whitelist.json"):
                latest = autofill_runtime.get_local_latest_date("moneyflow_ths", config)

        self.assertEqual(latest, "20260428")

    def test_get_local_latest_date_scans_year_stock_layout_for_margin_detail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_root = Path(tmpdir) / "stock"
            target_dir = data_root / "margin_detail" / "2026"
            target_dir.mkdir(parents=True)
            pd.DataFrame(
                {
                    "trade_date": ["20260427", "20260428"],
                    "ts_code": ["000001.SZ", "000001.SZ"],
                    "rzye": [1, 2],
                    "rqye": [3, 4],
                }
            ).to_csv(target_dir / "margin_detail_000001.SZ.csv", index=False)
            config = {
                "path": "margin_detail",
                "prefix": "margin_detail_",
                "date_col": "trade_date",
                "root": "stock",
                "type": "standalone",
                "partition_by_year_dir": True,
                "save_granularity": "year_stock",
            }
            with mock.patch.object(autofill_runtime, "DATA_DIR", data_root), \
                mock.patch.object(autofill_runtime, "INDEX_DIR", Path(tmpdir)), \
                mock.patch.object(autofill_runtime, "FINANCIAL_DIR", Path(tmpdir)), \
                mock.patch.object(autofill_runtime, "WHITELIST_PATH", Path(tmpdir) / "missing_whitelist.json"):
                latest = autofill_runtime.get_local_latest_date("margin_detail", config)

        self.assertEqual(latest, "20260428")

    def test_get_local_latest_date_scans_legacy_year_file_without_prefix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            financial_root = Path(tmpdir) / "financial"
            target_dir = financial_root / "fina_mainbz"
            target_dir.mkdir(parents=True)
            pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "end_date": ["20251231"],
                    "bz_item": ["A"],
                }
            ).to_csv(target_dir / "2025.csv", index=False)
            config = {
                "path": "fina_mainbz",
                "prefix": "fina_mainbz_",
                "date_col": "end_date",
                "root": "financial",
                "type": "by_stock",
                "save_granularity": "year",
            }
            with mock.patch.object(autofill_runtime, "DATA_DIR", Path(tmpdir)), \
                mock.patch.object(autofill_runtime, "INDEX_DIR", Path(tmpdir)), \
                mock.patch.object(autofill_runtime, "FINANCIAL_DIR", financial_root), \
                mock.patch.object(autofill_runtime, "WHITELIST_PATH", Path(tmpdir) / "missing_whitelist.json"):
                latest = autofill_runtime.get_local_latest_date("fina_mainbz", config)

        self.assertEqual(latest, "20251231")

    def test_fill_by_code_interface_retries_stale_updated_resume_when_target_not_covered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            interface_dir = stock_dir / "margin" / "2026"
            interface_dir.mkdir(parents=True)
            pd.DataFrame(
                {
                    "trade_date": ["20260427"],
                    "ts_code": ["000001.SZ"],
                    "rzye": [1],
                    "rqye": [2],
                }
            ).to_csv(interface_dir / "margin_000001.SZ.csv", index=False)
            state_path = Path(tmpdir) / "margin_20260415_20260428.json"
            state_path.write_text(
                json.dumps(
                    {
                        "codes": {
                            "000001.SZ": {"status": "updated", "updated_at": "2026-04-29 00:00:00"}
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = {
                "path": "margin",
                "prefix": "margin_",
                "date_col": "trade_date",
                "root": "stock",
                "type": "by_stock",
                "save_granularity": "year_stock",
                "partition_by_year_dir": True,
                "resume_code_state": True,
                "progress_log_interval": 50,
                "active_code_log_interval": 0,
                "live_progress_include_code": False,
            }
            calls = []
            with mock.patch.object(autofill_runtime, "DATA_DIR", stock_dir), \
                mock.patch.object(autofill_runtime, "_code_resume_state_path", return_value=state_path), \
                mock.patch.object(autofill_runtime, "get_stock_code_list", return_value=["000001.SZ"]), \
                mock.patch.object(
                    autofill_runtime,
                    "_get_api_func",
                    return_value=lambda **kwargs: calls.append(kwargs) or pd.DataFrame(
                        {
                            "trade_date": ["20260428"],
                            "ts_code": ["000001.SZ"],
                            "rzye": [3],
                            "rqye": [4],
                        }
                    ),
                ):
                ok = autofill_runtime.fill_by_code_interface("margin", config, ["20260415", "20260428"], code_type="stock")

        self.assertTrue(ok)
        self.assertTrue(calls)

    def test_fill_by_code_interface_does_not_skip_no_data_resume_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            (stock_dir / "cyq_chips" / "2026").mkdir(parents=True)
            state_path = Path(tmpdir) / "cyq_chips_20260415_20260428.json"
            state_path.write_text(
                json.dumps(
                    {
                        "codes": {
                            "000001.SZ": {"status": "no_data", "updated_at": "2026-04-29 00:00:00"}
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            config = {
                "path": "cyq_chips",
                "prefix": "cyq_chips_",
                "date_col": "trade_date",
                "root": "stock",
                "type": "by_stock",
                "save_granularity": "year_stock",
                "partition_by_year_dir": True,
                "resume_code_state": True,
                "progress_log_interval": 50,
                "active_code_log_interval": 0,
                "live_progress_include_code": False,
            }
            calls = []
            with mock.patch.object(autofill_runtime, "DATA_DIR", stock_dir), \
                mock.patch.object(autofill_runtime, "_code_resume_state_path", return_value=state_path), \
                mock.patch.object(autofill_runtime, "get_stock_code_list", return_value=["000001.SZ"]), \
                mock.patch.object(
                    autofill_runtime,
                    "_get_api_func",
                    return_value=lambda **kwargs: calls.append(kwargs) or pd.DataFrame(
                        {
                            "trade_date": ["20260428"],
                            "ts_code": ["000001.SZ"],
                            "price": [10.0],
                            "percent": [0.1],
                        }
                    ),
                ):
                ok = autofill_runtime.fill_by_code_interface("cyq_chips", config, ["20260415", "20260428"], code_type="stock")

        self.assertTrue(ok)
        self.assertTrue(calls)

    def test_fill_margin_detail_by_date_treats_existing_target_day_as_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_root = Path(tmpdir) / "stock"
            year_dir = stock_root / "margin_detail" / "2026"
            year_dir.mkdir(parents=True)
            pd.DataFrame(
                {
                    "trade_date": ["20260428"],
                    "ts_code": ["000001.SZ"],
                    "rzye": [1],
                    "rqye": [2],
                }
            ).to_csv(year_dir / "margin_detail_000001.SZ.csv", index=False)

            config = {
                "path": "margin_detail",
                "prefix": "margin_detail_",
                "date_col": "trade_date",
                "root": "stock",
            }
            pro_stub = mock.Mock()
            pro_stub.margin_detail.side_effect = [
                pd.DataFrame(
                    {
                        "trade_date": ["20260428"],
                        "ts_code": ["000001.SZ"],
                        "rzye": [1],
                        "rqye": [2],
                    }
                ),
                pd.DataFrame(),
            ]
            with mock.patch.object(autofill_runtime, "DATA_DIR", stock_root), \
                mock.patch.object(autofill_runtime, "pro", pro_stub), \
                mock.patch.object(autofill_runtime, "get_stock_code_list", return_value=["000001.SZ"]):
                result = autofill_runtime.fill_margin_detail_by_date(config, ["20260428"])

        self.assertEqual(
            result,
            {"ok": True, "covered_target_date": True, "updated": 0},
        )

    def test_mark_interface_whitelisted_keeps_latest_date_at_validated_end(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path):
                autofill_runtime._mark_interface_whitelisted(
                    "daily",
                    latest_date="20260424",
                    mode="latest",
                    calendar_dates=["20260427", "20260428"],
                )
                payload = autofill_runtime._load_interface_whitelist()

        self.assertEqual(payload["daily"]["latest_date"], "20260428")

    def test_resolve_execution_mode_logs_progress(self):
        logs = []

        def fake_log(message, level="INFO"):
            logs.append((message, level))

        registry = build_auto_fill_registry()
        autofill_workflow.initialize_workflow(
            data_dir=Path("/tmp"),
            index_dir=Path("/tmp"),
            financial_dir=Path("/tmp"),
            stock_interface_config=registry["stock"],
            index_interface_config=registry["index"],
            log_fn=fake_log,
            get_trade_dates_fn=lambda: ["20260423", "20260424"],
            get_local_latest_date_fn=lambda name, config: "20260424",
            get_report_target_date_fn=lambda config, calendar: "20260424",
            resolve_run_target_trade_date_fn=lambda calendar: "20260424",
            repair_single_interface_fn=lambda *args, **kwargs: True,
            dispatch_fill_fn=lambda *args, **kwargs: True,
            get_interface_whitelist_record_fn=lambda name: None,
            calendar_window_covered_by_whitelist_fn=lambda record, calendar: False,
            preflight_api_health_check_fn=lambda: True,
            fill_dc_concept_cons_theme_fn=lambda *args, **kwargs: True,
            weekly_monthly_updater=mock.Mock(),
            mode="auto",
            lag_trigger_trade_days=1,
            latest_mode_trade_days=10,
        )

        mode, reasons = autofill_workflow.resolve_execution_mode(["20260423", "20260424"], "20260424")

        self.assertEqual(mode, "latest")
        self.assertEqual(reasons, [])
        self.assertTrue(any("模式判断进度" in message for message, _ in logs))

    def test_auto_mode_defaults_to_latest_first_without_pre_scan(self):
        logs = []
        events = []
        registry = {
            "stock": {"by_date": {"daily": {"path": "daily"}}, "by_stock": {}, "by_name": {}},
            "index": {"by_date": {}, "by_index": {}, "by_name": {}},
        }

        autofill_workflow.initialize_workflow(
            data_dir=Path("/tmp"),
            index_dir=Path("/tmp"),
            financial_dir=Path("/tmp"),
            stock_interface_config=registry["stock"],
            index_interface_config=registry["index"],
            log_fn=lambda message, level="INFO": logs.append((message, level)),
            get_trade_dates_fn=lambda: ["20260423", "20260424"],
            get_local_latest_date_fn=lambda name, config: "20260424",
            get_report_target_date_fn=lambda config, calendar: "20260424",
            resolve_run_target_trade_date_fn=lambda calendar: "20260424",
            repair_single_interface_fn=lambda *args, **kwargs: True,
            dispatch_fill_fn=lambda name, config, calendar, code_type=None, code_list=None: events.append(
                ("fill", name, tuple(calendar), code_type)
            ) or True,
            get_interface_whitelist_record_fn=lambda name: None,
            calendar_window_covered_by_whitelist_fn=lambda record, calendar: False,
            preflight_api_health_check_fn=lambda: True,
            fill_dc_concept_cons_theme_fn=lambda *args, **kwargs: True,
            weekly_monthly_updater=mock.Mock(update_weekly_monthly=lambda *args, **kwargs: True),
            mode="auto",
            lag_trigger_trade_days=1,
            latest_mode_trade_days=1,
        )

        with mock.patch.object(autofill_workflow, "resolve_execution_mode", side_effect=AssertionError("should not pre-scan")), \
            mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", return_value=None), \
            mock.patch.object(autofill_workflow, "_run_latest_interface_light_cycle", return_value=True) as light_cycle:
            ok = autofill_workflow.run_autofill_workflow()

        self.assertTrue(ok)
        self.assertEqual(events, [])
        light_cycle.assert_called_once()
        self.assertTrue(any("auto -> latest_first" in message for message, _ in logs))

    def test_auto_mode_records_pending_issue_without_escalating_full(self):
        events = []
        registry = {
            "stock": {"by_date": {"daily": {"path": "daily"}}, "by_stock": {}, "by_name": {}},
            "index": {"by_date": {}, "by_index": {}, "by_name": {}},
        }

        autofill_workflow.initialize_workflow(
            data_dir=Path("/tmp"),
            index_dir=Path("/tmp"),
            financial_dir=Path("/tmp"),
            stock_interface_config=registry["stock"],
            index_interface_config=registry["index"],
            log_fn=lambda message, level="INFO": events.append(("log", level, message)),
            get_trade_dates_fn=lambda: ["20260421", "20260422", "20260423", "20260424"],
            get_local_latest_date_fn=lambda name, config: "20260424",
            get_report_target_date_fn=lambda config, calendar: "20260424",
            resolve_run_target_trade_date_fn=lambda calendar: "20260424",
            repair_single_interface_fn=lambda *args, **kwargs: True,
            dispatch_fill_fn=lambda name, config, calendar, code_type=None, code_list=None: events.append(
                ("fill", name, tuple(calendar), code_type)
            ) or True,
            get_interface_whitelist_record_fn=lambda name: None,
            calendar_window_covered_by_whitelist_fn=lambda record, calendar: False,
            preflight_api_health_check_fn=lambda: True,
            fill_dc_concept_cons_theme_fn=lambda *args, **kwargs: True,
            weekly_monthly_updater=mock.Mock(update_weekly_monthly=lambda *args, **kwargs: True),
            mode="auto",
            lag_trigger_trade_days=1,
            latest_mode_trade_days=1,
        )

        with mock.patch.object(
            autofill_workflow,
            "_run_latest_interface_light_cycle",
            side_effect=lambda tasks, calendar, latest_date: events.append(
                ("light_cycle", tuple(calendar), latest_date)
            ) or False,
        ), mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", return_value=None):
            ok = autofill_workflow.run_autofill_workflow()

        self.assertTrue(ok)
        self.assertIn(("light_cycle", ("20260424",), "20260424"), events)
        self.assertTrue(any(item[0] == "log" and "留待下一轮继续处理" in item[2] for item in events))

    def test_selected_interfaces_skip_extra_followup_steps(self):
        events = []
        registry = {
            "stock": {"by_date": {"daily": {"path": "daily"}}, "by_stock": {}, "by_name": {}},
            "index": {"by_date": {}, "by_index": {}, "by_name": {}},
        }

        autofill_workflow.initialize_workflow(
            data_dir=Path("/tmp"),
            index_dir=Path("/tmp"),
            financial_dir=Path("/tmp"),
            stock_interface_config=registry["stock"],
            index_interface_config=registry["index"],
            log_fn=lambda message, level="INFO": events.append(("log", level, message)),
            get_trade_dates_fn=lambda: ["20260424"],
            get_local_latest_date_fn=lambda name, config: "20260424",
            get_report_target_date_fn=lambda config, calendar: "20260424",
            resolve_run_target_trade_date_fn=lambda calendar: "20260424",
            repair_single_interface_fn=lambda *args, **kwargs: True,
            dispatch_fill_fn=lambda *args, **kwargs: True,
            get_interface_whitelist_record_fn=lambda name: None,
            calendar_window_covered_by_whitelist_fn=lambda record, calendar: False,
            preflight_api_health_check_fn=lambda: True,
            fill_dc_concept_cons_theme_fn=lambda *args, **kwargs: events.append(("dc_concept_cons", args, kwargs)) or True,
            weekly_monthly_updater=mock.Mock(update_weekly_monthly=lambda *args, **kwargs: events.append(("weekly_monthly", args, kwargs)) or True),
            mode="latest",
            lag_trigger_trade_days=1,
            latest_mode_trade_days=1,
            selected_interfaces=["daily"],
        )

        with mock.patch.object(autofill_workflow, "_run_latest_interface_light_cycle", return_value=True), \
            mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", return_value=None):
            ok = autofill_workflow.run_autofill_workflow()

        self.assertTrue(ok)
        self.assertFalse(any(item[0] == "dc_concept_cons" for item in events))
        self.assertFalse(any(item[0] == "weekly_monthly" for item in events))
        self.assertTrue(any(item[0] == "log" and "跳过附加步骤" in item[2] for item in events))

    def test_full_mode_force_refetch_uses_history_calendar_and_runs_pre_refetch(self):
        events = []
        registry = {
            "stock": {"by_date": {}, "by_stock": {}, "by_name": {}},
            "index": {"by_date": {}, "by_index": {"index_daily": {"path": "index_daily"}}, "by_name": {}},
        }

        def fake_get_trade_dates(start_date=None, end_date=None):
            events.append(("calendar", start_date, end_date))
            return ["20200102", "20200103", "20260519"]

        autofill_workflow.initialize_workflow(
            data_dir=Path("/tmp"),
            index_dir=Path("/tmp"),
            financial_dir=Path("/tmp"),
            stock_interface_config=registry["stock"],
            index_interface_config=registry["index"],
            log_fn=lambda message, level="INFO": events.append(("log", level, message)),
            get_trade_dates_fn=fake_get_trade_dates,
            get_local_latest_date_fn=lambda name, config: None,
            get_report_target_date_fn=lambda config, calendar: calendar[-1] if calendar else None,
            resolve_run_target_trade_date_fn=lambda calendar: "20260519",
            repair_single_interface_fn=lambda *args, **kwargs: True,
            dispatch_fill_fn=lambda *args, **kwargs: {"ok": True, "covered_target_date": True},
            get_interface_whitelist_record_fn=lambda name: {"validated_intervals": [["20200102", "20260519"]]},
            calendar_window_covered_by_whitelist_fn=lambda record, calendar: True,
            calendar_dates_not_covered_by_whitelist_fn=lambda record, calendar: [],
            preflight_api_health_check_fn=lambda: True,
            fill_dc_concept_cons_theme_fn=lambda *args, **kwargs: True,
            weekly_monthly_updater=mock.Mock(update_weekly_monthly=lambda *args, **kwargs: True),
            mode="full",
            lag_trigger_trade_days=1,
            latest_mode_trade_days=10,
            selected_interfaces=["index_daily"],
            ignore_whitelist=True,
            force_refetch=True,
            history_start_date="20200101",
            history_end_date="20260519",
        )

        with mock.patch.object(
            autofill_workflow,
            "_run_force_refetch_full_fill",
            side_effect=lambda tasks, calendar: events.append(
                ("force_refetch", [name for name, _, _ in tasks], tuple(calendar))
            ),
        ), mock.patch.object(
            autofill_workflow,
            "_run_batched_interface_closure",
            side_effect=lambda tasks, calendar, latest_date, execution_mode, explicit_full_mode: events.append(
                ("closure", [name for name, _, _ in tasks], tuple(calendar), latest_date, execution_mode, explicit_full_mode)
            ) or True,
        ):
            ok = autofill_workflow.run_autofill_workflow()

        self.assertTrue(ok)
        self.assertIn(("calendar", "20200101", "20260519"), events)
        self.assertIn(("force_refetch", ["index_daily"], ("20200102", "20200103", "20260519")), events)
        self.assertIn(("closure", ["index_daily"], ("20200102", "20200103", "20260519"), "20260519", "full", True), events)

    def test_full_mode_ignore_whitelist_sets_explicit_full_mode(self):
        events = []
        registry = {
            "stock": {"by_date": {"daily": {"path": "daily"}}, "by_stock": {}, "by_name": {}},
            "index": {"by_date": {}, "by_index": {}, "by_name": {}},
        }

        autofill_workflow.initialize_workflow(
            data_dir=Path("/tmp"),
            index_dir=Path("/tmp"),
            financial_dir=Path("/tmp"),
            stock_interface_config=registry["stock"],
            index_interface_config=registry["index"],
            log_fn=lambda message, level="INFO": events.append(("log", level, message)),
            get_trade_dates_fn=lambda start_date=None, end_date=None: ["20260423", "20260424"],
            get_local_latest_date_fn=lambda name, config: "20260424",
            get_report_target_date_fn=lambda config, calendar: "20260424",
            resolve_run_target_trade_date_fn=lambda calendar: "20260424",
            repair_single_interface_fn=lambda *args, **kwargs: True,
            dispatch_fill_fn=lambda *args, **kwargs: {"ok": True, "covered_target_date": True},
            get_interface_whitelist_record_fn=lambda name: {"validated_intervals": [["20260423", "20260424"]]},
            calendar_window_covered_by_whitelist_fn=lambda record, calendar: True,
            calendar_dates_not_covered_by_whitelist_fn=lambda record, calendar: [],
            preflight_api_health_check_fn=lambda: True,
            fill_dc_concept_cons_theme_fn=lambda *args, **kwargs: True,
            weekly_monthly_updater=mock.Mock(update_weekly_monthly=lambda *args, **kwargs: True),
            mode="full",
            lag_trigger_trade_days=1,
            latest_mode_trade_days=10,
            selected_interfaces=["daily"],
            ignore_whitelist=True,
            force_refetch=False,
        )

        with mock.patch.object(
            autofill_workflow,
            "_run_batched_interface_closure",
            side_effect=lambda tasks, calendar, latest_date, execution_mode, explicit_full_mode: events.append(
                ("closure", explicit_full_mode)
            ) or True,
        ):
            ok = autofill_workflow.run_autofill_workflow()

        self.assertTrue(ok)
        self.assertIn(("closure", True), events)

    def test_auto_fill_main_rejects_unknown_selected_interface(self):
        with mock.patch.object(auto_fill_data, "parse_args", return_value=mock.Mock(
            mode="auto",
            lag_trigger_trade_days=1,
            latest_trade_days=10,
            interfaces="daily,not_exist_interface",
            ignore_whitelist=False,
            force_refetch=False,
            history_start_date="20200101",
            history_end_date="",
        )), mock.patch.object(auto_fill_data, "clear_updated_interfaces"), \
            mock.patch.object(auto_fill_data.runtime, "_dispatch_fill", mock.Mock()):
            result = auto_fill_data.main()

        self.assertEqual(result, 1)

    def test_auto_fill_main_rejects_force_refetch_without_selected_interfaces(self):
        with mock.patch.object(auto_fill_data, "parse_args", return_value=mock.Mock(
            mode="full",
            lag_trigger_trade_days=1,
            latest_trade_days=10,
            interfaces="",
            ignore_whitelist=False,
            force_refetch=True,
            history_start_date="20200101",
            history_end_date="20260519",
        )), mock.patch.object(auto_fill_data, "clear_updated_interfaces"), \
            mock.patch.object(auto_fill_data.runtime, "_dispatch_fill", mock.Mock()):
            result = auto_fill_data.main()

        self.assertEqual(result, 1)

    def test_whitelist_validated_window_skips_missing_scan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            config = {
                "path": "daily",
                "prefix": "daily_",
                "date_col": "trade_date",
                "root": "stock",
            }
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path), \
                mock.patch.object(autofill_runtime, "get_missing_trade_dates") as get_missing, \
                mock.patch.object(autofill_runtime, "_dispatch_fill") as dispatch_fill:
                autofill_runtime._mark_interface_whitelisted(
                    "daily",
                    latest_date="20260424",
                    mode="full",
                    calendar_dates=["20260423", "20260424"],
                )

                ok = autofill_runtime._repair_single_interface(
                    "daily",
                    config,
                    ["20260423", "20260424"],
                    "20260424",
                    execution_mode="full",
                    bypass_whitelist=False,
                )

            self.assertTrue(ok)
            get_missing.assert_not_called()
            dispatch_fill.assert_not_called()

    def test_dispatch_fill_uses_disclosure_date_ann_date_path(self):
        config = {
            "path": "disclosure_date",
            "prefix": "disclosure_date_",
            "date_col": "ann_date",
            "root": "financial",
            "save_granularity": "year",
        }
        with mock.patch.object(
            autofill_runtime,
            "fill_disclosure_date_by_announcement_date",
            return_value=True,
        ) as fill_special:
            ok = autofill_runtime._dispatch_fill(
                "disclosure_date",
                config,
                ["20260105", "20260424"],
                code_type="stock",
            )

        self.assertTrue(ok)
        fill_special.assert_called_once_with(config, ["20260105", "20260424"])

    def test_fill_by_date_interface_normalizes_iso_trade_date_before_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            index_dir = Path(tmpdir) / "index"
            financial_dir = Path(tmpdir) / "financial"
            stock_dir.mkdir()
            index_dir.mkdir()
            financial_dir.mkdir()
            autofill_runtime.initialize_runtime(
                pro_api=mock.Mock(),
                data_dir=stock_dir,
                index_dir=index_dir,
                financial_dir=financial_dir,
            )
            fake_df = pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "2026-04-22 00:00:00", "open": 1},
                    {"ts_code": "000002.SZ", "trade_date": "2026-04-22 00:00:00", "open": 2},
                ]
            )
            getattr(autofill_runtime.pro, "stk_nineturn").return_value = fake_df
            config = {
                "type": "by_date",
                "api": "stk_nineturn",
                "path": "stk_nineturn",
                "prefix": "stk_nineturn_",
                "group": "limit",
                "date_col": "trade_date",
                "root": "stock",
                "required_columns": ["open"],
                "force_by_date": True,
                "save_granularity": "year_date",
                "use_pagination": False,
            }

            ok = autofill_runtime.fill_by_date_interface("stk_nineturn", config, ["20260422"])

            self.assertTrue(ok)
            written = pd.read_csv(stock_dir / "stk_nineturn" / "2026" / "stk_nineturn_20260422.csv")
            self.assertEqual(set(written["trade_date"].astype(str).tolist()), {"20260422"})

    def test_by_stock_daily_update_prefers_batch_stock_pool_for_target_interfaces(self):
        for name in [
            "cyq_chips",
            "margin",
            "disclosure_date",
        ]:
            with self.subTest(name=name):
                self.assertTrue(INTERFACE_CONFIG[name].get("batch_stock_pool_request"))
        self.assertEqual(INTERFACE_CONFIG["margin"].get("batch_row_limit"), 4000)
        self.assertEqual(INTERFACE_CONFIG["margin"].get("batch_initial_code_chunk_size"), 500)
        self.assertEqual(INTERFACE_CONFIG["disclosure_date"].get("batch_row_limit"), 3000)
        self.assertEqual(INTERFACE_CONFIG["cyq_chips"].get("batch_row_limit"), 2000)
        self.assertFalse(INTERFACE_CONFIG["pledge_detail"].get("batch_stock_pool_request", False))

    def test_fill_by_code_interface_batch_mode_requests_each_trade_date_separately(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            index_dir = Path(tmpdir) / "index"
            financial_dir = Path(tmpdir) / "financial"
            stock_dir.mkdir()
            index_dir.mkdir()
            financial_dir.mkdir()
            autofill_runtime.initialize_runtime(
                pro_api=mock.Mock(),
                data_dir=stock_dir,
                index_dir=index_dir,
                financial_dir=financial_dir,
            )
            autofill_runtime.pro.express.side_effect = [
                pd.DataFrame([{"ts_code": "000001.SZ", "ann_date": "20260424"}]),
                pd.DataFrame([{"ts_code": "000001.SZ", "ann_date": "20260425"}]),
            ]
            config = {
                "api": "express",
                "path": "express",
                "prefix": "express_",
                "date_col": "ann_date",
                "root": "financial",
                "save_granularity": "year",
                "batch_stock_pool_request": True,
                "batch_per_trade_date_request": True,
                "batch_initial_code_chunk_size": 10,
            }

            with mock.patch.object(autofill_runtime, "get_stock_code_list", return_value=["000001.SZ", "000002.SZ"]):
                ok = autofill_runtime.fill_by_code_interface(
                    "express",
                    config,
                    ["20260424", "20260425"],
                    code_type="stock",
                )

            self.assertTrue(ok)
            calls = autofill_runtime.pro.express.call_args_list
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0].kwargs["start_date"], "20260424")
            self.assertEqual(calls[0].kwargs["end_date"], "20260424")
            self.assertEqual(calls[1].kwargs["start_date"], "20260425")
            self.assertEqual(calls[1].kwargs["end_date"], "20260425")

    def test_margin_uses_configured_api_for_batch_stock_pool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            index_dir = Path(tmpdir) / "index"
            financial_dir = Path(tmpdir) / "financial"
            stock_dir.mkdir()
            index_dir.mkdir()
            financial_dir.mkdir()
            pro_api = mock.Mock()
            pro_api.margin_detail.return_value = pd.DataFrame(
                [{"ts_code": "000001.SZ", "trade_date": "20260424", "rzye": 1, "rqye": 2}]
            )
            autofill_runtime.initialize_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                index_dir=index_dir,
                financial_dir=financial_dir,
            )
            config = {
                "api": "margin_detail",
                "path": "margin",
                "prefix": "margin_",
                "date_col": "trade_date",
                "root": "stock",
                "save_granularity": "year_stock",
                "batch_stock_pool_request": True,
                "batch_per_trade_date_request": True,
                "batch_row_limit": 4000,
                "batch_initial_code_chunk_size": 500,
            }

            with mock.patch.object(autofill_runtime, "get_stock_code_list", return_value=["000001.SZ", "000002.SZ"]):
                ok = autofill_runtime.fill_by_code_interface(
                    "margin",
                    config,
                    ["20260424"],
                    code_type="stock",
                )

            self.assertTrue(ok)
            self.assertEqual(pro_api.margin_detail.call_count, 1)
            self.assertEqual(pro_api.margin.call_count, 0)

    def test_fill_by_code_interface_falls_back_to_single_when_batch_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            index_dir = Path(tmpdir) / "index"
            financial_dir = Path(tmpdir) / "financial"
            stock_dir.mkdir()
            index_dir.mkdir()
            financial_dir.mkdir()

            pro_api = mock.Mock()

            def fake_cyq_chips(**kwargs):
                ts_code = kwargs.get("ts_code", "")
                if "," in ts_code:
                    return pd.DataFrame()
                return pd.DataFrame(
                    [{"ts_code": ts_code, "trade_date": "20260428", "price": 10.0, "percent": 1.0}]
                )

            pro_api.cyq_chips.side_effect = fake_cyq_chips
            autofill_runtime.initialize_runtime(
                pro_api=pro_api,
                data_dir=stock_dir,
                index_dir=index_dir,
                financial_dir=financial_dir,
            )
            config = {
                "api": "cyq_chips",
                "path": "cyq_chips",
                "prefix": "cyq_chips_",
                "date_col": "trade_date",
                "root": "stock",
                "save_granularity": "year_stock",
                "batch_stock_pool_request": True,
                "batch_per_trade_date_request": True,
                "batch_initial_code_chunk_size": 10,
                "batch_row_limit": 2000,
                "required_columns": ["price", "percent"],
            }

            with mock.patch.object(autofill_runtime, "get_stock_code_list", return_value=["000001.SZ", "000002.SZ"]):
                ok = autofill_runtime.fill_by_code_interface(
                    "cyq_chips",
                    config,
                    ["20260428"],
                    code_type="stock",
                )

            self.assertTrue(ok)
            self.assertGreaterEqual(pro_api.cyq_chips.call_count, 3)

    def test_fill_cyq_chips_by_stock_only_requests_missing_trade_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            index_dir = Path(tmpdir) / "index"
            financial_dir = Path(tmpdir) / "financial"
            (stock_dir / "cyq_chips" / "2026").mkdir(parents=True, exist_ok=True)
            index_dir.mkdir()
            financial_dir.mkdir()

            pd.DataFrame(
                [{"ts_code": "000001.SZ", "trade_date": "20260424", "price": 10.0, "percent": 1.0}]
            ).to_csv(stock_dir / "cyq_chips" / "2026" / "cyq_chips_000001.SZ.csv", index=False)

            autofill_runtime.initialize_runtime(
                pro_api=mock.Mock(),
                data_dir=stock_dir,
                index_dir=index_dir,
                financial_dir=financial_dir,
            )

            captured_batches = []

            def fake_run(cmd, capture_output=False, text=False, timeout=None):
                payload = json.loads(cmd[3])
                captured_batches.append(payload)
                return mock.Mock(stdout="000001.SZ:EMPTY\n")

            config = {
                "path": "cyq_chips",
                "prefix": "cyq_chips_",
                "date_col": "trade_date",
                "root": "stock",
                "save_granularity": "year_stock",
                "batch_size": 5,
                "parallel_batches": 1,
                "batch_timeout_sec": 120,
                "retry_timeout_sec": 60,
                "page_limit": 2000,
                "max_pages": 8,
                "retry_per_trade_date": 2,
            }

            with mock.patch.object(autofill_runtime, "get_stock_code_list", return_value=["000001.SZ"]), \
                mock.patch.object(autofill_runtime, "get_trade_dates", return_value=["20260424", "20260427", "20260428", "20260429"]), \
                mock.patch("subprocess.run", side_effect=fake_run):
                ok = autofill_runtime.fill_cyq_chips_by_stock(config, ["20260429"])

            self.assertFalse(ok)
            self.assertTrue(captured_batches)
            self.assertEqual(captured_batches[0][0]["trade_dates"], ["20260427", "20260428", "20260429"])

    def test_fill_cyq_chips_by_stock_auto_splits_large_missing_pool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            index_dir = Path(tmpdir) / "index"
            financial_dir = Path(tmpdir) / "financial"
            (stock_dir / "cyq_chips").mkdir(parents=True, exist_ok=True)
            index_dir.mkdir()
            financial_dir.mkdir()

            autofill_runtime.initialize_runtime(
                pro_api=mock.Mock(),
                data_dir=stock_dir,
                index_dir=index_dir,
                financial_dir=financial_dir,
            )

            captured_batch_sizes = []
            stock_codes = [f"{i:06d}.SZ" for i in range(1, 22)]

            def fake_run(cmd, capture_output=False, text=False, timeout=None):
                payload = json.loads(cmd[3])
                captured_batch_sizes.append(len(payload))
                stdout = "\n".join(f"{item['ts_code']}:OK" for item in payload)
                return mock.Mock(stdout=stdout)

            config = {
                "path": "cyq_chips",
                "prefix": "cyq_chips_",
                "date_col": "trade_date",
                "root": "stock",
                "save_granularity": "year_stock",
                "batch_size": 0,
                "estimated_rows_per_code": 100,
                "target_pages_per_batch": 1,
                "parallel_batches": 1,
                "batch_timeout_sec": 120,
                "retry_timeout_sec": 60,
                "page_limit": 2000,
                "max_pages": 8,
                "retry_per_trade_date": 2,
            }

            with mock.patch.object(autofill_runtime, "get_stock_code_list", return_value=stock_codes), \
                mock.patch("subprocess.run", side_effect=fake_run):
                ok = autofill_runtime.fill_cyq_chips_by_stock(config, ["20260429"])

            self.assertTrue(ok)
            self.assertEqual(captured_batch_sizes, [20, 1])

    def test_stk_factor_pro_has_longer_timeout_and_retries(self):
        config = INTERFACE_CONFIG["stk_factor_pro"]
        self.assertEqual(config.get("api_timeout_sec"), 60)
        self.assertEqual(config.get("date_request_retry_count"), 3)

    def test_latest_progress_skips_completed_interfaces_on_rerun(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_path = Path(tmpdir) / "latest_mode_progress.json"
            payload = {
                "window_start": "20260424",
                "window_end": "20260427",
                "completed_interfaces": ["daily"],
            }
            progress_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tasks = [
                ("daily", {"path": "daily"}, None),
                ("daily_basic", {"path": "daily_basic"}, None),
            ]
            dispatched = []
            with mock.patch.object(autofill_workflow, "LATEST_PROGRESS_PATH", progress_path), \
                mock.patch.object(
                    autofill_workflow,
                    "DISPATCH_FILL",
                    side_effect=lambda name, config, trade_dates, code_type=None: dispatched.append(name) or True,
                ), \
                mock.patch.object(
                    autofill_workflow,
                    "GET_LOCAL_LATEST_DATE",
                    side_effect=lambda name, config: "20260427" if name == "daily" else None,
                ), \
                mock.patch.object(autofill_workflow, "GET_REPORT_TARGET_DATE", return_value="20260427"), \
                mock.patch.object(autofill_workflow, "LOG"):
                autofill_workflow._run_direct_latest_fill(tasks, ["20260424", "20260427"])

            self.assertEqual(dispatched, ["daily_basic"])

    def test_latest_direct_fill_skips_when_local_latest_already_covers_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_path = Path(tmpdir) / "latest_mode_progress.json"
            tasks = [
                ("daily", {"path": "daily"}, None),
                ("daily_basic", {"path": "daily_basic"}, None),
            ]
            dispatched = []

            def fake_local_latest(name, config):
                return "20260427" if name == "daily" else "20260420"

            with mock.patch.object(autofill_workflow, "LATEST_PROGRESS_PATH", progress_path), \
                mock.patch.object(
                    autofill_workflow,
                    "DISPATCH_FILL",
                    side_effect=lambda name, config, trade_dates, code_type=None: dispatched.append(name) or True,
                ), \
                mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", side_effect=fake_local_latest), \
                mock.patch.object(autofill_workflow, "GET_REPORT_TARGET_DATE", return_value="20260427"), \
                mock.patch.object(autofill_workflow, "LOG"):
                autofill_workflow._run_direct_latest_fill(tasks, ["20260424", "20260427"])

            self.assertEqual(dispatched, ["daily_basic"])

    def test_latest_direct_fill_skips_when_whitelist_already_covers_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_path = Path(tmpdir) / "latest_mode_progress.json"
            tasks = [
                ("daily", {"path": "daily"}, None),
                ("daily_basic", {"path": "daily_basic"}, None),
            ]
            dispatched = []

            def fake_whitelist_record(name):
                if name == "daily":
                    return {
                        "validated_start_date": "20260414",
                        "validated_end_date": "20260427",
                        "validated_intervals": [["20260414", "20260427"]],
                    }
                return None

            def fake_window_covered(record, calendar_dates):
                return bool(record) and calendar_dates == ["20260424", "20260427"]

            with mock.patch.object(autofill_workflow, "LATEST_PROGRESS_PATH", progress_path), \
                mock.patch.object(
                    autofill_workflow,
                    "DISPATCH_FILL",
                    side_effect=lambda name, config, trade_dates, code_type=None: dispatched.append(name) or True,
                ), \
                mock.patch.object(autofill_workflow, "GET_INTERFACE_WHITELIST_RECORD", side_effect=fake_whitelist_record), \
                mock.patch.object(autofill_workflow, "CALENDAR_WINDOW_COVERED_BY_WHITELIST", side_effect=fake_window_covered), \
                mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", return_value=None), \
                mock.patch.object(autofill_workflow, "GET_REPORT_TARGET_DATE", return_value="20260427"), \
                mock.patch.object(autofill_workflow, "LOG"):
                autofill_workflow._run_direct_latest_fill(tasks, ["20260424", "20260427"])

            self.assertEqual(dispatched, ["daily_basic"])

    def test_latest_direct_fill_only_dispatches_uncovered_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_path = Path(tmpdir) / "latest_mode_progress.json"
            tasks = [("daily_basic", {"path": "daily_basic"}, None)]
            dispatched = []

            whitelist_record = {
                "validated_start_date": "20260327",
                "validated_end_date": "20260428",
                "validated_intervals": [
                    {"start": "20260327", "end": "20260428"},
                ],
            }

            with mock.patch.object(autofill_workflow, "LATEST_PROGRESS_PATH", progress_path), \
                mock.patch.object(
                    autofill_workflow,
                    "DISPATCH_FILL",
                    side_effect=lambda name, config, trade_dates, code_type=None: dispatched.append(list(trade_dates)) or {
                        "ok": True,
                        "covered_target_date": True,
                    },
                ), \
                mock.patch.object(autofill_workflow, "GET_INTERFACE_WHITELIST_RECORD", return_value=whitelist_record), \
                mock.patch.object(autofill_workflow, "CALENDAR_WINDOW_COVERED_BY_WHITELIST", return_value=False), \
                mock.patch.object(
                    autofill_workflow,
                    "CALENDAR_DATES_NOT_COVERED_BY_WHITELIST",
                    return_value=["20260429"],
                ), \
                mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", return_value="20260428"), \
                mock.patch.object(autofill_workflow, "GET_REPORT_TARGET_DATE", return_value="20260429"), \
                mock.patch.object(autofill_workflow, "LOG"):
                autofill_workflow._run_direct_latest_fill(tasks, ["20260416", "20260429"])

            self.assertEqual(dispatched, [["20260429"]])

    def test_latest_direct_fill_does_not_mark_completed_when_target_day_not_covered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_path = Path(tmpdir) / "latest_mode_progress.json"
            tasks = [("stk_auction_o", {"path": "stk_auction_o"}, None)]

            with mock.patch.object(autofill_workflow, "LATEST_PROGRESS_PATH", progress_path), \
                mock.patch.object(
                    autofill_workflow,
                    "DISPATCH_FILL",
                    return_value={"ok": True, "covered_target_date": False},
                ), \
                mock.patch.object(autofill_workflow, "GET_INTERFACE_WHITELIST_RECORD", return_value=None), \
                mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", return_value=None), \
                mock.patch.object(autofill_workflow, "GET_REPORT_TARGET_DATE", return_value="20260428"), \
                mock.patch.object(autofill_workflow, "LOG"):
                autofill_workflow._run_direct_latest_fill(tasks, ["20260427", "20260428"])

            if progress_path.exists():
                payload = json.loads(progress_path.read_text(encoding="utf-8"))
                self.assertEqual(payload.get("completed_interfaces"), [])
            else:
                self.assertFalse(progress_path.exists())

    def test_latest_direct_fill_prunes_stale_completed_entry_when_not_really_covered(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_path = Path(tmpdir) / "latest_mode_progress.json"
            progress_path.write_text(
                json.dumps(
                    {
                        "window_start": "20260427",
                        "window_end": "20260428",
                        "completed_interfaces": ["stk_auction_o"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            tasks = [("stk_auction_o", {"path": "stk_auction_o"}, None)]
            dispatched = []

            with mock.patch.object(autofill_workflow, "LATEST_PROGRESS_PATH", progress_path), \
                mock.patch.object(
                    autofill_workflow,
                    "DISPATCH_FILL",
                    side_effect=lambda name, config, trade_dates, code_type=None: dispatched.append(name) or {
                        "ok": True,
                        "covered_target_date": False,
                    },
                ), \
                mock.patch.object(autofill_workflow, "GET_INTERFACE_WHITELIST_RECORD", return_value=None), \
                mock.patch.object(autofill_workflow, "CALENDAR_WINDOW_COVERED_BY_WHITELIST", return_value=False), \
                mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", return_value="20260424"), \
                mock.patch.object(autofill_workflow, "GET_REPORT_TARGET_DATE", return_value="20260428"), \
                mock.patch.object(autofill_workflow, "LOG"):
                autofill_workflow._run_direct_latest_fill(tasks, ["20260427", "20260428"])

    def test_latest_light_cycle_only_dispatches_uncovered_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_path = Path(tmpdir) / "latest_mode_progress.json"
            tasks = [("daily_basic", {"path": "daily_basic"}, None)]
            dispatched = []

            whitelist_record = {
                "validated_start_date": "20260327",
                "validated_end_date": "20260428",
                "validated_intervals": [
                    {"start": "20260327", "end": "20260428"},
                ],
            }

            with mock.patch.object(autofill_workflow, "LATEST_PROGRESS_PATH", progress_path), \
                mock.patch.object(
                    autofill_workflow,
                    "DISPATCH_FILL",
                    side_effect=lambda name, config, trade_dates, code_type=None: dispatched.append(list(trade_dates)) or {
                        "ok": True,
                        "covered_target_date": True,
                    },
                ), \
                mock.patch.object(
                    autofill_workflow,
                    "REPAIR_SINGLE_INTERFACE",
                    return_value={"complete": True},
                ), \
                mock.patch.object(autofill_workflow, "GET_INTERFACE_WHITELIST_RECORD", return_value=whitelist_record), \
                mock.patch.object(autofill_workflow, "CALENDAR_WINDOW_COVERED_BY_WHITELIST", return_value=False), \
                mock.patch.object(
                    autofill_workflow,
                    "CALENDAR_DATES_NOT_COVERED_BY_WHITELIST",
                    return_value=["20260429"],
                ), \
                mock.patch.object(autofill_workflow, "LOG"):
                autofill_workflow._run_latest_interface_light_cycle(tasks, ["20260416", "20260429"], "20260429")

            self.assertEqual(dispatched, [["20260429"]])

    def test_latest_direct_fill_uses_interface_trade_day_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_path = Path(tmpdir) / "latest_mode_progress.json"
            tasks = [("pledge_detail", {"path": "pledge_detail", "latest_trade_days_override": 3}, None)]
            dispatched = []

            with mock.patch.object(autofill_workflow, "LATEST_PROGRESS_PATH", progress_path), \
                mock.patch.object(
                    autofill_workflow,
                    "DISPATCH_FILL",
                    side_effect=lambda name, config, trade_dates, code_type=None: dispatched.append(list(trade_dates)) or {
                        "ok": True,
                        "covered_target_date": True,
                    },
                ), \
                mock.patch.object(autofill_workflow, "GET_INTERFACE_WHITELIST_RECORD", return_value=None), \
                mock.patch.object(autofill_workflow, "GET_LOCAL_LATEST_DATE", return_value=None), \
                mock.patch.object(autofill_workflow, "GET_REPORT_TARGET_DATE", return_value=None), \
                mock.patch.object(autofill_workflow, "LOG"):
                autofill_workflow._run_direct_latest_fill(
                    tasks,
                    ["20260416", "20260417", "20260420", "20260421", "20260422"],
                )

            self.assertEqual(dispatched, [["20260420", "20260421", "20260422"]])

    def test_latest_light_cycle_uses_interface_trade_day_override(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            progress_path = Path(tmpdir) / "latest_mode_progress.json"
            tasks = [("pledge_detail", {"path": "pledge_detail", "latest_trade_days_override": 2}, None)]
            dispatched = []

            with mock.patch.object(autofill_workflow, "LATEST_PROGRESS_PATH", progress_path), \
                mock.patch.object(
                    autofill_workflow,
                    "DISPATCH_FILL",
                    side_effect=lambda name, config, trade_dates, code_type=None: dispatched.append(list(trade_dates)) or {
                        "ok": True,
                        "covered_target_date": True,
                    },
                ), \
                mock.patch.object(
                    autofill_workflow,
                    "REPAIR_SINGLE_INTERFACE",
                    return_value={"complete": True},
                ), \
                mock.patch.object(autofill_workflow, "GET_INTERFACE_WHITELIST_RECORD", return_value=None), \
                mock.patch.object(autofill_workflow, "LOG"):
                autofill_workflow._run_latest_interface_light_cycle(
                    tasks,
                    ["20260416", "20260417", "20260420"],
                    "20260420",
                )

            self.assertEqual(dispatched, [["20260417", "20260420"]])

    def test_batched_interface_closure_syncs_completed_interface_once(self):
        tasks = [("daily", {"path": "daily"}, None)]
        synced = []

        with mock.patch.object(
            autofill_workflow,
            "REPAIR_SINGLE_INTERFACE",
            return_value={"complete": True},
        ), mock.patch.object(autofill_workflow, "LOG"):
            ok = autofill_workflow._run_batched_interface_closure(
                tasks,
                ["20260424"],
                "20260424",
                "latest",
                False,
            )

        self.assertTrue(ok)
        self.assertEqual(synced, ["daily"])

    def test_batched_interface_closure_skips_health_when_whitelist_covers_window(self):
        tasks = [("daily", {"path": "daily"}, None), ("daily_basic", {"path": "daily_basic"}, None)]
        inspected = []

        def fake_repair(name, *args, **kwargs):
            inspected.append(name)
            return {"complete": True}

        def fake_whitelist_record(name):
            if name == "daily":
                return {"validated_intervals": [["20260415", "20260428"]]}
            return None

        with mock.patch.object(
            autofill_workflow,
            "REPAIR_SINGLE_INTERFACE",
            side_effect=fake_repair,
        ), mock.patch.object(
            autofill_workflow,
            "GET_INTERFACE_WHITELIST_RECORD",
            side_effect=fake_whitelist_record,
        ), mock.patch.object(
            autofill_workflow,
            "CALENDAR_WINDOW_COVERED_BY_WHITELIST",
            side_effect=lambda record, calendar: bool(record) and calendar == ["20260415", "20260428"],
        ), mock.patch.object(autofill_workflow, "LOG"):
            ok = autofill_workflow._run_batched_interface_closure(
                tasks,
                ["20260415", "20260428"],
                "20260428",
                "latest",
                False,
            )

        self.assertTrue(ok)
        self.assertEqual(inspected, ["daily_basic"])

    def test_latest_light_cycle_runs_dispatch_then_inspect_then_sync_per_interface(self):
        tasks = [("daily", {"path": "daily"}, None), ("daily_basic", {"path": "daily_basic"}, None)]
        steps = []

        def fake_dispatch(name, config, trade_dates, code_type=None):
            steps.append(("dispatch", name))
            return {"ok": True, "covered_target_date": True}

        def fake_repair(name, config, trade_dates, latest_trade_date, **kwargs):
            steps.append(("inspect", name, kwargs.get("inspect_only")))
            return {"complete": True}

        def fake_sync(name):
            steps.append(("sync", name))
            return True

        with mock.patch.object(autofill_workflow, "DISPATCH_FILL", side_effect=fake_dispatch), \
            mock.patch.object(autofill_workflow, "REPAIR_SINGLE_INTERFACE", side_effect=fake_repair), \
            mock.patch.object(autofill_workflow, "GET_INTERFACE_WHITELIST_RECORD", return_value=None), \
            mock.patch.object(autofill_workflow, "CALENDAR_WINDOW_COVERED_BY_WHITELIST", return_value=False), \
            mock.patch.object(autofill_workflow, "LOG"):
            ok = autofill_workflow._run_latest_interface_light_cycle(
                tasks,
                ["20260415", "20260428"],
                "20260428",
            )

        self.assertTrue(ok)
        self.assertEqual(
            steps,
            [
                ("dispatch", "daily"),
                ("inspect", "daily", True),
                ("sync", "daily"),
                ("dispatch", "daily_basic"),
                ("inspect", "daily_basic", True),
                ("sync", "daily_basic"),
            ],
        )

    def test_latest_light_cycle_records_pending_issue_without_same_run_repair(self):
        tasks = [("daily", {"path": "daily"}, None)]
        with mock.patch.object(
            autofill_workflow,
            "DISPATCH_FILL",
            return_value={"ok": True, "covered_target_date": True},
        ), mock.patch.object(
            autofill_workflow,
            "REPAIR_SINGLE_INTERFACE",
            return_value={"complete": False},
        ) as repair_mock, mock.patch.object(
            autofill_workflow,
            "GET_INTERFACE_WHITELIST_RECORD",
            return_value=None,
        ), mock.patch.object(
            autofill_workflow,
            "CALENDAR_WINDOW_COVERED_BY_WHITELIST",
            return_value=False,
        ), mock.patch.object(autofill_workflow, "LOG"):
            ok = autofill_workflow._run_latest_interface_light_cycle(
                tasks,
                ["20260415", "20260428"],
                "20260428",
            )

        self.assertFalse(ok)
        repair_mock.assert_called_once()

    def test_disclosure_date_batches_non_st_stock_pool_by_announcement_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            index_dir = Path(tmpdir) / "index"
            financial_dir = Path(tmpdir) / "financial"
            stock_dir.mkdir()
            index_dir.mkdir()
            financial_dir.mkdir()
            autofill_runtime.initialize_runtime(
                pro_api=mock.Mock(),
                data_dir=stock_dir,
                index_dir=index_dir,
                financial_dir=financial_dir,
            )
            autofill_runtime.pro.disclosure_date.side_effect = [
                pd.DataFrame([{"ann_date": "20260424", "ts_code": "000001.SZ"}]),
                pd.DataFrame([{"ann_date": "20260424", "ts_code": "000002.SZ"}]),
            ]
            config = {
                "path": "disclosure_date",
                "prefix": "disclosure_date_",
                "date_col": "ann_date",
                "root": "financial",
                "save_granularity": "year",
                "batch_initial_code_chunk_size": 1,
            }

            with mock.patch.object(autofill_runtime, "get_stock_code_list", return_value=["000001.SZ", "000002.SZ"]):
                ok = autofill_runtime.fill_disclosure_date_by_announcement_date(config, ["20260424"])

            self.assertTrue(ok)
            calls = autofill_runtime.pro.disclosure_date.call_args_list
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0].kwargs["ts_code"], "000001.SZ")
            self.assertEqual(calls[1].kwargs["ts_code"], "000002.SZ")

    def test_partial_whitelist_skips_only_clean_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            config = {
                "path": "daily",
                "prefix": "daily_",
                "date_col": "trade_date",
                "root": "stock",
            }
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path), \
                mock.patch.object(autofill_runtime, "deduplicate_interface"), \
                mock.patch.object(autofill_runtime, "get_missing_trade_dates", return_value=[]), \
                mock.patch.object(
                    autofill_runtime,
                    "scan_incomplete_records",
                    return_value={"dates": [], "codes_by_date": {}, "empty_codes": []},
                ) as scan, \
                mock.patch.object(autofill_runtime, "get_local_latest_date", return_value="20260424"):
                autofill_runtime._mark_interface_whitelisted(
                    "daily",
                    latest_date="20260424",
                    mode="full",
                    calendar_dates=["20260421", "20260422", "20260424"],
                )

                ok = autofill_runtime._repair_single_interface(
                    "daily",
                    config,
                    ["20260421", "20260422", "20260423", "20260424"],
                    "20260424",
                    execution_mode="full",
                    bypass_whitelist=False,
                    inspect_only=True,
                )

        self.assertTrue(ok)
        scan.assert_called_once_with("daily", config, calendar_dates=["20260423"])

    def test_whitelist_fast_path_can_run_when_auto_resolves_full(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            config = {
                "path": "daily",
                "prefix": "daily_",
                "date_col": "trade_date",
                "root": "stock",
            }
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path), \
                mock.patch.object(autofill_runtime, "_dispatch_fill") as dispatch_fill, \
                mock.patch.object(autofill_runtime, "get_missing_trade_dates", return_value=[]), \
                mock.patch.object(autofill_runtime, "get_local_latest_date", return_value="20260424"):
                autofill_runtime._mark_interface_whitelisted(
                    "daily",
                    latest_date="20260423",
                    mode="full",
                )

                ok = autofill_runtime._repair_single_interface(
                    "daily",
                    config,
                    ["20260423", "20260424"],
                    "20260424",
                    execution_mode="full",
                    bypass_whitelist=False,
                )

                self.assertTrue(ok)
                dispatch_fill.assert_not_called()
                payload = autofill_runtime._load_interface_whitelist()
                self.assertEqual(payload["daily"]["latest_date"], "20260424")
                self.assertEqual(payload["daily"]["validated_mode"], "full")

    def test_whitelist_fast_path_only_fills_missing_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            config = {
                "path": "daily",
                "prefix": "daily_",
                "date_col": "trade_date",
                "root": "stock",
            }
            missing_results = [["20260424"], []]
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path), \
                mock.patch.object(autofill_runtime, "_dispatch_fill") as dispatch_fill, \
                mock.patch.object(autofill_runtime, "get_missing_trade_dates", side_effect=missing_results), \
                mock.patch.object(autofill_runtime, "get_local_latest_date", return_value="20260424"):
                autofill_runtime._mark_interface_whitelisted(
                    "daily",
                    latest_date="20260423",
                    mode="full",
                )

                ok = autofill_runtime._repair_single_interface(
                    "daily",
                    config,
                    ["20260423", "20260424"],
                    "20260424",
                    execution_mode="full",
                    bypass_whitelist=False,
                )

                self.assertTrue(ok)
                dispatch_fill.assert_called_once_with(
                    "daily",
                    config,
                    ["20260424"],
                    code_type=None,
                )

    def test_repeated_health_result_skips_final_rescan(self):
        config = {
            "path": "stk_factor_pro",
            "prefix": "stk_factor_pro_",
            "date_col": "trade_date",
            "root": "stock",
        }
        health_report = {
            "dates": ["20260423"],
            "codes_by_date": {"20260423": ["000001.SZ"]},
            "empty_codes": [],
        }
        with mock.patch.object(autofill_runtime, "resolve_stable_trade_date", return_value=None), \
            mock.patch.object(autofill_runtime, "deduplicate_interface") as deduplicate_interface, \
            mock.patch.object(autofill_runtime, "get_missing_trade_dates", return_value=[]), \
            mock.patch.object(autofill_runtime, "scan_incomplete_records", return_value=health_report), \
            mock.patch.object(autofill_runtime, "_dispatch_fill"):
            ok = autofill_runtime._repair_single_interface(
                "stk_factor_pro",
                config,
                ["20260423", "20260424"],
                "20260424",
                max_rounds=3,
                execution_mode="full",
                bypass_whitelist=True,
            )

        self.assertFalse(ok)
        self.assertEqual(deduplicate_interface.call_count, 2)

    def test_full_health_checks_before_first_repair_then_whitelists_after_recheck(self):
        config = {
            "path": "daily",
            "prefix": "daily_",
            "date_col": "trade_date",
            "root": "stock",
        }
        health_reports = iter([
            {"dates": ["20260423"], "codes_by_date": {}, "empty_codes": []},
            {"dates": [], "codes_by_date": {}, "empty_codes": []},
        ])
        events = []

        def record_deduplicate(*args, **kwargs):
            events.append("deduplicate")

        def record_missing(*args, **kwargs):
            events.append("missing")
            return []

        def record_scan(*args, **kwargs):
            events.append("scan")
            return next(health_reports)

        def record_dispatch(interface_name, interface_config, dates, **kwargs):
            events.append(("dispatch", list(dates)))

        with tempfile.TemporaryDirectory() as tmpdir:
            whitelist_path = Path(tmpdir) / "autofill_interface_whitelist.json"
            with mock.patch.object(autofill_runtime, "WHITELIST_PATH", whitelist_path), \
                mock.patch.object(autofill_runtime, "resolve_stable_trade_date", return_value="20260424"), \
                mock.patch.object(autofill_runtime, "deduplicate_interface", side_effect=record_deduplicate), \
                mock.patch.object(autofill_runtime, "get_missing_trade_dates", side_effect=record_missing), \
                mock.patch.object(autofill_runtime, "scan_incomplete_records", side_effect=record_scan), \
                mock.patch.object(autofill_runtime, "_dispatch_fill", side_effect=record_dispatch), \
                mock.patch.object(autofill_runtime, "get_local_latest_date", return_value="20260424"):
                ok = autofill_runtime._repair_single_interface(
                    "daily",
                    config,
                    ["20260423", "20260424"],
                    "20260424",
                    max_rounds=3,
                    execution_mode="full",
                    bypass_whitelist=True,
                )

                payload = autofill_runtime._load_interface_whitelist()

        self.assertTrue(ok)
        self.assertEqual(
            events,
            [
                "deduplicate",
                "missing",
                "scan",
                ("dispatch", ["20260423"]),
                "deduplicate",
                "missing",
                "scan",
            ],
        )
        self.assertTrue(payload["daily"]["enabled"])
        self.assertEqual(payload["daily"]["validated_start_date"], "20260423")
        self.assertEqual(payload["daily"]["validated_end_date"], "20260424")

    def test_explicit_full_bypasses_whitelist_eligibility(self):
        config = {"calendar_aligned": True}
        self.assertTrue(
            autofill_runtime._is_whitelist_eligible(config, bypass_whitelist=False)
        )
        self.assertFalse(
            autofill_runtime._is_whitelist_eligible(config, bypass_whitelist=True)
        )

    def test_non_calendar_interface_can_use_whitelist_fast_path(self):
        config = {"calendar_aligned": False}
        self.assertTrue(
            autofill_runtime._is_whitelist_eligible(config, bypass_whitelist=False)
        )

    def test_dispatch_margin_detail_uses_by_date_bulk_path(self):
        config = {
            "path": "margin_detail",
            "prefix": "margin_detail_",
            "date_col": "trade_date",
            "root": "stock",
        }
        with mock.patch.object(autofill_runtime, "fill_margin_detail_by_date", return_value=True) as bulk_fill, \
            mock.patch.object(autofill_runtime, "fill_by_code_interface") as by_code_fill:
            ok = autofill_runtime._dispatch_fill(
                "margin_detail",
                config,
                ["20260424"],
                code_type="stock",
            )

        self.assertTrue(ok)
        bulk_fill.assert_called_once_with(config, ["20260424"])
        by_code_fill.assert_not_called()

    def test_dispatch_cyq_chips_uses_special_parallel_path(self):
        config = {
            "path": "cyq_chips",
            "prefix": "cyq_chips_",
            "date_col": "trade_date",
            "root": "stock",
            "save_granularity": "year_stock",
        }
        with mock.patch.object(autofill_runtime, "fill_cyq_chips_by_stock", return_value=True) as special_fill, \
            mock.patch.object(autofill_runtime, "fill_by_code_interface") as by_code_fill:
            ok = autofill_runtime._dispatch_fill(
                "cyq_chips",
                config,
                ["20260424"],
                code_type="stock",
            )

        self.assertTrue(ok)
        special_fill.assert_called_once_with(config, ["20260424"])
        by_code_fill.assert_not_called()

    def test_dispatch_pledge_stat_uses_special_end_date_path(self):
        config = {
            "path": "pledge_stat",
            "prefix": "pledge_stat_",
            "date_col": "end_date",
            "root": "stock",
            "save_granularity": "year_stock",
        }
        with mock.patch.object(autofill_runtime, "fill_pledge_stat_by_end_date", return_value=True) as special_fill, \
            mock.patch.object(autofill_runtime, "fill_by_code_interface") as by_code_fill:
            ok = autofill_runtime._dispatch_fill(
                "pledge_stat",
                config,
                ["20260424", "20260429"],
                code_type="stock",
            )

        self.assertTrue(ok)
        special_fill.assert_called_once_with(config, ["20260424", "20260429"])
        by_code_fill.assert_not_called()

    def test_dispatch_express_uses_express_vip_path(self):
        config = {
            "path": "express",
            "prefix": "express_",
            "date_col": "ann_date",
            "root": "financial",
            "save_granularity": "year",
        }
        with mock.patch.object(autofill_runtime, "fill_express_vip_by_period", return_value=True) as special_fill, \
            mock.patch.object(autofill_runtime, "fill_by_code_interface") as by_code_fill:
            ok = autofill_runtime._dispatch_fill(
                "express",
                config,
                ["20260429"],
                code_type="stock",
            )

        self.assertTrue(ok)
        special_fill.assert_called_once_with(config, ["20260429"])
        by_code_fill.assert_not_called()

    def test_fill_express_vip_skips_outside_release_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            index_dir = Path(tmpdir) / "index"
            financial_dir = Path(tmpdir) / "financial"
            stock_dir.mkdir()
            index_dir.mkdir()
            (financial_dir / "express").mkdir(parents=True)
            autofill_runtime.initialize_runtime(
                pro_api=mock.Mock(),
                data_dir=stock_dir,
                index_dir=index_dir,
                financial_dir=financial_dir,
            )
            result = autofill_runtime.fill_express_vip_by_period(
                {"path": "express", "prefix": "express_", "date_col": "ann_date", "root": "financial"},
                ["20260615"],
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["covered_target_date"])

    def test_code_resume_state_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "pledge_detail_20260424_20260424.json"
            with mock.patch.object(autofill_runtime, "_code_resume_state_path", return_value=state_path):
                payload = {"codes": {}}
                autofill_runtime._mark_code_resume_state(payload, "000001.SZ", "no_data")
                autofill_runtime._save_code_resume_state(
                    "pledge_detail",
                    "20260424",
                    "20260424",
                    payload,
                )
                loaded = autofill_runtime._load_code_resume_state(
                    "pledge_detail",
                    "20260424",
                    "20260424",
                )

            self.assertEqual(loaded["codes"]["000001.SZ"]["status"], "no_data")

    def test_pledge_detail_enables_resume_code_state(self):
        self.assertTrue(INTERFACE_CONFIG["pledge_detail"].get("resume_code_state"))

    def test_pledge_detail_uses_announcement_style_window(self):
        config = INTERFACE_CONFIG["pledge_detail"]
        self.assertFalse(config.get("calendar_aligned", True))
        self.assertEqual(config.get("latest_trade_days_override"), 60)
        self.assertEqual(config.get("health_recent_trade_days"), 365)
        self.assertTrue(config.get("partition_by_year_dir"))
        self.assertEqual(config.get("save_granularity"), "year_stock")

    def test_pledge_stat_uses_year_stock_structure(self):
        config = INTERFACE_CONFIG["pledge_stat"]
        self.assertEqual(config.get("save_granularity"), "year_stock")
        self.assertTrue(config.get("partition_by_year_dir"))
        self.assertEqual(config.get("date_col"), "end_date")
        self.assertFalse(config.get("calendar_aligned", True))

    def test_margin_detail_allows_nullable_rqye(self):
        config = INTERFACE_CONFIG["margin_detail"]
        self.assertIn("rqye", config.get("required_columns", []))
        self.assertIn("rqye", config.get("nullable_columns", []))

    def test_pledge_stat_is_registered_before_pledge_detail(self):
        pledge_stat_index = AUTO_FILL_STOCK_BY_STOCK_NAMES.index("pledge_stat")
        self.assertGreaterEqual(pledge_stat_index, 0)
        self.assertNotIn("pledge_detail", AUTO_FILL_STOCK_BY_STOCK_NAMES)

    def test_financial_by_stock_interfaces_have_timeout_resume_and_frequent_progress(self):
        for name in [
            "express",
            "fina_mainbz",
            "disclosure_date",
        ]:
            with self.subTest(name=name):
                config = INTERFACE_CONFIG[name]
                self.assertTrue(config.get("resume_code_state"))
                self.assertEqual(config.get("api_process_timeout_sec"), 20)
                self.assertEqual(config.get("progress_log_interval"), 50)
                self.assertEqual(config.get("active_code_log_interval"), 0)
                self.assertFalse(config.get("live_progress_include_code", True))

    def test_autofill_registry_adds_progress_defaults_to_all_interfaces(self):
        registry = build_auto_fill_registry()
        for name, config in (
            (name, config)
            for category in registry.values()
            for bucket in category.values()
            for name, config in bucket.items()
        ):
            with self.subTest(name=name):
                self.assertEqual(config.get("health_scan_log_interval"), 1000)

        for name, config in registry["stock"]["by_stock"].items():
            if name == "cyq_chips":
                continue
            with self.subTest(name=name):
                self.assertTrue(config.get("resume_code_state"))
                self.assertLessEqual(config.get("api_process_timeout_sec"), 20)
                self.assertLessEqual(config.get("progress_log_interval"), 50)
                self.assertEqual(config.get("active_code_log_interval"), 0)
                self.assertFalse(config.get("live_progress_include_code", True))

        cyq_config = registry["stock"]["by_stock"]["cyq_chips"]
        self.assertEqual(cyq_config.get("batch_size"), 0)
        self.assertEqual(cyq_config.get("parallel_batches"), 1)
        self.assertLessEqual(cyq_config.get("batch_timeout_sec"), 120)
        self.assertLessEqual(cyq_config.get("retry_timeout_sec"), 60)
        self.assertLessEqual(cyq_config.get("progress_log_interval"), 50)

    def test_index_quote_interfaces_hide_current_code_in_live_progress(self):
        for name in ["index_daily", "index_weekly", "index_monthly"]:
            with self.subTest(name=name):
                self.assertFalse(INTERFACE_CONFIG[name].get("live_progress_include_code", True))

    def test_get_index_code_list_prefers_local_index_basic_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index_dir = Path(tmpdir)
            basic_dir = index_dir / "index_basic"
            basic_dir.mkdir(parents=True)
            pd.DataFrame(
                [
                    {"ts_code": "000001.SH"},
                    {"ts_code": "000300.SH"},
                    {"ts_code": "000905.CJ"},
                    {"ts_code": "SPX"},
                    {"ts_code": "000001.SH"},
                ]
            ).to_csv(basic_dir / "index_basic_all.csv", index=False)

            original_index_dir = autofill_runtime.INDEX_DIR
            original_pro = autofill_runtime.pro
            autofill_runtime.INDEX_DIR = index_dir
            autofill_runtime.pro = mock.Mock()
            try:
                code_list = autofill_runtime.get_index_code_list()
            finally:
                autofill_runtime.INDEX_DIR = original_index_dir
                autofill_runtime.pro = original_pro

            self.assertEqual(code_list, ["000001.SH", "000300.SH"])

    def test_get_index_code_list_prefers_local_active_index_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            index_dir = Path(tmpdir)
            basic_dir = index_dir / "index_basic"
            daily_dir = index_dir / "index_daily" / "2026"
            basic_dir.mkdir(parents=True)
            daily_dir.mkdir(parents=True)
            pd.DataFrame(
                [
                    {"ts_code": "000001.SH"},
                    {"ts_code": "000300.SH"},
                    {"ts_code": "399001.SZ"},
                ]
            ).to_csv(basic_dir / "index_basic_all.csv", index=False)
            pd.DataFrame([{"trade_date": "20260424", "close": 1}]).to_csv(
                daily_dir / "index_daily_000300.SH.csv",
                index=False,
            )

            original_index_dir = autofill_runtime.INDEX_DIR
            original_pro = autofill_runtime.pro
            autofill_runtime.INDEX_DIR = index_dir
            autofill_runtime.pro = mock.Mock()
            try:
                code_list = autofill_runtime.get_index_code_list(
                    {"path": "index_daily", "prefix": "index_daily_"}
                )
            finally:
                autofill_runtime.INDEX_DIR = original_index_dir
                autofill_runtime.pro = original_pro

            self.assertEqual(code_list, ["000300.SH"])

    def test_stk_factor_pro_bad_qfq_row_remains_date_specific_issue(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir)
            data_dir = stock_dir / "stk_factor_pro" / "2026"
            data_dir.mkdir(parents=True)
            pd.DataFrame(
                [
                    {
                        "ts_code": "600103.SH",
                        "trade_date": "20260423",
                        "open": 5.32,
                        "high": 5.69,
                        "low": 5.13,
                        "close": 5.14,
                        "open_qfq": "",
                        "high_qfq": "",
                        "low_qfq": "",
                        "close_qfq": "",
                    }
                ]
            ).to_csv(data_dir / "stk_factor_pro_600103.SH.csv", index=False)

            report = scan_incomplete_records(
                "stk_factor_pro",
                INTERFACE_CONFIG["stk_factor_pro"],
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )

        self.assertEqual(report["dates"], ["20260423"])
        self.assertEqual(report["codes_by_date"], {"20260423": ["600103.SH"]})

    def test_deduplicate_interface_skips_files_outside_target_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "stk_factor_pro" / "2026"
            data_dir.mkdir(parents=True)
            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260422", "close": 1},
                    {"ts_code": "000001.SZ", "trade_date": "20260422", "close": 1},
                ]
            ).to_csv(data_dir / "stk_factor_pro_000001.SZ.csv", index=False)
            pd.DataFrame(
                [
                    {"ts_code": "600103.SH", "trade_date": "20260423", "close": 2},
                    {"ts_code": "600103.SH", "trade_date": "20260423", "close": 3},
                ]
            ).to_csv(data_dir / "stk_factor_pro_600103.SH.csv", index=False)

            config = {
                "path": "stk_factor_pro",
                "prefix": "stk_factor_pro_",
                "date_col": "trade_date",
                "partition_by_year_dir": True,
                "root": "stock",
            }
            with mock.patch.object(autofill_runtime, "get_root_dir", return_value=Path(tmpdir)):
                removed = autofill_runtime.deduplicate_interface(
                    "stk_factor_pro",
                    config,
                    calendar_dates=["20260423"],
                )

            untouched = pd.read_csv(data_dir / "stk_factor_pro_000001.SZ.csv")
            deduped = pd.read_csv(data_dir / "stk_factor_pro_600103.SH.csv")

        self.assertEqual(removed, 1)
        self.assertEqual(len(untouched), 2)
        self.assertEqual(len(deduped), 1)
        self.assertEqual(int(deduped["close"].iloc[0]), 3)

    def test_missing_trade_dates_accepts_local_latest_after_calendar_target(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir)
            data_dir = stock_dir / "pledge_detail" / "2026"
            data_dir.mkdir(parents=True)
            pd.DataFrame(
                [{"ts_code": "000001.SZ", "ann_date": "20260425"}]
            ).to_csv(data_dir / "pledge_detail_000001.SZ.csv", index=False)

            missing = autofill_runtime.shared_get_missing_trade_dates(
                "pledge_detail",
                {
                    "path": "pledge_detail",
                    "prefix": "pledge_detail_",
                    "date_col": "ann_date",
                    "partition_by_year_dir": True,
                    "root": "stock",
                },
                ["20260424"],
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )

        self.assertEqual(missing, [])

    def test_stock_code_list_defaults_to_non_st_pool(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_basic_dir = Path(tmpdir) / "stock_basic"
            stock_basic_dir.mkdir(parents=True)
            pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ"]}).to_csv(
                stock_basic_dir / "stock_basic_non_st.csv",
                index=False,
            )

            with mock.patch.object(autofill_runtime, "DATA_DIR", Path(tmpdir)), \
                mock.patch.object(autofill_runtime, "pro") as pro_api:
                codes = autofill_runtime.get_stock_code_list({})

            self.assertEqual(codes, ["000001.SZ", "000002.SZ"])
            pro_api.stock_basic.assert_not_called()

    def test_stock_code_list_requires_explicit_include_st_opt_in(self):
        frame = pd.DataFrame({"ts_code": ["000001.SZ", "000003.SZ"]})

        with mock.patch.object(autofill_runtime, "pro") as pro_api:
            pro_api.stock_basic.return_value = frame
            codes = autofill_runtime.get_stock_code_list({"include_st_codes": True})

        self.assertEqual(codes, ["000001.SZ", "000003.SZ"])
        pro_api.stock_basic.assert_called_once_with(
            exchange="",
            list_status="L",
            fields="ts_code",
        )

    def test_date_fetch_with_stock_pool_passes_non_st_codes(self):
        config = {
            "path": "forecast",
            "prefix": "forecast_",
            "date_col": "ann_date",
            "root": "financial",
            "save_granularity": "year",
            "use_pagination": False,
            "force_by_date": True,
            "date_fetch_with_stock_pool": True,
            "code_param": "ts_code",
        }

        calls = []

        class FakePro:
            def forecast(self, **kwargs):
                calls.append(kwargs)
                return pd.DataFrame(
                    [{"ts_code": "000001.SZ", "ann_date": kwargs["ann_date"], "type": "预增"}]
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            financial_dir = Path(tmpdir) / "financial"
            financial_dir.mkdir(parents=True)
            with mock.patch.object(autofill_runtime, "FINANCIAL_DIR", financial_dir), \
                mock.patch.object(autofill_runtime, "pro", FakePro()), \
                mock.patch.object(
                    autofill_runtime,
                    "get_stock_code_list",
                    return_value=["000001.SZ", "000002.SZ"],
                ):
                ok = autofill_runtime.fill_by_date_interface("forecast", config, ["20260424"])

            output = financial_dir / "forecast" / "forecast_2026.csv"
            output_exists = output.exists()

        self.assertTrue(ok)
        self.assertEqual(calls, [{"ann_date": "20260424", "ts_code": "000001.SZ,000002.SZ"}])
        self.assertTrue(output_exists)

    def test_range_fetch_with_stock_pool_passes_start_end_and_non_st_codes(self):
        config = {
            "path": "forecast",
            "prefix": "forecast_",
            "date_col": "ann_date",
            "root": "financial",
            "save_granularity": "year",
            "use_date_range_fetch": True,
            "date_fetch_with_stock_pool": True,
            "code_param": "ts_code",
        }

        calls = []

        class FakePro:
            def forecast(self, **kwargs):
                calls.append(kwargs)
                return pd.DataFrame(
                    [{"ts_code": "000001.SZ", "ann_date": kwargs["start_date"], "type": "预增"}]
                )

        with tempfile.TemporaryDirectory() as tmpdir:
            financial_dir = Path(tmpdir) / "financial"
            financial_dir.mkdir(parents=True)
            with mock.patch.object(autofill_runtime, "FINANCIAL_DIR", financial_dir), \
                mock.patch.object(autofill_runtime, "pro", FakePro()), \
                mock.patch.object(
                    autofill_runtime,
                    "get_stock_code_list",
                    return_value=["000001.SZ", "000002.SZ"],
                ):
                ok = autofill_runtime.fill_by_date_interface(
                    "forecast",
                    config,
                    ["20260105", "20260424"],
                )

            output = financial_dir / "forecast" / "forecast_2026.csv"
            output_exists = output.exists()

        self.assertTrue(ok)
        self.assertEqual(
            calls,
            [{
                "start_date": "20260105",
                "end_date": "20260424",
                "ts_code": "000001.SZ,000002.SZ",
            }],
        )
        self.assertTrue(output_exists)

    def test_scan_incomplete_records_detects_missing_required_fields(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            data_dir = stock_dir / "daily"
            data_dir.mkdir(parents=True, exist_ok=True)
            filepath = data_dir / "daily_000001.SZ.csv"
            pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260410",
                        "open": 10.0,
                        "high": 10.5,
                        "low": 9.8,
                        "close": 10.2,
                        "vol": 1000,
                        "amount": 2000,
                    },
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260411",
                        "open": 10.1,
                        "high": 10.6,
                        "low": 9.9,
                        "close": None,
                        "vol": 1100,
                        "amount": 2100,
                    },
                ]
            ).to_csv(filepath, index=False)

            config = {
                "path": "daily",
                "prefix": "daily_",
                "date_col": "trade_date",
                "root": "stock",
                "required_columns": ["open", "high", "low", "close", "vol", "amount"],
            }
            report = scan_incomplete_records(
                "daily",
                config,
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )

            self.assertEqual(report["dates"], ["20260411"])
            self.assertEqual(report["codes_by_date"], {"20260411": ["000001.SZ"]})

    def test_scan_incomplete_records_reports_progress(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            data_dir = stock_dir / "daily"
            data_dir.mkdir(parents=True, exist_ok=True)
            for code in ["000001.SZ", "000002.SZ"]:
                pd.DataFrame(
                    [
                        {
                            "ts_code": code,
                            "trade_date": "20260424",
                            "open": 10.0,
                            "close": 10.2,
                        }
                    ]
                ).to_csv(data_dir / f"daily_{code}.csv", index=False)

            messages = []
            config = {
                "path": "daily",
                "prefix": "daily_",
                "date_col": "trade_date",
                "root": "stock",
                "required_columns": ["open", "close"],
            }
            scan_incomplete_records(
                "daily",
                config,
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
                progress_fn=messages.append,
                progress_interval=1,
            )

            self.assertIn("  🔎 daily: 缺参扫描 0/2", messages)
            self.assertIn("  🔎 daily: 缺参扫描 1/2", messages)
            self.assertIn("  🔎 daily: 缺参扫描 2/2", messages)
            self.assertIn("  🔎 daily: 缺参扫描完成 2/2", messages)

    def test_get_local_latest_date_scans_all_by_stock_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            data_dir = stock_dir / "cyq_chips"
            data_dir.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260410", "price": 10},
                    {"ts_code": "000001.SZ", "trade_date": "20260411", "price": 11},
                ]
            ).to_csv(data_dir / "cyq_chips_000001.SZ.csv", index=False)
            pd.DataFrame(
                [
                    {"ts_code": "000002.SZ", "trade_date": "20260410", "price": 20},
                    {"ts_code": "000002.SZ", "trade_date": "20260415", "price": 21},
                ]
            ).to_csv(data_dir / "cyq_chips_000002.SZ.csv", index=False)

            config = {
                "type": "by_stock",
                "path": "cyq_chips",
                "prefix": "cyq_chips_",
                "date_col": "trade_date",
                "root": "stock",
            }
            latest = get_local_latest_date(
                "cyq_chips",
                config,
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )
            self.assertEqual(latest, "20260415")

    def test_get_local_latest_date_ignores_malformed_tail_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            data_dir = stock_dir / "daily_basic"
            data_dir.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260410", "close": 10.0},
                    {"ts_code": "000001.SZ", "trade_date": "910841", "close": 10.1},
                ]
            ).to_csv(data_dir / "daily_basic_000001.SZ.csv", index=False)
            pd.DataFrame(
                [
                    {"ts_code": "000002.SZ", "trade_date": "20260416", "close": 11.0},
                ]
            ).to_csv(data_dir / "daily_basic_000002.SZ.csv", index=False)

            config = {
                "type": "standalone",
                "path": "daily_basic",
                "prefix": "daily_basic_",
                "date_col": "trade_date",
                "root": "stock",
            }
            latest = get_local_latest_date(
                "daily_basic",
                config,
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )
            self.assertEqual(latest, "20260416")

    def test_get_local_latest_date_returns_none_when_only_malformed_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            data_dir = stock_dir / "daily_basic"
            data_dir.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "16", "close": 10.0},
                    {"ts_code": "000001.SZ", "trade_date": "910841", "close": 10.1},
                ]
            ).to_csv(data_dir / "daily_basic_000001.SZ.csv", index=False)

            config = {
                "type": "standalone",
                "path": "daily_basic",
                "prefix": "daily_basic_",
                "date_col": "trade_date",
                "root": "stock",
            }
            latest = get_local_latest_date(
                "daily_basic",
                config,
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )
            self.assertIsNone(latest)

    def test_scan_incomplete_records_detects_date_partitioned_empty_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            data_dir = stock_dir / "limit_list_d"
            data_dir.mkdir(parents=True, exist_ok=True)
            filepath = data_dir / "limit_list_d_20260411.csv"
            filepath.write_text("", encoding="utf-8")

            config = {
                "path": "limit_list_d",
                "prefix": "limit_list_d_",
                "date_col": "trade_date",
                "root": "stock",
            }
            report = scan_incomplete_records(
                "limit_list_d",
                config,
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )

            self.assertEqual(report["dates"], ["20260411"])

    def test_scan_incomplete_records_respects_nullable_columns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            data_dir = stock_dir / "daily_basic"
            data_dir.mkdir(parents=True, exist_ok=True)
            filepath = data_dir / "daily_basic_000001.SZ.csv"
            pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SZ",
                        "trade_date": "20260411",
                        "close": 10.2,
                        "turnover_rate": 1.2,
                        "pb": 0.8,
                        "total_mv": 123456.0,
                        "pe": None,
                    }
                ]
            ).to_csv(filepath, index=False)

            config = {
                "path": "daily_basic",
                "prefix": "daily_basic_",
                "date_col": "trade_date",
                "root": "stock",
                "required_columns": ["close", "turnover_rate", "pb", "total_mv"],
                "nullable_columns": ["pe", "pe_ttm", "ps", "ps_ttm"],
            }
            report = scan_incomplete_records(
                "daily_basic",
                config,
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )

            self.assertEqual(report["dates"], [])
            self.assertEqual(report["codes_by_date"], {})

    def test_get_local_latest_date_supports_fixed_file_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            data_dir = stock_dir / "hm_list"
            data_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260410"},
                    {"ts_code": "000001.SZ", "trade_date": "20260417"},
                ]
            ).to_csv(data_dir / "hm_list.csv", index=False)

            config = {
                "type": "by_date",
                "path": "hm_list",
                "prefix": "hm_list_",
                "fixed_file_name": "hm_list.csv",
                "date_col": "trade_date",
                "root": "stock",
            }
            latest = get_local_latest_date(
                "hm_list",
                config,
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )
            self.assertEqual(latest, "20260417")

    def test_scan_incomplete_records_supports_fixed_file_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            data_dir = stock_dir / "hm_list"
            data_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260410", "close": 10.0},
                    {"ts_code": "000001.SZ", "trade_date": "20260411", "close": None},
                ]
            ).to_csv(data_dir / "hm_list.csv", index=False)

            config = {
                "path": "hm_list",
                "prefix": "hm_list_",
                "fixed_file_name": "hm_list.csv",
                "date_col": "trade_date",
                "root": "stock",
                "required_columns": ["close"],
            }
            report = scan_incomplete_records(
                "hm_list",
                config,
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )
            self.assertEqual(report["dates"], ["20260411"])

    def test_check_interface_by_date_supports_fixed_file_name(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            data_dir = stock_dir / "hm_list"
            data_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "trade_date": "20260410"},
                    {"ts_code": "000002.SZ", "trade_date": "20260412"},
                ]
            ).to_csv(data_dir / "hm_list.csv", index=False)

            config = {
                "path": "hm_list",
                "prefix": "hm_list_",
                "fixed_file_name": "hm_list.csv",
                "date_col": "trade_date",
                "root": "stock",
            }
            result = check_interface_by_date(
                "hm_list",
                config,
                stock_dir=stock_dir,
                index_dir=Path(tmpdir) / "index",
            )
            self.assertTrue(result["exists"])
            self.assertEqual(result["files"], 1)
            self.assertEqual(result["latest_date"], "20260412")

    def test_get_local_latest_date_supports_financial_root_ymd_stock(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            financial_dir = Path(tmpdir) / "financial"
            data_dir = financial_dir / "income" / "2026" / "04" / "22"
            data_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "ann_date": "20260422", "end_date": "20260331", "n_income": 100},
                    {"ts_code": "000001.SZ", "ann_date": "20260422", "end_date": "20251231", "n_income": 90},
                ]
            ).to_csv(data_dir / "income_000001.SZ.csv", index=False)

            config = {
                "type": "by_stock",
                "path": "income",
                "prefix": "income_",
                "date_col": "ann_date",
                "save_granularity": "ymd_stock",
                "root": "financial",
            }
            latest = get_local_latest_date(
                "income",
                config,
                stock_dir=Path(tmpdir) / "stock",
                index_dir=Path(tmpdir) / "index",
                financial_dir=financial_dir,
            )
            self.assertEqual(latest, "20260422")

    def test_scan_incomplete_records_supports_financial_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            financial_dir = Path(tmpdir) / "financial"
            data_dir = financial_dir / "fina_indicator" / "2026" / "04" / "22"
            data_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "ann_date": "20260422", "end_date": "20260331", "eps": None},
                ]
            ).to_csv(data_dir / "fina_indicator_000001.SZ.csv", index=False)

            config = {
                "path": "fina_indicator",
                "prefix": "fina_indicator_",
                "date_col": "ann_date",
                "save_granularity": "ymd_stock",
                "root": "financial",
                "required_columns": ["eps"],
            }
            report = scan_incomplete_records(
                "fina_indicator",
                config,
                stock_dir=Path(tmpdir) / "stock",
                index_dir=Path(tmpdir) / "index",
                financial_dir=financial_dir,
            )
            self.assertEqual(report["dates"], ["20260422"])
            self.assertEqual(report["codes_by_date"], {"20260422": ["000001.SZ"]})

    def test_classify_api_error_invalid_token(self):
        category, message = classify_api_error(Exception("您的token不对，请确认。"))
        self.assertEqual(category, "invalid_token")
        self.assertIn("TUSHARE_TOKEN", message)

    def test_classify_api_error_token_expired(self):
        category, message = classify_api_error(Exception("token expired"))
        self.assertEqual(category, "token_expired")
        self.assertIn("已过期", message)

    def test_classify_api_error_relay_inner_service_down(self):
        category, message = classify_api_error(
            Exception("HTTPConnectionPool(host='127.0.0.1', port=18010): Max retries exceeded with url: /")
        )
        self.assertEqual(category, "relay_inner_service_down")
        self.assertIn("备用中转", message)

    def test_stk_mins_is_registered_as_standalone_minute_interface(self):
        config = INTERFACE_CONFIG["stk_mins"]
        self.assertEqual(config["type"], "standalone")
        self.assertEqual(config["func"], "update_stk_mins")
        self.assertEqual(config["api"], "stk_mins")
        self.assertEqual(config["path"], "分钟数据")
        self.assertEqual(config["save_granularity"], "ymd_stock")
        self.assertEqual(config["freq"], "1min")
        self.assertEqual(config["max_rows_per_call"], 8000)
        self.assertFalse(config["calendar_aligned"])
        self.assertTrue(config["include_st_codes"])

    def test_dispatch_stk_mins_uses_special_single_stock_path(self):
        with mock.patch.object(
            autofill_runtime, "fill_stk_mins_single_stock", return_value={"ok": True, "covered_target_date": True}
        ) as mocked:
            result = autofill_runtime._dispatch_fill(
                "stk_mins",
                INTERFACE_CONFIG["stk_mins"],
                ["20260514"],
                code_type=None,
            )
        mocked.assert_called_once()
        self.assertEqual(result, {"ok": True, "covered_target_date": True})

    def test_stk_mins_is_in_default_main_workflow_by_stock_list(self):
        self.assertIn("stk_mins", AUTO_FILL_STOCK_BY_STOCK_NAMES)

    def test_stk_mins_splits_time_window_when_rows_near_8000_limit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            stock_dir = Path(tmpdir) / "stock"
            index_dir = Path(tmpdir) / "index"
            financial_dir = Path(tmpdir) / "financial"
            stock_dir.mkdir()
            index_dir.mkdir()
            financial_dir.mkdir()
            autofill_runtime.initialize_runtime(
                pro_api=mock.Mock(),
                data_dir=stock_dir,
                index_dir=index_dir,
                financial_dir=financial_dir,
            )

            call_windows = []

            def fake_call_api_in_process(api_name, timeout_sec=None, **kwargs):
                start_text = kwargs["start_date"]
                end_text = kwargs["end_date"]
                call_windows.append((start_text, end_text))
                if start_text.endswith("09:30:00") and end_text.endswith("15:00:00"):
                    return pd.DataFrame(
                        [{"ts_code": "000001.SZ", "trade_time": f"2026-05-14 09:{i%60:02d}:00", "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1, "amount": 1} for i in range(7900)]
                    )
                return pd.DataFrame(
                    [{"ts_code": "000001.SZ", "trade_time": start_text, "open": 1, "high": 1, "low": 1, "close": 1, "vol": 1, "amount": 1}]
                )

            with mock.patch.object(autofill_runtime, "get_stock_code_list", return_value=["000001.SZ"]), \
                mock.patch.object(autofill_runtime, "_call_api_in_process", side_effect=fake_call_api_in_process):
                result = autofill_runtime.fill_stk_mins_single_stock(
                    INTERFACE_CONFIG["stk_mins"],
                    ["20260514"],
                )

            self.assertTrue(result["ok"])
            self.assertGreaterEqual(len(call_windows), 3)
            self.assertTrue((stock_dir / "分钟数据" / "2026" / "05" / "14" / "000001" / "1min.csv").exists())

    def test_diagnose_api_connection_missing_token(self):
        with mock.patch.dict("os.environ", {}, clear=True), mock.patch(
            "utils.tushare_client.DEFAULT_TOKEN", ""
        ), mock.patch(
            "utils.tushare_client.get_tushare_bootstrap_config",
            return_value=mock.Mock(token="", http_url="http://124.220.22.110:8020/"),
        ):
            result = diagnose_api_connection(pro=None)
        self.assertFalse(result["ok"])

    def test_create_pro_api_falls_back_when_primary_relay_inner_service_down(self):
        class FakePro:
            def __init__(self):
                self._DataApi__http_url = None
                self.calls = 0

            def query(self, api_name, fields='', **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise Exception("HTTPConnectionPool(host='127.0.0.1', port=18010): Max retries exceeded with url: /")
                return pd.DataFrame([{"ok": 1}])

        fake_pro = FakePro()
        with mock.patch("utils.tushare_client.ts.pro_api", return_value=fake_pro):
            pro = tushare_client.create_pro_api(token="abc", timeout=10)
            frame = pro.query("dc_concept", trade_date="20260515")

        self.assertEqual(len(frame), 1)
        self.assertEqual(fake_pro._DataApi__http_url, tushare_client.FALLBACK_RELAY_API_URL)


if __name__ == "__main__":
    unittest.main()
