#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from data.config_loader import cfg

DEFAULT_HERMES_EXECUTOR_DIR = Path(cfg.get("paths", "external", "hermes_executor") or str(Path.home() / "agent-skills" / "custom" / "hermes-executor"))
DEFAULT_HERMES_DOCKER_PATH = Path(cfg.get("paths", "external", "hermes_docker") or str(Path.home() / "hermes-docker"))
DEFAULT_STOCK_SKILL_SCRIPTS = Path(cfg.get("paths", "external", "stock_skill_scripts") or str(Path.home() / "agent-skills" / "custom" / "stock-deep-analysis" / "scripts"))
DEFAULT_RUNTIME_ARTIFACT_DIR = cfg.paths("temp_dir") / "hermes-browser-fetch"


def _load_hermes_client(executor_dir: Path):
    if not executor_dir.exists():
        raise FileNotFoundError(f"hermes-executor not found: {executor_dir}")
    sys.path.insert(0, str(executor_dir))
    from hermes_client import HermesClient  # type: ignore

    return HermesClient


def _news_task(args: argparse.Namespace) -> str:
    date = args.trade_date
    presets = ", ".join(args.preset) if args.preset else "eastmoney, cls"
    stock_name_hint = f"（股票名：{args.stock_name}）" if args.stock_name else ""
    return (
        f"你是执行层智能体。请用浏览器完成 A 股新闻抓取任务："
        f"标的 {args.symbol}，交易日 {date}{stock_name_hint}。"
        f"优先渠道：{presets}。"
        f"输出要求：仅返回 JSON，包含 articles/news_sentiment/narrative_context。"
        f"articles 至少 6 条，字段必须含 title/source/published_at/url/summary。"
        f"不要流式输出，不要额外解释。"
    )


def _minute_task(args: argparse.Namespace) -> str:
    minute_script = DEFAULT_STOCK_SKILL_SCRIPTS / "fetchers" / "fetch_minute_data.py"
    # 新结构：分钟数据/YYYY/MM/DD/{symbol}/1m.csv
    trade_date = f"{args.trade_date[:4]}-{args.trade_date[4:6]}-{args.trade_date[6:8]}"
    y, m, d = trade_date.split("-")
    output_path = (
        cfg.paths("stock_data_root") / "分钟数据"
        / y / m / d
        / args.symbol.upper()
        / "1m.csv"
    )
    return (
        f"你是执行层智能体。这个任务只需要终端，不需要浏览器。目标是拿到 {args.symbol} 在 {args.trade_date} 的完整分钟线。"
        f"不要在第一步失败后直接返回失败，必须按下面的固定顺序继续尝试。"
        f"统一分钟入口脚本是 `{minute_script}`。"
        f"不要直接调用 `fetch_eastmoney_minute_kline.mjs`，不要自行拼装东财 minute 接口。"
        f"固定尝试顺序："
        f"步骤1：运行 `python3 {minute_script} --symbol {args.symbol} --trade-date {args.trade_date} --timeout {args.timeout}`。"
        f"步骤2：如果返回失败或分钟文件不完整，立刻再运行一次，但加大重试："
        f"`python3 {minute_script} --symbol {args.symbol} --trade-date {args.trade_date} --timeout {args.timeout} --max-rounds 2 --round-sleep 1`。"
        f"步骤3：如果仍失败或分钟文件仍不完整，直接在终端请求腾讯分钟接口 "
        f"`https://web.ifzq.gtimg.cn/appstock/app/minute/query`，把结果写成 `{output_path}` 的标准 CSV 格式。"
        f"步骤4：检查该 CSV 是否至少覆盖 `09:30` 到 `14:59/15:00`；只有完整才算成功。"
        f"只有在上述方法都尝试后仍拿不到完整分钟线，才允许返回失败。"
        f"并且仅返回 JSON。优先返回完整分钟结构："
        f"{{symbol, trade_date, bars, day_stats, source}}。"
        f"bars 至少含 30 个时间点，每个点含 time/open/high/low/close/volume/amount。"
        f"day_stats 含 open/high/low/last/pct_change/turnover/amount。"
        f"如果最终仍失败，返回 JSON 时必须包含 `attempted_methods`，明确写出每一步尝试了什么、失败原因是什么。"
        f"不要流式输出，不要额外解释。"
    )


def _build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    task_kind = args.task_kind
    if task_kind == "news":
        task = _news_task(args)
        mode = "browser"
        skills = args.skills or ["market-news-intelligence"]
    elif task_kind == "minute":
        task = _minute_task(args)
        mode = "chat"
        skills = args.skills or ["stock-deep-analysis"]
    else:
        raise ValueError(f"unsupported task kind: {task_kind}")

    message_id = args.message_id or f"{args.task_kind}-{uuid.uuid4().hex[:12]}"
    request_id = args.request_id or f"req-{uuid.uuid4().hex[:12]}"

    payload: Dict[str, Any] = {
        "task": task,
        "mode": mode,
        "agent": args.agent,
        "sessionId": args.session_id,
        "messageId": message_id,
        "requestId": request_id,
        "stream": False,
        "save_to_memory": args.save_to_memory,
        "timeout": args.timeout,
    }
    if skills:
        payload["skills"] = skills
    return payload


def _normalize_symbol_for_minute_script(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if "." in symbol:
        return symbol.split(".", 1)[0]
    return symbol


def _detect_parent_command() -> str:
    try:
        out = subprocess.run(
            ["ps", "-o", "command=", "-p", str(os.getppid())],
            capture_output=True,
            text=True,
            check=False,
        )
        return (out.stdout or "").strip()
    except Exception:
        return ""


def _detect_executor(args: argparse.Namespace) -> str:
    if args.executor in {"local", "hermes"}:
        return args.executor

    env_override = os.getenv("STOCK_FETCH_EXECUTOR", "").strip().lower()
    if env_override in {"local", "hermes"}:
        return env_override

    # Inside Hermes execution context: avoid recursive Hermes->Hermes calls.
    if os.getenv("OPENCLAW_AGENT_ID") or os.getenv("OPENCLAW_THREAD_ID"):
        return "local"

    parent = _detect_parent_command().lower()
    if "openclaw" in parent:
        return "hermes"
    return "local"


def _local_news_cmd(args: argparse.Namespace) -> List[str]:
    cmd = [
        "python3",
        str(DEFAULT_STOCK_SKILL_SCRIPTS / "run_news_pipeline.py"),
        "--symbol",
        args.symbol,
        "--trade-date",
        args.trade_date,
    ]
    if args.stock_name:
        cmd.extend(["--stock-name", args.stock_name])
    for p in args.preset:
        cmd.extend(["--preset", p])
    return cmd


def _local_minute_cmd(args: argparse.Namespace) -> List[str]:
    return [
        "python3",
        str(DEFAULT_STOCK_SKILL_SCRIPTS / "fetch_minute_data.py"),
        "--symbol",
        args.symbol,
        "--trade-date",
        args.trade_date,
        "--timeout",
        str(args.timeout),
    ]


def _minute_output_path(symbol: str, trade_date: str) -> Path:
    compact = str(trade_date).replace("-", "")
    normalized = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    y, m, d = normalized.split("-")
    full_symbol = symbol.strip().upper()
    return cfg.paths("stock_data_root") / "分钟数据" / y / m / d / full_symbol / "1m.csv"


def _minute_file_complete(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return False
    times = [str(r.get("datetime") or "")[-5:] for r in rows]
    first_dt = str(rows[0].get("datetime") or "")
    last_dt = str(rows[-1].get("datetime") or "")
    required = {
        "open_window": any("09:30" <= t <= "09:35" for t in times),
        "first_push_window": any("09:48" <= t <= "09:56" for t in times),
        "pre_noon_window": any("11:25" <= t <= "11:30" for t in times),
        "pm_open_window": any("13:01" <= t <= "13:30" for t in times),
        "pm_tail_window": any("14:30" <= t <= "15:00" for t in times),
    }
    return (
        len(rows) >= 200
        and first_dt.endswith(("09:30", "09:31", "09:32", "09:33", "09:34", "09:35"))
        and last_dt[-5:] >= "14:59"
        and all(required.values())
    )


def _should_force_realtime_refresh(trade_date: str) -> bool:
    compact = str(trade_date).replace("-", "")
    normalized = f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if now.strftime("%Y-%m-%d") != normalized:
        return False
    return now.time() < datetime.strptime("15:00", "%H:%M").time()


def _run_local(args: argparse.Namespace) -> Dict[str, Any]:
    if args.task_kind == "news":
        cmd = _local_news_cmd(args)
    elif args.task_kind == "minute":
        cmd = _local_minute_cmd(args)
    else:
        raise ValueError(f"unsupported task kind: {args.task_kind}")

    if args.dry_run:
        return {
            "success": True,
            "dry_run": True,
            "executor": "local",
            "cmd": cmd,
        }

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "success": False,
            "executor": "local",
            "cmd": cmd,
            "returncode": 124,
            "stdout": exc.stdout or "",
            "stderr": (exc.stderr or "").strip() or f"local_timeout_after_{args.timeout}s",
            "timeout": True,
        }
    except Exception as exc:
        return {
            "success": False,
            "executor": "local",
            "cmd": cmd,
            "returncode": 1,
            "stdout": "",
            "stderr": f"{exc.__class__.__name__}: {exc}",
        }
    return {
        "success": result.returncode == 0,
        "executor": "local",
        "cmd": cmd,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _run_hermes(args: argparse.Namespace) -> Dict[str, Any]:
    payload = _build_payload(args)
    if args.dry_run:
        return {
            "success": True,
            "dry_run": True,
            "executor": "hermes",
            "payload": payload,
        }
    HermesClient = _load_hermes_client(Path(args.hermes_executor_dir))
    client = HermesClient(args.hermes_docker_path)
    hermes_timeout = min(args.timeout, 60) if args.task_kind == "minute" else args.timeout
    payload["timeout"] = hermes_timeout
    result = client.execute_from_payload(payload)
    result["executor"] = "hermes"
    if args.task_kind == "minute":
        minute_path = _minute_output_path(args.symbol, args.trade_date)
        force_refresh = _should_force_realtime_refresh(args.trade_date)
        if _minute_file_complete(minute_path) and not force_refresh:
            return {
                "success": True,
                "executor": "hermes",
                "fallback_used": "existing_complete_minute_file",
                "minute_path": str(minute_path),
                "minute_complete": True,
                "hermes_attempt": result,
            }

        local_result = _run_local(args)
        local_minute_ok = _minute_file_complete(minute_path)
        if local_result.get("success") and local_minute_ok:
            return {
                "success": True,
                "executor": "hermes",
                "fallback_used": "local_minute_fetch",
                "minute_path": str(minute_path),
                "minute_complete": True,
                "hermes_attempt": result,
                "local_attempt": local_result,
            }
        return {
            "success": False,
            "executor": "hermes",
            "minute_path": str(minute_path),
            "minute_complete": False,
            "hermes_attempt": result,
            "local_attempt": local_result,
        }
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run stock browser-fetch tasks with auto executor routing."
    )
    parser.add_argument(
        "--task-kind",
        choices=["news", "minute"],
        required=True,
        help="Task template type.",
    )
    parser.add_argument(
        "--executor",
        choices=["auto", "local", "hermes"],
        default="auto",
        help="Execution backend. auto: detect by caller/env.",
    )
    parser.add_argument("--symbol", required=True, help="A-share symbol, e.g. 600110.SH")
    parser.add_argument("--trade-date", required=True, help="Trade date, e.g. 20260410")
    parser.add_argument("--stock-name", default="", help="Optional stock display name.")
    parser.add_argument(
        "--preset",
        action="append",
        default=[],
        help="News preset hints (repeatable), e.g. --preset eastmoney --preset cls",
    )
    parser.add_argument("--agent", default="stock-agent", help="Hermes agent id.")
    parser.add_argument("--session-id", default="stock-agent:default", help="Hermes thread/session id.")
    parser.add_argument("--message-id", default="", help="Idempotency message id.")
    parser.add_argument("--request-id", default="", help="Correlation request id.")
    parser.add_argument("--skills", nargs="*", default=[], help="Optional Hermes skills list.")
    parser.add_argument("--save-to-memory", action="store_true", help="Persist result in Hermes memory.")
    parser.add_argument("--timeout", type=int, default=cfg.network("browser", "timeout_ms", default=300000) // 1000, help="Hermes timeout in seconds.")
    parser.add_argument("--klt", type=int, default=cfg.fetcher("minute_klt", default=5), help="Historical minute K line type for local minute mode.")
    parser.add_argument("--dry-run", action="store_true", help="Print resolved payload only.")
    parser.add_argument(
        "--hermes-executor-dir",
        default=str(DEFAULT_HERMES_EXECUTOR_DIR),
        help="Path to hermes-executor skill dir.",
    )
    parser.add_argument(
        "--hermes-docker-path",
        default=str(DEFAULT_HERMES_DOCKER_PATH),
        help="Path to hermes-docker workspace.",
    )
    parser.add_argument(
        "--artifact-dir",
        default=str(DEFAULT_RUNTIME_ARTIFACT_DIR),
        help="Where to persist structured Hermes/local task results for debugging.",
    )
    return parser.parse_args()


def _artifact_path(args: argparse.Namespace) -> Path:
    trade = str(args.trade_date).replace("-", "")
    symbol = str(args.symbol).replace(".", "_")
    return Path(args.artifact_dir).expanduser() / args.task_kind / f"{symbol}_{trade}.json"


def _persist_artifact(args: argparse.Namespace, result: Dict[str, Any]) -> None:
    path = _artifact_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "captured_at": datetime.now().isoformat(timespec="seconds"),
        "task_kind": args.task_kind,
        "symbol": args.symbol,
        "trade_date": args.trade_date,
        "executor": result.get("executor"),
        "success": bool(result.get("success")),
        "result": result,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    executor = _detect_executor(args)
    try:
        if executor == "hermes":
            result = _run_hermes(args)
        else:
            result = _run_local(args)
    except subprocess.TimeoutExpired as exc:
        result = {
            "success": False,
            "executor": executor,
            "returncode": 124,
            "stdout": "",
            "stderr": f"timeout_after_{getattr(exc, 'timeout', args.timeout)}s",
            "timeout": True,
        }
    except Exception as exc:
        result = {
            "success": False,
            "executor": executor,
            "returncode": 1,
            "stdout": "",
            "stderr": f"{exc.__class__.__name__}: {exc}",
        }
    _persist_artifact(args, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
