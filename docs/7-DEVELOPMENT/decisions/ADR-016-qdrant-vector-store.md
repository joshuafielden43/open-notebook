# ADR-016: Optional Qdrant vector store for source chunks

- **Status**: Accepted
- **Date**: 2026-08
- **Related**: [ADR-011](ADR-011-vector-index-investigation.md), [ADR-014](ADR-014-runtime-vector-index.md), [PDR-003](PDR-003-fork-install-posture.md)

## Context

Live profiling during a full embedding rebuild (326 items, 2026-08-11) showed
SurrealDB's single writer thread pinned at 100% while bulk chunk vectors
inserted — and, because reads queue behind that write pressure, every listing
endpoint in the UI degraded for the duration. The M4 embedding provider is the
throughput floor for ingest (~0.93 batches/s, measured), so moving vector
writes elsewhere does not make rebuilds meaningfully faster; it makes the app
**stay usable while ingesting**, which was the actual pain.

A Qdrant instance already runs in the lab (CT 207, v1.18.2). Qdrant's
tenancy unit is the collection — per-collection dimension and distance —
so the dimension-adaptivity problem that forced ADR-014's runtime MTREE
machinery does not exist there.

## Decision

`OPEN_NOTEBOOK_VECTOR_STORE=qdrant` (default: `surreal`) moves **source chunk
vectors only** to a Qdrant collection (`QDRANT_COLLECTION`, default
`open_notebook_chunks`, at `QDRANT_URL` with `QDRANT_API_KEY`):

- **Write path** (`embeddings/core.py::embed_source`): chunk vectors upsert to
  Qdrant with deterministic point ids (uuid5 of `source_id:order`); the
  `source_embedding` table stays empty. Note and insight embeddings remain
  single-record UPDATEs in SurrealDB — negligible write pressure, not worth
  the divergence.
- **Read path** (`domain/notebook.py::vector_search` →
  `vectorstore/search.py`): chunk KNN in Qdrant grouped per source, merged
  with `fn::vector_search` results (whose chunk block returns nothing over
  the empty table, and which still owns insights and notes), same output
  shape (`id, parent_id, title, similarity, matches`).
- **Cascade**: source delete best-effort removes the source's points; orphans
  are unreachable by the search join and replaced on re-embed, so a Qdrant
  outage never fails a delete.
- **Index provisioning** (`database/vector_index.py`): in qdrant mode the
  MTREE/dimension machinery skips `source_embedding` and manages only
  `source_insight` and `note`.
- **Backfill**: `python -m open_notebook.vectorstore.backfill
  [--delete-surreal]` copies existing rows (idempotent), verifies counts,
  and only then optionally clears the surreal table.

The client is ~150 lines of httpx against Qdrant's REST API — no SDK
dependency.

## Consequences

- Default installs are byte-identical to upstream behavior; the fork's CT
  deployment opts in via env.
- Dual-store consistency is eventual by design and bounded: points carry
  `source_id`, every re-embed replaces them, deletes are best-effort with a
  logged fallback.
- Upstream evolution of the surreal-native search path continues to merge
  cleanly; the qdrant branch is additive and env-gated.
- Rollback is `OPEN_NOTEBOOK_VECTOR_STORE=surreal` plus a re-run of the
  rebuild (or restoring the surreal rows if `--delete-surreal` hasn't run).
