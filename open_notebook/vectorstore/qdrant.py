"""Thin async Qdrant REST client — httpx only, no SDK dependency (ADR-016).

Exactly what the fork needs: ensure a collection, upsert chunk points,
delete a source's points, KNN search, count. Point ids are uuid5 of
``source_id:order`` so re-embedding a source overwrites its points instead
of duplicating them.
"""

from __future__ import annotations

import os
import uuid
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from open_notebook.exceptions import ConfigurationError

DEFAULT_COLLECTION = "open_notebook_chunks"


def vector_store_backend() -> str:
    """Configured vector backend: "surreal" (default) or "qdrant"."""
    value = os.getenv("OPEN_NOTEBOOK_VECTOR_STORE", "surreal").strip().lower()
    return "qdrant" if value == "qdrant" else "surreal"


def qdrant_enabled() -> bool:
    return vector_store_backend() == "qdrant"


def collection_name() -> str:
    return os.getenv("QDRANT_COLLECTION", DEFAULT_COLLECTION).strip() or (
        DEFAULT_COLLECTION
    )


def chunk_point_id(source_id: str, order: int) -> str:
    """Deterministic point id so upserts replace rather than duplicate."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{order}"))


def _base_url() -> str:
    url = os.getenv("QDRANT_URL", "").strip().rstrip("/")
    if not url:
        raise ConfigurationError(
            "QDRANT_URL is not set but OPEN_NOTEBOOK_VECTOR_STORE=qdrant"
        )
    return url


def _headers() -> Dict[str, str]:
    key = os.getenv("QDRANT_API_KEY", "").strip()
    return {"api-key": key} if key else {}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=_base_url(), headers=_headers(), timeout=30.0)


async def ensure_collection(dimension: int) -> None:
    """Create the chunk collection if absent; existing collections are kept.

    Dimension is a per-collection property in Qdrant, so this never conflicts
    with other tenants of the same server.
    """
    async with _client() as client:
        existing = await client.get(f"/collections/{collection_name()}")
        if existing.status_code != 200:
            created = await client.put(
                f"/collections/{collection_name()}",
                json={"vectors": {"size": dimension, "distance": "Cosine"}},
            )
            created.raise_for_status()
        # Keyword payload index on source_id: required for faceting (the
        # embedded indicators) and it accelerates the delete/count filters.
        # Idempotent — re-creating an existing index is a tolerated no-op.
        index = await client.put(
            f"/collections/{collection_name()}/index",
            json={"field_name": "source_id", "field_schema": "keyword"},
        )
        if index.status_code >= 400:
            logger.debug(
                f"source_id payload index create returned {index.status_code} "
                "(already exists is fine)"
            )


async def upsert_chunks(
    source_id: str,
    chunks: List[str],
    embeddings: List[List[float]],
    start_order: int = 0,
) -> int:
    """Upsert one point per chunk; returns the number of points written.

    ``start_order`` lets batched callers keep chunk orders (and so point ids)
    globally consecutive for the source.
    """
    points = [
        {
            "id": chunk_point_id(source_id, order),
            "vector": embedding,
            "payload": {"source_id": source_id, "order": order, "content": chunk},
        }
        for order, (chunk, embedding) in enumerate(
            zip(chunks, embeddings), start=start_order
        )
    ]
    async with _client() as client:
        response = await client.put(
            f"/collections/{collection_name()}/points",
            params={"wait": "true"},
            json={"points": points},
        )
        response.raise_for_status()
    return len(points)


async def delete_source_points(source_id: str) -> None:
    """Remove every point belonging to a source (re-embed or source delete)."""
    async with _client() as client:
        response = await client.post(
            f"/collections/{collection_name()}/points/delete",
            params={"wait": "true"},
            json={
                "filter": {
                    "must": [{"key": "source_id", "match": {"value": source_id}}]
                }
            },
        )
        # A missing collection means there is nothing to delete.
        if response.status_code == 404:
            return
        response.raise_for_status()


async def search_points(
    vector: List[float], limit: int, min_score: Optional[float] = None
) -> List[Dict[str, Any]]:
    """KNN search; returns raw hits [{id, score, payload}, ...]."""
    body: Dict[str, Any] = {"vector": vector, "limit": limit, "with_payload": True}
    if min_score is not None:
        body["score_threshold"] = min_score
    async with _client() as client:
        response = await client.post(
            f"/collections/{collection_name()}/points/search", json=body
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return response.json().get("result", [])


async def count_points() -> int:
    """Exact point count in the chunk collection (0 when absent)."""
    async with _client() as client:
        response = await client.post(
            f"/collections/{collection_name()}/points/count",
            json={"exact": True},
        )
        if response.status_code == 404:
            return 0
        response.raise_for_status()
        return int(response.json().get("result", {}).get("count", 0))


async def count_source_points(source_id: str) -> int:
    """Point count for one source (0 when the collection is absent)."""
    async with _client() as client:
        response = await client.post(
            f"/collections/{collection_name()}/points/count",
            json={
                "exact": True,
                "filter": {
                    "must": [{"key": "source_id", "match": {"value": source_id}}]
                },
            },
        )
        if response.status_code == 404:
            return 0
        response.raise_for_status()
        return int(response.json().get("result", {}).get("count", 0))


async def facet_source_counts(limit: int = 10_000) -> Dict[str, int]:
    """Per-source point counts in one call (facet on the source_id payload).

    Backs the sources list's "embedded" indicator without a count query per
    row. Empty dict when the collection does not exist yet.
    """
    async with _client() as client:
        response = await client.post(
            f"/collections/{collection_name()}/facet",
            json={"key": "source_id", "limit": limit, "exact": True},
        )
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        hits = response.json().get("result", {}).get("hits", [])
        return {
            str(hit["value"]): int(hit.get("count", 0))
            for hit in hits
            if hit.get("value")
        }
