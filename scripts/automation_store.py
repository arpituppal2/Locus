"""Local automation definitions for Locus.

This module stores schedules only. It does not install launch agents or run
background jobs by itself; the scheduler/runner can consume this file later.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STATE_DIR = Path.home() / ".local-computer"
AUTOMATIONS_PATH = STATE_DIR / "automations.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _interval(schedule: str) -> timedelta | None:
    match = re.search(r"\bevery\s+(\d+)\s*(minute|minutes|hour|hours|day|days)\b", schedule, re.IGNORECASE)
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2).lower()
    if unit.startswith("minute"):
        return timedelta(minutes=amount)
    if unit.startswith("hour"):
        return timedelta(hours=amount)
    return timedelta(days=amount)


def _load() -> list[dict[str, Any]]:
    try:
        data = json.loads(AUTOMATIONS_PATH.read_text())
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _write(items: list[dict[str, Any]]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    AUTOMATIONS_PATH.write_text(json.dumps(items, indent=2) + "\n")


def _clean_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    return cleaned[:80] if cleaned else f"auto-{uuid.uuid4().hex[:10]}"


def list_automations(limit: int = 100, include_disabled: bool = True) -> list[dict[str, Any]]:
    items = _load()
    if not include_disabled:
        items = [item for item in items if item.get("enabled", True)]
    items.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    return items[: max(1, min(int(limit), 500))]


def create_automation(
    *,
    name: str,
    prompt: str,
    schedule: str,
    workspace: str | None = None,
    enabled: bool = True,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not str(name or "").strip():
        raise ValueError("automation name is required")
    if not str(prompt or "").strip():
        raise ValueError("automation prompt is required")
    if not str(schedule or "").strip():
        raise ValueError("automation schedule is required")

    now = _utc_now()
    item = {
        "id": _clean_id(str(name)),
        "name": str(name).strip(),
        "prompt": str(prompt).strip(),
        "schedule": str(schedule).strip(),
        "workspace": str(workspace or "").strip(),
        "enabled": bool(enabled),
        "last_run_at": "",
        "last_run_ok": None,
        "last_run_summary": "",
        "created_at": now,
        "updated_at": now,
        "metadata": metadata or {},
    }
    existing = _load()
    ids = {str(existing_item.get("id")) for existing_item in existing}
    base_id = item["id"]
    suffix = 2
    while item["id"] in ids:
        item["id"] = f"{base_id}-{suffix}"
        suffix += 1
    existing.append(item)
    _write(existing)
    return item


def update_automation(automation_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    automation_id = str(automation_id or "").strip()
    if not automation_id:
        raise ValueError("automation id is required")
    items = _load()
    for item in items:
        if str(item.get("id")) != automation_id:
            continue
        for key in ("name", "prompt", "schedule", "workspace"):
            if key in updates:
                item[key] = str(updates.get(key) or "").strip()
        if "enabled" in updates:
            item["enabled"] = bool(updates.get("enabled"))
        if "metadata" in updates and isinstance(updates.get("metadata"), dict):
            item["metadata"] = updates["metadata"]
        item["updated_at"] = _utc_now()
        _write(items)
        return item
    raise ValueError(f"automation not found: {automation_id}")


def delete_automation(automation_id: str) -> dict[str, Any]:
    automation_id = str(automation_id or "").strip()
    if not automation_id:
        raise ValueError("automation id is required")
    items = _load()
    remaining = [item for item in items if str(item.get("id")) != automation_id]
    if len(remaining) == len(items):
        raise ValueError(f"automation not found: {automation_id}")
    _write(remaining)
    return {"id": automation_id, "deleted": True}


def is_due(item: dict[str, Any], now: datetime | None = None) -> bool:
    if not item.get("enabled", True):
        return False
    now = now or datetime.now(timezone.utc)
    schedule = str(item.get("schedule") or "").strip().lower()
    last_run = _parse_time(str(item.get("last_run_at") or ""))
    if schedule in {"now", "once", "asap"}:
        return last_run is None
    interval = _interval(schedule)
    if interval is not None:
        return last_run is None or now - last_run >= interval
    due_at = _parse_time(schedule)
    if due_at is not None:
        return now >= due_at and last_run is None
    return False


def due_automations(limit: int = 50) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    return [item for item in list_automations(limit=500) if is_due(item, now=now)][: max(1, min(int(limit), 200))]


def record_automation_run(automation_id: str, *, ok: bool, summary: str = "") -> dict[str, Any]:
    automation_id = str(automation_id or "").strip()
    items = _load()
    for item in items:
        if str(item.get("id")) != automation_id:
            continue
        item["last_run_at"] = _utc_now()
        item["last_run_ok"] = bool(ok)
        item["last_run_summary"] = str(summary or "")[:500]
        item["updated_at"] = _utc_now()
        _write(items)
        return item
    raise ValueError(f"automation not found: {automation_id}")
