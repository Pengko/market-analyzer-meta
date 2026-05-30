#!/usr/bin/env python3
"""
主要作用:
- 覆盖接口探测模式的关键路径
- 验证日期选择、CLI 参数、结果分类和报告输出
"""

import argparse
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from core.probe import (
    add_probe_arguments,
    get_sample_stock_codes,
    probe_interface,
    resolve_recent_period_end,
    resolve_stable_trade_date,
    run_probe_suite,
    write_probe_report,
)


class FakePro:
    def __init__(self):
        self.calls = []

    def stock_basic(self, exchange="", list_status="L"):
        self.calls.append(("stock_basic", {"exchange": exchange, "list_status": list_status}))
        return pd.DataFrame(
            [
                {"ts_code": "000001.SZ", "name": "平安银行"},
                {"ts_code": "000002.SZ", "name": "万科A"},
                {"ts_code": "000003.SZ", "name": "ST测试"},
                {"ts_code": "000004.SZ", "name": "国华网安"},
            ]
        )

    def daily(self, trade_date=None, limit=None, offset=None):
        self.calls.append(("daily", {"trade_date": trade_date, "limit": limit, "offset": offset}))
        if offset == 0:
            return pd.DataFrame([{"ts_code": "000001.SZ", "trade_date": trade_date}])
        return pd.DataFrame()

    def margin(self, ts_code=None, start_date=None, end_date=None):
        self.calls.append(
            ("margin", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})
        )
        if ts_code == "000001.SZ":
            return pd.DataFrame([{"ts_code": ts_code, "trade_date": start_date}])
        return pd.DataFrame()

    def index_weekly(self, ts_code=None, start_date=None, end_date=None):
        self.calls.append(
            ("index_weekly", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})
        )
        return pd.DataFrame([{"ts_code": ts_code, "trade_date": end_date}])

    def weekly(self, ts_code=None, start_date=None, end_date=None):
        self.calls.append(
            ("weekly", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})
        )
        return pd.DataFrame([{"ts_code": ts_code, "trade_date": end_date}])

    def monthly(self, ts_code=None, start_date=None, end_date=None):
        self.calls.append(
            ("monthly", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})
        )
        return pd.DataFrame([{"ts_code": ts_code, "trade_date": end_date}])

    def stk_weekly_monthly(self, ts_code=None, freq=None, start_date=None, end_date=None):
        self.calls.append(
            (
                "stk_weekly_monthly",
                {
                    "ts_code": ts_code,
                    "freq": freq,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )
        )
        return pd.DataFrame()


class ErrorPro(FakePro):
    def daily(self, trade_date=None, limit=None, offset=None):
        raise Exception("Read timed out")


class ProbeTests(unittest.TestCase):
    def test_resolve_stable_trade_date_defaults_to_previous_trade_day(self):
        calendar = ["20260414", "20260415", "20260416", "20260417"]
        self.assertEqual(resolve_stable_trade_date(calendar), "20260416")

    def test_resolve_recent_period_end_uses_last_completed_week_and_month(self):
        calendar = [
            "20260407",
            "20260408",
            "20260409",
            "20260410",
            "20260414",
            "20260415",
            "20260416",
        ]
        self.assertEqual(resolve_recent_period_end(calendar, "weekly", anchor_date="20260416"), "20260410")
        self.assertEqual(resolve_recent_period_end(calendar, "monthly", anchor_date="20260416"), "20260416")

    def test_get_sample_stock_codes_filters_st_and_honors_sample_size(self):
        pro = FakePro()
        codes = get_sample_stock_codes(pro, sample_size=2)
        self.assertEqual(codes, ["000001.SZ", "000002.SZ"])

    def test_probe_interface_classifies_success_empty_and_error(self):
        calendar = ["20260415", "20260416", "20260417"]
        success = probe_interface(
            "daily",
            {"type": "standalone", "group": "core"},
            FakePro(),
            calendar,
            sample_size=2,
        )
        self.assertEqual(success["status"], "success")
        self.assertEqual(success["row_count"], 1)

        empty = probe_interface(
            "margin",
            {"type": "by_stock", "group": "margin", "api": "margin"},
            FakePro(),
            calendar,
            sample_size=1,
            sample_codes=["000002.SZ"],
        )
        self.assertEqual(empty["status"], "empty")

        error = probe_interface(
            "daily",
            {"type": "standalone", "group": "core"},
            ErrorPro(),
            calendar,
            sample_size=2,
        )
        self.assertEqual(error["status"], "error")
        self.assertEqual(error["error_category"], "timeout")

    def test_probe_uses_registry_pagination_config(self):
        calendar = ["20260415", "20260416", "20260417"]
        pro = FakePro()
        result = probe_interface(
            "daily",
            {
                "type": "by_date",
                "group": "core",
                "api": "daily",
                "page_limit": 3,
                "max_pages": 3,
            },
            pro,
            calendar,
            probe_date="20260416",
        )
        self.assertEqual(result["status"], "success")
        daily_calls = [kwargs for name, kwargs in pro.calls if name == "daily"]
        self.assertEqual([call["offset"] for call in daily_calls], [0, 3, 6])
        self.assertTrue(all(call["limit"] == 3 for call in daily_calls))

    def test_probe_weekly_uses_primary_shape_before_fallback(self):
        calendar = ["20260415", "20260416", "20260417"]
        result = probe_interface(
            "weekly",
            {"type": "standalone", "group": "core", "fetch_granularity": "weekly"},
            FakePro(),
            calendar,
            sample_size=1,
            sample_codes=["000001.SZ"],
        )
        self.assertEqual(result["status"], "success")
        self.assertIn("standalone_weekly_api_shape", result["request_mode"])

    def test_probe_weekly_uses_stk_weekly_monthly_before_fallback(self):
        class WeeklyPrimaryPro(FakePro):
            def weekly(self, ts_code=None, start_date=None, end_date=None):
                self.calls.append(
                    ("weekly", {"ts_code": ts_code, "start_date": start_date, "end_date": end_date})
                )
                return pd.DataFrame()

            def stk_weekly_monthly(self, ts_code=None, freq=None, start_date=None, end_date=None):
                self.calls.append(
                    (
                        "stk_weekly_monthly",
                        {
                            "ts_code": ts_code,
                            "freq": freq,
                            "start_date": start_date,
                            "end_date": end_date,
                        },
                    )
                )
                return pd.DataFrame([{"ts_code": ts_code, "trade_date": end_date}])

        calendar = ["20260415", "20260416", "20260417"]
        pro = WeeklyPrimaryPro()
        result = probe_interface(
            "weekly",
            {"type": "standalone", "group": "core", "fetch_granularity": "weekly"},
            pro,
            calendar,
            sample_size=1,
            sample_codes=["000001.SZ"],
        )
        self.assertEqual(result["status"], "success")
        call_names = [name for name, _ in pro.calls]
        self.assertIn("stk_weekly_monthly", call_names)
        self.assertNotIn("weekly", call_names)

    def test_write_probe_report_keeps_success_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            report_path = Path(tmpdir) / "probe.md"
            write_probe_report(
                [
                    {
                        "interface": "daily",
                        "group": "core",
                        "status": "success",
                        "request_mode": "standalone_paged",
                        "params_summary": "trade_date=20260416",
                        "row_count": 10,
                        "elapsed_ms": 120,
                        "error_category": "",
                        "error_message": "",
                    },
                    {
                        "interface": "moneyflow",
                        "group": "core",
                        "status": "error",
                        "request_mode": "standalone_paged",
                        "params_summary": "trade_date=20260416",
                        "row_count": 0,
                        "elapsed_ms": 88,
                        "error_category": "timeout",
                        "error_message": "Read timed out",
                    },
                ],
                report_path,
            )
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("## daily", content)
            self.assertNotIn("moneyflow", content)

    def test_run_probe_suite_respects_group_order_and_summary(self):
        calls = []

        def fake_probe(interface_name, config, pro, trade_calendar, **kwargs):
            calls.append(interface_name)
            status = "success" if interface_name == "daily" else "empty"
            return {
                "interface": interface_name,
                "group": config["group"],
                "status": status,
                "request_mode": "mock",
                "params_summary": "mock=true",
                "row_count": 1 if status == "success" else 0,
                "elapsed_ms": 5,
                "error_category": "",
                "error_message": "",
            }

        pro = FakePro()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_probe_suite(
                pro=pro,
                trade_calendar=["20260415", "20260416", "20260417"],
                group="core",
                report_path=Path(tmpdir) / "probe.md",
                interface_config={
                    "daily": {"group": "core", "type": "standalone"},
                    "moneyflow": {"group": "core", "type": "standalone"},
                },
                groups={"core": ["daily", "moneyflow"]},
                diagnose_func=lambda pro=None: {"ok": True, "message": "ok"},
                sleep_func=lambda seconds: None,
                probe_func=fake_probe,
            )

        self.assertEqual(calls, ["daily", "moneyflow"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], {"success": 1, "empty": 1, "error": 0})

    def test_probe_argument_builder_accepts_probe_options(self):
        parser = argparse.ArgumentParser()
        parser.add_argument("--group")
        parser.add_argument("interfaces", nargs="*")
        add_probe_arguments(parser)
        args = parser.parse_args(["--probe", "--group", "limit"])
        self.assertTrue(args.probe)
        self.assertEqual(args.group, "limit")

        args = parser.parse_args(["--probe", "daily", "top_list"])
        self.assertEqual(args.interfaces, ["daily", "top_list"])

        args = parser.parse_args(["--probe", "--probe-date", "20260416", "--sample-size", "5"])
        self.assertEqual(args.probe_date, "20260416")
        self.assertEqual(args.sample_size, 5)


if __name__ == "__main__":
    unittest.main()
