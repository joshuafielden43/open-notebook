"""Runtime vector-index provisioning (ADR-014) decision logic."""

from unittest.mock import patch

import pytest

from open_notebook.database import vector_index
from open_notebook.database.vector_index import (
    DIMENSION_ERROR_MARKER,
    KNN_CANDIDATES,
    _vector_search_sql,
    drop_vector_indexes,
    ensure_vector_indexes,
    reindex_if_drained,
)


class FakeRepo:
    """Records queries; answers dimension probes, INFO FOR TABLE, queue counts."""

    def __init__(self, dims_by_table=None, index_defs=None, in_flight=0):
        self.dims_by_table = dims_by_table or {}
        self.index_defs = index_defs or {}
        self.in_flight = in_flight
        self.queries = []

    async def __call__(self, query_str, vars=None):
        self.queries.append(query_str)
        if query_str.startswith("SELECT array::len"):
            table = query_str.split(" FROM ")[1].split(" ")[0]
            return [{"d": d} for d in self.dims_by_table.get(table, [])]
        if query_str.startswith("INFO FOR TABLE"):
            # The websocket SDK returns INFO results as a bare dict (the live
            # shape that broke on first deploy); exercise that path.
            table = query_str.rsplit(" ", 1)[1]
            return {"indexes": self.index_defs.get(table, {})}
        if "FROM command" in query_str:
            return [{"c": self.in_flight}]
        return []


def _patched(fake):
    return patch.object(vector_index, "repo_query", new=fake)


@pytest.mark.asyncio
async def test_no_embeddings_leaves_everything_alone():
    fake = FakeRepo()
    with _patched(fake):
        outcome = await ensure_vector_indexes()
    assert outcome.startswith("no-embeddings")
    assert not any("DEFINE" in q or "REMOVE" in q for q in fake.queries)


@pytest.mark.asyncio
async def test_mixed_dimensions_restores_scan_function():
    fake = FakeRepo(dims_by_table={"source_embedding": [768], "note": [1024]})
    with _patched(fake):
        outcome = await ensure_vector_indexes()
    assert "mixed-dimensions" in outcome
    overwrites = [q for q in fake.queries if "DEFINE FUNCTION OVERWRITE" in q]
    assert len(overwrites) == 1
    assert "vector::similarity::cosine" in overwrites[0]
    assert f"<|{KNN_CANDIDATES}|>" not in overwrites[0]
    assert not any("DEFINE INDEX" in q for q in fake.queries)


@pytest.mark.asyncio
async def test_uniform_dimension_defines_indexes_and_knn_function():
    fake = FakeRepo(dims_by_table={"source_embedding": [768], "source_insight": [768]})
    with _patched(fake):
        outcome = await ensure_vector_indexes()
    assert outcome == "indexed (dimension 768)"
    defines = [q for q in fake.queries if q.startswith("DEFINE INDEX")]
    assert len(defines) == 3
    assert all("MTREE DIMENSION 768 DIST COSINE" in q for q in defines)
    fn = next(q for q in fake.queries if "DEFINE FUNCTION OVERWRITE" in q)
    assert f"<|{KNN_CANDIDATES}|>" in fn
    assert "1 - vector::distance::knn()" in fn


@pytest.mark.asyncio
async def test_matching_existing_index_is_not_redefined():
    defs = {
        t: {
            f"idx_{t}_vec": f"DEFINE INDEX idx_{t}_vec ON {t} FIELDS embedding MTREE DIMENSION 768 DIST COSINE"
        }
        for t in ("source_embedding", "source_insight", "note")
    }
    fake = FakeRepo(dims_by_table={"source_embedding": [768]}, index_defs=defs)
    with _patched(fake):
        outcome = await ensure_vector_indexes()
    assert outcome == "indexed (dimension 768)"
    assert not any(q.startswith("DEFINE INDEX") for q in fake.queries)
    assert not any(q.startswith("REMOVE INDEX") for q in fake.queries)


@pytest.mark.asyncio
async def test_dimension_change_removes_then_redefines():
    defs = {
        "source_embedding": {
            "idx_source_embedding_vec": "DEFINE INDEX idx_source_embedding_vec ON source_embedding FIELDS embedding MTREE DIMENSION 1024 DIST COSINE"
        }
    }
    fake = FakeRepo(dims_by_table={"source_embedding": [768]}, index_defs=defs)
    with _patched(fake):
        await ensure_vector_indexes()
    removes = [q for q in fake.queries if q.startswith("REMOVE INDEX")]
    assert len(removes) == 1 and "source_embedding" in removes[0]
    assert any(
        q.startswith("DEFINE INDEX")
        and "source_embedding" in q
        and "DIMENSION 768" in q
        for q in fake.queries
    )


@pytest.mark.asyncio
async def test_drop_removes_indexes_and_restores_scan_function():
    fake = FakeRepo()
    with _patched(fake):
        await drop_vector_indexes()
    removes = [q for q in fake.queries if q.startswith("REMOVE INDEX")]
    assert len(removes) == 3
    fn = next(q for q in fake.queries if "DEFINE FUNCTION OVERWRITE" in q)
    assert "vector::similarity::cosine" in fn
    assert f"<|{KNN_CANDIDATES}|>" not in fn


@pytest.mark.asyncio
async def test_reindex_if_drained_noop_when_index_present():
    defs = {
        "source_embedding": {
            "idx_source_embedding_vec": "DEFINE INDEX idx_source_embedding_vec ON source_embedding FIELDS embedding MTREE DIMENSION 768 DIST COSINE"
        }
    }
    fake = FakeRepo(index_defs=defs)
    with _patched(fake):
        assert await reindex_if_drained() is None
    # Common case must stay cheap: a single INFO probe, no queue count.
    assert len(fake.queries) == 1


@pytest.mark.asyncio
async def test_reindex_if_drained_waits_for_queue():
    fake = FakeRepo(dims_by_table={"source_embedding": [1024]}, in_flight=5)
    with _patched(fake):
        assert await reindex_if_drained() is None
    assert not any("DEFINE" in q for q in fake.queries)


@pytest.mark.asyncio
async def test_reindex_if_drained_provisions_when_last():
    fake = FakeRepo(dims_by_table={"source_embedding": [1024]}, in_flight=1)
    with _patched(fake):
        outcome = await reindex_if_drained()
    assert outcome == "indexed (dimension 1024)"
    defines = [q for q in fake.queries if q.startswith("DEFINE INDEX")]
    assert len(defines) == 3
    assert all("DIMENSION 1024" in q for q in defines)


def test_dimension_error_marker_matches_live_error():
    # Live SurrealDB 2.6.5 rejection text, verified against the CT.
    live = "Incorrect vector dimension (3). Expected a vector of 768 dimension."
    assert DIMENSION_ERROR_MARKER in live


def test_knn_function_filters_after_aggregation():
    sql = _vector_search_sql(use_index=True)
    assert "similarity >= $min_similarity" in sql
    assert "LIMIT $match_count" in sql
    # KNN operator cannot take a parameter for K — must be a literal.
    assert f"<|{KNN_CANDIDATES}|>" in sql
