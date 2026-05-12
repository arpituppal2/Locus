"""Persistent run history for Locus."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.runtime_policy import workspace_root

STATE_DIR = Path.home() / ".local-computer"
RUNS_DB = STATE_DIR / "runs.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(RUNS_DB)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_root TEXT NOT NULL,
            query TEXT NOT NULL,
            mode TEXT NOT NULL,
            answer TEXT,
            plan_json TEXT,
            result_json TEXT,
            elapsed_ms INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_workspace_created ON runs(workspace_root, created_at DESC)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tool_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_root TEXT NOT NULL,
            plugin TEXT NOT NULL,
            tool TEXT NOT NULL,
            state TEXT NOT NULL,
            risk TEXT,
            args_json TEXT,
            result_json TEXT,
            ok INTEGER,
            reason TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_events_workspace_created ON tool_events(workspace_root, created_at DESC)")
    return conn


def _json_preview(value: Any, limit: int = 16_000) -> str:
    text = json.dumps(value if value is not None else {}, ensure_ascii=False)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def _text_preview(value: Any, limit: int = 2_000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _result_preview(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    preview: dict[str, Any] = {}
    for key in ("ok", "plugin", "tool", "returncode", "error", "path", "relative_path", "draft_path", "bytes", "appended"):
        if key in result:
            preview[key] = result[key]
    for key in ("stdout", "stderr"):
        if key in result:
            preview[key] = _text_preview(result.get(key))
    for key in ("events", "runs", "files", "hits", "todos", "uploads"):
        if isinstance(result.get(key), list):
            preview[f"{key}_count"] = len(result[key])
    if isinstance(result.get("index"), dict):
        index = result["index"]
        preview["index"] = {
            "root": index.get("root"),
            "file_count": index.get("file_count"),
            "workspace_id": index.get("workspace_id"),
        }
    return preview


def store_run(query: str, mode: str, result: dict[str, Any]) -> int:
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO runs(workspace_root, query, mode, answer, plan_json, result_json, elapsed_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(workspace_root()),
                query,
                mode,
                str(result.get("answer", "")),
                json.dumps(result.get("plan", [])),
                json.dumps(result),
                int(result.get("elapsed_ms", 0) or 0),
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid)


def store_tool_event(
    plugin: str,
    tool: str,
    state: str,
    *,
    args: dict[str, Any] | None = None,
    risk: str | None = None,
    ok: bool | None = None,
    reason: str | None = None,
    result: dict[str, Any] | None = None,
) -> int:
    ok_value = None if ok is None else 1 if ok else 0
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO tool_events(workspace_root, plugin, tool, state, risk, args_json, result_json, ok, reason, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(workspace_root()),
                plugin,
                tool,
                state,
                risk or "",
                _json_preview(args or {}),
                _json_preview(_result_preview(result)),
                ok_value,
                reason or "",
                _utc_now(),
            ),
        )
        return int(cursor.lastrowid)


def list_tool_events(limit: int = 30) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, plugin, tool, state, risk, args_json, result_json, ok, reason, created_at
            FROM tool_events
            WHERE workspace_root = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (str(workspace_root()), max(1, min(limit, 200))),
        ).fetchall()

    events = []
    for row in rows:
        try:
            args = json.loads(row["args_json"] or "{}")
        except json.JSONDecodeError:
            args = {}
        try:
            result = json.loads(row["result_json"] or "{}")
        except json.JSONDecodeError:
            result = {}
        ok = row["ok"]
        events.append(
            {
                "id": int(row["id"]),
                "plugin": row["plugin"],
                "tool": row["tool"],
                "state": row["state"],
                "risk": row["risk"] or "",
                "args": args,
                "result": result,
                "ok": None if ok is None else bool(ok),
                "reason": row["reason"] or "",
                "created_at": row["created_at"],
            }
        )
    return events


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, workspace_root, query, mode, answer, plan_json, elapsed_ms, created_at
            FROM runs
            WHERE workspace_root = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (str(workspace_root()), max(1, min(limit, 200))),
        ).fetchall()
    runs = []
    for row in rows:
        try:
            plan = json.loads(row["plan_json"] or "[]")
        except json.JSONDecodeError:
            plan = []
        runs.append(
            {
                "id": int(row["id"]),
                "workspace_root": row["workspace_root"],
                "query": row["query"],
                "mode": row["mode"],
                "answer_preview": (row["answer"] or "")[:600],
                "plan": plan,
                "elapsed_ms": int(row["elapsed_ms"] or 0),
                "created_at": row["created_at"],
            }
        )
    return runs


def get_run(run_id: int) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT result_json FROM runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    try:
        result = json.loads(row["result_json"])
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect Locus run history")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--tools", action="store_true", help="Show tool audit events instead of user runs")
    args = parser.parse_args()
    if args.tools:
        events = list_tool_events(limit=args.limit)
        if args.json:
            print(json.dumps({"events": events}, indent=2))
            return
        for event in events:
            print(
                f"{event['id']:>4} {event['created_at']} {event['state']} "
                f"{event['plugin']}.{event['tool']} {event.get('risk') or 'read'}"
            )
        return
    runs = list_runs(limit=args.limit)
    if args.json:
        print(json.dumps({"runs": runs}, indent=2))
        return
    for run in runs:
        print(f"{run['id']:>4} {run['created_at']} {run['mode']} {run['query'][:100]}")


if __name__ == "__main__":
    main()
