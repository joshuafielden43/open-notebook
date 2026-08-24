"""Qdrant-mode vector search: chunk KNN in Qdrant + the rest in SurrealDB.

In qdrant mode the ``source_embedding`` table is empty, so the existing
``fn::vector_search`` still handles insights and notes correctly (its chunk
block simply returns nothing) and this module supplies the chunk hits,
grouped per source to match the function's output shape:
``{id, parent_id, title, similarity, matches}``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.vectorstore.qdrant import search_points

# fn::vector_search's KNN variant considers 100 candidates; mirror that so
# grouping by source doesn't starve the result list.
CHUNK_CANDIDATES = 100


async def _chunk_groups(
    embed: List[float], match_count: int, minimum_score: float
) -> List[Dict[str, Any]]:
    hits = await search_points(
        embed,
        limit=min(max(match_count * 4, match_count), CHUNK_CANDIDATES),
        min_score=minimum_score,
    )

    grouped: Dict[str, Dict[str, Any]] = {}
    for hit in hits:
        payload = hit.get("payload") or {}
        source_id = payload.get("source_id")
        if not source_id:
            continue
        entry = grouped.setdefault(source_id, {"similarity": 0.0, "matches": []})
        entry["similarity"] = max(entry["similarity"], float(hit.get("score", 0.0)))
        content = payload.get("content")
        if content:
            entry["matches"].append(content)

    if not grouped:
        return []

    titles: Dict[str, Any] = {}
    try:
        rows = await repo_query(
            "SELECT id, title FROM source WHERE id IN $ids",
            {"ids": [ensure_record_id(sid) for sid in grouped]},
        )
        titles = {str(row["id"]): row.get("title") for row in rows or []}
    except Exception as e:
        logger.warning(f"Chunk title hydration failed: {e}")

    return [
        {
            "id": source_id,
            "parent_id": source_id,
            "title": titles.get(source_id),
            "similarity": entry["similarity"],
            "matches": entry["matches"],
        }
        for source_id, entry in grouped.items()
    ]


async def vector_search_qdrant(
    embed: List[float],
    results: int,
    source: bool,
    note: bool,
    minimum_score: float,
) -> List[Dict[str, Any]]:
    """Merge Qdrant chunk groups with SurrealDB insight/note hits."""
    surreal_rows = await repo_query(
        "SELECT * FROM fn::vector_search($embed, $results, $source, $note, $minimum_score);",
        {
            "embed": embed,
            "results": results,
            "source": source,
            "note": note,
            "minimum_score": minimum_score,
        },
    )
    chunk_rows = await _chunk_groups(embed, results, minimum_score) if source else []
    merged = list(chunk_rows) + list(surreal_rows or [])
    merged.sort(key=lambda row: row.get("similarity") or 0.0, reverse=True)
    return merged[:results]
