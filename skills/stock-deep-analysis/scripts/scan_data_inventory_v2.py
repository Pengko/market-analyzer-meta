#!/usr/bin/env python3
"""
可靠的本地数据盘点脚本。
规则：
1. 检查目录结构（按年份 / 按类别 / 平铺）
2. 对于文件名不含日期的数据，读取文件内容提取 trade_date
3. 随机抽样验证，避免偏差
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

STOCK_DATA_ROOT = Path.home() / "quant-data/tushare/股票数据"

YEAR_PATTERN = re.compile(r"^\d{4}$")
DATE_IN_FILENAME = re.compile(r"(\d{8})")


def detect_dir_structure(data_dir: Path) -> dict[str, Any]:
    """检测目录结构：年份目录 / 类别目录 / 平铺"""
    if not data_dir.exists():
        return {"type": "missing"}

    subdirs = [d for d in data_dir.iterdir() if d.is_dir()]
    files = [f for f in data_dir.iterdir() if f.is_file()]

    if not subdirs and not files:
        return {"type": "empty"}

    year_dirs = [d.name for d in subdirs if YEAR_PATTERN.match(d.name)]
    if year_dirs:
        return {"type": "by_year", "years": sorted(year_dirs)}

    if subdirs:
        return {
            "type": "by_category",
            "categories": sorted([d.name for d in subdirs]),
        }

    return {"type": "flat", "file_count": len(files)}


def extract_latest_date_from_content(
    file_path: Path,
    date_column: str = "trade_date",
    sample_rows: int = 3,
) -> str | None:
    """从 CSV 文件内容提取最新日期。随机抽样几行验证，避免只看最后一行出错。"""
    try:
        with open(file_path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                return None
            # 查找日期列
            date_col = None
            for col in reader.fieldnames:
                if col.lower() in ("trade_date", "date", "end_date", "ann_date"):
                    date_col = col
                    break
            if not date_col:
                # 没有标准日期列，尝试直接看第一列
                date_col = reader.fieldnames[0]

            rows = list(reader)
            if not rows:
                return None

            # 随机抽样验证最后几行
            check_indices = list(range(max(0, len(rows) - sample_rows), len(rows)))
            dates = []
            for idx in check_indices:
                val = str(rows[idx].get(date_col, "")).strip()
                if val and len(val) == 8 and val.isdigit():
                    dates.append(val)
                elif val and "-" in val:
                    # YYYY-MM-DD 格式
                    d = val.replace("-", "")
                    if len(d) == 8:
                        dates.append(d)

            if not dates:
                return None
            return max(dates)
    except Exception:
        return None


def scan_data_inventory(
    root: Path = STOCK_DATA_ROOT,
    sample_size: int = 20,
    specific_dirs: list[str] | None = None,
) -> dict[str, Any]:
    """扫描整个数据目录，返回每个子目录的结构和最新日期。"""
    if specific_dirs:
        dirs = [root / d for d in specific_dirs]
    else:
        dirs = sorted([d for d in root.iterdir() if d.is_dir()])

    results = {}
    for data_dir in dirs:
        name = data_dir.name
        structure = detect_dir_structure(data_dir)

        if structure["type"] == "missing":
            results[name] = {"exists": False}
            continue
        if structure["type"] == "empty":
            results[name] = {"exists": True, "type": "empty", "files": 0}
            continue

        # 计算总文件数
        total_files = sum(1 for f in data_dir.rglob("*") if f.is_file())

        # 根据结构选取要扫描的文件
        # 原则：对于 by_year 目录，优先扫描最新年份的全部文件（避免旧年份文件多导致抽样偏差）
        # 只有文件名不含日期时，才需要读取文件内容
        dates_from_filename: list[str] = []
        dates_from_content: list[str] = []
        sampled_files: list[Path] = []

        if structure["type"] == "by_year":
            years = structure["years"]
            # 只扫描最新年份，避免旧年份文件干扰
            latest_year = years[-1] if years else None
            if latest_year:
                year_dir = data_dir / latest_year
                if year_dir.exists():
                    all_files = [f for f in year_dir.iterdir() if f.is_file()]
                    for f in all_files:
                        m = DATE_IN_FILENAME.search(f.name)
                        if m:
                            dates_from_filename.append(m.group(1))
                        else:
                            sampled_files.append(f)
        elif structure["type"] == "by_category":
            # 递归收集所有子目录的文件
            all_files = [f for f in data_dir.rglob("*") if f.is_file()]
            for f in all_files:
                m = DATE_IN_FILENAME.search(f.name)
                if m:
                    dates_from_filename.append(m.group(1))
                else:
                    sampled_files.append(f)
        else:
            # 平铺
            all_files = [f for f in data_dir.iterdir() if f.is_file()]
            for f in all_files:
                m = DATE_IN_FILENAME.search(f.name)
                if m:
                    dates_from_filename.append(m.group(1))
                else:
                    sampled_files.append(f)

        # 对于文件名不含日期的文件，随机抽样读取内容
        if sampled_files:
            check_files = random.sample(
                sampled_files, min(sample_size, len(sampled_files))
            )
            for f in check_files:
                content_date = extract_latest_date_from_content(f)
                if content_date:
                    dates_from_content.append(content_date)

        # 综合判断最新日期
        all_dates = dates_from_filename + dates_from_content
        if all_dates:
            latest_date = max(all_dates)
            latest_formatted = f"{latest_date[:4]}-{latest_date[4:6]}-{latest_date[6:8]}"
        else:
            latest_formatted = None

        results[name] = {
            "exists": True,
            "type": structure["type"],
            "files": total_files,
            "latest_date": latest_formatted,
            "years": structure.get("years"),
            "categories": structure.get("categories"),
            "sampled": len(sampled_files),
            "dates_from_filename": len(dates_from_filename),
            "dates_from_content": len(dates_from_content),
        }

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="可靠的本地数据盘点")
    parser.add_argument(
        "--dirs",
        nargs="+",
        help="指定检查的目录名，不指定则扫描全部",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=20,
        help="每个目录随机抽样文件数，默认20",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出 JSON 格式",
    )
    args = parser.parse_args()

    results = scan_data_inventory(
        specific_dirs=args.dirs,
        sample_size=args.sample,
    )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"{'目录名':<20} {'结构':<15} {'文件数':<10} {'最新日期':<12} {'抽样验证':<10}")
        print("-" * 70)
        for name, info in sorted(results.items()):
            if not info.get("exists"):
                print(f"{name:<20} {'缺失':<15} {'-':<10} {'-':<12} {'-':<10}")
                continue
            t = info.get("type", "?")
            files = info.get("files", 0)
            latest = info.get("latest_date") or "未知"
            sampled = info.get("sampled", 0)
            from_content = info.get("dates_from_content", 0)
            tag = f"{sampled}个文件"
            if from_content > 0:
                tag += f"(含{from_content}个读内容)"
            print(f"{name:<20} {t:<15} {files:<10} {latest:<12} {tag:<10}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
