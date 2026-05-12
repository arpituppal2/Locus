"""Deterministic task planner for model-free workspace mode."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.plugin_runtime import parse_tool_directive


@dataclass
class ToolStep:
    plugin: str
    tool: str
    args: dict[str, Any]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _first_quoted(text: str) -> str | None:
    match = re.search(r"`([^`]+)`|\"([^\"]+)\"|'([^']+)'", text)
    if not match:
        return None
    return next(group for group in match.groups() if group)


def _path_after(text: str, verbs: str) -> str | None:
    quoted = _first_quoted(text)
    if quoted and ("/" in quoted or "." in quoted or quoted.startswith("~")):
        return quoted
    match = re.search(rf"\b(?:{verbs})\s+(?:file\s+)?([A-Za-z0-9_./~-]+)", text, re.IGNORECASE)
    return match.group(1) if match else None


def _search_term(text: str) -> str | None:
    quoted = _first_quoted(text)
    if quoted:
        return quoted
    match = re.search(r"\b(?:search|find|grep)(?:\s+(?:for|text|files))?\s+(.+)$", text, re.IGNORECASE)
    if match:
        term = match.group(1).strip()
        term = re.sub(r"\s+(?:in|under)\s+[A-Za-z0-9_./~-]+$", "", term).strip()
        return term
    return None


def _path_scope(text: str) -> str:
    match = re.search(r"\b(?:in|under)\s+([A-Za-z0-9_./~-]+)", text, re.IGNORECASE)
    return match.group(1) if match else "."


def _command(text: str) -> str | None:
    if text.strip().startswith("$ "):
        return text.strip()[2:].strip()
    quoted = _first_quoted(text)
    if quoted and re.search(r"\b(run|command|shell|terminal|execute)\b", text, re.IGNORECASE):
        return quoted
    match = re.search(r"\b(?:run|execute)\s+(?:command\s+)?(.+)$", text, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _write_file_step(text: str, append: bool = False) -> ToolStep | None:
    path = _path_after(text, "write|create|append|save")
    if not path:
        return None
    content = ""
    match = re.search(r"\b(?:with|containing|content:)\s+(.+)$", text, re.IGNORECASE | re.DOTALL)
    if match:
        content = match.group(1).strip()
        if (content.startswith('"') and content.endswith('"')) or (content.startswith("'") and content.endswith("'")):
            content = content[1:-1]
    return ToolStep(
        "filesystem",
        "write_file",
        {"path": path, "content": content, "append": append},
        "write requested local workspace file",
    )


def _email_step(text: str) -> ToolStep | None:
    if not re.search(r"\b(email|mail)\b", text, re.IGNORECASE):
        return None
    to_match = re.search(r"\bto\s+([^\s,;]+@[^\s,;]+)", text, re.IGNORECASE)
    subject_match = re.search(r"\bsubject\s+['\"]?(.+?)(?:['\"]?\s+\bbody\b|$)", text, re.IGNORECASE | re.DOTALL)
    body_match = re.search(r"\bbody\s+['\"]?(.+?)['\"]?$", text, re.IGNORECASE | re.DOTALL)
    if "draft" not in text.lower() and "write" not in text.lower():
        return None
    return ToolStep(
        "email",
        "draft_email",
        {
            "to": to_match.group(1) if to_match else "",
            "subject": subject_match.group(1).strip() if subject_match else "",
            "body": body_match.group(1).strip() if body_match else "",
        },
        "create an email draft without sending",
    )


def _browser_step(text: str) -> ToolStep | None:
    if not re.search(r"\b(browser|web|page|site|url|open)\b", text, re.IGNORECASE):
        return None
    quoted = _first_quoted(text)
    url_match = re.search(r"\bhttps?://[^\s\"'`]+|\bfile://[^\s\"'`]+", text, re.IGNORECASE)
    url = quoted if quoted and re.match(r"^(?:https?|file)://", quoted, re.IGNORECASE) else (url_match.group(0) if url_match else "")
    lower = text.lower()
    if "screenshot" in lower:
        return ToolStep("browser", "screenshot", {"url": url} if url else {}, "capture a local browser screenshot")
    if any(word in lower for word in ["extract", "text", "read page", "summarize page"]):
        return ToolStep("browser", "extract_text", {"url": url} if url else {}, "extract page text in the local browser")
    if any(word in lower for word in ["open", "load", "visit"]) and url:
        return ToolStep("browser", "open_page", {"url": url}, "open a page in the local browser")
    return None


def _automation_step(text: str) -> ToolStep | None:
    lower = text.lower()
    if not any(word in lower for word in ["automation", "automations", "reminder", "schedule", "scheduled", "recurring"]):
        return None
    if any(phrase in lower for phrase in ["run due", "run scheduled", "check due", "process due"]):
        return ToolStep("automations", "run_due_automations", {}, "run due local automations")
    if any(word in lower for word in ["list", "show", "status", "existing", "current"]):
        return ToolStep("automations", "list_automations", {}, "list local automations")
    if any(word in lower for word in ["delete", "remove", "cancel"]):
        target = _first_quoted(text) or _path_after(text, "delete|remove|cancel")
        return ToolStep("automations", "delete_automation", {"id": target or ""}, "delete local automation")
    if any(word in lower for word in ["create", "add", "schedule", "remind"]):
        name = _first_quoted(text) or "Local automation"
        return ToolStep(
            "automations",
            "create_automation",
            {"name": name, "prompt": text, "schedule": "manual"},
            "create local automation definition",
        )
    return ToolStep("automations", "list_automations", {}, "list local automations")


def _plan_single_task(text: str, uploads: list[dict[str, Any]] | None = None) -> list[ToolStep]:
    """Return deterministic tool steps for a natural-language task."""
    text = (text or "").strip()
    lower = text.lower()
    uploads = uploads or []

    directive = parse_tool_directive(text)
    if directive:
        plugin, tool, args = directive
        return [ToolStep(plugin, tool, args, "explicit @tool directive")]

    if lower.startswith(("plan ", "plan mode", "make a plan", "show plan")) or "plan mode" in lower:
        task = re.sub(r"^(?:plan(?: mode)?|make a plan(?: for)?|show plan(?: for)?)\s*", "", text, flags=re.IGNORECASE).strip()
        return [ToolStep("workspace", "plan_task", {"query": task or text}, "show tool plan without executing")]

    if re.search(r"\b(models?|hardware|ram|gpu|cpu|download|model recommendation)\b", lower):
        return [ToolStep("workspace", "model_recommendation", {}, "recommend local models without downloading or running them")]

    automation = _automation_step(text)
    if automation:
        return [automation]

    email = _email_step(text)
    if email:
        return [email]

    browser = _browser_step(text)
    if browser:
        return [browser]

    if any(phrase in lower for phrase in ["run history", "recent runs", "task history", "what have you done"]):
        return [ToolStep("workspace", "run_history", {"limit": 20}, "show persistent Locus run history")]

    if any(word in lower for word in ["todo", "fixme", "hack markers", "technical debt"]):
        return [ToolStep("workspace", "todo_report", {}, "inspect workspace TODO/FIXME markers")]

    if any(
        phrase in lower
        for phrase in [
            "what is this repo",
            "what is this project",
            "project brief",
            "workspace brief",
            "repo overview",
            "project overview",
            "explain this folder",
            "explain the folder",
            "explain this repo",
            "summarize this repo",
            "summarize this project",
        ]
    ):
        return [ToolStep("workspace", "workspace_brief", {}, "build a repo-native workspace briefing")]

    if any(
        phrase in lower
        for phrase in [
            "health report",
            "repo health",
            "project health",
            "workspace health",
            "audit this repo",
            "audit the repo",
            "audit project",
            "check this repo",
            "check the repo",
            "what should i run",
            "verification commands",
            "verify this project",
            "local health",
        ]
    ):
        return [ToolStep("workspace", "health_report", {}, "summarize project health and likely checks")]

    if any(phrase in lower for phrase in ["index workspace", "index repo", "scan workspace", "scan repo"]):
        return [ToolStep("workspace", "workspace_index", {}, "build persistent workspace index")]

    if "git diff" in lower or lower.strip() == "diff":
        path = _path_after(text, "diff")
        return [ToolStep("git", "git_diff", {"path": path} if path else {}, "inspect repository diff")]

    if "git log" in lower or "recent commits" in lower:
        return [ToolStep("git", "git_log", {"limit": 8}, "inspect recent commits")]

    if "git status" in lower or lower.strip() in {"status", "repo status"}:
        return [ToolStep("git", "git_status", {}, "inspect repository status")]

    command = _command(text)
    if command:
        return [ToolStep("shell", "run_command", {"command": command, "timeout": 30}, "run explicit shell command")]

    if lower.startswith(("append ", "append file")):
        step = _write_file_step(text, append=True)
        return [step] if step else []

    if lower.startswith(("write ", "create ", "save ")):
        step = _write_file_step(text, append=False)
        return [step] if step else []

    if lower.startswith(("read upload", "show upload", "open upload")):
        target = _first_quoted(text)
        if not target and uploads:
            target = uploads[-1].get("path") or uploads[-1].get("name")
        return [ToolStep("uploads", "read_upload", {"path": target or ""}, "read uploaded file")]

    if "upload" in lower or "attachment" in lower:
        return [ToolStep("uploads", "list_uploads", {}, "list uploaded files")]

    if lower.startswith(("read ", "open ", "show file", "cat ")):
        path = _path_after(text, "read|open|show|cat")
        if path:
            return [ToolStep("filesystem", "read_file", {"path": path}, "read local file")]

    if lower.startswith(("search", "find", "grep")) or " search for " in lower:
        term = _search_term(text)
        if term:
            return [ToolStep("filesystem", "search_text", {"query": term, "path": _path_scope(text)}, "search workspace text")]

    if "connector" in lower or (
        "plugin" in lower
        and any(
            word in lower
            for word in ["status", "list", "show", "enabled", "available", "diagnostic", "diagnostics", "audit", "health", "implemented", "pending", "real", "executable"]
        )
    ):
        return [ToolStep("workspace", "plugin_diagnostics", {}, "inspect plugin implementation and connector health")]

    if lower.startswith(("list files", "show files", "tree")):
        return [ToolStep("filesystem", "list_files", {"path": _path_scope(text), "limit": 120}, "list workspace files")]

    if lower.startswith(("workspace", "repo summary")):
        return [ToolStep("workspace", "workspace_brief", {}, "build a repo-native workspace briefing")]

    if uploads and any(word in lower for word in ["summarize", "inspect", "analyze"]):
        return [ToolStep("uploads", "read_upload", {"path": uploads[-1].get("path") or uploads[-1].get("name")}, "inspect latest upload")]

    return []


def _split_compound_task(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw:
        return []
    if parse_tool_directive(raw) or raw.startswith("$ "):
        return [raw]
    if _command(raw):
        return [raw]

    normalized = re.sub(r"[ \t]+", " ", raw)
    parts = re.split(r"\s*(?:;|\n+)\s*|\s+\b(?:and then|then|also)\b\s+", normalized, flags=re.IGNORECASE)
    expanded: list[str] = []
    intent = r"(?:show|list|git|run|read|open|search|find|grep|write|create|append|index|scan|check|audit|verify|what|workspace|repo|project|todo|fixme|upload|plugin|connector|model|hardware|ram|gpu)"
    for part in parts:
        part = part.strip(" ,")
        if not part:
            continue
        subparts = re.split(rf"\s+\band\s+(?={intent}\b)", part, flags=re.IGNORECASE)
        expanded.extend(item.strip(" ,") for item in subparts if item.strip(" ,"))
    return expanded or [raw]


def _dedupe_steps(steps: list[ToolStep]) -> list[ToolStep]:
    deduped: list[ToolStep] = []
    seen: set[tuple[str, str, str]] = set()
    for step in steps:
        key = (step.plugin, step.tool, json.dumps(step.args, sort_keys=True, ensure_ascii=False))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(step)
    return deduped


def plan_workspace_task(text: str, uploads: list[dict[str, Any]] | None = None) -> list[ToolStep]:
    """Return deterministic tool steps for a natural-language task."""
    text = (text or "").strip()
    if not text:
        return []
    if parse_tool_directive(text):
        return _plan_single_task(text, uploads=uploads)

    parts = _split_compound_task(text)
    if len(parts) == 1:
        return _plan_single_task(parts[0], uploads=uploads)

    steps: list[ToolStep] = []
    for part in parts:
        steps.extend(_plan_single_task(part, uploads=uploads))
    return _dedupe_steps(steps)


def plan_to_json(plan: list[ToolStep]) -> list[dict[str, Any]]:
    return [step.to_dict() for step in plan]


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Plan a model-free workspace task")
    parser.add_argument("task", nargs="*")
    args = parser.parse_args()
    print(json.dumps(plan_to_json(plan_workspace_task(" ".join(args.task))), indent=2))


if __name__ == "__main__":
    main()
