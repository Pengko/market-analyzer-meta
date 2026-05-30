#!/usr/bin/env python3
"""Theme-specific autofill helpers kept separate from the main orchestrator."""

import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from core.files import write_multi_format_bundle
from core.logging_utils import start_live_spinner, stop_live_spinner, update_live_spinner

PRO = None
DATA_DIR = None
GET_ROOT_DIR = None
LOG = None


def initialize_theme_runtime(*, pro_api, data_dir, get_root_dir_fn, log_fn):
    global PRO, DATA_DIR, GET_ROOT_DIR, LOG
    PRO = pro_api
    DATA_DIR = data_dir
    GET_ROOT_DIR = get_root_dir_fn
    LOG = log_fn


def _sanitize_name(name):
    return re.sub(r'[\\/:*?"<>|]', "_", str(name))


def _write_theme_bundle(filepath, frame, interface_name):
    frame.to_csv(filepath, index=False)
    write_multi_format_bundle(filepath, frame, interface_name=interface_name)


def fill_dc_concept_theme(config, trade_dates):
    """补全 dc_concept（按月 CSV + 按年 parquet）"""
    out_dir = GET_ROOT_DIR(config) / config["path"]
    out_dir.mkdir(parents=True, exist_ok=True)
    LOG(f"\n{'=' * 60}")
    LOG("补全 dc_concept（theme 专用）")
    LOG("=" * 60)
    success = 0
    successful_dates = set()
    total_days = len(trade_dates)
    for index, trade_date in enumerate(trade_dates, start=1):
        all_rows = []
        start_live_spinner(f"dc_concept: 正在拉取 {trade_date} ({index}/{total_days})")
        for offset in [0, 5000]:
            try:
                df = PRO.dc_concept(trade_date=trade_date, limit=5000, offset=offset)
                if df is not None and not df.empty:
                    all_rows.append(df)
            except Exception as exc:
                stop_live_spinner(f"  ❌ {trade_date} offset={offset} 失败: {str(exc)[:60]}", "ERROR")
                break
            time.sleep(0.2)
        if not all_rows:
            stop_live_spinner(f"  ⚪ {trade_date}: 无数据")
            continue
        df = pd.concat(all_rows, ignore_index=True)
        # 按月 CSV + 按年 parquet
        write_multi_format_bundle(out_dir / "tmp.csv", df, interface_name="dc_concept")
        success += 1
        successful_dates.add(str(trade_date))
        stop_live_spinner(f"  ✅ {trade_date}: {len(df)} 条", "SUCCESS")
    LOG(f"完成: 成功 {success}/{len(trade_dates)}")
    target_trade_date = str(trade_dates[-1]) if trade_dates else None
    return {
        "ok": success > 0,
        "covered_target_date": bool(target_trade_date and target_trade_date in successful_dates),
    }


def fill_kpl_concept_cons_theme(config, trade_dates):
    """补全 kpl_concept_cons（按月 CSV + 按年 parquet）"""
    root = GET_ROOT_DIR(config) / config["path"]
    root.mkdir(parents=True, exist_ok=True)
    LOG(f"\n{'=' * 60}")
    LOG("补全 kpl_concept_cons（theme 专用）")
    LOG("=" * 60)
    success = 0
    successful_dates = set()
    total_days = len(trade_dates)
    for index, trade_date in enumerate(trade_dates, start=1):
        all_rows = []
        start_live_spinner(f"kpl_concept_cons: 正在拉取 {trade_date} ({index}/{total_days})")
        for offset in [0, 5000]:
            try:
                df = PRO.kpl_concept_cons(trade_date=trade_date, limit=5000, offset=offset)
                if df is not None and not df.empty:
                    all_rows.append(df)
            except Exception as exc:
                stop_live_spinner(f"  ❌ {trade_date} offset={offset} 失败: {str(exc)[:60]}", "ERROR")
                break
            time.sleep(0.2)
        if not all_rows:
            stop_live_spinner(f"  ⚪ {trade_date}: 无数据")
            continue
        df = pd.concat(all_rows, ignore_index=True)
        write_multi_format_bundle(root / "tmp.csv", df, interface_name="kpl_concept_cons")
        success += 1
        successful_dates.add(str(trade_date))
        stop_live_spinner(f"  ✅ {trade_date}: {len(df)} 条", "SUCCESS")
    LOG(f"完成: 成功 {success}/{len(trade_dates)}")
    target_trade_date = str(trade_dates[-1]) if trade_dates else None
    return {
        "ok": success > 0,
        "covered_target_date": bool(target_trade_date and target_trade_date in successful_dates),
    }


def fill_dc_concept_cons_theme(*args):
    """补全 dc_concept_cons（按月 CSV + 按年 parquet）
    兼容 handler(config, trade_dates) 和直接 (trade_dates,) 两种调用。
    """
    trade_dates = args[-1] if len(args) >= 2 and isinstance(args[-1], (list, tuple)) else (args[0] if args else [])
    out_dir = DATA_DIR / "theme_data" / "dc_concept_cons"
    out_dir.mkdir(parents=True, exist_ok=True)
    LOG(f"\n{'=' * 60}")
    LOG("补全 dc_concept_cons（theme 专用）")
    LOG("=" * 60)
    success = 0
    successful_dates = set()
    total_days = len(trade_dates)
    for index, trade_date in enumerate(trade_dates, start=1):
        all_rows = []
        start_live_spinner(f"dc_concept_cons: 正在拉取 {trade_date} ({index}/{total_days})")
        for offset in [0, 5000]:
            try:
                df = PRO.dc_concept_cons(trade_date=trade_date, limit=5000, offset=offset)
                if df is not None and not df.empty:
                    all_rows.append(df)
            except Exception as exc:
                stop_live_spinner(f"  ❌ {trade_date} offset={offset} 失败: {str(exc)[:60]}", "ERROR")
                break
            time.sleep(0.2)
        if not all_rows:
            stop_live_spinner(f"  ⚪ {trade_date}: 无数据")
            continue
        df = pd.concat(all_rows, ignore_index=True)
        write_multi_format_bundle(out_dir / "tmp.csv", df, interface_name="dc_concept_cons")
        success += 1
        successful_dates.add(str(trade_date))
        stop_live_spinner(f"  ✅ {trade_date}: {len(df)} 条", "SUCCESS")
    LOG(f"完成: 成功 {success}/{len(trade_dates)}")
    target_trade_date = str(trade_dates[-1]) if trade_dates else None
    return {
        "ok": success > 0,
        "covered_target_date": bool(target_trade_date and target_trade_date in successful_dates),
    }


def fill_ths_index_theme(config, trade_dates):
    """补全 ths_index（原样直存）"""
    root = GET_ROOT_DIR(config) / config["path"]
    root.mkdir(parents=True, exist_ok=True)

    LOG(f"\n{'=' * 60}")
    LOG("补全 ths_index（theme 专用）")
    LOG("=" * 60)
    start_live_spinner("ths_index: 正在拉取全量快照")
    try:
        df = PRO.ths_index()
    except Exception as exc:
        stop_live_spinner(f"  ❌ ths_index: {str(exc)[:80]}", "ERROR")
        return {"ok": False, "covered_target_date": False}

    if df is None or df.empty:
        stop_live_spinner("  ⚪ ths_index: 无数据")
        return {"ok": False, "covered_target_date": False}

    _write_theme_bundle(root / "ths_index_all.csv", df, "ths_index")

    stop_live_spinner(f"  ✅ ths_index: {len(df)} 条", "SUCCESS")
    return {"ok": True, "covered_target_date": True}


def fill_ths_member_theme(config, trade_dates):
    """补全 ths_member（原样直存）"""
    root = GET_ROOT_DIR(config) / config["path"]
    root.mkdir(parents=True, exist_ok=True)

    index_source = GET_ROOT_DIR({"root": "stock"}) / "theme_data" / "ths_index" / "ths_index_all.csv"
    if not index_source.exists():
        LOG("❌ ths_member 依赖 ths_index_all.csv，请先补全 ths_index", "ERROR")
        return {"ok": False, "covered_target_date": False}

    try:
        index_df = pd.read_csv(index_source, low_memory=False)
    except Exception as exc:
        LOG(f"❌ 读取 ths_index_all.csv 失败: {str(exc)[:120]}", "ERROR")
        return {"ok": False, "covered_target_date": False}

    if "ts_code" not in index_df.columns:
        LOG("❌ ths_index_all.csv 缺少 ts_code 列", "ERROR")
        return {"ok": False, "covered_target_date": False}

    index_df["ts_code"] = index_df["ts_code"].astype(str)
    concept_index_df = index_df[index_df.get("type", "").astype(str).eq("N")].copy() if "type" in index_df.columns else index_df.copy()
    concept_codes = concept_index_df["ts_code"].dropna().astype(str).drop_duplicates().tolist()
    if not concept_codes:
        LOG("⚪ ths_member: ths_index 中没有可用概念指数代码", "WARNING")
        return {"ok": False, "covered_target_date": False}

    LOG(f"\n{'=' * 60}")
    LOG("补全 ths_member（theme 专用）")
    LOG("=" * 60)

    success = 0
    index_count = len(concept_codes)

    for idx, ts_code in enumerate(concept_codes, start=1):
        start_live_spinner(f"ths_member: 正在拉取 {ts_code} ({idx}/{index_count})")
        try:
            df = PRO.ths_member(ts_code=ts_code)
        except Exception as exc:
            stop_live_spinner(f"  ❌ {ts_code}: {str(exc)[:80]}", "ERROR")
            continue

        if df is None or df.empty:
            stop_live_spinner(f"  ⚪ {ts_code}: 无数据")
            continue

        if "ts_code" in df.columns:
            df["ts_code"] = df["ts_code"].astype(str)
        if "con_code" in df.columns:
            df["con_code"] = df["con_code"].astype(str)

        name = ""
        if "name" in concept_index_df.columns:
            matched = concept_index_df.loc[concept_index_df["ts_code"] == ts_code, "name"]
            if not matched.empty:
                name = str(matched.iloc[0])
        safe_name = _sanitize_name(name)
        index_filename = f"{ts_code}_{safe_name}.csv" if safe_name else f"{ts_code}.csv"
        _write_theme_bundle(root / index_filename, df, "ths_member")

        success += 1
        stop_live_spinner(f"  ✅ {ts_code}: {len(df)} 条", "SUCCESS")
        time.sleep(0.1)

    LOG(f"完成: 成功 {success}/{index_count}")
    return {"ok": success > 0, "covered_target_date": success > 0}


def _load_ths_index_code_pool():
    index_source = GET_ROOT_DIR({"root": "stock"}) / "theme_data" / "ths_index" / "ths_index_all.csv"
    if index_source.exists():
        try:
            index_df = pd.read_csv(index_source, low_memory=False)
            if "ts_code" in index_df.columns:
                return index_df["ts_code"].dropna().astype(str).drop_duplicates().tolist()
        except Exception:
            pass

    try:
        remote_df = PRO.ths_index()
        if remote_df is not None and not remote_df.empty and "ts_code" in remote_df.columns:
            return remote_df["ts_code"].dropna().astype(str).drop_duplicates().tolist()
    except Exception:
        pass
    return []


def _load_dc_index_code_pool(trade_date=None):
    index_source = GET_ROOT_DIR({"root": "stock"}) / "theme_data" / "dc_index" / "dc_index_all.csv"
    if index_source.exists():
        try:
            index_df = pd.read_csv(index_source, low_memory=False)
            if "ts_code" in index_df.columns:
                return index_df["ts_code"].dropna().astype(str).drop_duplicates().tolist()
        except Exception:
            pass

    if trade_date:
        page_limit = 2000
        rows = []
        try:
            for page in range(4):
                offset = page * page_limit
                df = PRO.dc_index(trade_date=trade_date, limit=page_limit, offset=offset)
                if df is not None and not df.empty:
                    rows.append(df)
                if df is None or df.empty or len(df) < page_limit:
                    break
        except Exception:
            rows = []
        if rows:
            merged = pd.concat(rows, ignore_index=True)
            if "ts_code" in merged.columns:
                return merged["ts_code"].dropna().astype(str).drop_duplicates().tolist()
    return []


def _load_dc_index_name_map(trade_date=None):
    index_source = GET_ROOT_DIR({"root": "stock"}) / "theme_data" / "dc_index" / "dc_index_all.csv"
    if index_source.exists():
        try:
            index_df = pd.read_csv(index_source, low_memory=False)
            if "ts_code" in index_df.columns:
                index_df["ts_code"] = index_df["ts_code"].astype(str)
                if "name" in index_df.columns:
                    return {
                        str(row["ts_code"]): str(row["name"])
                        for _, row in index_df[["ts_code", "name"]].dropna(subset=["ts_code"]).drop_duplicates(subset=["ts_code"], keep="last").iterrows()
                    }
        except Exception:
            pass

    if trade_date:
        page_limit = 2000
        rows = []
        try:
            for page in range(4):
                offset = page * page_limit
                df = PRO.dc_index(trade_date=trade_date, limit=page_limit, offset=offset)
                if df is not None and not df.empty:
                    rows.append(df)
                if df is None or df.empty or len(df) < page_limit:
                    break
        except Exception:
            rows = []
        if rows:
            merged = pd.concat(rows, ignore_index=True)
            if "ts_code" in merged.columns and "name" in merged.columns:
                merged["ts_code"] = merged["ts_code"].astype(str)
                return {
                    str(row["ts_code"]): str(row["name"])
                    for _, row in merged[["ts_code", "name"]].dropna(subset=["ts_code"]).drop_duplicates(subset=["ts_code"], keep="last").iterrows()
                }
    return {}


def fill_ths_daily_theme(config, trade_dates):
    """补全 ths_daily（原样直存，按日文件）"""
    root = GET_ROOT_DIR(config) / config["path"]
    root.mkdir(parents=True, exist_ok=True)

    LOG(f"\n{'=' * 60}")
    LOG("补全 ths_daily（theme 专用）")
    LOG("=" * 60)

    success = 0
    successful_dates = set()
    total_days = len(trade_dates)
    row_limit = int(config.get("page_limit", 3000) or 3000)
    fallback_threshold = int(config.get("row_truncation_guard", row_limit) or row_limit)

    for index, trade_date in enumerate(trade_dates, start=1):
        start_live_spinner(f"ths_daily: 正在拉取 {trade_date} ({index}/{total_days})")
        try:
            df = PRO.ths_daily(trade_date=trade_date)
        except Exception as exc:
            stop_live_spinner(f"  ❌ {trade_date}: {str(exc)[:80]}", "ERROR")
            continue

        if df is None or df.empty:
            stop_live_spinner(f"  ⚪ {trade_date}: 无数据")
            continue

        if len(df) >= fallback_threshold:
            update_live_spinner(f"ths_daily: {trade_date} 返回 {len(df)} 条，接近阈值 {row_limit}，转逐指数防截断")
            ts_codes = _load_ths_index_code_pool()
            detailed_rows = []
            if ts_codes:
                for code_index, ts_code in enumerate(ts_codes, start=1):
                    try:
                        detail_df = PRO.ths_daily(ts_code=ts_code, trade_date=trade_date)
                        if detail_df is not None and not detail_df.empty:
                            detailed_rows.append(detail_df)
                    except Exception:
                        continue
                    if code_index % 50 == 0 or code_index == len(ts_codes):
                        update_live_spinner(
                            f"ths_daily: 正在补齐 {trade_date} | 指数 {code_index}/{len(ts_codes)}"
                        )
                    time.sleep(0.05)
                if detailed_rows:
                    df = pd.concat(detailed_rows, ignore_index=True)

        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].astype(str)
        if "ts_code" in df.columns:
            df["ts_code"] = df["ts_code"].astype(str)
        dedup_cols = [col for col in ["ts_code", "trade_date"] if col in df.columns]
        if dedup_cols:
            df = df.drop_duplicates(subset=dedup_cols, keep="last")

        _write_theme_bundle(root / f"ths_daily_{trade_date}.csv", df, "ths_daily")
        success += 1
        successful_dates.add(str(trade_date))
        stop_live_spinner(f"  ✅ {trade_date}: {len(df)} 条", "SUCCESS")

    LOG(f"完成: 成功 {success}/{len(trade_dates)}")
    target_trade_date = str(trade_dates[-1]) if trade_dates else None
    return {
        "ok": success > 0,
        "covered_target_date": bool(target_trade_date and target_trade_date in successful_dates),
    }


def fill_dc_daily_theme(config, trade_dates):
    """补全 dc_daily（原样直存，按日文件）"""
    root = GET_ROOT_DIR(config) / config["path"]
    root.mkdir(parents=True, exist_ok=True)

    LOG(f"\n{'=' * 60}")
    LOG("补全 dc_daily（theme 专用）")
    LOG("=" * 60)

    success = 0
    successful_dates = set()
    total_days = len(trade_dates)
    page_limit = 2000

    for index, trade_date in enumerate(trade_dates, start=1):
        all_rows = []
        start_live_spinner(f"dc_daily: 正在拉取 {trade_date} ({index}/{total_days})")
        for page in range(4):
            offset = page * page_limit
            try:
                df = PRO.dc_daily(trade_date=trade_date, limit=page_limit, offset=offset)
                if df is not None and not df.empty:
                    all_rows.append(df)
                if df is None or df.empty or len(df) < page_limit:
                    break
            except Exception as exc:
                stop_live_spinner(f"  ❌ {trade_date} offset={offset} 失败: {str(exc)[:60]}", "ERROR")
                all_rows = []
                break
            time.sleep(0.2)
        if not all_rows:
            stop_live_spinner(f"  ⚪ {trade_date}: 无数据")
            continue

        df = pd.concat(all_rows, ignore_index=True)
        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].astype(str)
        dedup_cols = [col for col in ["ts_code", "trade_date"] if col in df.columns]
        if dedup_cols:
            df = df.drop_duplicates(subset=dedup_cols, keep="last")

        _write_theme_bundle(root / f"dc_daily_{trade_date}.csv", df, "dc_daily")
        success += 1
        successful_dates.add(str(trade_date))
        stop_live_spinner(f"  ✅ {trade_date}: {len(df)} 条", "SUCCESS")

    LOG(f"完成: 成功 {success}/{len(trade_dates)}")
    target_trade_date = str(trade_dates[-1]) if trade_dates else None
    return {
        "ok": success > 0,
        "covered_target_date": bool(target_trade_date and target_trade_date in successful_dates),
    }


def fill_dc_index_theme(config, trade_dates):
    """补全 dc_index（原样直存，单文件最新快照）"""
    root = GET_ROOT_DIR(config) / config["path"]
    root.mkdir(parents=True, exist_ok=True)

    LOG(f"\n{'=' * 60}")
    LOG("补全 dc_index（theme 专用）")
    LOG("=" * 60)

    page_limit = int(config.get("page_limit", 2000) or 2000)
    max_pages = int(config.get("max_pages", 4) or 4)
    target_trade_date = str(trade_dates[-1]) if trade_dates else None
    candidate_dates = [str(item) for item in trade_dates]
    latest_first_dates = list(reversed(candidate_dates))
    total_days = len(latest_first_dates)
    chosen_trade_date = None
    chosen_df = None

    for index, trade_date in enumerate(latest_first_dates, start=1):
        all_rows = []
        start_live_spinner(f"dc_index: 正在拉取 {trade_date} ({index}/{total_days})")
        for page in range(max_pages):
            offset = page * page_limit
            try:
                df = PRO.dc_index(trade_date=trade_date, limit=page_limit, offset=offset)
                if df is not None and not df.empty:
                    all_rows.append(df)
                if df is None or df.empty or len(df) < page_limit:
                    break
            except Exception as exc:
                stop_live_spinner(f"  ❌ {trade_date} offset={offset} 失败: {str(exc)[:60]}", "ERROR")
                all_rows = []
                break
            time.sleep(0.2)

        if not all_rows:
            stop_live_spinner(f"  ⚪ {trade_date}: 无数据")
            continue

        df = pd.concat(all_rows, ignore_index=True)
        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].astype(str)
        dedup_cols = [col for col in ["ts_code", "trade_date"] if col in df.columns]
        if dedup_cols:
            df = df.drop_duplicates(subset=dedup_cols, keep="last")
        chosen_trade_date = str(trade_date)
        chosen_df = df
        label = (
            f"  ✅ {trade_date}: {len(df)} 条"
            if chosen_trade_date == target_trade_date
            else f"  ✅ {trade_date}: {len(df)} 条 (作为最新可用快照)"
        )
        stop_live_spinner(label, "SUCCESS")
        break

    if chosen_df is None:
        LOG(f"完成: 成功 0/{len(trade_dates)}")
        return {"ok": False, "covered_target_date": False}

    _write_theme_bundle(root / "dc_index_all.csv", chosen_df, "dc_index")
    LOG(f"完成: 成功 1/{len(trade_dates)}")
    return {
        "ok": True,
        "covered_target_date": bool(target_trade_date and chosen_trade_date == target_trade_date),
    }


def fill_dc_member_theme(config, trade_dates):
    """补全 dc_member（按板块文件保存，结构与 ths_member 对齐）"""
    root = GET_ROOT_DIR(config) / config["path"]
    root.mkdir(parents=True, exist_ok=True)

    LOG(f"\n{'=' * 60}")
    LOG("补全 dc_member（theme 专用）")
    LOG("=" * 60)

    success = 0
    successful_dates = set()
    total_days = len(trade_dates)
    page_limit = int(config.get("page_limit", 5000) or 5000)
    max_pages = int(config.get("max_pages", 10) or 10)
    per_code_sleep = float(config.get("per_code_sleep_sec", 0.5) or 0.5)
    rate_limit_retry_sleep = float(config.get("rate_limit_retry_sleep_sec", 1.5) or 1.5)
    max_per_code_retries = int(config.get("max_per_code_retries", 3) or 3)

    for index, trade_date in enumerate(trade_dates, start=1):
        all_rows = []
        start_live_spinner(f"dc_member: 正在拉取 {trade_date} ({index}/{total_days})")
        board_name_map = _load_dc_index_name_map(trade_date=trade_date)

        date_only_ok = False
        date_only_full_pages = 0
        date_only_pages = 0
        try:
            for page in range(max_pages):
                offset = page * page_limit
                df = PRO.dc_member(trade_date=trade_date, limit=page_limit, offset=offset)
                date_only_pages += 1
                if df is not None and not df.empty:
                    all_rows.append(df)
                    if len(df) >= page_limit:
                        date_only_full_pages += 1
                if df is None or df.empty or len(df) < page_limit:
                    break
            date_only_ok = bool(all_rows)
        except Exception as exc:
            LOG(f"  ⚠️ {trade_date}: 按交易日直拉失败，回退逐板块模式: {str(exc)[:80]}", "WARNING")
            all_rows = []

        if date_only_ok and date_only_full_pages >= max_pages:
            LOG(
                f"  ⚠️ {trade_date}: 按交易日直拉连续命中 {max_pages} 个满页，疑似存在截断，回退逐板块模式",
                "WARNING",
            )
            date_only_ok = False
            all_rows = []

        if not date_only_ok:
            index_codes = _load_dc_index_code_pool(trade_date=trade_date)
            if not index_codes:
                stop_live_spinner(f"  ⚪ {trade_date}: dc_index 无可用板块代码，跳过", "WARNING")
                continue

            for offset, ts_code in enumerate(index_codes, start=1):
                df = None
                last_exc = None
                for retry_index in range(max_per_code_retries):
                    try:
                        df = PRO.dc_member(trade_date=trade_date, ts_code=ts_code)
                        last_exc = None
                        break
                    except Exception as exc:
                        last_exc = exc
                        error_text = str(exc)
                        if "请求速度过快" in error_text and retry_index < max_per_code_retries - 1:
                            sleep_seconds = rate_limit_retry_sleep * (retry_index + 1)
                            update_live_spinner(
                                f"dc_member: {ts_code} 命中限速，{sleep_seconds:.1f}s 后重试 "
                                f"({retry_index + 1}/{max_per_code_retries})"
                            )
                            time.sleep(sleep_seconds)
                            continue
                        break
                if last_exc is not None:
                    stop_live_spinner(f"  ❌ {trade_date} {ts_code}: {str(last_exc)[:60]}", "ERROR")
                    all_rows = []
                    break
                if df is not None and not df.empty:
                    all_rows.append(df)
                if offset % 20 == 0 or offset == len(index_codes):
                    update_live_spinner(f"dc_member: 正在拉取 {trade_date} | 板块 {offset}/{len(index_codes)}")
                time.sleep(per_code_sleep)

        if not all_rows:
            stop_live_spinner(f"  ⚪ {trade_date}: 无数据")
            continue

        if date_only_ok:
            update_live_spinner(
                f"dc_member: 正在拉取 {trade_date} | 按交易日汇总 {sum(len(df) for df in all_rows)} 条 | 页数 {date_only_pages}"
            )

        df = pd.concat(all_rows, ignore_index=True)
        if "trade_date" in df.columns:
            df["trade_date"] = df["trade_date"].astype(str)
        dedup_cols = [col for col in ["ts_code", "con_code", "trade_date"] if col in df.columns]
        if dedup_cols:
            df = df.drop_duplicates(subset=dedup_cols, keep="last")
        file_count = 0
        if "ts_code" in df.columns:
            df["ts_code"] = df["ts_code"].astype(str)
            for ts_code, group in df.groupby("ts_code"):
                filename = f"{ts_code}.csv"
                filepath = root / filename
                if filepath.exists() and filepath.stat().st_size > 0:
                    try:
                        old = pd.read_csv(filepath, low_memory=False)
                        if "trade_date" in old.columns:
                            old["trade_date"] = old["trade_date"].astype(str)
                        merged = pd.concat([old, group], ignore_index=True)
                    except Exception:
                        merged = group.copy()
                else:
                    merged = group.copy()
                local_dedup_cols = [col for col in ["ts_code", "con_code", "trade_date"] if col in merged.columns]
                if local_dedup_cols:
                    merged = merged.drop_duplicates(subset=local_dedup_cols, keep="last")
                sort_cols = [col for col in ["trade_date", "con_code"] if col in merged.columns]
                if sort_cols:
                    merged = merged.sort_values(sort_cols).reset_index(drop=True)
                _write_theme_bundle(filepath, merged, "dc_member")
                file_count += 1
        else:
            fallback_path = root / f"{trade_date}.csv"
            _write_theme_bundle(fallback_path, df, "dc_member")
            file_count = 1
        success += 1
        successful_dates.add(str(trade_date))
        stop_live_spinner(f"  ✅ {trade_date}: {len(df)} 条 | 板块文件 {file_count}", "SUCCESS")

    LOG(f"完成: 成功 {success}/{len(trade_dates)}")
    target_trade_date = str(trade_dates[-1]) if trade_dates else None
    return {
        "ok": success > 0,
        "covered_target_date": bool(target_trade_date and target_trade_date in successful_dates),
    }
