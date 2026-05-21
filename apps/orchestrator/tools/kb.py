"""Knowledge base: cross-sprint memory of insights, competitors, lessons.

Phase 1 stores entries without embeddings — retrieval is by kind/sprint
filter and SQL ILIKE. Embeddings come later when we wire up Voyage or
sentence-transformers.
"""
from __future__ import annotations

import logging
from typing import Literal

import psycopg

from apps.orchestrator.config import settings
from apps.orchestrator.state import AgentRole

logger = logging.getLogger(__name__)

KBKind = Literal["market_signal", "competitor", "lesson", "insight"]


def _conn():
    return psycopg.connect(settings.postgres_dsn, autocommit=True)


def write(
    *,
    sprint_id: str | None,
    agent_role: AgentRole,
    kind: KBKind,
    title: str,
    body: str,
    metadata: dict | None = None,
) -> str:
    """Append an entry to the KB. Returns the inserted row's UUID."""
    metadata = metadata or {}
    with _conn() as conn:
        row = conn.execute(
            """
            INSERT INTO kb_entries (sprint_id, agent_role, kind, title, body, metadata)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (sprint_id, agent_role.value, kind, title, body, _json(metadata)),
        ).fetchone()
    if row is None:
        raise RuntimeError("KB insert returned no row")
    return str(row[0])


def search(
    *,
    kind: KBKind | None = None,
    text_query: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Retrieve KB entries. Phase 1: filter by kind and ILIKE on title/body."""
    clauses: list[str] = []
    params: list = []
    if kind is not None:
        clauses.append("kind = %s")
        params.append(kind)
    if text_query:
        clauses.append("(title ILIKE %s OR body ILIKE %s)")
        like = f"%{text_query}%"
        params.extend([like, like])

    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    params.append(limit)

    with _conn() as conn:
        rows = conn.execute(
            f"""
            SELECT id, sprint_id, agent_role, kind, title, body, metadata, created_at
            FROM kb_entries{where}
            ORDER BY created_at DESC
            LIMIT %s
            """,
            params,
        ).fetchall()

    return [
        {
            "id": str(r[0]),
            "sprint_id": r[1],
            "agent_role": r[2],
            "kind": r[3],
            "title": r[4],
            "body": r[5],
            "metadata": r[6],
            "created_at": r[7].isoformat(),
        }
        for r in rows
    ]


def _json(obj: dict) -> str:
    import json
    return json.dumps(obj, default=str)
