# ADR-011: Vector search indexing deferred (investigation)

- **Status**: Superseded by [ADR-014](ADR-014-runtime-vector-index.md) (runtime dimension-adaptive indexing)
- **Date**: 2026-07
- **Related**: [ADR-001](ADR-001-surrealdb.md), deep-critique #1551

## Context

`fn::vector_search` (migrations through 9.surrealql) ranks rows with
`vector::similarity::cosine` over `source_embedding`, `source_insight`, and
`note` without an approximate nearest-neighbour index. At tens of thousands of
chunks this becomes a full-table scan per query. SurrealDB 2.x offers MTREE
indexes that require a fixed `DIMENSION` and distance metric.

## Decision

Do **not** ship a hard-coded MTREE index in the shared schema while embedding
dimensions remain provider/model-selected at runtime (portable multi-provider
core). Operators who fix a single embedding dimension for an install may add a
local index; the product default stays unindexed cosine with BM25 text search
alongside.

Revisit when Platform v-next (SurrealDB v3 migration) lands: evaluate HNSW/MTREE
tied to the active embedding model’s dimension, or a dedicated vector store
behind the same search API.

## Consequences

- Serious corpora may need higher latency budgets or fewer parallel Ask fans.
- Connection pooling and batched context load reduce *other* hot paths first.
- No false promise of ANN performance in docs until dimension is fixed per DB.
