#!/usr/bin/env python3
"""
一次性迁移“按股票扁平文件”到“年/月/日按日期文件”。

默认接口:
- moneyflow
- stk_factor_pro
- cyq_perf
- cyq_chips

行为:
1) 把根目录 legacy 股票文件迁移到 _by_stock 缓存目录
2) 从 _by_stock 重建 YYYY/MM/DD/<prefix><trade_date>.csv(+parquet)
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.files import write_multi_format_bundle
from core.registry import build_auto_fill_registry
from utils.paths import get_stock_data_dir


DEFAULT_INTERFACES = ["moneyflow", "stk_factor_pro", "cyq_perf", "cyq_chips"]


def _normalize_trade_date(series: pd.Series) -> pd.Series:
    normalized = (
        series.astype("string")
        .str.extract(r"(\d{8})", expand=False)
        .fillna("")
        .str.strip()
    )
    return normalized


def _is_date_named_csv(csv_file: Path, prefix: str) -> bool:
    return re.match(rf"^{re.escape(prefix)}\d{{8}}\.csv$", csv_file.name) is not None


def _merge_csv_file(target: Path, source: Path) -> None:
    try:
        left = pd.read_csv(target, low_memory=False)
        right = pd.read_csv(source, low_memory=False)
        merged = pd.concat([left, right], ignore_index=True).drop_duplicates(keep="last")
        merged.to_csv(target, index=False)
        source.unlink(missing_ok=True)
    except Exception:
        backup = target.with_name(f"{target.stem}.dup{source.suffix}")
        shutil.move(str(source), str(backup))


def _move_legacy_root_files(data_dir: Path, prefix: str, cache_dir: Path) -> tuple[int, int]:
    moved = 0
    merged = 0
    cache_dir.mkdir(parents=True, exist_ok=True)

    for csv_file in sorted(data_dir.glob("*.csv")):
        if _is_date_named_csv(csv_file, prefix):
            continue
        target = cache_dir / csv_file.name
        if target.exists():
            merged += 1
            _merge_csv_file(target, csv_file)
        else:
            shutil.move(str(csv_file), str(target))
            moved += 1

    for parquet_file in sorted(data_dir.glob("*.parquet")):
        if re.match(rf"^{re.escape(prefix)}\d{{8}}\.parquet$", parquet_file.name):
            continue
        target = cache_dir / parquet_file.name
        if target.exists():
            parquet_file.unlink(missing_ok=True)
        else:
            shutil.move(str(parquet_file), str(target))
    return moved, merged


def _iter_source_cache_csv(cache_dir: Path, prefix: str):
    for csv_file in sorted(cache_dir.glob("*.csv")):
        if _is_date_named_csv(csv_file, prefix):
            continue
        yield csv_file


def _append_temp_by_date(
    source_file: Path,
    tmp_dir: Path,
    start_date: str | None,
    end_date: str | None,
) -> tuple[int, int]:
    try:
        frame = pd.read_csv(source_file, low_memory=False)
    except Exception:
        return 0, 1

    if frame.empty or "trade_date" not in frame.columns:
        return 0, 0

    frame = frame.copy()
    frame["trade_date"] = _normalize_trade_date(frame["trade_date"])
    frame = frame[(frame["trade_date"] != "")]
    if start_date:
        frame = frame[frame["trade_date"] >= start_date]
    if end_date:
        frame = frame[frame["trade_date"] <= end_date]
    if frame.empty:
        return 0, 0

    count = 0
    for trade_date, part in frame.groupby("trade_date"):
        tmp_file = tmp_dir / f"{trade_date}.csv"
        part.to_csv(tmp_file, mode="a", header=not tmp_file.exists(), index=False)
        count += len(part)
    return count, 0


def _write_ymd_file(data_dir: Path, prefix: str, trade_date: str, new_rows: pd.DataFrame) -> tuple[int, int]:
    y = trade_date[:4]
    m = trade_date[4:6]
    d = trade_date[6:8]
    out_dir = data_dir / y / m / d
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / f"{prefix}{trade_date}.csv"

    if out_csv.exists():
        existing = pd.read_csv(out_csv, low_memory=False)
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows.copy()

    dedup_cols = [col for col in ["trade_date", "ts_code", "index_code", "con_code", "name"] if col in combined.columns]
    if dedup_cols:
        combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
    else:
        combined = combined.drop_duplicates(keep="last")

    if "trade_date" in combined.columns:
        combined["trade_date"] = _normalize_trade_date(combined["trade_date"])
        combined = combined.sort_values(["trade_date"] + ([c for c in ["ts_code", "index_code"] if c in combined.columns]))

    combined.to_csv(out_csv, index=False)
    write_multi_format_bundle(out_csv, combined)
    return len(new_rows), len(combined)


def migrate_interface(interface_name: str, config: dict, start_date: str | None, end_date: str | None) -> None:
    data_dir = Path(get_stock_data_dir()) / config["path"]
    prefix = config["prefix"]
    cache_subdir = config.get("code_cache_subdir", "_by_stock")
    cache_dir = data_dir / cache_subdir
    tmp_dir = data_dir / "_tmp_ymd_migrate"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n=== 迁移 {interface_name} ===")
    print(f"数据目录: {data_dir}")
    moved, merged = _move_legacy_root_files(data_dir, prefix, cache_dir)
    print(f"迁移 legacy 根目录文件 -> {cache_subdir}: moved={moved}, merged={merged}")

    scanned_files = 0
    appended_rows = 0
    failed_files = 0
    for source_file in _iter_source_cache_csv(cache_dir, prefix):
        scanned_files += 1
        rows, err = _append_temp_by_date(source_file, tmp_dir, start_date, end_date)
        appended_rows += rows
        failed_files += err
        if scanned_files % 500 == 0:
            print(f"  已扫描 {scanned_files} 个缓存文件...")

    print(
        f"缓存扫描完成: files={scanned_files}, appended_rows={appended_rows}, failed_files={failed_files}"
    )

    date_files = sorted(tmp_dir.glob("*.csv"))
    print(f"待落盘日期文件: {len(date_files)}")
    wrote_dates = 0
    for idx, tmp_file in enumerate(date_files, 1):
        trade_date = tmp_file.stem
        new_rows = pd.read_csv(tmp_file, low_memory=False)
        _write_ymd_file(data_dir, prefix, trade_date, new_rows)
        if idx % 100 == 0:
            print(f"  已写入 {idx}/{len(date_files)} 天...")
        wrote_dates += 1

    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"{interface_name} 迁移完成: 写入 {wrote_dates} 天")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="迁移扁平接口到年/月/日结构")
    parser.add_argument(
        "--interfaces",
        nargs="+",
        default=DEFAULT_INTERFACES,
        help=f"接口列表，默认: {' '.join(DEFAULT_INTERFACES)}",
    )
    parser.add_argument("--start-date", default=None, help="起始日期 YYYYMMDD")
    parser.add_argument("--end-date", default=None, help="结束日期 YYYYMMDD")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stock_registry = build_auto_fill_registry()["stock"]
    registry = {}
    for section in ("by_date", "by_stock"):
        registry.update(stock_registry.get(section, {}))
    for name in args.interfaces:
        config = registry.get(name)
        if not config:
            print(f"[跳过] 未找到接口配置: {name}")
            continue
        migrate_interface(name, config, args.start_date, args.end_date)


if __name__ == "__main__":
    main()
