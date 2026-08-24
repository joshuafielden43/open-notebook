# ADR-014: Dimension-adaptive vector indexes provisioned at runtime

- **Status**: Accepted
- **Date**: 2026-07
- **Supersedes**: [ADR-011](ADR-011-vector-index-investigation.md)
- **Related**: [PDR-002](PDR-002-provider-agnostic-core.md), [PDR-003](PDR-003-fork-install-posture.md), [ADR-006](ADR-006-migration-granularity.md)

## Context

ADR-011 weighed vector indexing as a shared-schema decision. At that layer it
was right to decline: an MTREE index needs a fixed `DIMENSION`, embedding
models are provider/model-selected per install (PDR-002), and a hard-coded
dimension would break every install that chose differently. This record makes
a different choice at a different layer, with the benefit of a mechanism that
was not on the table then: every *install* does fix one dimension in practice
— whichever embedding model the operator picked — and the running system can
see it in the data even though the schema cannot know it in advance.

Measured on a live install (2,440 × 768-dim vectors, SurrealDB 2.6.5): the
scan-based `fn::vector_search` costs ~230ms+ per table walk and computes cosine
twice per row (WHERE and ORDER BY); an MTREE index with the KNN operator
answers the same top-K in ~35ms, with one-time build cost of ~5s.

## Decision

**Vector indexes are provisioned at runtime, after migrations, adapted to the
dimension found in the data.** On API startup, `ensure_vector_indexes()`
(`open_notebook/database/vector_index.py`):

1. Detects the distinct embedding dimensions across `source_embedding`,
   `source_insight`, `note`.
2. If exactly one dimension exists: (re)defines an
   `MTREE DIMENSION <d> DIST COSINE` index per table, and overwrites
   `fn::vector_search` with a KNN-operator variant that actually uses the
   index (`WHERE embedding <|100|> $query`; the operator requires a literal
   candidate count — parameters are a parse error).
3. If no embeddings exist yet, or dimensions are mixed (an install mid
   re-embed after switching models): keeps/restores the scan-based function,
   which tolerates heterogeneous rows. Indexing engages on a later startup
   once the data is uniform.

Provisioning failures are logged and never block startup; the scan function
from the migrations remains the working floor.

## Alternatives considered

- **Keep ADR-011's deferral** — simplest and always correct, at a steady-state
  scan cost on every query. We prefer paying at the rare switch instead.
- **Hard-code a dimension in shared migrations** — breaks any install using a
  different model; exactly what ADR-011 rightly rejected.
- **Index on model *configuration* instead of data** — config may not state
  dimensions and can disagree with historical rows; the data is the truth.

**Why this trade:** a model switch is a rare, deliberate event with a release
valve — the rebuild-embeddings flow — while search runs on every query,
constantly. Paying the adaptation cost at the switch (drop, re-embed,
re-index) rather than a scan cost in steady state is the right side of that
asymmetry.

Two hooks make the cycle self-healing with **no restart**: SurrealDB rejects
writes to an MTREE-indexed field of a different dimension (verified live:
"Incorrect vector dimension"), so (a) `rebuild_embeddings` drops the vector
indexes up front and (b) any embed job hitting that rejection drops them and
lets its retry proceed. When the embedding queue drains, the last embed job
re-provisions via `reindex_if_drained()`.

## Consequences

- Fresh installs and model switches converge to an indexed state without
  operator action; the interim state is correct, just slower.
- The "rebuild embeddings" button is the complete model-switch story: rebuild
  drops indexes, embeds at the new dimension, and re-indexes on drain.
- `<|100|>` caps KNN candidates per table; requests with `match_count` > 100
  return at most 100 per table. The search API's practical limits sit well
  below this.
- Migration 28 separately indexes the `reference`/`artifact` edge tables —
  those are dimension-independent and belong in the shared migrations.
