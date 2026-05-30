#!/usr/bin/env python3
"""
主要作用:
- 维护 `weekly` / `monthly` 两类周期数据
- 直接以 `stk_weekly_monthly` 接口为准写入本地文件
- 结合白名单跳过已验证周期，避免重复校验
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.paths import get_stock_data_dir
from utils.tushare_client import classify_api_error, create_pro_api
from aggregate_weekly_monthly import get_stock_list, load_trade_cal
from core.autofill_runtime import (
    _calendar_dates_not_covered_by_whitelist,
    _get_interface_whitelist_record,
    _mark_interface_whitelisted,
)
from core.registry import INTERFACE_CONFIG

BASE_DIR = Path(get_stock_data_dir())
WEEKLY_DIR = BASE_DIR / "weekly"
MONTHLY_DIR = BASE_DIR / "monthly"
OFFICIAL_PERIOD_PAGE_LIMITS = {"weekly": 6000, "monthly": 4500}
OFFICIAL_PERIOD_MAX_PAGES = 4
PERIOD_API_TIMEOUT_SEC = 30
PERIOD_REQUEST_RETRY_COUNT = 3
PERIOD_REQUEST_RETRY_SLEEP_SEC = 1.0
WEEKLY_DIR.mkdir(parents=True, exist_ok=True)
MONTHLY_DIR.mkdir(parents=True, exist_ok=True)

pro = create_pro_api(timeout=PERIOD_API_TIMEOUT_SEC)
week_map, month_map = load_trade_cal()


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def get_recent_period_ends(period_map, n=3, fetch_all=False):
    """获取最近 N 个或全部不晚于今天的周期结束日。"""
    today = int(datetime.now().strftime("%Y%m%d"))
    ends = sorted(set(int(v) for v in period_map.values()))
    ends = [e for e in ends if e <= today]
    if fetch_all:
        return ends
    return ends[-n:] if len(ends) >= n else ends


def get_target_period_dates(period_map, n=3, fetch_all=False):
    """获取本轮应请求的周期日期，并标记当前活跃周期日期。"""
    today = int(datetime.now().strftime("%Y%m%d"))
    trade_dates = sorted(int(k) for k in period_map.keys())
    trade_dates = [d for d in trade_dates if d <= today]
    if not trade_dates:
        return [], set()

    latest_trade_date = trade_dates[-1]
    is_live_trade_day = latest_trade_date == today
    closed_period_ends = sorted(
        set(
            int(v)
            for k, v in period_map.items()
            if int(k) <= today and int(v) <= today
        )
    )

    targets = list(closed_period_ends) if fetch_all else list(get_recent_period_ends(period_map, n=n, fetch_all=False))
    volatile_dates = set()

    if is_live_trade_day:
        active_date = str(latest_trade_date)
        volatile_dates.add(active_date)
        if latest_trade_date not in targets:
            if fetch_all:
                targets.append(latest_trade_date)
            else:
                stable_keep = max(0, int(n or 0) - 1)
                stable_targets = targets[-stable_keep:] if stable_keep > 0 else []
                targets = stable_targets + [latest_trade_date]

    deduped = []
    seen = set()
    for value in sorted(targets):
        text = str(value)
        if text in seen:
            continue
        deduped.append(text)
        seen.add(text)
    return deduped, volatile_dates


def _normalize_target_dates(target_dates):
    if not target_dates:
        return []
    if isinstance(target_dates, str):
        raw_items = target_dates.replace("，", ",").replace("\n", ",").split(",")
    else:
        raw_items = list(target_dates)

    normalized = []
    seen = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return sorted(normalized)


def _get_period_config(api_name):
    return INTERFACE_CONFIG[api_name]


def _resolve_uncovered_periods(api_name, period_dates, bypass_whitelist=False, always_fetch_dates=None):
    always_fetch_dates = {str(d) for d in (always_fetch_dates or set())}
    if bypass_whitelist:
        log(f"  ⚠️ {api_name}: 已指定忽略白名单，本轮强制重查 {len(period_dates)} 个周期")
        return [str(d) for d in period_dates]

    config = _get_period_config(api_name)
    whitelist_record = _get_interface_whitelist_record(api_name)
    if not whitelist_record:
        return [str(d) for d in period_dates]

    stable_dates = [str(d) for d in period_dates if str(d) not in always_fetch_dates]
    uncovered = _calendar_dates_not_covered_by_whitelist(
        whitelist_record,
        stable_dates,
    )
    uncovered_set = set(uncovered) | always_fetch_dates
    covered_count = len(stable_dates) - len(uncovered)
    if covered_count > 0:
        log(
            f"  ⚡ {api_name}: 白名单已覆盖 {covered_count}/{len(stable_dates)} 个稳定周期，"
            f"本轮仅拉取未覆盖周期 {', '.join(uncovered[:6])}"
            + (f" ... (+{len(uncovered) - 6})" if len(uncovered) > 6 else "")
        )
    if always_fetch_dates:
        log(
            f"  🔄 {api_name}: 当前活跃周期不走白名单，每日重抓 {', '.join(sorted(always_fetch_dates))}"
        )
    return [str(d) for d in period_dates if str(d) in uncovered_set]


def _write_period_rows(frame, data_dir, prefix):
    if frame is None or frame.empty or "ts_code" not in frame.columns:
        return 0

    written = 0
    frame = frame.copy()
    frame["trade_date"] = frame["trade_date"].astype(str)
    for ts_code, part in frame.groupby("ts_code"):
        target = data_dir / f"{prefix}_{ts_code}.csv"
        payload = part.sort_values("trade_date", ascending=False)
        if target.exists():
            existing = pd.read_csv(target, low_memory=False)
            if "trade_date" in existing.columns:
                existing["trade_date"] = existing["trade_date"].astype(str)
                existing = existing[~existing["trade_date"].isin(payload["trade_date"].unique())]
            payload = pd.concat([existing, payload], ignore_index=True)
            payload = payload.drop_duplicates(subset=["trade_date"], keep="last")
            payload = payload.sort_values("trade_date", ascending=False).reset_index(drop=True)
        payload.to_csv(target, index=False)
        written += 1
    return written


def _call_with_timeout_retry(fetcher, *, api_name, trade_date, source_name):
    last_exc = None
    for retry_idx in range(PERIOD_REQUEST_RETRY_COUNT):
        try:
            return fetcher()
        except Exception as exc:
            last_exc = exc
            category, _ = classify_api_error(exc)
            is_last_try = retry_idx + 1 >= PERIOD_REQUEST_RETRY_COUNT
            if category != "timeout" or is_last_try:
                raise
            log(
                f"  ⚠️ {api_name} {trade_date} | {source_name} 第 {retry_idx + 1}/{PERIOD_REQUEST_RETRY_COUNT} 次超时，准备重试"
            )
            time.sleep(PERIOD_REQUEST_RETRY_SLEEP_SEC * (retry_idx + 1))
    raise last_exc


def _fetch_market_period_data(api_name, trade_date):
    freq = "week" if api_name == "weekly" else "month"
    primary = _call_with_timeout_retry(
        lambda: pro.stk_weekly_monthly(trade_date=str(trade_date), freq=freq),
        api_name=api_name,
        trade_date=str(trade_date),
        source_name="stk_weekly_monthly",
    )
    if primary is not None and not primary.empty:
        frame = primary.copy()
        if "trade_date" in frame.columns:
            frame["trade_date"] = frame["trade_date"].astype(str)
        return frame, "stk_weekly_monthly"

    fallback_api = getattr(pro, api_name, None)
    if fallback_api is None:
        return None, "empty"

    page_limit = OFFICIAL_PERIOD_PAGE_LIMITS[api_name]
    frames = []
    for page in range(OFFICIAL_PERIOD_MAX_PAGES):
        offset = page * page_limit
        frame = _call_with_timeout_retry(
            lambda: fallback_api(
                trade_date=str(trade_date),
                limit=page_limit,
                offset=offset,
            ),
            api_name=api_name,
            trade_date=str(trade_date),
            source_name=f"{api_name}[offset={offset}]",
        )
        if frame is None or frame.empty:
            break
        frame = frame.copy()
        if "trade_date" in frame.columns:
            frame["trade_date"] = frame["trade_date"].astype(str)
        frames.append(frame)
        if len(frame) < page_limit:
            break

    if not frames:
        return None, "empty"

    merged = pd.concat(frames, ignore_index=True)
    dedup_cols = [col for col in ["ts_code", "trade_date"] if col in merged.columns]
    if dedup_cols:
        merged = merged.drop_duplicates(subset=dedup_cols, keep="last")
    return merged.reset_index(drop=True), api_name


def update_period(
    api_name,
    data_dir,
    prefix,
    period_map,
    n_periods=3,
    verbose=True,
    bypass_whitelist=False,
    fetch_all=False,
    target_dates=None,
):
    """直接按周期日拉取全市场周/月线，并把成功周期写入白名单。"""
    normalized_target_dates = _normalize_target_dates(target_dates)
    if normalized_target_dates:
        period_dates = normalized_target_dates
        today = int(datetime.now().strftime("%Y%m%d"))
        trade_dates = sorted(int(k) for k in period_map.keys())
        trade_dates = [d for d in trade_dates if d <= today]
        latest_trade_date = trade_dates[-1] if trade_dates else None
        volatile_dates = {
            str(d)
            for d in period_dates
            if latest_trade_date is not None and latest_trade_date == today and str(d) == str(latest_trade_date)
        }
    else:
        period_dates, volatile_dates = get_target_period_dates(
            period_map,
            n=n_periods,
            fetch_all=fetch_all,
        )

    if not period_dates:
        if verbose:
            log(f"{api_name}: 无可拉取周期")
        return {
            "requested_periods": 0,
            "fetched_periods": 0,
            "written_codes": 0,
            "skipped_whitelist_periods": 0,
            "failed_periods": [],
        }

    to_fetch = _resolve_uncovered_periods(
        api_name,
        period_dates,
        bypass_whitelist=bypass_whitelist,
        always_fetch_dates=volatile_dates,
    )
    skipped = len(period_dates) - len(to_fetch)
    if not to_fetch:
        if verbose:
            log(f"\n📊 {api_name}: 目标周期全部命中白名单，跳过拉取")
        return {
            "requested_periods": len(period_dates),
            "fetched_periods": 0,
            "written_codes": 0,
            "skipped_whitelist_periods": skipped,
            "failed_periods": [],
        }

    if verbose:
        log(f"\n📊 {api_name}: 以 stk_weekly_monthly 为准，按周期批量拉取")
        if normalized_target_dates:
            scope_text = f"指定 {len(period_dates)} 个"
        else:
            scope_text = f"全量 {len(period_dates)} 个" if fetch_all else f"最近 {len(period_dates)} 个"
        log(f"  目标周期: {min(period_dates)} ~ {max(period_dates)} ({scope_text})")
        log(f"  本轮待拉取周期: {', '.join(to_fetch)}")

    written_codes = 0
    fetched_periods = []
    failed_periods = []
    for idx, trade_date in enumerate(to_fetch, start=1):
        try:
            frame, source_api = _fetch_market_period_data(api_name, trade_date)
            if frame is None or frame.empty:
                failed_periods.append(trade_date)
                if verbose:
                    log(f"  ⚪ {api_name} {trade_date}: 无数据")
                continue

            written = _write_period_rows(frame, data_dir, prefix)
            written_codes += written
            fetched_periods.append(trade_date)
            if verbose:
                log(
                    f"  ✅ {api_name} {trade_date}: {len(frame)} 条，写入/更新 {written} 只"
                    f" | 来源 {source_api}"
                    f" ({idx}/{len(to_fetch)})"
                )
        except Exception as exc:
            failed_periods.append(trade_date)
            if verbose:
                log(f"  ❌ {api_name} {trade_date}: {str(exc)[:120]}")

    stable_fetched_periods = [item for item in fetched_periods if str(item) not in volatile_dates]
    if stable_fetched_periods:
        _mark_interface_whitelisted(
            api_name,
            latest_date=max(stable_fetched_periods),
            mode="stk_weekly_monthly_period_batch",
            calendar_dates=stable_fetched_periods,
        )

    if verbose:
        log(
            f"  {api_name} 完成: 成功周期 {len(fetched_periods)}/{len(to_fetch)}"
            f" | 白名单跳过 {skipped}"
            f" | 失败周期 {len(failed_periods)}"
        )

    return {
        "requested_periods": len(period_dates),
        "fetched_periods": len(fetched_periods),
        "written_codes": written_codes,
        "skipped_whitelist_periods": skipped,
        "failed_periods": failed_periods,
    }


def update_weekly(n_periods=3, verbose=True, bypass_whitelist=False, fetch_all=False, target_dates=None):
    """更新 weekly 数据。"""
    return update_period(
        "weekly",
        WEEKLY_DIR,
        "weekly",
        week_map,
        n_periods,
        verbose,
        bypass_whitelist=bypass_whitelist,
        fetch_all=fetch_all,
        target_dates=target_dates,
    )


def update_monthly(n_periods=3, verbose=True, bypass_whitelist=False, fetch_all=False, target_dates=None):
    """更新 monthly 数据。"""
    return update_period(
        "monthly",
        MONTHLY_DIR,
        "monthly",
        month_map,
        n_periods,
        verbose,
        bypass_whitelist=bypass_whitelist,
        fetch_all=fetch_all,
        target_dates=target_dates,
    )


def update_weekly_monthly(n_periods=3, verbose=True, bypass_whitelist=False, fetch_all=False, target_dates=None):
    """同时更新 weekly 和 monthly 数据。"""
    return {
        "weekly": update_weekly(
            n_periods,
            verbose,
            bypass_whitelist=bypass_whitelist,
            fetch_all=fetch_all,
            target_dates=target_dates,
        ),
        "monthly": update_monthly(
            n_periods,
            verbose,
            bypass_whitelist=bypass_whitelist,
            fetch_all=fetch_all,
            target_dates=target_dates,
        ),
    }


def run_selected_interface(
    interface_name="both",
    n_periods=3,
    verbose=True,
    bypass_whitelist=False,
    fetch_all=False,
    target_dates=None,
):
    """按指定接口执行周/月线补全。"""
    interface_name = str(interface_name or "both").strip().lower()
    if interface_name == "weekly":
        return {
            "weekly": update_weekly(
                n_periods,
                verbose,
                bypass_whitelist=bypass_whitelist,
                fetch_all=fetch_all,
                target_dates=target_dates,
            )
        }
    if interface_name == "monthly":
        return {
            "monthly": update_monthly(
                n_periods,
                verbose,
                bypass_whitelist=bypass_whitelist,
                fetch_all=fetch_all,
                target_dates=target_dates,
            )
        }
    return update_weekly_monthly(
        n_periods,
        verbose,
        bypass_whitelist=bypass_whitelist,
        fetch_all=fetch_all,
        target_dates=target_dates,
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="单独补全 stk_weekly_monthly 对应的 weekly/monthly 数据")
    parser.add_argument(
        "--interface",
        choices=["weekly", "monthly", "both"],
        default="both",
        help="选择要补全的接口：weekly / monthly / both",
    )
    parser.add_argument(
        "--periods",
        type=int,
        default=3,
        help="补全最近多少个周期，默认 3",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="静默模式，减少日志输出",
    )
    parser.add_argument(
        "--ignore-whitelist",
        action="store_true",
        help="忽略现有白名单，强制重新检查并重拉目标周期",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="拉取截至今天的全部历史周期，忽略 --periods",
    )
    parser.add_argument(
        "--trade-dates",
        help="只补指定周期日期，多个用逗号分隔，如 20250516,20250523,20250530",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.all and args.trade_dates:
        raise SystemExit("--trade-dates 不能与 --all 同时使用")
    periods = max(1, int(args.periods or 1))
    target_dates = _normalize_target_dates(args.trade_dates)
    result = run_selected_interface(
        interface_name=args.interface,
        n_periods=periods,
        verbose=not args.quiet,
        bypass_whitelist=bool(args.ignore_whitelist),
        fetch_all=bool(args.all),
        target_dates=target_dates,
    )
    if args.quiet:
        log(f"完成: {result}")
    return result


if __name__ == "__main__":
    main()
