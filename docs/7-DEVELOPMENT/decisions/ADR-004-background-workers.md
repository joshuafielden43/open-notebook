# ADR-004: Long-running work runs on background workers

- **Status**: Accepted; amended 2026-07 — background work must be *visible*,
  not just observable (see Decision)
- **Date**: 2026-07 (retroactive record — decision dates from the async rework)
- **Related**: #381, [ADR-001](ADR-001-surrealdb.md), [podcasts.md](../podcasts.md), [ADR-015](ADR-015-watchdog-birth-certificate.md)

## Context

Open Notebook routinely processes work that takes seconds to minutes: ingesting and embedding large volumes of content, generating insights, producing podcast episodes. Users run it on machines of very different sizes — from small home servers to beefy workstations — so the same job can be fast on one deployment and slow on another. None of that may lock product usage: the API and UI must stay responsive while heavy work happens (async-first principle).

## Decision

**Long-running operations run as background jobs on a dedicated worker process, never inline in the API request cycle.** Submission is fire-and-forget (returns a job id immediately), status is observable (`/commands/jobs/{id}`), failures are explicit (permanent vs. retriable), and the UI polls or resumes rather than blocking.

**HTTP submit is not a free bus.** `POST /commands/jobs` is allowlist-locked
(empty by default — #1606). Product work is enqueued through typed routers
(podcasts, sources, embeddings, transformations) that validate fail-closed;
clients only need GET status/list on `/commands/jobs`.

**Amendment — visible, not merely observable.** Fire-and-forget describes the
transport, never the user experience. Every action that enqueues background
work must announce itself in the UI at submission (what was queued, where to
watch it) and remain discoverable while it runs; failures surface as loudly
as successes. An endpoint a client *could* poll does not satisfy this — the
default UI must actually show the work. Rationale from the field: a swallowed
request and a healthy queued job looked identical for a full day because
both were silent; a system whose broken and working states present the same
face has no working state a user can trust. Implementation tracked as the
ambient activity indicator (fork ticket #1626).

The *queue implementation* is deliberately an implementation detail behind this decision. Today it's [surreal-commands](https://github.com/lfnovo/surreal-commands) — chosen because it reuses the SurrealDB we already run, adding zero infrastructure for self-hosters ([ADR-001](ADR-001-surrealdb.md)). A move to Celery is under evaluation as part of the Platform v-next cluster (#381); if it happens, it replaces the implementation, not this decision.

## Alternatives considered

- **Run heavy work inline in API handlers** — simplest, but locks the product for minutes and ties job success to the HTTP connection.
- **FastAPI BackgroundTasks / asyncio tasks** — no persistence: jobs die with the process, no status tracking, no retry.
- **Celery + Redis from the start** — battle-tested, but two extra services for self-hosters before we knew we needed the features.

## Consequences

- A worker process is **required** for anything async to actually run (documented in the root `AGENTS.md`; forgetting it is a silent-queue failure mode).
- Features must be designed for the job model: idempotent-ish under retry, explicit permanent failures (`ValueError` → no retry), status exposed to the UI (e.g. podcasts use `max_attempts: 1` + an explicit retry endpoint).
- Deployment has one more moving part than a monolith — the price of not locking usage on slow machines.
