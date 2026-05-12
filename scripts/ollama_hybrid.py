"""Compatibility wrapper for sending prompts through local Ollama."""
from __future__ import annotations

import json

import httpx

from scripts.ollama_client import DEFAULT_OLLAMA_OPTIONS, MODEL_SYNTHESIZER

API_URL = "http://127.0.0.1:11434/api/generate"


def send_prompt(
    prompt: str,
    model: str = MODEL_SYNTHESIZER,
    use_mlx_for_heavy: bool = True,
    max_tokens: int = 1024,
) -> str:
    """Dispatch a prompt to Ollama.

    Parameters are kept for backward compatibility with older call-sites.
    """
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "options": {
            "num_predict": max_tokens,
            **DEFAULT_OLLAMA_OPTIONS,
        },
    }

    chunks: list[str] = []
    with httpx.stream("POST", API_URL, json=payload, timeout=120) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            chunks.append(data.get("response", ""))
            if data.get("done"):
                break

    return "".join(chunks).strip()
