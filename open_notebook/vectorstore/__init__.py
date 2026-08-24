"""Vector store selection (ADR-016).

``OPEN_NOTEBOOK_VECTOR_STORE=qdrant`` moves source-chunk vectors out of
SurrealDB (whose single writer thread makes bulk ingest degrade every read)
into a Qdrant collection. Default remains ``surreal`` — upstream behavior,
zero new infrastructure.
"""

from open_notebook.vectorstore.qdrant import (
    chunk_point_id,
    collection_name,
    count_points,
    count_source_points,
    delete_source_points,
    ensure_collection,
    facet_source_counts,
    qdrant_enabled,
    search_points,
    upsert_chunks,
    vector_store_backend,
)
from open_notebook.vectorstore.search import vector_search_qdrant

__all__ = [
    "chunk_point_id",
    "collection_name",
    "count_points",
    "count_source_points",
    "delete_source_points",
    "facet_source_counts",
    "ensure_collection",
    "qdrant_enabled",
    "search_points",
    "upsert_chunks",
    "vector_store_backend",
    "vector_search_qdrant",
]
