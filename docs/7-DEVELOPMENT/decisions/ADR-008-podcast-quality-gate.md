# ADR-008: Reject and repair structurally bad podcast transcripts

- **Status**: Amended 2026-07 — word-range checks are **retired** (commit
  a29ca16, migrations 25/26). Episode length is steered by segments + profile
  briefing, never enforced post-hoc; word enforcement produced padded or
  truncated scripts. The deterministic structural gate remains: repeated-
  passage and outline-restart detection gets one bounded regeneration attempt,
  then fails with measured reasons. Do not reintroduce word budgets without a
  new record.
- **Date**: 2026-07
- **Related**: [ADR-002](ADR-002-external-libraries.md), [ADR-004](ADR-004-background-workers.md), [podcasts.md](../podcasts.md)

## Context

A provider can return schema-valid dialogue that is still unusable audio:
overlong, substantially repetitive, or repeatedly restarting earlier outline
segments. Successful JSON parsing and TTS completion do not establish podcast
quality, and asking a user to diagnose and rewrite prompts after routine runs
is not an acceptable operating model.

## Decision

The Open Notebook job boundary applies deterministic transcript checks before
publishing an episode. A rejected transcript gets one automatic regeneration
with concise machine-generated repair instructions. If the second attempt also
fails, the job fails with the measured reasons instead of publishing audio
known to violate its contract. The command itself remains `max_attempts: 1`, so
one user request creates one episode record.

## Alternatives considered

- **Prompt changes only** — improve the average result but cannot prevent a
  provider from occasionally ignoring the contract.
- **An LLM-as-judge** — adds cost, latency, provider dependence, and another
  nondeterministic failure mode to every run.
- **Publish and let the user retry** — preserves bad output as success and
  requires routine human intervention.

## Consequences

- Repeated passages and large backwards outline jumps are testable acceptance
  conditions; episode length is not a post-generation acceptance gate.
- A poor first draft can consume one extra transcript attempt; with creator
  versions lacking progress callbacks it can also consume one extra TTS pass.
- The gate deliberately handles structural defects, not subjective taste.
