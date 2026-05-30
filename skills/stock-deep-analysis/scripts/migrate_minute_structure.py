#!/usr/bin/env python3
"""
分钟数据目录结构迁移脚本
将旧结构统一迁移到：分钟数据/YYYY/MM/DD/{symbol}/{granularity}.csv

支持三种旧结构：
1. 旧结构A: {exchange}{code}/minute_{date}.csv/json  （如 sh600103/minute_20260422.csv）
2. 旧结构B: {code}/{date}/minute_kline{granularity}.csv （如 002471/2026-04-08/minute_kline.csv）
3. 过渡扁平结构: YYYY/MM/DD/{symbol}_1m.csv  → 改为 YYYY/MM/DD/{symbol}/1m.csv

说明：
- 当前权威分钟目录是 `分钟数据/YYYY/MM/DD/{symbol}/1m.csv`
- 本脚本只负责把旧结构收敛到这套标准目录
"""

import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from common import STOCK_DATA_ROOT, MINUTE_DATA_ROOT

MINUTE_ROOT = MINUTE_DATA_ROOT


def guess_market(code: str) -> str:
    """根据代码首位判断市场"""
    if code.startswith("6") or code.startswith("5") or code.startswith("9"):
        return "SH"
    return "SZ"


def parse_old_structure_a():
    """解析旧结构A: {exchange}{code}/minute_{date}.csv/json"""
    pattern = re.compile(r"^(sh|sz)(\d{6})")
    date_pattern = re.compile(r"minute_(\d{4}-?\d{2}-?\d{2})")

    results = []
    for item in MINUTE_ROOT.iterdir():
        if not item.is_dir():
            continue
        m = pattern.match(item.name)
        if not m:
            continue
        exchange, code = m.groups()
        symbol = f"{code}.{exchange.upper()}"

        for file in item.iterdir():
            if not file.is_file():
                continue
            dm = date_pattern.search(file.name)
            if not dm:
                continue
            date_raw = dm.group(1)
            # 统一日期格式
            if len(date_raw) == 8:
                date_text = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}"
            else:
                date_text = date_raw

            # 推断粒度（旧结构A都是1m）
            granularity = "1m"
            results.append({
                "source": file,
                "symbol": symbol,
                "date": date_text,
                "granularity": granularity,
                "structure": "A",
            })
    return results


def parse_old_structure_b():
    """解析旧结构B: {code}/{date}/minute_kline{granularity}.csv"""
    code_pattern = re.compile(r"^\d{6}$")
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    file_pattern = re.compile(r"minute_kline(_?\d*m?)\.csv$")

    results = []
    for item in MINUTE_ROOT.iterdir():
        if not item.is_dir():
            continue
        if not code_pattern.match(item.name):
            continue
        code = item.name
        symbol = f"{code}.{guess_market(code)}"

        for subdir in item.iterdir():
            if not subdir.is_dir():
                continue
            if not date_pattern.match(subdir.name):
                continue
            date_text = subdir.name

            for file in subdir.iterdir():
                if not file.is_file():
                    continue
                fm = file_pattern.search(file.name)
                if not fm:
                    continue
                gran_raw = fm.group(1)
                if gran_raw in ("", "_"):
                    granularity = "1m"
                else:
                    granularity = gran_raw.lstrip("_")  # e.g. "5m", "15m"

                results.append({
                    "source": file,
                    "symbol": symbol,
                    "date": date_text,
                    "granularity": granularity,
                    "structure": "B",
                })
    return results


def parse_current_flat_structure():
    """解析当前扁平新结构: YYYY/MM/DD/{symbol}_{granularity}.csv"""
    results = []
    file_pattern = re.compile(r"^(\d{6}\.[A-Z]{2})_(\d+m)\.csv$")

    for year_dir in MINUTE_ROOT.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit() or len(year_dir.name) != 4:
            continue
        for month_dir in year_dir.iterdir():
            if not month_dir.is_dir() or not month_dir.name.isdigit() or len(month_dir.name) != 2:
                continue
            for day_dir in month_dir.iterdir():
                if not day_dir.is_dir() or not day_dir.name.isdigit() or len(day_dir.name) != 2:
                    continue
                date_text = f"{year_dir.name}-{month_dir.name}-{day_dir.name}"
                for file in day_dir.iterdir():
                    if not file.is_file():
                        continue
                    fm = file_pattern.match(file.name)
                    if not fm:
                        continue
                    symbol, granularity = fm.groups()
                    results.append({
                        "source": file,
                        "symbol": symbol,
                        "date": date_text,
                        "granularity": granularity,
                        "structure": "flat",
                    })
    return results


def compute_target(entry: dict) -> Path:
    """计算目标路径"""
    y, m, d = entry["date"].split("-")
    return MINUTE_ROOT / y / m / d / entry["symbol"] / f"{entry['granularity']}.csv"


def run_migration(dry_run: bool = True):
    entries = []
    entries.extend(parse_old_structure_a())
    entries.extend(parse_old_structure_b())
    entries.extend(parse_current_flat_structure())

    print(f"发现 {len(entries)} 个待迁移文件")
    print("=" * 60)

    moved = 0
    skipped = 0
    errors = 0

    for entry in entries:
        source = entry["source"]
        target = compute_target(entry)

        if target.exists():
            print(f"[SKIP] 目标已存在: {target}")
            skipped += 1
            continue

        print(f"[MOVE] {source}  →  {target}")
        if not dry_run:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(target))
                moved += 1
            except Exception as e:
                print(f"[ERROR] 迁移失败: {e}")
                errors += 1
        else:
            moved += 1

    print("=" * 60)
    print(f"dry_run={dry_run} | 总计: {len(entries)} | 可迁移: {moved} | 已存在跳过: {skipped} | 错误: {errors}")

    # 如果非 dry_run，尝试清理空目录
    if not dry_run:
        cleaned = 0
        for item in MINUTE_ROOT.iterdir():
            if not item.is_dir():
                continue
            # 只清理旧结构的根目录（非年目录）
            if item.name.isdigit() and len(item.name) == 4:
                continue  # 跳过年目录
            try:
                # 递归删除空目录
                for root, dirs, files in os.walk(str(item), topdown=False):
                    for d in dirs:
                        dpath = Path(root) / d
                        if dpath.exists() and not any(dpath.iterdir()):
                            dpath.rmdir()
                            cleaned += 1
                if item.exists() and not any(item.iterdir()):
                    item.rmdir()
                    cleaned += 1
            except Exception:
                pass
        print(f"清理空目录: {cleaned} 个")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="迁移分钟数据目录结构")
    parser.add_argument("--execute", action="store_true", help="实际执行迁移（默认 dry-run）")
    args = parser.parse_args()

    run_migration(dry_run=not args.execute)
