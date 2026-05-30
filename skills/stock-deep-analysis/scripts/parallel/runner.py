"""
并行 Agent 调度器。使用 ThreadPoolExecutor 并发执行多个 Agent，
支持每个 Agent 独立的超时控制和兜底默认值。

文件通信协议：结果同时写入 _tmp/{agent_name}_{date}.json 和返回 dict，
保证即使主进程崩溃也能从文件恢复。
"""

from __future__ import annotations

import json
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
from threading import Lock
from typing import Any, Callable, NamedTuple, Optional

# ── 临时目录 ─────────────────────────────────────────
TMP_DIR = Path(__file__).resolve().parent.parent.parent / "_tmp"


class ParallelAgent(NamedTuple):
    """一个并行 Agent 的配置"""
    name: str
    func: Callable[[], dict[str, Any]]
    timeout: float = 60.0
    default_result: Optional[dict[str, Any]] = None


_write_lock = Lock()


def _write_result(name: str, date_compact: str, result: dict[str, Any]) -> None:
    """文件通信：将结果写入 _tmp/{name}_{date}.json"""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    path = TMP_DIR / f"{name}_{date_compact}.json"
    with _write_lock:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)


def _read_result(name: str, date_compact: str) -> Optional[dict[str, Any]]:
    """从文件恢复结果（如果主进程崩溃后重启）"""
    path = TMP_DIR / f"{name}_{date_compact}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cleanup_results(date_compact: str) -> None:
    """清理单次分析的 _tmp 文件"""
    if not TMP_DIR.exists():
        return
    for f in TMP_DIR.iterdir():
        if f.is_file() and (date_compact in f.name or f.suffix == ".json"):
            try:
                f.unlink()
            except Exception:
                pass


def _run_single_agent(
    agent: ParallelAgent, date_compact: str
) -> tuple[str, dict[str, Any], bool]:
    """
    运行单个 Agent。返回 (name, result, had_error)。
    Agent 的 func 在本线程中执行，超时由调用者控制。
    """
    name = agent.name
    start = time.time()
    try:
        result = agent.func()
        elapsed = time.time() - start
        result["_elapsed_seconds"] = round(elapsed, 2)
        result["_status"] = "completed"
        _write_result(name, date_compact, result)
        return name, result, False
    except Exception as e:
        elapsed = time.time() - start
        err_result = agent.default_result.copy() if agent.default_result else {}
        err_result["_elapsed_seconds"] = round(elapsed, 2)
        err_result["_status"] = "error"
        err_result["_error"] = f"{type(e).__name__}: {e}"
        err_result["_traceback"] = traceback.format_exc()
        _write_result(name, date_compact, err_result)
        return name, err_result, True


def run_parallel(
    agents: list[ParallelAgent],
    date_compact: str,
    max_workers: int = 5,
    file_recovery: bool = True,
) -> dict[str, dict[str, Any]]:
    """
    并发执行所有 Agent。
    
    参数:
        agents: 需要并发的 Agent 列表
        date_compact: 交易日 YYYYMMDD，用于文件通信
        max_workers: 最大并发数（默认5，5个Agent同时跑）
        file_recovery: 是否先检查文件缓存（用于崩溃恢复）
    
    返回:
        {agent_name: result_dict, ...}
        每个 result_dict 包含 _status、_elapsed_seconds，出错时含 _error。
    """
    results: dict[str, dict[str, Any]] = {}

    # 先尝试从文件恢复
    if file_recovery:
        for agent in agents:
            cached = _read_result(agent.name, date_compact)
            if cached is not None:
                results[agent.name] = cached

    # 跳过已恢复的 Agent
    pending = [a for a in agents if a.name not in results]
    if not pending:
        return results

    # 并发执行
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {}
        for agent in pending:
            fut = executor.submit(_run_single_agent, agent, date_compact)
            future_map[fut] = agent

        # 逐一获取结果（带独立超时）
        for fut, agent in future_map.items():
            try:
                name, result, _ = fut.result(timeout=agent.timeout + 5.0)
                results[name] = result
            except FutureTimeout:
                name = agent.name
                elapsed = agent.timeout + 5.0
                err_result = agent.default_result.copy() if agent.default_result else {}
                err_result["_elapsed_seconds"] = round(elapsed, 2)
                err_result["_status"] = "timeout"
                err_result["_error"] = f"Timeout after {agent.timeout}s"
                results[name] = err_result
                _write_result(name, date_compact, err_result)

    return results


def clear_tmp(date_compact: Optional[str] = None) -> None:
    """清理 _tmp 目录。如果提供了 date_compact，只清理该日期的文件。"""
    if not TMP_DIR.exists():
        return
    for f in TMP_DIR.iterdir():
        if not f.is_file() or f.suffix != ".json":
            continue
        if date_compact and date_compact not in f.name:
            continue
        try:
            f.unlink()
        except Exception:
            pass
