#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from data.config_loader import cfg

_SKILL_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _SKILL_ROOT / "references" / "data" / "schema.sql"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_db_path() -> Path:
    db_path = cfg.paths("sqlite_db")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(schema_path: Path | None = None) -> None:
    target = schema_path or _SCHEMA_PATH
    if not target.exists():
        raise FileNotFoundError(f"schema.sql not found: {target}")
    with get_conn() as conn:
        conn.executescript(target.read_text(encoding="utf-8"))


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, str):
            v = value.strip()
            normalized[key] = None if v == "" else v
        else:
            normalized[key] = value
    return normalized


def upsert_rows(
    table: str,
    rows: Iterable[dict[str, Any]],
    conflict_keys: list[str],
    conn: sqlite3.Connection | None = None,
) -> int:
    row_list = [_normalize_row(r) for r in rows if r]
    if not row_list:
        return 0

    columns = list(row_list[0].keys())
    placeholders = ", ".join(["?"] * len(columns))
    col_sql = ", ".join([f'"{c}"' for c in columns])
    conflict_sql = ", ".join([f'"{c}"' for c in conflict_keys])
    update_cols = [c for c in columns if c not in conflict_keys]
    if not update_cols:
        update_sql = ""
    else:
        assigns = ", ".join([f'"{c}"=excluded."{c}"' for c in update_cols])
        update_sql = f" DO UPDATE SET {assigns}"

    sql = (
        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_sql})"
    )
    if update_sql:
        sql += update_sql
    else:
        sql += " DO NOTHING"

    values = [tuple(r.get(c) for c in columns) for r in row_list]

    owns_conn = conn is None
    work_conn = conn or get_conn()
    try:
        with work_conn:
            work_conn.executemany(sql, values)
    finally:
        if owns_conn:
            work_conn.close()
    return len(row_list)


def fetch_daily_row(ts_code: str, trade_date: str) -> dict[str, Any] | None:
    sql = """
    SELECT *
    FROM daily_ohlcv
    WHERE ts_code = ? AND trade_date = ?
    LIMIT 1
    """
    with get_conn() as conn:
        row = conn.execute(sql, (ts_code, trade_date)).fetchone()
    return dict(row) if row else None


def fetch_daily_basic_row(ts_code: str, trade_date: str) -> dict[str, Any] | None:
    sql = """
    SELECT *
    FROM daily_basic
    WHERE ts_code = ? AND trade_date <= ?
    ORDER BY trade_date DESC
    LIMIT 1
    """
    with get_conn() as conn:
        row = conn.execute(sql, (ts_code, trade_date)).fetchone()
    return dict(row) if row else None


def insert_analysis_history(payload: dict[str, Any]) -> int:
    row = {
        "symbol": payload.get("symbol") or payload.get("ts_code") or "",
        "trade_date": str(payload.get("trade_date") or ""),
        "checkpoint": payload.get("checkpoint") or "close",
        "final_decision_summary": payload.get("final_decision_summary"),
        "payload_json": json.dumps(payload.get("payload") or payload, ensure_ascii=False),
        "status": payload.get("status") or "ok",
        "created_at": payload.get("created_at") or _utc_now_iso(),
    }
    if not row["symbol"] or not row["trade_date"]:
        raise ValueError("analysis_history requires symbol and trade_date")

    sql = """
    INSERT INTO analysis_history
      (symbol, trade_date, checkpoint, final_decision_summary, payload_json, status, created_at)
    VALUES
      (:symbol, :trade_date, :checkpoint, :final_decision_summary, :payload_json, :status, :created_at)
    """
    with get_conn() as conn:
        cur = conn.execute(sql, row)
        conn.commit()
        return int(cur.lastrowid)


def insert_validation_result(payload: dict[str, Any]) -> int:
    row = {
        "analysis_history_id": payload.get("analysis_history_id"),
        "symbol": payload.get("symbol") or payload.get("ts_code") or "",
        "trade_date": str(payload.get("trade_date") or ""),
        "checkpoint": payload.get("checkpoint"),
        "verdict": payload.get("verdict"),
        "score": payload.get("score"),
        "details_json": json.dumps(payload.get("details") or payload.get("details_json") or {}, ensure_ascii=False),
        "created_at": payload.get("created_at") or _utc_now_iso(),
    }
    if not row["symbol"] or not row["trade_date"]:
        raise ValueError("validation_results requires symbol and trade_date")

    sql = """
    INSERT INTO validation_results
      (analysis_history_id, symbol, trade_date, checkpoint, verdict, score, details_json, created_at)
    VALUES
      (:analysis_history_id, :symbol, :trade_date, :checkpoint, :verdict, :score, :details_json, :created_at)
    """
    with get_conn() as conn:
        cur = conn.execute(sql, row)
        conn.commit()
        return int(cur.lastrowid)
