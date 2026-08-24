# PDR-003: This fork is an install, not the product

- **Status**: Accepted
- **Date**: 2026-07
- **Related**: [PDR-002](PDR-002-provider-agnostic-core.md), [ADR-011](ADR-011-vector-index-investigation.md), [ADR-001](ADR-001-surrealdb.md)

## Context

This repository is a fork (joshuafielden43/open-notebook) run as **one deployment
with one operator**: fixed models (local 768-dimension embeddings, GLM on
outline/transcript, Kokoro TTS), fixed providers, one CT (proxmox02/CT208), one
user. Upstream's ADRs/PDRs encode *product* posture — portable multi-provider
core (PDR-002), zero-infra self-hosters, defaults that must work on any stack.

Sessions working on this fork have been treating those product defaults as
binding constraints. They are not: several ADRs explicitly distinguish the
shipped default from what an operator with a fixed stack may do (ADR-011:
"Operators who fix a single embedding dimension for an install may add a local
index"). Leaving that distinction implicit costs real optimization work and
re-litigates the same questions every session.

## Decision

**Where an upstream ADR/PDR distinguishes a product default from a per-install
choice, this fork takes the per-install choice.** Concretely:

- Embedding dimension is currently **768** for this install — an install fact,
  not a schema constant. Vector indexing is handled product-side by runtime
  provisioning ([ADR-014](ADR-014-runtime-vector-index.md), which superseded
  ADR-011), so no fork-local index carve-out is needed; this bullet stands as
  the pattern for future per-install choices.
- Provider portability (PDR-002) does not bind fork-local optimization. Code
  paths may assume the configured stack; they must not be *contributed upstream*
  in that form.
- Tuning that upstream leaves conservative (queue batching, context budgets,
  scan shapes) may be set for this deployment's measured workload.
- Runtime dependencies are immutable image contents. Docling is baked into the
  fork image; Crawl4AI is a separate Tailnet service. No API, worker command, or
  boot hook installs Python packages into a running container.
- Service ingress is Tailnet-only. Native browser media routes may remain
  app-layer unauthenticated because media elements cannot attach bearer headers,
  but host binds and firewall exposure must remain loopback plus Tailscale.

**What this does not change:**

- Upstream ADR *decisions* (worker model ADR-004, migration granularity
  ADR-006, parked adapters ADR-013) still hold — they record engineering
  judgment, not portability posture.
- Divergence must be deliberate and minimal: prefer additive local migrations
  and config over rewrites, to keep upstream merges cheap.
- Anything sent upstream as a PR must satisfy upstream's posture, including
  PDR-002.

## Alternatives considered

- **Rewrite/retire the upstream ADRs in the fork** — destroys the record of why
  upstream is shaped as it is, and makes merges noisy.
- **Keep treating product defaults as binding** — the status quo this PDR ends:
  it costs sanctioned optimizations (e.g. the 768-dim index) for no benefit to
  a single-operator install.

## Consequences

- Agents and contributors working this fork check: is the constraint an
  engineering decision (binds) or product portability posture (per-install
  choice applies)?
- Fork-local migrations may assume dimension 768; a change of embedding model
  dimension requires revisiting those migrations explicitly.
- Architecture reviews should stop surfacing "blocked by ADR" for items
  ADR-011-style carve-outs already permit.
