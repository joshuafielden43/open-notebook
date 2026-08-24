"""Runtime vector-index provisioning (ADR-014).

Embedding dimension is chosen per install by whichever embedding model the
operator configures, so MTREE vector indexes cannot live in the shared
migrations (ADR-011's objection). Instead, after migrations run on startup,
this module detects the dimension actually present in the data and:

- (re)defines an MTREE COSINE index per vector table for that dimension, and
- overwrites ``fn::vector_search`` with a variant that uses the KNN operator
  (which is what actually hits the index — a plain ``WHERE cosine >= x``
  never does).

Installs with no embeddings yet keep the scan-based function from migration 9
and gain the indexes on a later startup. Mixed dimensions (an install mid
re-embed after switching models) fall back to the scan variant, which tolerates
heterogeneous rows.
"""

import re
from typing import Dict, Optional, Set

from loguru import logger

from open_notebook.database.repository import repo_query

VECTOR_TABLES = ("source_embedding", "source_insight", "note")


def _vector_tables() -> tuple:
    """Tables that carry vectors in SurrealDB under the active backend.

    In qdrant mode (ADR-016) source chunks live in Qdrant and the
    source_embedding table stays empty, so it needs neither an MTREE index
    nor a vote in dimension detection.
    """
    from open_notebook.vectorstore import qdrant_enabled

    if qdrant_enabled():
        return ("source_insight", "note")
    return VECTOR_TABLES

# The KNN operator requires a literal candidate count at definition time
# (a parameter is a parse error). 100 covers the search API's match_count
# ceiling; LIMIT $match_count still applies after aggregation.
KNN_CANDIDATES = 100

# SurrealDB rejects writes to an MTREE-indexed field whose vector dimension
# differs from the index (verified live: "Incorrect vector dimension (3).
# Expected a vector of 768 dimension."). Embed jobs match on this marker to
# self-heal after a model switch.
DIMENSION_ERROR_MARKER = "Incorrect vector dimension"


def _index_name(table: str) -> str:
    return f"idx_{table}_vec"


async def _data_dimensions() -> Set[int]:
    """Distinct embedding dimensions present across all vector tables."""
    dims: Set[int] = set()
    for table in _vector_tables():
        rows = await repo_query(
            f"SELECT array::len(embedding) AS d FROM {table} "
            "WHERE embedding != NONE GROUP BY d"
        )
        dims.update(row["d"] for row in rows if row.get("d"))
    return dims


async def _index_dimension(table: str) -> Optional[int]:
    """Dimension of the existing vector index on ``table``, or None."""
    rows = await repo_query(f"INFO FOR TABLE {table}")
    # The websocket SDK returns INFO results as a bare dict; the HTTP /sql
    # endpoint wraps them in a list. Accept both.
    if isinstance(rows, list):
        info: Dict = rows[0] if rows else {}
    else:
        info = rows or {}
    indexes: Dict[str, str] = info.get("indexes") or {}
    definition = indexes.get(_index_name(table))
    if not definition:
        return None
    match = re.search(r"DIMENSION (\d+)", definition)
    return int(match.group(1)) if match else None


def _knn_table_block(table: str, select_fields: str) -> str:
    # min_similarity is applied after aggregation: the KNN operator cannot be
    # combined with arbitrary extra predicates.
    return (
        f"SELECT {select_fields},"
        " (1 - vector::distance::knn()) AS similarity"
        f" FROM {table} WHERE embedding <|{KNN_CANDIDATES}|> $query"
    )


def _scan_table_block(table: str, select_fields: str) -> str:
    return (
        f"SELECT {select_fields},"
        " vector::similarity::cosine(embedding, $query) AS similarity"
        f" FROM {table} WHERE embedding != NONE"
        " AND array::len(embedding) = array::len($query)"
        " AND vector::similarity::cosine(embedding, $query) >= $min_similarity"
        " ORDER BY similarity DESC LIMIT $match_count"
    )


_SOURCE_EMBEDDING_FIELDS = (
    "source.id AS id, source.title AS title, content, source.id AS parent_id"
)
_SOURCE_INSIGHT_FIELDS = (
    "id, insight_type + ' - ' + (source.title OR '') AS title, content,"
    " source.id AS parent_id"
)
_NOTE_FIELDS = "id, title, content, id AS parent_id"


def _vector_search_sql(use_index: bool) -> str:
    block = _knn_table_block if use_index else _scan_table_block
    return f"""
DEFINE FUNCTION OVERWRITE fn::vector_search($query: array<float>, $match_count: int, $sources: bool, $show_notes: bool, $min_similarity: float) {{
    LET $source_embedding_search = IF $sources {{(
        {block("source_embedding", _SOURCE_EMBEDDING_FIELDS)}
    )}} ELSE {{ [] }};
    LET $source_insight_search = IF $sources {{(
        {block("source_insight", _SOURCE_INSIGHT_FIELDS)}
    )}} ELSE {{ [] }};
    LET $note_content_search = IF $show_notes {{(
        {block("note", _NOTE_FIELDS)}
    )}} ELSE {{ [] }};
    LET $all_results = array::union(
        array::union($source_embedding_search, $source_insight_search),
        $note_content_search
    );
    RETURN (SELECT id, parent_id, title, math::max(similarity) AS similarity,
        array::flatten(content) AS matches
        FROM $all_results
        WHERE id IS NOT NONE AND similarity >= $min_similarity
        GROUP BY id, parent_id, title
        ORDER BY similarity DESC LIMIT $match_count);
}};
"""


async def ensure_vector_indexes() -> str:
    """Detect the install's embedding dimension and provision indexes.

    Returns a short outcome string for logging. Never raises on the expected
    "not eligible" paths; callers should still guard with try/except so a
    provisioning failure cannot block API startup.
    """
    dims = await _data_dimensions()

    if not dims:
        return "no-embeddings (scan function retained)"

    if len(dims) > 1:
        # Mid re-embed after a model switch: the KNN variant would silently
        # drop rows of the wrong dimension, the scan variant tolerates them.
        await repo_query(_vector_search_sql(use_index=False))
        return f"mixed-dimensions {sorted(dims)} (scan function retained)"

    dim = dims.pop()
    for table in _vector_tables():
        existing = await _index_dimension(table)
        if existing == dim:
            continue
        if existing is not None:
            await repo_query(f"REMOVE INDEX IF EXISTS {_index_name(table)} ON {table}")
        logger.info(f"Defining vector index on {table} (dimension {dim})...")
        await repo_query(
            f"DEFINE INDEX IF NOT EXISTS {_index_name(table)} ON {table} "
            f"FIELDS embedding MTREE DIMENSION {dim} DIST COSINE"
        )

    await repo_query(_vector_search_sql(use_index=True))
    return f"indexed (dimension {dim})"


async def drop_vector_indexes() -> None:
    """Remove vector indexes and restore the scan-based fn::vector_search.

    Called when embeddings are about to change dimension (rebuild with a new
    model, or an embed write rejected with DIMENSION_ERROR_MARKER). The scan
    function stays correct over mixed dimensions; ensure_vector_indexes()
    re-indexes once the data is uniform again.
    """
    for table in _vector_tables():
        await repo_query(f"REMOVE INDEX IF EXISTS {_index_name(table)} ON {table}")
    await repo_query(_vector_search_sql(use_index=False))


async def reindex_if_drained() -> Optional[str]:
    """Re-provision once the embedding queue drains, if indexes are absent.

    Cheap enough to run at the end of every embed command: one INFO query in
    the common indexed case, one count query while a rebuild is draining. The
    in-flight threshold is 1 because the caller's own command is still
    'running' when this executes. Concurrent callers may both provision;
    every statement involved is idempotent.
    """
    if await _index_dimension(_vector_tables()[0]) is not None:
        return None
    rows = await repo_query(
        "SELECT count() AS c FROM command WHERE app = 'open_notebook'"
        " AND name IN ['embed_source', 'embed_note', 'embed_insight']"
        " AND status IN ['pending', 'new', 'queued', 'running'] GROUP ALL"
    )
    in_flight = int(rows[0].get("c") or 0) if rows else 0
    if in_flight > 1:
        return None
    return await ensure_vector_indexes()
