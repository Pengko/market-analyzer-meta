#!/usr/bin/env python3
"""Run auto_fill_data closed-loop repair for selected interfaces only."""

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import auto_fill_data as af


def parse_args():
    parser = argparse.ArgumentParser(description="Targeted runner for auto_fill_data interfaces")
    parser.add_argument(
        "--interfaces",
        nargs="+",
        default=["stk_auction_o", "stk_auction_c"],
        help="Interface names to repair (default: stk_auction_o stk_auction_c)",
    )
    parser.add_argument(
        "--target-date",
        required=True,
        help="Target end trade date in YYYYMMDD (e.g. 20260422)",
    )
    parser.add_argument(
        "--calendar-start",
        default="20200101",
        help="Trade calendar start date in YYYYMMDD (default: 20200101)",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip preflight API health check and run targeted interfaces directly",
    )
    return parser.parse_args()


def resolve_interface(interface_name):
    for bucket_name, bucket, code_type in (
        ("stock by_date", af.STOCK_INTERFACE_CONFIG.get("by_date", {}), None),
        ("stock by_stock", af.STOCK_INTERFACE_CONFIG.get("by_stock", {}), "stock"),
        ("index by_date", af.INDEX_INTERFACE_CONFIG.get("by_date", {}), "index"),
        ("index by_index", af.INDEX_INTERFACE_CONFIG.get("by_index", {}), "index"),
    ):
        config = bucket.get(interface_name)
        if config is not None:
            return config, code_type, bucket_name
    raise KeyError(f"Interface not found in auto-fill registry: {interface_name}")


def build_summary(interface_name, config, trade_calendar):
    latest_local = af.runtime.get_local_latest_date(interface_name, config)
    missing_dates = af.runtime.get_missing_trade_dates(interface_name, config, trade_calendar)
    report = af.runtime.scan_incomplete_records(interface_name, config)
    report = af.runtime._limit_health_report_to_calendar(report, trade_calendar)
    return {
        "latest_local_date": latest_local,
        "missing_trade_dates": missing_dates,
        "incomplete_dates": sorted(set(report.get("dates", []))),
        "empty_codes_count": len(report.get("empty_codes", [])),
        "empty_codes_sample": (report.get("empty_codes", []) or [])[:10],
    }


def main():
    args = parse_args()

    if not args.skip_preflight and not af.runtime.preflight_api_health_check():
        print("SUMMARY_JSON=" + json.dumps({"ok": False, "error": "preflight_failed"}, ensure_ascii=False))
        return 2

    trade_calendar = af.get_trade_dates(start_date=args.calendar_start, end_date=args.target_date)
    if not trade_calendar:
        print("SUMMARY_JSON=" + json.dumps({"ok": False, "error": "empty_trade_calendar"}, ensure_ascii=False))
        return 3

    latest_trade_date = trade_calendar[-1]
    requested = list(dict.fromkeys(args.interfaces))
    resolved = {name: resolve_interface(name) for name in requested}
    configs = {name: item[0] for name, item in resolved.items()}

    print(f"Target trade calendar: {trade_calendar[0]} ~ {latest_trade_date} ({len(trade_calendar)} days)")

    for name in requested:
        config, code_type, bucket_name = resolved[name]
        print(f"Running {name} from {bucket_name}, code_type={code_type or 'date'}")
        af.runtime._repair_single_interface(
            interface_name=name,
            config=config,
            trade_calendar=trade_calendar,
            latest_trade_date=latest_trade_date,
            code_type=code_type,
            bypass_whitelist=False,
        )

    summary = {
        "ok": True,
        "target_date": args.target_date,
        "latest_trade_date": latest_trade_date,
        "interfaces": {name: build_summary(name, configs[name], trade_calendar) for name in requested},
    }
    print("SUMMARY_JSON=" + json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
