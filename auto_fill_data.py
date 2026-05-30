#!/usr/bin/env python3
"""
主要作用:
- 作为项目的“体检 + 补齐”主入口
- 检查股票和指数数据的完整性
- 自动补齐缺失交易日的数据
- 在补齐前后执行必要的去重和结果汇总

⚠️ API 路由警示:
- 本项目默认走中转平台，不走官方域名。
- 如发现日志出现 `api.tushare.pro`，请立即检查 `utils/tushare_client.py` 与环境变量配置。

适用场景:
- 历史数据可能有断档、漏拉、重复写入时
- 需要一次性把数据状态修复到最新时
"""

import os
import sys
import json
import argparse
import traceback
from datetime import datetime
from pathlib import Path

for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
    os.environ.pop(key, None)

sys.path.insert(0, "/Users/penghongming/agent-skills/custom/tushare_pro")

from utils.paths import get_financial_data_dir, get_index_data_dir, get_stock_data_dir
from utils.tushare_client import create_pro_api
import update_weekly_monthly as wm_updater
import core.files
from core import autofill_runtime as runtime
from core import autofill_workflow as workflow
from core import theme_fillers
from core.logging_utils import log as shared_log
from core.registry import build_auto_fill_registry, list_auto_fill_interface_names

DATA_DIR = get_stock_data_dir()
INDEX_DIR = get_index_data_dir()
FINANCIAL_DIR = get_financial_data_dir()
UPDATED_INTERFACES = set()

pro = create_pro_api()
AUTO_FILL_REGISTRY = build_auto_fill_registry()
STOCK_INTERFACE_CONFIG = AUTO_FILL_REGISTRY["stock"]
INDEX_INTERFACE_CONFIG = AUTO_FILL_REGISTRY["index"]

log = runtime.log
get_trade_dates = runtime.get_trade_dates
get_local_latest_date = runtime.get_local_latest_date
get_root_dir = runtime.get_root_dir
get_report_target_date = runtime.get_report_target_date

fill_dc_concept_theme = theme_fillers.fill_dc_concept_theme
fill_kpl_concept_cons_theme = theme_fillers.fill_kpl_concept_cons_theme
fill_dc_concept_cons_theme = theme_fillers.fill_dc_concept_cons_theme
fill_ths_index_theme = theme_fillers.fill_ths_index_theme
fill_ths_member_theme = theme_fillers.fill_ths_member_theme
fill_ths_daily_theme = theme_fillers.fill_ths_daily_theme
fill_dc_daily_theme = theme_fillers.fill_dc_daily_theme
fill_dc_index_theme = theme_fillers.fill_dc_index_theme
fill_dc_member_theme = theme_fillers.fill_dc_member_theme


def run_autofill_workflow():
    return workflow.run_autofill_workflow()


def record_updated_interface(interface_name):
    if interface_name:
        UPDATED_INTERFACES.add(str(interface_name))


def clear_updated_interfaces():
    UPDATED_INTERFACES.clear()


def parse_args():
    parser = argparse.ArgumentParser(description="智能数据补全脚本")
    parser.add_argument(
        "--mode",
        choices=["auto", "full", "latest"],
        default="auto",
        help="执行模式: auto=自动判断, full=全量闭环, latest=仅补最近 N 个交易日",
    )
    parser.add_argument(
        "--lag-trigger-trade-days",
        type=int,
        default=1,
        help="auto 模式下，接口滞后超过多少个交易日时切到 full，默认 1",
    )
    parser.add_argument(
        "--latest-trade-days",
        type=int,
        default=10,
        help="latest 模式下补齐最近多少个交易日，默认 10",
    )
    parser.add_argument(
        "--interfaces",
        default="",
        help="只运行指定接口，逗号分隔；默认空表示运行主脚本全部接口",
    )
    parser.add_argument(
        "--ignore-whitelist",
        action="store_true",
        help="忽略现有白名单，执行完整体检闭环；不自动跳过白名单已覆盖区间",
    )
    parser.add_argument(
        "--skip-parquet-rebuild",
        action="store_true",
        help="跳过后置 CSV→parquet 重建",
    )
    parser.add_argument(
        "--force-refetch",
        action="store_true",
        help="仅 full 模式可用：先按历史区间强制重拉，再体检修复并更新白名单",
    )
    parser.add_argument(
        "--history-start-date",
        default="20200101",
        help="强制历史重拉的起始交易日，默认 20200101",
    )
    parser.add_argument(
        "--history-end-date",
        default="",
        help="强制历史重拉的截止交易日，默认空表示今天",
    )
    parser.add_argument(
        "--tencent-min",
        action="store_true",
        help="使用腾讯 API 获取当日实时分钟数据（单独模式，不执行主流程）",
    )
    parser.add_argument(
        "--tencent-min-symbols",
        default="",
        help="腾讯分钟模式: 指定股票代码，逗号分隔；空则使用本地非 ST 股票池",
    )
    parser.add_argument(
        "--tencent-min-batch-size",
        type=int,
        default=0,
        help="腾讯分钟模式: 最多获取多少只，0=全部",
    )
    parser.add_argument(
        "--tencent-min-workers",
        type=int,
        default=4,
        help="腾讯分钟模式: 并发线程数，默认 4",
    )
    return parser.parse_args()


def normalize_interface_filter(text):
    return [item.strip() for item in str(text or "").split(",") if item.strip()]


PARQUET_REBUILDABLE = sorted(
    core.files._FLAT_PARQUET_INTERFACES
    | core.files._COMBINED_PARQUET_INTERFACES
    | core.files._PER_STOCK_BY_DATE_INTERFACES
    | core.files._YEARLY_COMBINED_INTERFACES
    | core.files._MONTHLY_COMBINED_INTERFACES
)
PARQUET_WHITELIST_PATH = Path(__file__).resolve().parent / "logs" / "parquet_rebuild_whitelist.json"


def _parquet_rebuild(updated_interfaces):
    """Rebuild parquet from CSV for updated interfaces, then verify and whitelist."""
    if not updated_interfaces:
        log("ℹ️ 本轮无新增数据，跳过 parquet 重建", "INFO")
        return True

    from rebuild_parquet import rebuild_interface as _rebuild

    # Load existing whitelist
    whitelist = {}
    if PARQUET_WHITELIST_PATH.exists():
        try:
            whitelist = json.loads(PARQUET_WHITELIST_PATH.read_text())
        except Exception:
            whitelist = {}

    to_rebuild = [name for name in updated_interfaces if name in PARQUET_REBUILDABLE]
    if not to_rebuild:
        log("ℹ️ 本轮更新的接口无需 parquet 重建", "INFO")
        return True

    # Rebuild interfaces whose whitelist is missing but only if CSV data exists
    for name in PARQUET_REBUILDABLE:
        if name in whitelist or name in to_rebuild:
            continue
        data_root = DATA_DIR / name
        if not data_root.exists() or not any(data_root.rglob("*.csv")):
            continue
        to_rebuild.append(name)

    log(f"\n{'=' * 60}", "INFO")
    log(f"🔄 开始 parquet 重建 ({len(to_rebuild)} 个接口)", "INFO")
    log("=" * 60, "INFO")

    import time as _time
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _wl_lock = threading.Lock()

    def _save_whitelist(wl):
        PARQUET_WHITELIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        PARQUET_WHITELIST_PATH.write_text(json.dumps(wl, ensure_ascii=False, indent=2))

    total_ok = 0

    def _rebuild_one(name):
        t0 = _time.monotonic()
        try:
            count = _rebuild(name)
            dt = _time.monotonic() - t0
            entry = {
                "rebuilt_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "duration_seconds": round(dt, 1),
                "file_count": count,
            }
            with _wl_lock:
                whitelist[name] = entry
                _save_whitelist(whitelist)
            return name, count, dt, None
        except Exception as e:
            traceback.print_exc()
            with _wl_lock:
                _save_whitelist(whitelist)
            return name, 0, 0, e

    with ThreadPoolExecutor(max_workers=min(8, len(to_rebuild))) as pool:
        futs = {pool.submit(_rebuild_one, name): name for name in sorted(to_rebuild)}
        for f in as_completed(futs):
            name, count, dt, err = f.result()
            if err:
                log(f"  ❌ {name}: {err}", "ERROR")
            else:
                total_ok += 1
                log(f"  ✅ {name}: {count} parquet ({dt:.0f}s)", "SUCCESS")

    _save_whitelist(whitelist)

    log(f"🏁 parquet 重建完成: {total_ok}/{len(to_rebuild)} 成功", "SUCCESS")
    return total_ok == len(to_rebuild)


def main():
    started_at = runtime.time.monotonic()
    args = parse_args()

    # ─── 腾讯实时分钟模式 ───
    if args.tencent_min:
        sys.path.insert(0, str(Path(__file__).parent))
        from core.tencent_min_fetcher import (
            batch_fetch_and_save,
            get_stock_code_list_from_local,
        )

        if args.tencent_min_symbols:
            codes = [c.strip() for c in args.tencent_min_symbols.split(",") if c.strip()]
        else:
            codes = get_stock_code_list_from_local()

        if args.tencent_min_batch_size > 0:
            codes = codes[: args.tencent_min_batch_size]

        log(f"腾讯分钟模式: 准备获取 {len(codes)} 只股票的当日实时分钟数据", "INFO")
        results = batch_fetch_and_save(
            codes,
            max_workers=args.tencent_min_workers,
        )
        success = sum(1 for r in results if r["status"] == "success")
        log(f"腾讯分钟模式完成: 成功 {success}/{len(codes)}", "INFO")
        return 0

    selected_interfaces = normalize_interface_filter(args.interfaces)
    valid_interfaces = set(list_auto_fill_interface_names(AUTO_FILL_REGISTRY))
    unknown_interfaces = [name for name in selected_interfaces if name not in valid_interfaces]
    if unknown_interfaces:
        log(f"❌ 未识别的接口: {', '.join(unknown_interfaces)}", "ERROR")
        log(f"可选接口: {', '.join(sorted(valid_interfaces))}", "INFO")
        return 1
    if args.force_refetch and args.mode != "full":
        log("❌ --force-refetch 仅支持 --mode full", "ERROR")
        return 1
    if args.force_refetch and not selected_interfaces:
        log("❌ --force-refetch 需要同时指定 --interfaces，避免误触全库重拉", "ERROR")
        return 1
    clear_updated_interfaces()
    original_dispatch_fill = runtime._dispatch_fill

    def tracked_dispatch_fill(interface_name, config, trade_dates, code_list=None, code_type=None):
        result = original_dispatch_fill(
            interface_name,
            config,
            trade_dates,
            code_list=code_list,
            code_type=code_type,
        )
        ok = result.get("ok") if isinstance(result, dict) else bool(result)
        if ok:
            record_updated_interface(interface_name)
        return result

    runtime._dispatch_fill = tracked_dispatch_fill
    workflow.initialize_workflow(
        data_dir=DATA_DIR,
        index_dir=INDEX_DIR,
        financial_dir=FINANCIAL_DIR,
        stock_interface_config=STOCK_INTERFACE_CONFIG,
        index_interface_config=INDEX_INTERFACE_CONFIG,
        log_fn=log,
        get_trade_dates_fn=get_trade_dates,
        get_local_latest_date_fn=get_local_latest_date,
        get_report_target_date_fn=get_report_target_date,
        resolve_run_target_trade_date_fn=runtime.resolve_run_target_trade_date,
        repair_single_interface_fn=runtime._repair_single_interface,
        dispatch_fill_fn=runtime._dispatch_fill,
        get_interface_whitelist_record_fn=runtime._get_interface_whitelist_record,
        calendar_window_covered_by_whitelist_fn=runtime._calendar_window_covered_by_whitelist,
        calendar_dates_not_covered_by_whitelist_fn=runtime._calendar_dates_not_covered_by_whitelist,
        preflight_api_health_check_fn=runtime.preflight_api_health_check,
        fill_dc_concept_cons_theme_fn=fill_dc_concept_cons_theme,
        weekly_monthly_updater=wm_updater,
        mode=args.mode,
        lag_trigger_trade_days=args.lag_trigger_trade_days,
        latest_mode_trade_days=args.latest_trade_days,
        selected_interfaces=selected_interfaces,
        ignore_whitelist=args.ignore_whitelist or args.force_refetch,
        force_refetch=args.force_refetch,
        history_start_date=args.history_start_date,
        history_end_date=args.history_end_date or None,
    )
    try:
        ok = run_autofill_workflow()
        if not ok:
            return 1
        if not args.skip_parquet_rebuild:
            _parquet_rebuild(UPDATED_INTERFACES)
        log(f"🏁 主脚本完成，总耗时 {runtime.format_duration(runtime.time.monotonic() - started_at)}", "SUCCESS")
        return 0
    except KeyboardInterrupt:
        log(
            f"🚫 脚本被中断（KeyboardInterrupt / Ctrl+C），已运行 {runtime.format_duration(runtime.time.monotonic() - started_at)}",
            "ERROR",
        )
        return 130
    except BaseException as exc:
        log(
            f"💥 脚本异常退出: {exc} | 已运行 {runtime.format_duration(runtime.time.monotonic() - started_at)}",
            "ERROR",
        )
        for line in traceback.format_exc().rstrip().splitlines():
            log(line, "ERROR")
        return 1
    finally:
        runtime._dispatch_fill = original_dispatch_fill


runtime.initialize_runtime(
    pro_api=pro,
    data_dir=DATA_DIR,
    index_dir=INDEX_DIR,
    financial_dir=FINANCIAL_DIR,
    log_fn=lambda msg, level="INFO": shared_log(msg, level),
    theme_handlers={
        "dc_concept": fill_dc_concept_theme,
        "dc_concept_cons": fill_dc_concept_cons_theme,
        "kpl_concept_cons": fill_kpl_concept_cons_theme,
        "ths_index": fill_ths_index_theme,
        "ths_member": fill_ths_member_theme,
        "ths_daily": fill_ths_daily_theme,
        "dc_daily": fill_dc_daily_theme,
        "dc_index": fill_dc_index_theme,
        "dc_member": fill_dc_member_theme,
    },
)

theme_fillers.initialize_theme_runtime(
    pro_api=pro,
    data_dir=DATA_DIR,
    get_root_dir_fn=get_root_dir,
    log_fn=log,
)


if __name__ == "__main__":
    raise SystemExit(main())
