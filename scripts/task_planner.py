"""Task planning helpers for Locus research orchestration."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from scripts.ollama_client import MODEL_PLANNER, call_json

HEADLESS_ROLES = {"writer", "analyst", "planner", "critic", "summarizer", "coder", "file"}
BROWSER_ROLES = {"browser", "navigator", "actor"}
ONLINE_ROLES = {"researcher"}


@dataclass
class CapabilityPlan:
    needs_browser: bool = False
    needs_response: bool = True
    needs_subagents: bool = False
    needs_online: bool = False
    needs_api: bool = False
    confidence: float = 1.0
    reasoning: str = "heuristic"

    def to_log(self) -> str:
        flags: list[str] = []
        if self.needs_browser:
            flags.append("browser")
        if self.needs_response:
            flags.append("response")
        if self.needs_subagents:
            flags.append("subagents")
        if self.needs_online:
            flags.append("online")
        if self.needs_api:
            flags.append("api")
        return f"[{', '.join(flags)}] confidence={self.confidence:.2f} — {self.reasoning}"


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _normalize_candidates(candidates: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = re.sub(r"\s+", " ", str(candidate or "")).strip(" \t\n\r\"'")
        text = text.replace("?", "")
        if len(text) < 3:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
        if len(cleaned) >= 5:
            break
    return cleaned


def _fallback_decompose(query: str) -> list[str]:
    words = [w for w in re.findall(r"[A-Za-z0-9%.$+-]+", query) if len(w) > 2]
    if len(words) <= 6:
        return [query.strip(), f"{query.strip()} latest data"]

    third = max(3, len(words) // 3)
    chunks = [
        " ".join(words[:third]),
        " ".join(words[third:2 * third]),
        " ".join(words[2 * third:]),
    ]
    return _normalize_candidates([c for c in chunks if c.strip()])[:5]


def decompose_query(query: str) -> list[str]:
    """Decompose a user query into 2-5 keyword-focused sub-queries."""
    prompt = f"""
You decompose research requests into parallel web search strings.

User query:
{query}

Rules:
- Return between 2 and 5 sub-queries
- Each sub-query must be a short keyword-focused search string
- Do not ask questions
- Do not include punctuation-heavy prose
- Keep each sub-query under 12 words

Return JSON only:
{{"queries": ["...", "..."]}}
""".strip()

    data = call_json(prompt, model=MODEL_PLANNER)
    queries = data.get("queries", []) if isinstance(data, dict) else []
    normalized = _normalize_candidates(queries if isinstance(queries, list) else [])

    if len(normalized) < 2:
        normalized = _fallback_decompose(query)
    if len(normalized) < 2:
        normalized = [query.strip(), f"{query.strip()} background"]

    return normalized[:5]


def assess_capabilities(goal: str) -> CapabilityPlan:
    g = goal.lower()
    browser_terms = ["click", "fill", "login", "open website", "navigate"]
    online_terms = ["latest", "today", "current", "news", "research", "find", "search"]
    needs_browser = any(term in g for term in browser_terms)
    needs_online = any(term in g for term in online_terms) and not needs_browser
    needs_response = not needs_browser and not needs_online
    return CapabilityPlan(
        needs_browser=needs_browser,
        needs_response=needs_response,
        needs_subagents=False,
        needs_online=needs_online,
        needs_api=False,
        confidence=0.8,
        reasoning="keyword heuristic",
    )


def build_task_graph(goal: str, cap: CapabilityPlan | None = None) -> list[dict[str, Any]]:
    if cap is None:
        cap = assess_capabilities(goal)

    if cap.needs_online:
        gather_id = _uid()
        write_id = _uid()
        return [
            {
                "id": gather_id,
                "role": "researcher",
                "goal": goal,
                "depends_on": [],
                "max_steps": 12,
                "chatbot_mode": False,
                "priority": 1,
                "exec_mode": "online",
            },
            {
                "id": write_id,
                "role": "writer",
                "goal": f"Synthesize results for: {goal}",
                "depends_on": [gather_id],
                "max_steps": 8,
                "chatbot_mode": False,
                "priority": 2,
                "exec_mode": "response",
            },
        ]

    if cap.needs_browser:
        browse_id = _uid()
        write_id = _uid()
        return [
            {
                "id": browse_id,
                "role": "browser",
                "goal": goal,
                "depends_on": [],
                "max_steps": 18,
                "chatbot_mode": False,
                "priority": 1,
                "exec_mode": "browser",
            },
            {
                "id": write_id,
                "role": "writer",
                "goal": f"Summarize browser findings for: {goal}",
                "depends_on": [browse_id],
                "max_steps": 8,
                "chatbot_mode": False,
                "priority": 2,
                "exec_mode": "response",
            },
        ]

    return [
        {
            "id": _uid(),
            "role": "writer",
            "goal": goal,
            "depends_on": [],
            "max_steps": 6,
            "chatbot_mode": False,
            "priority": 1,
            "exec_mode": "response",
        }
    ]


def tasks_to_stages(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "stage": t["id"],
            "goal": t["goal"],
            "role": t["role"],
            "max_steps": t["max_steps"],
            "chatbot_mode": t["chatbot_mode"],
            "depends_on": t["depends_on"],
            "priority": t["priority"],
            "exec_mode": t.get("exec_mode", "response"),
        }
        for t in tasks
    ]


def _can_use_heavy() -> bool:
    """Compatibility shim retained for legacy imports."""
    return True
