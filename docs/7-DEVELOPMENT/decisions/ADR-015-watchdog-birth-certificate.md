# ADR-015: The stale-job reaper stamps newborns instead of killing them

- **Status**: Accepted
- **Date**: 2026-07
- **Related**: [ADR-004](ADR-004-background-workers.md), `open_notebook/jobs/recovery.py`, `commands/podcast_commands.py`

## Context

surreal-commands writes no timestamps on command rows: a job flips to
`running` with `created` and `updated` both absent, and `updated` stays NONE
until the job's own first `touch_command_heartbeat` (~300ms into a podcast
generation). The stale reaper's original predicate treated `updated IS NONE`
as stale, and the reaper runs opportunistically inside episode status reads —
which the UI triggers immediately after submitting a generation. Every
just-submitted job therefore raced its own first heartbeat against the
operator's status poll. Losing the race looked like operator action: the
generation's cancel-check saw the reaped status and aborted with
"was cancelled" (observed live: `command:h8xu454668y0zuuooc8y`, killed 300ms
after submission by the post-submit UI refetch).

The general failure shape: a liveness watchdog has two absence cases with
opposite meanings — *stopped reporting* (presumed dead) and *never reported
yet* (newborn). Collapsing them arms the watchdog against everything healthy
in the gap between birth and first report.

## Decision

**The staleness clock starts when the reaper first observes a job, never
before.** `fail_stale_running_commands` runs two phases:

1. **Birth certificate**: running/processing rows with `updated IS NONE` get
   `updated = time::now()` — stamped, not failed.
2. **Reap**: only rows whose (now always-present) `updated` lapsed the
   threshold are failed.

`is_stale_running` and `coerce_status_detail` treat a NONE heartbeat as
newborn, not dead, so status displays never paint a fresh job as failed
either. As a belt, `generate_podcast` heartbeats before any setup work.

A true zombie (worker died before ever heartbeating) still dies — one
threshold after the reaper first sees it instead of instantly. That delay is
the price of never killing a healthy newborn, and it is the right trade:
zombies are rare and patient; newborns are every single job.

## Alternatives considered

- **Keep NONE-is-stale, submit rows pre-stamped** — requires surreal-commands
  changes (upstream library, ADR-002 boundary) and still leaves any other
  producer's unstamped rows lethal.
- **Rate-limit or remove the opportunistic reaper** — shrinks the window
  without closing it; the race remains for any poll timed badly.
- **Heartbeat-first in the job only (belt without suspenders)** — the worker
  goes `running` before user code runs; a window survives no matter how early
  the job stamps.

## Consequences

- A worker that dies pre-heartbeat is reaped one threshold late (default 90
  minutes) instead of instantly. Retry stays available either way.
- Any future job type gets newborn protection for free; none needs to know
  the reaper exists.
- Regression tests pin both semantics: newborns survive the reaper and
  status coercion; the bulk reaper's phase A stamps and its phase B never
  matches `IS NONE`.
