#!/usr/bin/env python3
"""
待验证报告自动验证脚本

每个交易日收盘后运行，对个股分析报告/待验证 文件夹中的待验证报告进行深度验证：
1. 扫描所有待验证报告
2. 获取真实 T+1 / T+2 收盘数据
3. 计算预测命中率
4. 输出验证报告

用法：
    python3 validate_pending_reports.py
    python3 validate_pending_reports.py --date 2026-04-15
    python3 validate_pending_reports.py --format json
"""

import argparse
import csv
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from data.config_loader import cfg

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
PENDING_DIR = Path.home() / "quant-data" / "市场分析" / "reports" / "个股分析报告"
VALIDATIONS_DIR = PENDING_DIR / "已验证"
DATA_ROOT = cfg.paths("stock_data_root")
DAILY_DIR = DATA_ROOT / "daily"
TRADE_CAL_PATH = cfg.paths("trade_cal_dir") / "trade_cal_all.csv"



def is_trading_day(date_str: str) -> bool:
    """检查指定日期是否为交易日"""
    if TRADE_CAL_PATH.exists():
        date_normalized = date_str.replace("-", "")
        with open(TRADE_CAL_PATH, "r", encoding="utf-8-sig") as f:
            for line in f:
                if date_normalized in line and ",1," in line:
                    return True
        return False

    return is_workday_fallback(date_str)


def is_workday_fallback(date_str: str) -> bool:
    """兜底：基于网络/本地时间判断是否为工作日（周一到周五）"""
    try:
        from datetime import datetime

        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.weekday() < 5
    except:
        return False


def get_previous_trading_day(date_str: str) -> Optional[str]:
    """获取指定日期的前一个交易日"""
    if TRADE_CAL_PATH.exists():
        date_normalized = date_str.replace("-", "")
        prev_date = None
        with open(TRADE_CAL_PATH, "r", encoding="utf-8-sig") as f:
            for line in f:
                if ",1," in line:
                    parts = line.strip().split(",")
                    if len(parts) >= 2:
                        cal_date = parts[1]
                        if cal_date < date_normalized:
                            prev_date = f"{cal_date[:4]}-{cal_date[4:6]}-{cal_date[6:]}"
                            break
        return prev_date

    return get_previous_workday(date_str)


def get_previous_workday(date_str: str) -> Optional[str]:
    """兜底：获取前一个工作日"""
    try:
        from datetime import datetime, timedelta

        dt = datetime.strptime(date_str, "%Y-%m-%d")
        for i in range(1, 8):
            prev = dt - timedelta(days=i)
            if prev.weekday() < 5:
                return prev.strftime("%Y-%m-%d")
    except:
        pass
    return None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="待验证报告自动验证")
    parser.add_argument(
        "--date", default=None, help="验证指定日期的待验证报告，格式 YYYY-MM-DD"
    )
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="输出格式"
    )
    parser.add_argument("--symbol", default=None, help="只验证指定股票")
    return parser.parse_args()


def get_previous_trade_date(symbol: str, trade_date: str) -> Optional[str]:
    rows = load_daily_rows(symbol)
    dates = [r["trade_date"] for r in rows]
    if trade_date in dates:
        idx = dates.index(trade_date)
        if idx > 0:
            return dates[idx - 1]
    return None


def get_next_trade_date(symbol: str, trade_date: str) -> Optional[str]:
    rows = load_daily_rows(symbol)
    for row in rows:
        if row["trade_date"] > trade_date:
            return row["trade_date"]
    return None


def get_t_plus_n_close(symbol: str, base_date: str, n: int) -> Optional[dict]:
    rows = load_daily_rows(symbol)
    target_rows = []
    for row in rows:
        if row["trade_date"] > base_date:
            target_rows.append(row)
        if len(target_rows) >= n:
            return target_rows[n - 1]
    return None


def load_daily_rows(symbol: str) -> list[dict[str, Any]]:
    raw_rows = _read_stock_parquet("daily", symbol)
    rows = []
    for row in raw_rows:
        row_symbol = row.get("ts_code", "")
        if row_symbol != symbol:
            continue
        trade_date = str(row.get("trade_date", "")).split(".")[0].strip()
        if not trade_date:
            continue
        rows.append(
            {
                "trade_date": trade_date,
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "close": float(row.get("close", 0) or 0),
                "vol": float(row.get("vol", 0) or 0),
                "pct": float(row.get("pct_chg", 0) or 0),
            }
        )
    rows.sort(key=lambda x: x["trade_date"])
    return rows


def get_daily_row(symbol: str, trade_date: str) -> Optional[dict]:
    rows = load_daily_rows(symbol)
    for row in rows:
        if row["trade_date"] == trade_date:
            return row
    return None


def classify_by_pct(pct: float) -> str:
    if pct >= 7:
        return "次日强延续"
    if pct >= 2:
        return "次日偏强"
    if pct > -2:
        return "次日分歧"
    if pct > -7:
        return "次日偏弱"
    return "次日高位兑现"


def coarse_direction(label: str) -> str:
    if label in ("次日强延续", "次日偏强"):
        return "偏多"
    if label in ("次日偏弱", "次日高位兑现"):
        return "偏空"
    return "中性"


def extract_predictions(pending_md: str) -> dict[str, Any]:
    predictions = {
        "t1_prediction": None,
        "t2_prediction": None,
        "noon_intraday_label": None,
    }

    t1_match = re.search(
        r"- 隔夜次日预期\s*\n\s*- 预测[：:]\s*(.+?)(?:\n\s*-|\n\n|\Z)", pending_md
    )
    if t1_match:
        predictions["t1_prediction"] = t1_match.group(1).strip()

    t2_match = re.search(
        r"- 隔夜T\+2预期\s*\n\s*- 预测[：:]\s*(.+?)(?:\n\s*-|\n\n|\Z)", pending_md
    )
    if t2_match:
        predictions["t2_prediction"] = t2_match.group(1).strip()

    noon_match = re.search(
        r"- 午间强度标签\s*\n\s*- 预测[：:]\s*(.+?)(?:\n\s*-|\n\n|\Z)", pending_md
    )
    if noon_match:
        predictions["noon_intraday_label"] = noon_match.group(1).strip()

    return predictions


def extract_target_info(pending_md: str) -> dict[str, Any]:
    info = {
        "symbol": None,
        "target_date": None,
        "t1_date": None,
        "t2_date": None,
    }

    symbol_match = re.search(r"#\s*(\d{6}\.[A-Z]{2})\s*待验证记录", pending_md)
    if symbol_match:
        info["symbol"] = symbol_match.group(1)

    date_match = re.search(r"目标交易日[：:]\s*(\d{4}-\d{2}-\d{2})", pending_md)
    if date_match:
        info["target_date"] = date_match.group(1)

    t1_match = re.search(r"T\+1\s*目标交易日[：:]\s*(\d{4}-\d{2}-\d{2})", pending_md)
    if t1_match:
        info["t1_date"] = t1_match.group(1)

    t2_match = re.search(r"T\+2\s*目标交易日[：:]\s*(\d{4}-\d{2}-\d{2})", pending_md)
    if t2_match:
        info["t2_date"] = t2_match.group(1)

    return info


def extract_close_from_report(pending_md: str) -> Optional[float]:
    close_matches = re.findall(r"收盘[：:]\s*(\d+\.?\d*)", pending_md)
    if close_matches:
        return float(close_matches[0])
    return None


def validate_symbol(symbol: str, target_date: str, pending_md: str) -> dict[str, Any]:
    predictions = extract_predictions(pending_md)
    report_close = extract_close_from_report(pending_md)

    result = {
        "symbol": symbol,
        "target_date": target_date,
        "t1_prediction": predictions["t1_prediction"],
        "t2_prediction": predictions["t2_prediction"],
        "noon_label": predictions["noon_intraday_label"],
        "report_close": report_close,
        "t1_result": None,
        "t2_result": None,
        "close_result": None,
        "hits": {},
    }

    t1_data = get_t_plus_n_close(symbol, target_date.replace("-", ""), 1)
    if t1_data:
        t1_date = t1_data["trade_date"]
        t1_close = t1_data["close"]
        t1_pct = t1_data.get("pct", 0)
        t1_actual = classify_by_pct(t1_pct)
        t1_direction = coarse_direction(t1_actual)

        t1_exact_hit = False
        t1_direction_hit = False

        pred = predictions["t1_prediction"] or ""
        pred_lower = pred.lower()
        actual_lower = t1_actual.lower()

        if "强" in pred and "强" in actual_lower:
            t1_exact_hit = True
        elif "偏强" in pred and "偏强" in actual_lower:
            t1_exact_hit = True
        elif "分歧" in pred and "分歧" in actual_lower:
            t1_exact_hit = True
        elif "偏弱" in pred and "偏弱" in actual_lower:
            t1_exact_hit = True
        elif "兑现" in pred and "兑现" in actual_lower:
            t1_exact_hit = True

        if ("偏多" in pred_lower or "强" in pred_lower) and t1_direction == "偏多":
            t1_direction_hit = True
        elif (
            "偏空" in pred_lower or "弱" in pred_lower or "兑现" in pred_lower
        ) and t1_direction == "偏空":
            t1_direction_hit = True
        elif "分歧" in pred_lower and t1_direction == "中性":
            t1_direction_hit = True
        elif "分歧" in pred_lower and abs(t1_pct) < 2:
            t1_direction_hit = True

        result["t1_result"] = {
            "date": t1_date,
            "close": t1_close,
            "pct": t1_pct,
            "actual_label": t1_actual,
            "direction": t1_direction,
            "exact_hit": t1_exact_hit,
            "direction_hit": t1_direction_hit,
        }
        result["hits"]["t1_exact"] = t1_exact_hit
        result["hits"]["t1_direction"] = t1_direction_hit

    t2_data = get_t_plus_n_close(symbol, target_date.replace("-", ""), 2)
    if t2_data:
        t2_date = t2_data["trade_date"]
        t2_close = t2_data["close"]
        t2_pct = t2_data.get("pct", 0)
        t2_actual = classify_by_pct(t2_pct)
        t2_direction = coarse_direction(t2_actual)

        t2_exact_hit = False
        t2_direction_hit = False

        pred = predictions["t2_prediction"] or ""
        pred_lower = pred.lower()

        if "强" in pred and "强" in t2_actual.lower():
            t2_exact_hit = True
        elif "分歧" in pred and "分歧" in t2_actual.lower():
            t2_exact_hit = True
        elif "偏弱" in pred and "偏弱" in t2_actual.lower():
            t2_exact_hit = True
        elif "兑现" in pred and "兑现" in t2_actual.lower():
            t2_exact_hit = True

        if ("偏多" in pred_lower or "强" in pred_lower) and t2_direction == "偏多":
            t2_direction_hit = True
        elif (
            "偏空" in pred_lower or "弱" in pred_lower or "兑现" in pred_lower
        ) and t2_direction == "偏空":
            t2_direction_hit = True
        elif "分歧" in pred_lower and t2_direction == "中性":
            t2_direction_hit = True
        elif "分歧" in pred_lower and abs(t2_pct) < 2:
            t2_direction_hit = True

        result["t2_result"] = {
            "date": t2_date,
            "close": t2_close,
            "pct": t2_pct,
            "actual_label": t2_actual,
            "direction": t2_direction,
            "exact_hit": t2_exact_hit,
            "direction_hit": t2_direction_hit,
        }
        result["hits"]["t2_exact"] = t2_exact_hit
        result["hits"]["t2_direction"] = t2_direction_hit

    target_close_data = get_daily_row(symbol, target_date.replace("-", ""))
    if target_close_data and report_close:
        close_diff = abs(target_close_data["close"] - report_close) / report_close * 100
        result["close_result"] = {
            "date": target_date,
            "actual_close": target_close_data["close"],
            "reported_close": report_close,
            "diff_pct": close_diff,
            "accurate": close_diff < 1.0,
        }

    return result


def scan_pending_validations(target_date: Optional[str] = None) -> list:
    results = []
    patterns = ("待验证-*.md", "pending-validation-*.md")

    if target_date:
        td = f"{target_date[:4]}/{target_date[4:6]}/{target_date[6:]}"
        date_dir = PENDING_DIR / td
        if date_dir.exists():
            md_files = []
            for pattern in patterns:
                md_files.extend(date_dir.glob(pattern))
            for md_file in sorted(set(md_files)):
                results.append(
                    {
                        "file": str(md_file.relative_to(SKILL_DIR)),
                        "symbol": _extract_pending_symbol(md_file.name),
                        "content": md_file.read_text(encoding="utf-8"),
                    }
                )
    else:
        for date_dir in sorted(PENDING_DIR.iterdir()):
            if not date_dir.is_dir():
                continue
            md_files = []
            for pattern in patterns:
                md_files.extend(date_dir.glob(pattern))
            for md_file in sorted(set(md_files)):
                results.append(
                    {
                        "file": str(md_file.relative_to(SKILL_DIR)),
                        "symbol": _extract_pending_symbol(md_file.name),
                        "content": md_file.read_text(encoding="utf-8"),
                    }
                )

    return results


def _extract_pending_symbol(filename: str) -> Optional[str]:
    for pattern in (
        r"待验证-([0-9]{6}\.(?:SH|SZ|BJ))(?:-[^-]+)?(?:-(上午盘中|午间休盘|下午盘中|收盘))?\.md",
        r"pending-validation-(.+)\.md",
    ):
        m = re.search(pattern, filename)
        if m:
            return m.group(1)
    return None


def run_validation(
    target_date: Optional[str] = None, symbol_filter: Optional[str] = None
) -> dict:
    pending_reports = scan_pending_validations(target_date)

    validations = []
    t1_exact_hits = 0
    t1_direction_hits = 0
    t2_exact_hits = 0
    t2_direction_hits = 0
    t1_total = 0
    t2_total = 0

    for item in pending_reports:
        if symbol_filter and item["symbol"] != symbol_filter:
            continue

        info = extract_target_info(item["content"])
        symbol = info["symbol"] or item["symbol"]
        if not symbol:
            continue

        target = info["target_date"] or target_date
        if not target:
            continue

        validation = validate_symbol(symbol, target, item["content"])
        validation["file"] = item["file"]
        validations.append(validation)

        if validation["t1_result"]:
            t1_total += 1
            if validation["hits"].get("t1_exact"):
                t1_exact_hits += 1
            if validation["hits"].get("t1_direction"):
                t1_direction_hits += 1

        if validation["t2_result"]:
            t2_total += 1
            if validation["hits"].get("t2_exact"):
                t2_exact_hits += 1
            if validation["hits"].get("t2_direction"):
                t2_direction_hits += 1

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "target_date": target_date,
        "symbol_filter": symbol_filter,
        "total_reports": len(validations),
        "t1_stats": {
            "total": t1_total,
            "exact_hits": t1_exact_hits,
            "exact_hit_rate": round(t1_exact_hits / t1_total * 100, 1)
            if t1_total > 0
            else 0.0,
            "direction_hits": t1_direction_hits,
            "direction_hit_rate": round(t1_direction_hits / t1_total * 100, 1)
            if t1_total > 0
            else 0.0,
        },
        "t2_stats": {
            "total": t2_total,
            "exact_hits": t2_exact_hits,
            "exact_hit_rate": round(t2_exact_hits / t2_total * 100, 1)
            if t2_total > 0
            else 0.0,
            "direction_hits": t2_direction_hits,
            "direction_hit_rate": round(t2_direction_hits / t2_total * 100, 1)
            if t2_total > 0
            else 0.0,
        },
        "validations": validations,
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"# 待验证报告验证报告",
        f"",
        f"- 生成时间：{report['generated_at']}",
        f"- 目标日期：{report['target_date'] or '全部'}",
        f"- 验证报告数：{report['total_reports']}",
        f"",
        f"## 命中率统计",
        f"",
        f"### T+1 次日预期",
        f"- 样本数：`{report['t1_stats']['total']}`",
        f"- 精确命中：`{report['t1_stats']['exact_hits']} / {report['t1_stats']['total']} = {report['t1_stats']['exact_hit_rate']}%`",
        f"- 方向命中：`{report['t1_stats']['direction_hits']} / {report['t1_stats']['total']} = {report['t1_stats']['direction_hit_rate']}%`",
        f"",
        f"### T+2 预期",
        f"- 样本数：`{report['t2_stats']['total']}`",
        f"- 精确命中：`{report['t2_stats']['exact_hits']} / {report['t2_stats']['total']} = {report['t2_stats']['exact_hit_rate']}%`",
        f"- 方向命中：`{report['t2_stats']['direction_hits']} / {report['t2_stats']['total']} = {report['t2_stats']['direction_hit_rate']}%`",
        f"",
        f"## 逐项验证",
    ]

    for v in report["validations"]:
        lines.append(f"")
        lines.append(f"### {v['symbol']} ({v['target_date']})")
        lines.append(f"- 文件：`{v['file']}`")

        if v["t1_prediction"]:
            lines.append(f"- T+1 预测：`{v['t1_prediction']}`")
        if v["t1_result"]:
            t1 = v["t1_result"]
            hit_mark = "✅" if t1["exact_hit"] else "❌"
            dir_mark = "✅" if t1["direction_hit"] else "❌"
            lines.append(
                f"  - T+1 实际：`{t1['actual_label']}` ({t1['pct']:+.2f}%) @ {t1['date']}"
            )
            lines.append(f"  - 精确命中：{hit_mark}")
            lines.append(f"  - 方向命中：{dir_mark}")

        if v["t2_prediction"]:
            lines.append(f"- T+2 预测：`{v['t2_prediction']}`")
        if v["t2_result"]:
            t2 = v["t2_result"]
            hit_mark = "✅" if t2["exact_hit"] else "❌"
            dir_mark = "✅" if t2["direction_hit"] else "❌"
            lines.append(
                f"  - T+2 实际：`{t2['actual_label']}` ({t2['pct']:+.2f}%) @ {t2['date']}"
            )
            lines.append(f"  - 精确命中：{hit_mark}")
            lines.append(f"  - 方向命中：{dir_mark}")

    return "\n".join(lines)


def save_validation_report(report: dict[str, Any]) -> Path:
    VALIDATIONS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    output_file = VALIDATIONS_DIR / f"validation-report-{today}.md"

    content = render_text(report)
    content += "\n\n---\n\n"
    content += f"_此报告由 validate_pending_reports.py 自动生成_"

    output_file.write_text(content, encoding="utf-8")
    return output_file


def archive_validated_pending(report: dict[str, Any]) -> list[Path]:
    archived: list[Path] = []
    target_date = report.get("target_date")
    if not target_date:
        return archived
    td = f"{target_date[:4]}/{target_date[4:6]}/{target_date[6:]}"
    pending_date_dir = PENDING_DIR / td
    validated_date_dir = VALIDATIONS_DIR / td
    validated_date_dir.mkdir(parents=True, exist_ok=True)
    for item in report.get("validations") or []:
        symbol = item.get("symbol")
        if not symbol:
            continue
        # 归档 md 文件
        pending_md_candidates = sorted(pending_date_dir.glob(f"待验证-{symbol}*.md"))
        for pending_md in pending_md_candidates:
            suffix = pending_md.name.removeprefix(f"待验证-{symbol}")
            validated_md = validated_date_dir / f"已验证-{symbol}{suffix}"
            shutil.move(str(pending_md), str(validated_md))
            archived.append(validated_md)
        # 归档 parquet 文件
        pending_parquet_candidates = sorted(pending_date_dir.glob(f"待验证-{symbol}*.parquet"))
        for pending_parquet in pending_parquet_candidates:
            suffix = pending_parquet.name.removeprefix(f"待验证-{symbol}")
            validated_parquet = validated_date_dir / f"已验证-{symbol}{suffix}"
            shutil.move(str(pending_parquet), str(validated_parquet))
            archived.append(validated_parquet)
    return archived


def main() -> None:
    args = parse_args()

    today = datetime.now().strftime("%Y-%m-%d")

    if not is_trading_day(today):
        print(
            f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 今日非交易日，跳过验证"
        )
        return

    target_date = args.date
    if not target_date:
        target_date = get_previous_trading_day(today)
        if not target_date:
            print("无法确定最近交易日")
            return

    report = run_validation(target_date, args.symbol)

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))

    output_file = save_validation_report(report)
    archived_files = archive_validated_pending(report)
    print(f"\n报告已保存至：{output_file}")
    if archived_files:
        print("已归档待验证记录：")
        for path in archived_files:
            print(f"- {path}")


if __name__ == "__main__":
    main()
