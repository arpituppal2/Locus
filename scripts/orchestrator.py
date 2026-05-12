"""Research orchestrator: decomposition → retrieval → synthesis with inline citations."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from scripts.claim_extractor import extract_claims
from scripts.long_term_memory import retrieve_relevant_answers, store_query_answer
from scripts.navigation_agent import SourceResult, collect_sources_for_subquery
from scripts.ollama_client import (
    MODEL_CRITIC,
    MODEL_SYNTHESIZER,
    async_call,
    async_call_json,
    async_stream_chat,
)
from scripts.model_selector import effective_models_config
from scripts.resource_policy import resource_budget
from scripts.source_scoring import SourceScore, score_source
from scripts.task_planner import decompose_query

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

_MODEL_CFG = effective_models_config()
_RESOURCE_BUDGET = resource_budget()
_LOCAL_PARALLEL = max(1, int(_MODEL_CFG.get("max_local_parallel", 1)))
OLLAMA_SEMAPHORE = asyncio.Semaphore(_LOCAL_PARALLEL)
SUBQUERY_SEMAPHORE = asyncio.Semaphore(1 if _RESOURCE_BUDGET.low_ram_mode else min(2, _LOCAL_PARALLEL + 1))


@dataclass
class EnrichedSource:
    id: int
    url: str
    title: str
    content: str
    fetch_time_ms: int
    claims: list[str]
    score: float
    domain_tier: str
    claim_count: int


def _normalize_url(url: str) -> str:
    return url.rstrip("/")


async def _emit(
    callback: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
    event: dict[str, Any],
) -> None:
    if callback is None:
        return
    result = callback(event)
    if asyncio.iscoroutine(result):
        await result


async def _ollama_json(prompt: str, model: str) -> dict[str, Any]:
    async with OLLAMA_SEMAPHORE:
        return await async_call_json(prompt, model=model)


async def _ollama_text(prompt: str, model: str) -> str:
    async with OLLAMA_SEMAPHORE:
        return await async_call(prompt, model=model)


async def _decompose(query: str) -> list[str]:
    async with OLLAMA_SEMAPHORE:
        return await asyncio.to_thread(decompose_query, query)


async def _memory_recall(query: str) -> list[dict[str, Any]]:
    async with OLLAMA_SEMAPHORE:
        return await asyncio.to_thread(retrieve_relevant_answers, query, 3)


async def _memory_store(query: str, answer: str, sources: list[dict[str, Any]]) -> None:
    async with OLLAMA_SEMAPHORE:
        await asyncio.to_thread(store_query_answer, query, answer, sources)


async def _collect_subquery_sources(
    sub_query: str,
    emit_event: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
) -> list[SourceResult]:
    async def _thinking(msg: str):
        await _emit(emit_event, {"type": "thinking", "data": msg})

    async with SUBQUERY_SEMAPHORE:
        await _emit(emit_event, {"type": "thinking", "data": f"Searching sub-query: {sub_query}"})
        return await collect_sources_for_subquery(sub_query, thinking_cb=_thinking)


async def _enrich_source(source: SourceResult, source_id: int) -> EnrichedSource:
    async with OLLAMA_SEMAPHORE:
        claims = await asyncio.to_thread(extract_claims, source.content)
    score: SourceScore = score_source(source.url, source.content, claims)
    return EnrichedSource(
        id=source_id,
        url=source.url,
        title=source.title,
        content=source.content,
        fetch_time_ms=source.fetch_time_ms,
        claims=claims,
        score=score.score,
        domain_tier=score.domain_tier,
        claim_count=score.claim_count,
    )


def _build_sources_prompt(sources: list[EnrichedSource]) -> str:
    blocks: list[str] = []
    for source in sources:
        excerpt = re.sub(r"\s+", " ", source.content)[:600]
        claims = " | ".join(source.claims[:6])
        blocks.append(
            f"[{source.id}] {source.url}\n"
            f"TITLE: {source.title}\n"
            f"DOMAIN_TIER: {source.domain_tier}\n"
            f"SCORE: {source.score:.2f}\n"
            f"CLAIMS: {claims}\n"
            "---\n"
            f"{excerpt}"
        )
    return "\n\n".join(blocks)


def _build_memory_context(memory_hits: list[dict[str, Any]]) -> str:
    if not memory_hits:
        return ""
    parts = []
    for i, item in enumerate(memory_hits, start=1):
        parts.append(
            f"[M{i}] Query: {item['query']}\n"
            f"Similarity: {item['similarity']:.3f}\n"
            f"Answer excerpt: {(item['answer'] or '')[:500]}"
        )
    return "\n\n".join(parts)


async def _stream_synthesis(
    query: str,
    sources: list[EnrichedSource],
    memory_hits: list[dict[str, Any]],
    emit_event: Callable[[dict[str, Any]], Awaitable[None] | None] | None,
) -> str:
    memory_context = _build_memory_context(memory_hits)
    prompt = (
        f"You are a research synthesizer. Given the following sources and their extracted claims,\n"
        f"write a comprehensive answer to the query: \"{query}\"\n\n"
        "Format rules:\n"
        "- Write in flowing paragraphs, not bullet points\n"
        "- After every factual sentence, append a citation like [1] or [1][3]\n"
        "- At the end, output a SOURCES section listing each cited URL numbered [1], [2], etc.\n"
        "- Be direct. No filler phrases like \"Based on the sources...\" or \"It's worth noting...\"\n"
        "- If sources contradict, note the disagreement explicitly\n\n"
        "Relevant memory from past sessions (use only if directly relevant):\n"
        f"{memory_context or '[none]'}\n\n"
        "SOURCES:\n"
        f"{_build_sources_prompt(sources)}\n\n"
        f"QUERY: {query}"
    )

    tokens: list[str] = []
    async with OLLAMA_SEMAPHORE:
        async for token in async_stream_chat(
            [{"role": "user", "content": prompt}],
            model=MODEL_SYNTHESIZER,
        ):
            tokens.append(token)
            await _emit(emit_event, {"type": "token", "data": token})

    return "".join(tokens).strip()


def _parse_followups(raw: str) -> list[str]:
    raw = raw.strip()
    if not raw:
        return []

    candidates: list[str] = []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            candidates = [str(x).strip() for x in parsed if str(x).strip()]
            return candidates[:2]
    except json.JSONDecodeError:
        pass

    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, list):
                candidates = [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            candidates = []

    return candidates[:2]


async def _critic_followups(answer_text: str) -> list[str]:
    prompt = (
        "Given this answer, list up to 2 follow-up searches that would make it more complete.\n"
        "Output as JSON array of strings, or [] if none needed.\n"
        f"Answer: {answer_text}"
    )
    raw = await _ollama_text(prompt, MODEL_CRITIC)
    return _parse_followups(raw)


async def run_research_query(
    query: str,
    emit_event: Callable[[dict[str, Any]], Awaitable[None] | None] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    await _emit(emit_event, {"type": "thinking", "data": "Decomposing query"})

    sub_queries = await _decompose(query)
    if not sub_queries:
        sub_queries = [query]

    await _emit(
        emit_event,
        {"type": "thinking", "data": f"Running {len(sub_queries)} sub-queries in parallel"},
    )

    source_batches = await asyncio.gather(
        *[_collect_subquery_sources(sub_query, emit_event) for sub_query in sub_queries],
        return_exceptions=False,
    )

    unique_sources: dict[str, SourceResult] = {}
    for batch in source_batches:
        for source in batch:
            key = _normalize_url(source.url)
            if key not in unique_sources:
                unique_sources[key] = source

    flat_sources = list(unique_sources.values())
    await _emit(
        emit_event,
        {"type": "thinking", "data": f"Extracting claims from {len(flat_sources)} sources"},
    )

    enriched: list[EnrichedSource] = []
    for idx, source in enumerate(flat_sources, start=1):
        enriched_source = await _enrich_source(source, idx)
        enriched.append(enriched_source)
        await _emit(
            emit_event,
            {
                "type": "source",
                "data": {
                    "id": enriched_source.id,
                    "url": enriched_source.url,
                    "title": enriched_source.title,
                    "score": round(enriched_source.score, 2),
                },
            },
        )

    enriched.sort(key=lambda item: item.score, reverse=True)
    ranked_sources = [
        EnrichedSource(
            id=i,
            url=src.url,
            title=src.title,
            content=src.content,
            fetch_time_ms=src.fetch_time_ms,
            claims=src.claims,
            score=src.score,
            domain_tier=src.domain_tier,
            claim_count=src.claim_count,
        )
        for i, src in enumerate(enriched, start=1)
    ]

    await _emit(emit_event, {"type": "thinking", "data": "Retrieving relevant memory"})
    memory_hits = await _memory_recall(query)

    await _emit(emit_event, {"type": "thinking", "data": "Synthesizing answer"})
    answer = await _stream_synthesis(query, ranked_sources, memory_hits, emit_event)

    # One-hop follow-up research only.
    followups = await _critic_followups(answer)
    if followups:
        await _emit(
            emit_event,
            {"type": "thinking", "data": f"Running follow-up searches: {', '.join(followups)}"},
        )

        followup_batches = await asyncio.gather(
            *[_collect_subquery_sources(q, emit_event) for q in followups],
            return_exceptions=False,
        )

        followup_sources: list[SourceResult] = []
        for batch in followup_batches:
            followup_sources.extend(batch)

        followup_dedup: dict[str, SourceResult] = {}
        for source in followup_sources:
            key = _normalize_url(source.url)
            if key not in followup_dedup:
                followup_dedup[key] = source

        additional_sources: list[EnrichedSource] = []
        for source in followup_dedup.values():
            additional_sources.append(await _enrich_source(source, len(ranked_sources) + len(additional_sources) + 1))

        if additional_sources:
            await _emit(emit_event, {"type": "token", "data": "\n\n### Additional context\n\n"})
            additional_prompt = (
                "Given these follow-up sources, write a concise Additional context section.\n"
                "Rules:\n"
                "- Use flowing paragraphs\n"
                "- Cite every factual sentence as [N]\n"
                "- Mention any unresolved uncertainty\n\n"
                f"SOURCES:\n{_build_sources_prompt(additional_sources)}\n\n"
                f"QUERY: {query}\n"
                f"CURRENT ANSWER:\n{answer[:4000]}"
            )

            additional_tokens: list[str] = []
            async with OLLAMA_SEMAPHORE:
                async for token in async_stream_chat(
                    [{"role": "user", "content": additional_prompt}],
                    model=MODEL_SYNTHESIZER,
                ):
                    additional_tokens.append(token)
                    await _emit(emit_event, {"type": "token", "data": token})

            additional_text = "".join(additional_tokens).strip()
            if additional_text:
                answer = f"{answer}\n\n### Additional context\n\n{additional_text}"
                ranked_sources.extend(additional_sources)

    source_payload = [
        {
            "id": source.id,
            "url": source.url,
            "title": source.title,
            "score": source.score,
            "domain_tier": source.domain_tier,
            "claim_count": source.claim_count,
            "fetch_time_ms": source.fetch_time_ms,
            "claims": source.claims,
        }
        for source in ranked_sources
    ]

    await _memory_store(query, answer, source_payload)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    await _emit(
        emit_event,
        {"type": "done", "data": {"elapsed_ms": elapsed_ms, "sources_used": len(ranked_sources)}},
    )

    return {
        "query": query,
        "sub_queries": sub_queries,
        "answer": answer,
        "sources": source_payload,
        "elapsed_ms": elapsed_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Locus research orchestration")
    parser.add_argument("query", nargs="*", help="Research query")
    args = parser.parse_args()

    query = " ".join(args.query).strip() or input("Query: ").strip()
    if not query:
        raise SystemExit("No query provided")

    async def _runner() -> None:
        result = await run_research_query(query)
        out_path = ROOT / "outputs" / "result.md"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(result["answer"])
        sources_path = ROOT / "outputs" / "sources.json"
        sources_path.write_text(json.dumps(result["sources"], indent=2))
        print(result["answer"])

    asyncio.run(_runner())


if __name__ == "__main__":
    main()
