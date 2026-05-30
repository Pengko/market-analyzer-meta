#!/usr/bin/env python3
"""Top-level autofill orchestration kept separate from the CLI entrypoint."""

import json
from datetime import datetime
from pathlib import Path

from core.registry import iter_auto_fill_interfaces

DATA_DIR = None
INDEX_DIR = None
FINANCIAL_DATA_DIR = None
STOCK_INTERFACE_CONFIG = None
INDEX_INTERFACE_CONFIG = None
LOG = None
GET_TRADE_DATES = None
GET_LOCAL_LATEST_DATE = None
GET_REPORT_TARGET_DATE = None
RESOLVE_RUN_TARGET_TRADE_DATE = None
REPAIR_SINGLE_INTERFACE = None
PREFLIGHT_API_HEALTH_CHECK = None
FILL_DC_CONCEPT_CONS_THEME = None
WEEKLY_MONTHLY_UPDATER = None
DISPATCH_FILL = None
GET_INTERFACE_WHITELIST_RECORD = None
CALENDAR_WINDOW_COVERED_BY_WHITELIST = None
CALENDAR_DATES_NOT_COVERED_BY_WHITELIST = None
MODE = "auto"
LAG_TRIGGER_TRADE_DAYS = 1
LATEST_MODE_TRADE_DAYS = 10
SELECTED_INTERFACES = None
IGNORE_WHITELIST = False
FORCE_REFETCH = False
HISTORY_START_DATE = None
HISTORY_END_DATE = None
LATEST_PROGRESS_PATH = Path(__file__).resolve().parent.parent / "logs" / "latest_mode_progress.json"


def initialize_workflow(
    *,
    data_dir,
    index_dir,
    financial_dir=None,
    stock_interface_config,
    index_interface_config,
    log_fn,
    get_trade_dates_fn,
    get_local_latest_date_fn,
    get_report_target_date_fn,
    resolve_run_target_trade_date_fn,
    repair_single_interface_fn,
    dispatch_fill_fn,
    get_interface_whitelist_record_fn=None,
    calendar_window_covered_by_whitelist_fn=None,
    calendar_dates_not_covered_by_whitelist_fn=None,
    preflight_api_health_check_fn,
    fill_dc_concept_cons_theme_fn,
    weekly_monthly_updater,
    mode="auto",
    lag_trigger_trade_days=1,
    latest_mode_trade_days=10,
    selected_interfaces=None,
    ignore_whitelist=False,
    force_refetch=False,
    history_start_date=None,
    history_end_date=None,
):
    global DATA_DIR, INDEX_DIR, FINANCIAL_DATA_DIR, STOCK_INTERFACE_CONFIG, INDEX_INTERFACE_CONFIG
    global LOG, GET_TRADE_DATES, GET_LOCAL_LATEST_DATE, GET_REPORT_TARGET_DATE
    global RESOLVE_RUN_TARGET_TRADE_DATE, REPAIR_SINGLE_INTERFACE
    global PREFLIGHT_API_HEALTH_CHECK, FILL_DC_CONCEPT_CONS_THEME, WEEKLY_MONTHLY_UPDATER
    global DISPATCH_FILL
    global GET_INTERFACE_WHITELIST_RECORD, CALENDAR_WINDOW_COVERED_BY_WHITELIST
    global CALENDAR_DATES_NOT_COVERED_BY_WHITELIST
    global MODE, LAG_TRIGGER_TRADE_DAYS, LATEST_MODE_TRADE_DAYS, SELECTED_INTERFACES
    global IGNORE_WHITELIST, FORCE_REFETCH, HISTORY_START_DATE, HISTORY_END_DATE

    DATA_DIR = data_dir
    INDEX_DIR = index_dir
    FINANCIAL_DATA_DIR = financial_dir
    STOCK_INTERFACE_CONFIG = stock_interface_config
    INDEX_INTERFACE_CONFIG = index_interface_config
    LOG = log_fn
    GET_TRADE_DATES = get_trade_dates_fn
    GET_LOCAL_LATEST_DATE = get_local_latest_date_fn
    GET_REPORT_TARGET_DATE = get_report_target_date_fn
    RESOLVE_RUN_TARGET_TRADE_DATE = resolve_run_target_trade_date_fn
    REPAIR_SINGLE_INTERFACE = repair_single_interface_fn
    DISPATCH_FILL = dispatch_fill_fn
    GET_INTERFACE_WHITELIST_RECORD = get_interface_whitelist_record_fn
    CALENDAR_WINDOW_COVERED_BY_WHITELIST = calendar_window_covered_by_whitelist_fn
    CALENDAR_DATES_NOT_COVERED_BY_WHITELIST = calendar_dates_not_covered_by_whitelist_fn
    PREFLIGHT_API_HEALTH_CHECK = preflight_api_health_check_fn
    FILL_DC_CONCEPT_CONS_THEME = fill_dc_concept_cons_theme_fn
    WEEKLY_MONTHLY_UPDATER = weekly_monthly_updater
    MODE = mode
    LAG_TRIGGER_TRADE_DAYS = max(0, int(lag_trigger_trade_days))
    LATEST_MODE_TRADE_DAYS = max(1, int(latest_mode_trade_days))
    SELECTED_INTERFACES = [str(item) for item in (selected_interfaces or []) if str(item).strip()] or None
    IGNORE_WHITELIST = bool(ignore_whitelist)
    FORCE_REFETCH = bool(force_refetch)
    HISTORY_START_DATE = str(history_start_date).strip() if history_start_date else None
    HISTORY_END_DATE = str(history_end_date).strip() if history_end_date else None


def _selected_interface_set():
    return set(SELECTED_INTERFACES or [])


def _trade_day_distance(trade_calendar, start_date, end_date):
    if not start_date or not end_date or start_date == end_date:
        return 0
    try:
        start_idx = trade_calendar.index(start_date)
        end_idx = trade_calendar.index(end_date)
    except ValueError:
        return None
    return max(0, end_idx - start_idx)


def _iter_all_interfaces():
    registry = {"stock": STOCK_INTERFACE_CONFIG, "index": INDEX_INTERFACE_CONFIG}
    selected = _selected_interface_set()
    for _, _, name, config in iter_auto_fill_interfaces(registry):
        if selected and name not in selected:
            continue
        yield name, config


def resolve_execution_mode(trade_calendar, latest_trade_date):
    if MODE in {"full", "latest"}:
        return MODE, []

    reasons = []
    interfaces = list(_iter_all_interfaces())
    total = len(interfaces)
    for index, (name, config) in enumerate(interfaces, start=1):
        if index == 1 or index % 10 == 0 or index == total:
            LOG(f"模式判断进度: {index}/{total} | 当前接口 {name}")
        expected_date = GET_REPORT_TARGET_DATE(config, trade_calendar)
        if expected_date is None:
            continue
        local_latest = GET_LOCAL_LATEST_DATE(name, config)
        if not local_latest:
            reasons.append(f"{name}: 本地无数据")
            continue
        if local_latest >= expected_date:
            continue
        lag_days = _trade_day_distance(trade_calendar, local_latest, expected_date)
        if lag_days is None:
            reasons.append(f"{name}: 最新 {local_latest} 无法映射到交易日历")
            continue
        if lag_days > LAG_TRIGGER_TRADE_DAYS:
            reasons.append(f"{name}: 滞后 {lag_days} 个交易日 ({local_latest} -> {expected_date})")

    if reasons:
        return "full", reasons
    return "latest", []


def build_latest_mode_calendar(trade_calendar, latest_trade_date, window_trade_days=None):
    if not trade_calendar or not latest_trade_date:
        return []
    window_trade_days = max(1, int(window_trade_days or LATEST_MODE_TRADE_DAYS))
    effective_trade_calendar = [d for d in trade_calendar if d <= latest_trade_date]
    if not effective_trade_calendar:
        return []
    return effective_trade_calendar[-window_trade_days:]


def _build_interface_tasks():
    registry = {"stock": STOCK_INTERFACE_CONFIG, "index": INDEX_INTERFACE_CONFIG}
    tasks = []
    selected = _selected_interface_set()
    for domain, bucket_name, name, config in iter_auto_fill_interfaces(registry):
        if selected and name not in selected:
            continue
        code_type = None
        if bucket_name == "by_stock":
            code_type = "stock"
        elif bucket_name == "by_index" or (domain == "index" and name != "sw_daily"):
            code_type = "index"
        tasks.append((name, config, code_type))
    return tasks


def _load_latest_progress(window_start, window_end):
    if not LATEST_PROGRESS_PATH.exists():
        return {"window_start": window_start, "window_end": window_end, "completed_interfaces": []}
    try:
        payload = json.loads(LATEST_PROGRESS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"window_start": window_start, "window_end": window_end, "completed_interfaces": []}
    if payload.get("window_start") != window_start or payload.get("window_end") != window_end:
        return {"window_start": window_start, "window_end": window_end, "completed_interfaces": []}
    payload.setdefault("completed_interfaces", [])
    return payload


def _save_latest_progress(payload):
    LATEST_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LATEST_PROGRESS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _resolve_dates_to_pull(whitelist_record, scoped_trade_calendar):
    if not whitelist_record or not CALENDAR_DATES_NOT_COVERED_BY_WHITELIST:
        return list(scoped_trade_calendar)
    try:
        uncovered = CALENDAR_DATES_NOT_COVERED_BY_WHITELIST(whitelist_record, scoped_trade_calendar)
    except Exception:
        return list(scoped_trade_calendar)
    if uncovered is None:
        return list(scoped_trade_calendar)
    return list(uncovered)


def _resolve_interface_trade_calendar(config, scoped_trade_calendar):
    override_days = int(config.get("latest_trade_days_override", 0) or 0)
    if override_days <= 0 or len(scoped_trade_calendar) <= override_days:
        return list(scoped_trade_calendar)
    return list(scoped_trade_calendar[-override_days:])


def _resolve_full_interface_trade_calendar(config, scoped_trade_calendar):
    override_days = int(config.get("full_trade_days_override", 0) or 0)
    if override_days <= 0 or len(scoped_trade_calendar) <= override_days:
        return list(scoped_trade_calendar)
    return list(scoped_trade_calendar[-override_days:])


def _clear_latest_progress():
    try:
        LATEST_PROGRESS_PATH.unlink()
    except FileNotFoundError:
        pass


def _fetch_trade_calendar_for_run():
    if FORCE_REFETCH and MODE == "full":
        start_date = HISTORY_START_DATE or "20200101"
        end_date = HISTORY_END_DATE or datetime.now().strftime("%Y%m%d")
        return GET_TRADE_DATES(start_date=start_date, end_date=end_date)
    return GET_TRADE_DATES()


def _run_force_refetch_full_fill(tasks, scoped_trade_calendar):
    LOG("\n🚛 第 0 阶段：强制重拉目标历史窗口")
    LOG("=" * 60)
    total = len(tasks)
    for index, (name, config, code_type) in enumerate(tasks, start=1):
        interface_trade_calendar = _resolve_full_interface_trade_calendar(config, scoped_trade_calendar)
        if not interface_trade_calendar:
            LOG(f"强制重拉进度: {index}/{total} | 当前接口 {name} | 无有效日期，跳过", "WARNING")
            continue
        if len(interface_trade_calendar) > 1:
            LOG(
                f"强制重拉进度: {index}/{total} | 当前接口 {name} | "
                f"历史区间 {interface_trade_calendar[0]} ~ {interface_trade_calendar[-1]} "
                f"({len(interface_trade_calendar)} 个交易日)",
                "INFO",
            )
        else:
            LOG(
                f"强制重拉进度: {index}/{total} | 当前接口 {name} | 目标日期 {interface_trade_calendar[-1]}",
                "INFO",
            )
        result = DISPATCH_FILL(
            name,
            config,
            interface_trade_calendar,
            code_type=code_type,
        )
        ok = result.get("ok") if isinstance(result, dict) else bool(result)
        if not ok:
            LOG(f"⚠️ {name}: 强制重拉阶段未完全成功，继续进入体检修复", "WARNING")


def _run_batched_interface_closure(tasks, scoped_trade_calendar, latest_trade_date, execution_mode, explicit_full_mode):
    LOG("\n📊 第 1 阶段：批量体检，健康接口直接加入白名单")
    LOG("=" * 60)
    pending = []
    for name, config, code_type in tasks:
        interface_trade_calendar = _resolve_full_interface_trade_calendar(config, scoped_trade_calendar)
        whitelist_record = None
        if not explicit_full_mode and GET_INTERFACE_WHITELIST_RECORD:
            whitelist_record = GET_INTERFACE_WHITELIST_RECORD(name)
        if (
            whitelist_record
            and CALENDAR_WINDOW_COVERED_BY_WHITELIST
            and CALENDAR_WINDOW_COVERED_BY_WHITELIST(whitelist_record, interface_trade_calendar)
        ):
            LOG(
                f"⚡ 第 1 阶段跳过 {name}: 白名单已覆盖窗口 "
                f"{interface_trade_calendar[0]}~{interface_trade_calendar[-1]}",
                "INFO",
            )
            continue
        result = REPAIR_SINGLE_INTERFACE(
            name,
            config,
            interface_trade_calendar,
            latest_trade_date,
            code_type=code_type,
            execution_mode=execution_mode,
            bypass_whitelist=explicit_full_mode,
            inspect_only=True,
        )
        if isinstance(result, dict) and result.get("complete"):
            continue
        if not isinstance(result, dict) or not result.get("complete"):
            pending.append((name, config, code_type, interface_trade_calendar, result if isinstance(result, dict) else None))

    if not pending:
        LOG("✅ 批量体检全部通过，无需修复")
        return True

    LOG(f"\n🧰 第 2 阶段：统一修复问题接口 {len(pending)} 个")
    LOG("=" * 60)
    round_index = 1
    max_rounds = 3
    while pending and round_index <= max_rounds:
        LOG(f"\n🔁 统一修复第 {round_index} 轮：先修复所有问题接口 ({len(pending)} 个)")
        repaired = []
        for name, config, code_type, interface_trade_calendar, health_result in pending:
            REPAIR_SINGLE_INTERFACE(
                name,
                config,
                interface_trade_calendar,
                latest_trade_date,
                code_type=code_type,
                max_rounds=1,
                execution_mode=execution_mode,
                bypass_whitelist=explicit_full_mode,
                repair_only=True,
                initial_health_result=health_result,
            )
            repaired.append((name, config, code_type, interface_trade_calendar))

        LOG(f"\n🔎 统一复检第 {round_index} 轮：复检修复过的接口 ({len(repaired)} 个)")
        next_pending = []
        for name, config, code_type, interface_trade_calendar in repaired:
            result = REPAIR_SINGLE_INTERFACE(
                name,
                config,
                interface_trade_calendar,
                latest_trade_date,
                code_type=code_type,
                execution_mode=execution_mode,
                bypass_whitelist=explicit_full_mode,
                inspect_only=True,
            )
            if isinstance(result, dict) and result.get("complete"):
                continue
            if not isinstance(result, dict) or not result.get("complete"):
                next_pending.append((name, config, code_type, interface_trade_calendar, result if isinstance(result, dict) else None))
        pending = next_pending
        round_index += 1

    if pending:
        LOG(f"⚠️ 统一修复后仍有 {len(pending)} 个接口残留问题", "WARNING")
        return False
    LOG("✅ 统一修复完成，问题接口已通过复检或给出残留报告")
    return True


def _run_direct_latest_fill(tasks, scoped_trade_calendar):
    LOG("\n🚚 第 1 阶段：latest 模式直接拉取最近窗口")
    LOG("=" * 60)
    window_start = scoped_trade_calendar[0]
    window_end = scoped_trade_calendar[-1]
    progress = _load_latest_progress(window_start, window_end)
    completed = set(progress.get("completed_interfaces", []))
    total = len(tasks)
    for index, (name, config, code_type) in enumerate(tasks, start=1):
        interface_trade_calendar = _resolve_interface_trade_calendar(config, scoped_trade_calendar)
        interface_window_start = interface_trade_calendar[0]
        interface_window_end = interface_trade_calendar[-1]
        expected_date = GET_REPORT_TARGET_DATE(config, interface_trade_calendar)
        local_latest = GET_LOCAL_LATEST_DATE(name, config)
        whitelist_record = GET_INTERFACE_WHITELIST_RECORD(name) if GET_INTERFACE_WHITELIST_RECORD else None
        whitelist_covers_window = bool(
            whitelist_record
            and CALENDAR_WINDOW_COVERED_BY_WHITELIST
            and CALENDAR_WINDOW_COVERED_BY_WHITELIST(whitelist_record, interface_trade_calendar)
        )
        if name in completed:
            latest_covers_target = bool(expected_date and local_latest and str(local_latest) >= str(expected_date))
            if not whitelist_covers_window and not latest_covers_target:
                completed.discard(name)
                progress["completed_interfaces"] = sorted(completed)
                _save_latest_progress(progress)
                LOG(
                    f"直拉进度: {index}/{total} | 当前接口 {name} | 发现旧 completed 状态未真正覆盖目标窗口，已移除并重新执行",
                    "WARNING",
                )
            else:
                LOG(
                    f"直拉进度: {index}/{total} | 当前接口 {name} | 已在窗口 {interface_window_start}~{interface_window_end} 完成，跳过",
                    "INFO",
                )
                continue
        if whitelist_covers_window:
            LOG(
                f"直拉进度: {index}/{total} | 当前接口 {name} | 白名单已覆盖窗口 {interface_window_start}~{interface_window_end}，跳过直拉",
                "INFO",
            )
            completed.add(name)
            progress["completed_interfaces"] = sorted(completed)
            _save_latest_progress(progress)
            continue
        if expected_date and local_latest and str(local_latest) >= str(expected_date):
            LOG(
                f"直拉进度: {index}/{total} | 当前接口 {name} | 本地已到 {local_latest} (覆盖目标 {expected_date})，跳过直拉",
                "INFO",
            )
            completed.add(name)
            progress["completed_interfaces"] = sorted(completed)
            _save_latest_progress(progress)
            continue
        target_dates = _resolve_dates_to_pull(whitelist_record, interface_trade_calendar)
        if not target_dates:
            LOG(
                f"直拉进度: {index}/{total} | 当前接口 {name} | 白名单未覆盖日期为空，跳过直拉",
                "INFO",
            )
            completed.add(name)
            progress["completed_interfaces"] = sorted(completed)
            _save_latest_progress(progress)
            continue
        if len(target_dates) < len(interface_trade_calendar):
            LOG(
                f"直拉进度: {index}/{total} | 当前接口 {name} | 仅拉取未覆盖日期 "
                f"{target_dates[0]}~{target_dates[-1]} ({len(target_dates)}/{len(interface_trade_calendar)})",
                "INFO",
            )
        else:
            LOG(
                f"直拉进度: {index}/{total} | 当前接口 {name} | 日期数 {len(target_dates)}",
                "INFO",
            )
        result = DISPATCH_FILL(
            name,
            config,
            target_dates,
            code_type=code_type,
        )
        ok = result.get("ok") if isinstance(result, dict) else bool(result)
        covered_target_date = result.get("covered_target_date") if isinstance(result, dict) else ok
        if ok and covered_target_date:
            completed.add(name)
            progress["completed_interfaces"] = sorted(completed)
            _save_latest_progress(progress)


def _run_latest_interface_light_cycle(tasks, scoped_trade_calendar, latest_trade_date):
    LOG("\n🚚 第 1 阶段：latest 模式接口级轻闭环")
    LOG("=" * 60)
    window_start = scoped_trade_calendar[0]
    window_end = scoped_trade_calendar[-1]
    progress = _load_latest_progress(window_start, window_end)
    completed = set(progress.get("completed_interfaces", []))
    total = len(tasks)
    has_pending_issues = False

    for index, (name, config, code_type) in enumerate(tasks, start=1):
        interface_trade_calendar = _resolve_interface_trade_calendar(config, scoped_trade_calendar)
        interface_window_start = interface_trade_calendar[0]
        interface_window_end = interface_trade_calendar[-1]
        whitelist_record = GET_INTERFACE_WHITELIST_RECORD(name) if GET_INTERFACE_WHITELIST_RECORD else None
        whitelist_covers_window = bool(
            whitelist_record
            and CALENDAR_WINDOW_COVERED_BY_WHITELIST
            and CALENDAR_WINDOW_COVERED_BY_WHITELIST(whitelist_record, interface_trade_calendar)
        )

        if name in completed:
            if whitelist_covers_window:
                LOG(
                    f"轻闭环进度: {index}/{total} | 当前接口 {name} | 已在窗口 {interface_window_start}~{interface_window_end} 完成，跳过",
                    "INFO",
                )
                continue
            completed.discard(name)
            progress["completed_interfaces"] = sorted(completed)
            _save_latest_progress(progress)
            LOG(
                f"轻闭环进度: {index}/{total} | 当前接口 {name} | 旧 completed 未命中白名单窗口，已移除并重新执行",
                "WARNING",
            )

        if whitelist_covers_window:
            LOG(
                f"轻闭环进度: {index}/{total} | 当前接口 {name} | 白名单已覆盖窗口 {interface_window_start}~{interface_window_end}，跳过",
                "INFO",
            )
            completed.add(name)
            progress["completed_interfaces"] = sorted(completed)
            _save_latest_progress(progress)
            continue

        target_dates = _resolve_dates_to_pull(whitelist_record, interface_trade_calendar)
        if not target_dates:
            LOG(
                f"轻闭环进度: {index}/{total} | 当前接口 {name} | 白名单未覆盖日期为空，跳过",
                "INFO",
            )
            completed.add(name)
            progress["completed_interfaces"] = sorted(completed)
            _save_latest_progress(progress)
            continue
        if len(target_dates) < len(interface_trade_calendar):
            LOG(
                f"轻闭环进度: {index}/{total} | 当前接口 {name} | 仅拉取未覆盖日期 "
                f"{target_dates[0]}~{target_dates[-1]} ({len(target_dates)}/{len(interface_trade_calendar)})",
                "INFO",
            )
        else:
            LOG(
                f"轻闭环进度: {index}/{total} | 当前接口 {name} | 日期数 {len(target_dates)}",
                "INFO",
            )
        result = DISPATCH_FILL(
            name,
            config,
            target_dates,
            code_type=code_type,
        )
        ok = result.get("ok") if isinstance(result, dict) else bool(result)
        if not ok:
            has_pending_issues = True
            LOG(f"⚠️ 轻闭环记录问题 {name}: 直拉未成功，留待下一轮", "WARNING")
            continue

        inspect_result = REPAIR_SINGLE_INTERFACE(
            name,
            config,
            interface_trade_calendar,
            latest_trade_date,
            code_type=code_type,
            execution_mode="latest",
            bypass_whitelist=False,
            inspect_only=True,
        )
        inspect_complete = isinstance(inspect_result, dict) and inspect_result.get("complete")
        if inspect_complete:
            completed.add(name)
            progress["completed_interfaces"] = sorted(completed)
            _save_latest_progress(progress)
        else:
            has_pending_issues = True
            LOG(f"⚠️ 轻闭环记录问题 {name}: 体检未通过，留待下一轮修复", "WARNING")

    return not has_pending_issues


def run_autofill_workflow():
    """新流程：latest/auto 走逐接口轻闭环，full 保留完整修复闭环。"""
    print("=" * 70)
    print("🚀 智能数据补全脚本 (股票 + 指数 + 财务)")
    print("=" * 70)
    print(f"股票数据目录: {DATA_DIR}")
    print(f"指数数据目录: {INDEX_DIR}")
    print(f"财务数据目录: {FINANCIAL_DATA_DIR}")
    print(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if SELECTED_INTERFACES:
        print(f"指定接口: {', '.join(SELECTED_INTERFACES)}")
    print("=" * 70)

    if not PREFLIGHT_API_HEALTH_CHECK():
        return False

    LOG("\n📅 交易日历准备")
    LOG("=" * 60)
    trade_calendar = _fetch_trade_calendar_for_run()
    if not trade_calendar:
        LOG("❌ 无法获取交易日历，无法执行闭环补全", "ERROR")
        return False
    calendar_latest_trade_date = trade_calendar[-1]
    latest_trade_date = RESOLVE_RUN_TARGET_TRADE_DATE(trade_calendar)
    if latest_trade_date is None:
        LOG("❌ 无法解析本轮目标交易日", "ERROR")
        return False
    effective_trade_calendar = [d for d in trade_calendar if d <= latest_trade_date]
    default_stable_trade_date = latest_trade_date
    LOG(f"交易日历范围: {trade_calendar[0]} ~ {calendar_latest_trade_date}")
    if latest_trade_date != calendar_latest_trade_date:
        LOG(
            f"本轮目标交易日: {latest_trade_date} "
            f"(当前时间早于 15:00，跳过当日交易日 {calendar_latest_trade_date})"
        )
    else:
        LOG(f"本轮目标交易日: {latest_trade_date}")
    LOG(f"默认稳定出数日期: {default_stable_trade_date}")

    mode_reasons = []
    if MODE == "auto":
        execution_mode = "latest"
        LOG("执行模式: auto -> latest_first (默认先走最近窗口，发现问题再升级 full)")
    else:
        execution_mode, mode_reasons = resolve_execution_mode(effective_trade_calendar, latest_trade_date)

    if execution_mode == "latest":
        scoped_trade_calendar = build_latest_mode_calendar(
            effective_trade_calendar,
            latest_trade_date,
        )
        LOG(
            f"执行模式: latest_only (先直接拉取最近 {len(scoped_trade_calendar)} 个交易日，"
            f"随后逐接口体检并更新白名单，"
            f"范围 {scoped_trade_calendar[0]} ~ {scoped_trade_calendar[-1]})"
        )
    else:
        scoped_trade_calendar = effective_trade_calendar
        LOG("执行模式: full (执行全量闭环补全)")
        if mode_reasons:
            LOG(f"触发原因: {'; '.join(mode_reasons[:6])}")
        if FORCE_REFETCH:
            history_start = HISTORY_START_DATE or (scoped_trade_calendar[0] if scoped_trade_calendar else "N/A")
            history_end = HISTORY_END_DATE or latest_trade_date
            LOG(
                f"强制重拉: 已启用忽略白名单 + 历史重拉模式，范围 {history_start} ~ {history_end}",
                "WARNING",
            )
        elif IGNORE_WHITELIST:
            LOG("忽略白名单: 已启用，将执行完整体检闭环且不走白名单快路径", "WARNING")

    explicit_full_mode = bool(IGNORE_WHITELIST or FORCE_REFETCH)
    tasks = _build_interface_tasks()

    if execution_mode == "latest":
        closure_ok = _run_latest_interface_light_cycle(
            tasks,
            scoped_trade_calendar,
            latest_trade_date,
        )
        final_report_calendar = scoped_trade_calendar
    else:
        if FORCE_REFETCH:
            _run_force_refetch_full_fill(tasks, scoped_trade_calendar)
        closure_ok = _run_batched_interface_closure(
            tasks,
            scoped_trade_calendar,
            latest_trade_date,
            execution_mode,
            explicit_full_mode,
        )
        final_report_calendar = scoped_trade_calendar

    if MODE == "auto" and execution_mode == "latest" and not closure_ok:
        LOG("\n⚠️ auto 模式 latest 发现残留问题：已记录，留待下一轮继续处理", "WARNING")

    if not SELECTED_INTERFACES:
        FILL_DC_CONCEPT_CONS_THEME([default_stable_trade_date])

        LOG("\n【周线/月线数据】")
        WEEKLY_MONTHLY_UPDATER.update_weekly_monthly(n_periods=3, verbose=True)
    else:
        LOG("\nℹ️ 已指定接口过滤，跳过附加步骤 dc_concept_cons / 周线月线更新")

    LOG("\n" + "=" * 70)
    LOG("📋 最终报告")
    LOG("=" * 70)
    for bucket_name, bucket in (
        ("股票数据", STOCK_INTERFACE_CONFIG["by_date"]),
        ("股票按代码", STOCK_INTERFACE_CONFIG["by_stock"]),
        ("指数数据", INDEX_INTERFACE_CONFIG["by_date"]),
        ("指数按代码", INDEX_INTERFACE_CONFIG["by_index"]),
    ):
        selected = _selected_interface_set()
        items = [(name, config) for name, config in bucket.items() if not selected or name in selected]
        if not items:
            continue
        LOG(f"\n【{bucket_name}】")
        for name, config in items:
            latest_date = GET_LOCAL_LATEST_DATE(name, config)
            expected_date = GET_REPORT_TARGET_DATE(config, final_report_calendar)
            if expected_date is None:
                status = "✅" if latest_date else "⚠️"
                LOG(f"{status} {name}: 最新 {latest_date or 'N/A'} | 非日频接口")
                continue
            status = "✅" if latest_date == expected_date else "⚠️"
            LOG(f"{status} {name}: 最新 {latest_date or 'N/A'} | 目标 {expected_date}")

    LOG("\n" + "=" * 70)
    LOG("🎉 数据补全完成！")
    LOG("=" * 70)
    _clear_latest_progress()
    return True
