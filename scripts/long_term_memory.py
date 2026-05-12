#!/usr/bin/env python3
"""Persistent long-term memory backed by SQLite and Ollama embeddings."""
from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.runtime_policy import local_models_allowed

if TYPE_CHECKING:
    from scripts.memory import Memory

MEMORY_DIR = Path.home() / ".local-computer"
MEMORY_DB = MEMORY_DIR / "memory.db"


def _embed_text_safe(text: str) -> list[float]:
    if not local_models_allowed():
        return []
    from scripts.ollama_client import embed_text

    return embed_text(text)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_db() -> None:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(MEMORY_DB) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS query_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                answer TEXT NOT NULL,
                embedding TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id INTEGER NOT NULL,
                entity TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(query_id) REFERENCES query_history(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                title TEXT,
                score REAL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(query_id) REFERENCES query_history(id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_query_created ON query_history(created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fact_query ON memory_facts(query_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_source_query ON source_history(query_id)")


def _connect() -> sqlite3.Connection:
    _ensure_db()
    conn = sqlite3.connect(MEMORY_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _extract_entities(answer: str, max_entities: int = 30) -> list[str]:
    candidates = re.findall(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}|[A-Z]{2,}(?:\s+[A-Z]{2,}){0,2})\b", answer)
    cleaned: list[str] = []
    seen: set[str] = set()
    for entity in candidates:
        ent = re.sub(r"\s+", " ", entity).strip()
        if len(ent) < 3:
            continue
        if ent.lower() in {"the", "this", "that", "there"}:
            continue
        key = ent.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(ent)
        if len(cleaned) >= max_entities:
            break
    return cleaned


def store_query_answer(query: str, answer: str, sources: list[dict[str, Any]] | None = None) -> int:
    if not query.strip() or not answer.strip():
        return -1

    embedding = _embed_text_safe(f"{query}\n\n{answer}"[:10000])
    created_at = _utc_now()

    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO query_history(query, answer, embedding, created_at) VALUES (?, ?, ?, ?)",
            (query.strip(), answer.strip(), json.dumps(embedding), created_at),
        )
        query_id = int(cursor.lastrowid)

        entities = _extract_entities(answer)
        conn.executemany(
            "INSERT INTO memory_facts(query_id, entity, created_at) VALUES (?, ?, ?)",
            [(query_id, entity, created_at) for entity in entities],
        )

        for src in sources or []:
            conn.execute(
                "INSERT INTO source_history(query_id, url, title, score, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    query_id,
                    str(src.get("url", "")),
                    str(src.get("title", "")),
                    float(src.get("score", 0.0) or 0.0),
                    created_at,
                ),
            )

    return query_id


def retrieve_relevant_answers(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    query = query.strip()
    if not query:
        return []

    query_embedding = _embed_text_safe(query)
    rows: list[sqlite3.Row]
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, query, answer, embedding, created_at FROM query_history ORDER BY created_at DESC LIMIT 300"
        ).fetchall()

    scored: list[dict[str, Any]] = []
    query_terms = set(re.findall(r"\w+", query.lower()))

    for row in rows:
        answer_embedding: list[float] = []
        try:
            answer_embedding = [float(x) for x in json.loads(row["embedding"] or "[]")]
        except Exception:
            answer_embedding = []

        similarity = _cosine(query_embedding, answer_embedding) if query_embedding and answer_embedding else 0.0
        if similarity == 0.0:
            row_terms = set(re.findall(r"\w+", (row["query"] or "").lower()))
            overlap = len(query_terms & row_terms)
            similarity = overlap / max(1, len(query_terms))

        scored.append(
            {
                "id": int(row["id"]),
                "query": row["query"],
                "answer": row["answer"],
                "created_at": row["created_at"],
                "similarity": float(similarity),
            }
        )

    scored.sort(key=lambda item: item["similarity"], reverse=True)
    return [item for item in scored if item["similarity"] > 0][:top_k]


def list_recent_queries(limit: int = 5) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT query, answer, created_at FROM query_history ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [
        {
            "query": row["query"],
            "answer": row["answer"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def get_cached_answer(query: str) -> str | None:
    q = query.strip().lower()
    if not q:
        return None
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT answer FROM query_history
            WHERE lower(query) = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (q,),
        ).fetchone()
    return str(row["answer"]) if row else None


def should_read(goal: str) -> bool:
    return bool(retrieve_relevant_answers(goal, top_k=1))


def read_relevant(goal: str) -> str:
    relevant = retrieve_relevant_answers(goal, top_k=3)
    if not relevant:
        return ""
    chunks = []
    for idx, item in enumerate(relevant, start=1):
        chunks.append(
            f"[{idx}] Previous query: {item['query']}\n"
            f"Answer excerpt: {(item['answer'] or '')[:700]}\n"
            f"Timestamp: {item['created_at']}"
        )
    return "\n\n".join(chunks)


def manage_memory(goal: str, memory: "Memory", summary: str) -> None:
    if not goal.strip() or not summary.strip():
        return
    sources: list[dict[str, Any]] = []
    for evidence in getattr(memory, "evidence", []) or []:
        sources.append(
            {
                "url": evidence.get("url", ""),
                "title": evidence.get("title", ""),
                "score": evidence.get("score", 0.0),
            }
        )
    store_query_answer(goal, summary, sources=sources)


def should_write(goal: str, summary: str) -> bool:
    return bool(goal.strip() and summary.strip())


def write_entry(goal: str, memory: "Memory", summary: str) -> None:
    manage_memory(goal, memory, summary)
