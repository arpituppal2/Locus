"""Generates PROSE-specific deterministic browser step plans."""
import json
import sys
from pathlib import Path

import requests

from scripts.ollama_client import DEFAULT_OLLAMA_OPTIONS, MODEL_PLANNER

OLLAMA_URL     = "http://localhost:11434/api/generate"
PLANNER_MODEL  = MODEL_PLANNER
ROOT           = Path(__file__).resolve().parent.parent
PROMPT_PATH    = ROOT / "prompts" / "browser_steps_prose.txt"


def call_planner(task: str) -> dict:
    system = PROMPT_PATH.read_text()
    prompt = system + "\n\nUser instruction:\n" + task + "\n\nJSON only:"
    options = {**DEFAULT_OLLAMA_OPTIONS, "temperature": 0.1}
    payload = {"model": PLANNER_MODEL, "prompt": prompt, "stream": False, "options": options}
    resp = requests.post(OLLAMA_URL, json=payload, timeout=300)
    resp.raise_for_status()
    text  = resp.json().get("response", "").strip()
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Planner did not return JSON:\n" + text)
    return json.loads(text[start:end + 1])


def main() -> None:
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else sys.stdin.read().strip()
    if not task:
        print("Usage: python scripts/plan_prose.py 'task description'")
        raise SystemExit(1)
    plan       = call_planner(task)
    start_url  = plan.get("start_url") or "https://prose.example.com/login"
    steps      = plan.get("steps") or []
    out_dir    = ROOT / "tasks"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path   = out_dir / "prose_steps.json"
    out_path.write_text(json.dumps({"start_url": start_url, "steps": steps}, indent=2))
    print(f"Wrote plan to {out_path}")
    print(json.dumps({"start_url": start_url, "steps": steps}, indent=2))


if __name__ == "__main__":
    main()
