"""Source credibility scoring for multi-source web research."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

KNOWN_NEWS = {
    "reuters.com",
    "apnews.com",
    "bbc.com",
    "nytimes.com",
    "washingtonpost.com",
    "wsj.com",
    "theguardian.com",
    "ft.com",
    "npr.org",
}


@dataclass
class SourceScore:
    url: str
    score: float
    domain_tier: str
    claim_count: int


def domain_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _domain_base_score(domain: str) -> tuple[float, str]:
    if domain.endswith("wikipedia.org"):
        return 0.90, "wikipedia"
    if domain.endswith(".gov") or domain.endswith(".edu"):
        return 0.85, "public_institution"
    if any(domain.endswith(news) for news in KNOWN_NEWS):
        return 0.75, "known_news"
    return 0.50, "other"


def _word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w.strip()])


def score_source(url: str, content: str, claims: list[str] | None = None) -> SourceScore:
    domain = domain_of(url)
    base, tier = _domain_base_score(domain)

    word_count = _word_count(content)
    length_bonus = 0.10 if word_count > 500 else 0.0

    claim_count = len(claims or [])
    word_blocks = max(1.0, word_count / 500.0)
    density = claim_count / word_blocks
    density_bonus = min(0.12, max(0.0, density / 40.0))

    final_score = min(1.0, round(base + length_bonus + density_bonus, 4))
    return SourceScore(
        url=url,
        score=final_score,
        domain_tier=tier,
        claim_count=claim_count,
    )
