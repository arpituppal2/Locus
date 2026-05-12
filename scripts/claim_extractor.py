"""Claim extraction for web source content."""
from __future__ import annotations

import re

from scripts.ollama_client import MODEL_EXECUTOR, call_json


def _fallback_claims(text: str, min_claims: int = 3, max_claims: int = 8) -> list[str]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    candidates: list[str] = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) < 8:
            continue
        if re.search(r"\b(is|are|was|were|reported|found|shows|states|according)\b", sentence, re.I):
            candidates.append(sentence)
        if len(candidates) >= max_claims:
            break
    if len(candidates) < min_claims:
        candidates.extend(sentences[: max(0, min_claims - len(candidates))])
    deduped: list[str] = []
    seen: set[str] = set()
    for claim in candidates:
        c = claim.strip().strip("-• ")
        if not c:
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)
        if len(deduped) >= max_claims:
            break
    return deduped


def _normalize_claims(raw_claims: list[str], min_claims: int = 3, max_claims: int = 8) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for claim in raw_claims:
        c = re.sub(r"\s+", " ", str(claim or "")).strip().strip("-• ")
        if len(c.split()) < 5:
            continue
        key = c.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(c)
        if len(cleaned) >= max_claims:
            break
    return cleaned[:max_claims] if len(cleaned) >= min_claims else cleaned


def extract_claims(content_or_title: str, url: str = "", text: str | None = None) -> list[str]:
    """Extract 3-8 factual claims from source text.

    The function supports both signatures:
    - extract_claims(content)
    - extract_claims(title, url, content)
    """
    content = text if text is not None else content_or_title
    content = (content or "").strip()
    if not content:
        return []

    excerpt = content[:12000]
    prompt = f"""
Extract 3 to 8 factual claims from the source text below.
Rules:
- Claims must be short, concrete, and verifiable
- Do not include opinions, calls to action, ads, or navigation text
- Keep each claim to one sentence

Return JSON only:
{{"claims": ["claim 1", "claim 2"]}}

SOURCE TEXT:
{excerpt}
""".strip()

    data = call_json(prompt, model=MODEL_EXECUTOR)
    claims = data.get("claims", []) if isinstance(data, dict) else []
    normalized = _normalize_claims(claims if isinstance(claims, list) else [])

    if len(normalized) < 3:
        return _fallback_claims(content)
    return normalized[:8]
