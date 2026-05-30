#!/usr/bin/env python3
"""
将股票日线数据散落的按年分片 parquet 合并为按 ts_code 的扁平 parquet 文件。
适用接口: daily

合并前: daily/2025/daily_000001.SZ.parquet + daily/2026/daily_000001.SZ.parquet
合并后: daily/000001.SZ.parquet (包含所有年份)
"""

from pathlib import Path
import pandas as pd
import re

STOCK_DATA_ROOT = Path("/Users/penghongming/quant-data/tushare/股票数据")

INTERFACES = {
    "daily": {"prefix": "daily_"},
}


def migrate_interface(interface_name: str, prefix: str):
    print(f"\n=== 迁移 {interface_name} ===")
    interface_dir = STOCK_DATA_ROOT / interface_name
    if not interface_dir.exists():
        print(f"  {interface_dir} 不存在，跳过")
        return

    code_files: dict[str, list[Path]] = {}
    pattern = re.compile(rf"^{re.escape(prefix)}(\S+)\.parquet$")

    for year_dir in sorted(interface_dir.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        for pq_file in sorted(year_dir.glob(f"{prefix}*.parquet")):
            match = pattern.match(pq_file.name)
            if not match:
                continue
            ts_code = match.group(1)
            code_files.setdefault(ts_code, []).append(pq_file)

    if not code_files:
        print("  未找到散落 parquet 文件")
        return

    print(f"  发现 {len(code_files)} 个股票代码")
    for ts_code, files in sorted(code_files.items()):
        target = interface_dir / f"{ts_code}.parquet"
        print(f"  {ts_code}: {len(files)} 个分片 -> {target.name}", end="")

        try:
            frames = []
            for f in files:
                df = pd.read_parquet(f)
                if not df.empty:
                    frames.append(df)
            if not frames:
                print("  ⚠️ 空")
                continue
            combined = pd.concat(frames, ignore_index=True)
            dedup_cols = [c for c in ["trade_date", "ts_code"] if c in combined.columns]
            if dedup_cols:
                combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
            if "trade_date" in combined.columns:
                combined = combined.sort_values("trade_date")
            combined.to_parquet(target, index=False)
            print(f"  ✅ {len(combined)} 条")
        except Exception as e:
            print(f"  ❌ 失败: {e}")


def main():
    for name, cfg in INTERFACES.items():
        migrate_interface(name, cfg["prefix"])

    print("\n=== 验证 ===")
    for name in INTERFACES:
        dir_path = STOCK_DATA_ROOT / name
        if not dir_path.exists():
            continue
        flat_pqs = sorted(dir_path.glob("*.parquet"))
        yearly_pqs = sorted(dir_path.rglob("*/*.parquet"))
        size_mb = sum(f.stat().st_size for f in flat_pqs) / 1024 / 1024
        print(f"  {name}: {len(flat_pqs)} 扁平文件 ({size_mb:.0f}MB), {len(yearly_pqs)} 年分片文件")


if __name__ == "__main__":
    main()
