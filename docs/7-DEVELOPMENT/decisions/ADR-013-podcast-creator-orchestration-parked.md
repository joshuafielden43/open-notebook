# ADR-013: Open Notebook orchestrates podcast-creator stages (adapter parked)

- **Status**: Accepted
- **Date**: 2026-07
- **Related**: [ADR-002](ADR-002-external-libraries.md), [podcasts.md](../podcasts.md), `open_notebook/podcasts/episode_generation.py`, `open_notebook/podcasts/generation.py`

## Context

Architecture review candidate #3 proposed a `PodcastGenerator` interface with
real and fake adapters so Open Notebook would stop importing
`podcast_creator.nodes` and process-global `configure()`. That would be a clean
ports-and-adapters seam. After deepening Episode Generation (#1) and Model
Resolution (#2), pin knowledge and the configure lock already concentrate in
`episode_generation` / `generation.py`. A second named adapter without a second
real client is speculative.

## Decision

**Park the PodcastGenerator adapter.** Open Notebook continues to own staged
orchestration (outline → transcript → quality → TTS → combine) against the
git-pinned podcast-creator revision. That is intentional until one of:

1. **Upstream** exposes a stable public staged interface (or quality-before-TTS
   land on PyPI) so Open Notebook can call one deep library entrypoint, or
2. **We need a second production adapter** (e.g. an in-memory generator used by
   real integration tests of the job module, not only unit fakes of helpers).

Until then, do not introduce a shallow wrapper that only renames the existing
orchestration.

## Alternatives considered

- **Adapter now** — real pin + fake. Rejected: one production adapter = hypothetical
  seam; Episode Generation already provides the job-level interface for tests and
  the worker command.
- **Never orchestrate; black-box `create_podcast` only** — rejected: quality-before-TTS
  and stage checkpoints require staged control (ADR-008, pin policy in podcasts.md).

## Consequences

- Future architecture reviews should not re-propose a PodcastGenerator module
  without evidence of (1) or (2) above.
- Pin upgrades and node import sites stay in `podcasts/generation.py` (and the
  Episode Generation module that calls it) — locality for pin churn.
- When upstream ships a public staged API, prefer thinning Open Notebook over
  inventing a parallel adapter layer first.
