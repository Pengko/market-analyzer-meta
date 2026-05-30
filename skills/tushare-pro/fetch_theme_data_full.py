#!/usr/bin/env python3
"""
主要作用:
- 全量抓取 theme_data 下的主题接口
- 当前支持: ths_index / ths_member / ths_daily / dc_daily / dc_index / dc_member
"""

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.paths import get_financial_data_dir, get_index_data_dir, get_stock_data_dir
from utils.tushare_client import create_pro_api
from core import autofill_runtime as runtime
from core import theme_fillers
from core.calendar import get_trade_dates
from core.health import get_root_dir
from core.registry import build_auto_fill_registry


def log(msg, level="INFO"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {level}: {msg}")
    sys.stdout.flush()


def parse_args():
    parser = argparse.ArgumentParser(description="全量抓取主题接口数据")
    parser.add_argument(
        "--interfaces",
        default="ths_index,ths_member,ths_daily,dc_daily,dc_index,dc_member",
        help="要抓取的接口，逗号分隔: ths_index,ths_member,ths_daily,dc_daily,dc_index,dc_member",
    )
    parser.add_argument(
        "--start-date",
        nargs="?",
        const="20200101",
        default="20200101",
        help="ths_daily/dc_daily/dc_index/dc_member 全量开始日期，默认 20200101",
    )
    parser.add_argument(
        "--end-date",
        nargs="?",
        const=None,
        default=None,
        help="ths_daily/dc_daily/dc_index/dc_member 截止日期，默认今天",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=3,
        help="分轮修复的最大轮数，默认 3",
    )
    return parser.parse_args()


def normalize_interfaces(text):
    items = [item.strip() for item in str(text or "").split(",") if item.strip()]
    normalized = []
    for item in items:
        if item not in normalized:
            normalized.append(item)
    return normalized


def resolve_interface_trade_dates(interface_name, config, pro, start_date, end_date):
    if interface_name in {"ths_daily", "dc_daily", "dc_index", "dc_member"}:
        trade_dates = get_trade_dates(pro, start_date=start_date, end_date=end_date)
        override_days = int(config.get("full_trade_days_override", 0) or 0)
        if override_days > 0:
            return [str(item) for item in trade_dates[-override_days:]]
        return trade_dates
    return [str(end_date)]


def inspect_theme_interface(interface_name, config, trade_dates):
    latest_trade_date = str(trade_dates[-1]) if trade_dates else datetime.now().strftime("%Y%m%d")
    return runtime._repair_single_interface(
        interface_name,
        config,
        trade_dates,
        latest_trade_date,
        code_type=None,
        execution_mode="full",
        bypass_whitelist=True,
        inspect_only=True,
    )


def fetch_theme_interface(interface_name, config, trade_dates, fill_fn):
    result = fill_fn(config, trade_dates)
    ok = result.get("ok") if isinstance(result, dict) else bool(result)
    if not ok:
        return {"ok": False, "complete": False, "inspect_result": None}

    inspect_result = inspect_theme_interface(
        interface_name,
        config,
        trade_dates,
    )
    inspect_complete = isinstance(inspect_result, dict) and inspect_result.get("complete")
    return {
        "ok": True,
        "complete": inspect_complete,
        "inspect_result": inspect_result,
    }


def repair_theme_interface(interface_name, config, trade_dates, health_result):
    latest_trade_date = str(trade_dates[-1]) if trade_dates else datetime.now().strftime("%Y%m%d")
    return runtime._repair_single_interface(
        interface_name,
        config,
        trade_dates,
        latest_trade_date,
        code_type=None,
        max_rounds=1,
        execution_mode="full",
        bypass_whitelist=True,
        repair_only=True,
        initial_health_result=health_result,
    )


def run_theme_rounds(tasks, max_rounds=3):
    if not tasks:
        log("没有可执行的 theme 接口任务", "WARNING")
        return True

    pending = []
    total = len(tasks)

    log("\n📦 第 1 轮：逐接口全量拉取 -> 体检 -> 更新白名单")
    log("=" * 60)
    for index, task in enumerate(tasks, start=1):
        interface_name = task["name"]
        config = task["config"]
        trade_dates = task["trade_dates"]
        fill_fn = task["fill_fn"]

        if not trade_dates:
            log(f"第 1 轮进度: {index}/{total} | 当前接口 {interface_name} | 无有效日期，跳过", "WARNING")
            pending.append({**task, "reason": "no_trade_dates", "health_result": None})
            continue

        if len(trade_dates) > 1:
            log(
                f"第 1 轮进度: {index}/{total} | 当前接口 {interface_name} | "
                f"全量区间 {trade_dates[0]} ~ {trade_dates[-1]} ({len(trade_dates)} 个交易日)",
                "INFO",
            )
        else:
            log(
                f"第 1 轮进度: {index}/{total} | 当前接口 {interface_name} | 目标日期 {trade_dates[-1]}",
                "INFO",
            )

        fetch_result = fetch_theme_interface(interface_name, config, trade_dates, fill_fn)
        if not fetch_result.get("ok"):
            log(f"⚠️ {interface_name}: 第 1 轮拉取未成功，加入下一轮补拉队列", "WARNING")
            pending.append({**task, "reason": "fetch_failed", "health_result": None})
            continue
        if fetch_result.get("complete"):
            log(f"✅ {interface_name}: 第 1 轮体检通过，已更新白名单", "SUCCESS")
            continue

        log(f"⚠️ {interface_name}: 第 1 轮体检未完全通过，已写入干净子区间白名单", "WARNING")
        pending.append(
            {
                **task,
                "reason": "inspect_incomplete",
                "health_result": fetch_result.get("inspect_result"),
            }
        )

    if not pending:
        log("✅ 第 1 轮完成后，所有 theme 接口均已通过体检", "SUCCESS")
        return True

    round_number = 2
    while pending and round_number <= max_rounds:
        log(f"\n🔁 第 {round_number} 轮：仅处理上一轮未完成接口 {len(pending)} 个")
        log("=" * 60)
        next_pending = []
        for index, task in enumerate(pending, start=1):
            interface_name = task["name"]
            config = task["config"]
            trade_dates = task["trade_dates"]
            fill_fn = task["fill_fn"]
            reason = task.get("reason")
            health_result = task.get("health_result")

            log(
                f"第 {round_number} 轮进度: {index}/{len(pending)} | 当前接口 {interface_name} | 原因 {reason}",
                "INFO",
            )

            if reason == "inspect_incomplete" and health_result:
                repair_theme_interface(interface_name, config, trade_dates, health_result)
                inspect_result = inspect_theme_interface(interface_name, config, trade_dates)
                complete = isinstance(inspect_result, dict) and inspect_result.get("complete")
                if complete:
                    log(f"✅ {interface_name}: 第 {round_number} 轮修复后体检通过", "SUCCESS")
                    continue
                log(f"⚠️ {interface_name}: 第 {round_number} 轮修复后仍未完全通过", "WARNING")
                next_pending.append(
                    {
                        **task,
                        "reason": "inspect_incomplete",
                        "health_result": inspect_result,
                    }
                )
                continue

            fetch_result = fetch_theme_interface(interface_name, config, trade_dates, fill_fn)
            if not fetch_result.get("ok"):
                log(f"⚠️ {interface_name}: 第 {round_number} 轮补拉仍失败", "WARNING")
                next_pending.append({**task, "reason": "fetch_failed", "health_result": None})
                continue
            if fetch_result.get("complete"):
                log(f"✅ {interface_name}: 第 {round_number} 轮补拉后体检通过", "SUCCESS")
                continue
            log(f"⚠️ {interface_name}: 第 {round_number} 轮补拉后仍未完全通过", "WARNING")
            next_pending.append(
                {
                    **task,
                    "reason": "inspect_incomplete",
                    "health_result": fetch_result.get("inspect_result"),
                }
            )

        pending = next_pending
        round_number += 1

    if pending:
        log(f"⚠️ Theme 全量抓取结束后仍有 {len(pending)} 个接口残留问题", "WARNING")
        for task in pending:
            log(f"  - {task['name']}: {task.get('reason')}", "WARNING")
        return False

    log("✅ Theme 全量抓取分轮闭环完成", "SUCCESS")
    return True


def main():
    args = parse_args()
    interfaces = normalize_interfaces(args.interfaces)
    today = datetime.now().strftime("%Y%m%d")
    end_date = str(args.end_date or today)

    pro = create_pro_api()
    data_dir = get_stock_data_dir()
    index_dir = get_index_data_dir()
    financial_dir = get_financial_data_dir()
    auto_fill_registry = build_auto_fill_registry()
    stock_by_date_config = auto_fill_registry["stock"]["by_date"]
    theme_handlers = {
        "dc_concept": theme_fillers.fill_dc_concept_theme,
        "kpl_concept_cons": theme_fillers.fill_kpl_concept_cons_theme,
        "ths_index": theme_fillers.fill_ths_index_theme,
        "ths_member": theme_fillers.fill_ths_member_theme,
        "ths_daily": theme_fillers.fill_ths_daily_theme,
        "dc_daily": theme_fillers.fill_dc_daily_theme,
        "dc_index": theme_fillers.fill_dc_index_theme,
        "dc_member": theme_fillers.fill_dc_member_theme,
    }
    runtime.initialize_runtime(
        pro_api=pro,
        data_dir=data_dir,
        index_dir=index_dir,
        financial_dir=financial_dir,
        log_fn=log,
        theme_handlers=theme_handlers,
    )
    theme_fillers.initialize_theme_runtime(
        pro_api=pro,
        data_dir=data_dir,
        get_root_dir_fn=lambda config: get_root_dir(config, data_dir, index_dir, financial_dir=financial_dir),
        log_fn=log,
    )

    print("=" * 70)
    print("🚀 Theme 数据全量抓取")
    print("=" * 70)
    print(f"股票数据目录: {data_dir}")
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"接口列表: {', '.join(interfaces)}")
    print(f"起始日期: {args.start_date}")
    print(f"截止日期: {end_date}")
    print(f"日期范围: {args.start_date} ~ {end_date}")
    print(f"最大轮数: {args.max_rounds}")
    print("执行逻辑: 第 1 轮全接口拉取并体检，后续轮次只处理剩余失败接口")
    print("=" * 70)

    fill_functions = {
        "ths_index": theme_fillers.fill_ths_index_theme,
        "ths_member": theme_fillers.fill_ths_member_theme,
        "ths_daily": theme_fillers.fill_ths_daily_theme,
        "dc_daily": theme_fillers.fill_dc_daily_theme,
        "dc_index": theme_fillers.fill_dc_index_theme,
        "dc_member": theme_fillers.fill_dc_member_theme,
    }
    tasks = []
    for interface_name in interfaces:
        if interface_name not in fill_functions:
            log(f"{interface_name}: 当前脚本暂不支持", "WARNING")
            continue
        config = stock_by_date_config[interface_name]
        trade_dates = resolve_interface_trade_dates(interface_name, config, pro, args.start_date, end_date)
        if interface_name in {"ths_daily", "dc_daily", "dc_index", "dc_member"} and not trade_dates:
            log(f"{interface_name}: 未获取到交易日历，无法全量抓取", "ERROR")
            return 1
        tasks.append(
            {
                "name": interface_name,
                "config": config,
                "trade_dates": trade_dates,
                "fill_fn": fill_functions[interface_name],
            }
        )

    ok = run_theme_rounds(tasks, max_rounds=max(1, int(args.max_rounds or 1)))
    if ok:
        log("Theme 全量抓取完成", "SUCCESS")
        return 0
    log("Theme 全量抓取完成，但仍有残留问题接口", "WARNING")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
