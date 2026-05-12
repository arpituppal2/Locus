#!/usr/bin/env python3
"""Run due local Locus automations."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.automation_store import due_automations, record_automation_run
from scripts.workspace_agent import run_workspace_query


async def run_due(limit: int = 20) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in due_automations(limit=limit):
        try:
            result = await run_workspace_query(str(item.get("prompt") or ""))
            ok = True
            summary = str(result.get("answer") or "")[:500]
        except Exception as exc:
            result = {"ok": False, "error": str(exc), "type": exc.__class__.__name__}
            ok = False
            summary = str(exc)
        updated = record_automation_run(str(item.get("id")), ok=ok, summary=summary)
        results.append({"automation": updated, "result": result, "ok": ok})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run due Locus automations")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    results = asyncio.run(run_due(limit=args.limit))
    if args.json:
        print(json.dumps({"ok": True, "ran": len(results), "results": results}, indent=2))
        return
    if not results:
        print("No due automations.")
        return
    for item in results:
        automation = item["automation"]
        print(f"{'ok' if item['ok'] else 'failed'} {automation.get('id')} {automation.get('name')}")


if __name__ == "__main__":
    main()
