# open_notebook_fork backlog (owned)

Project: Vikunja `open_notebook_fork` (id 50).
Maintained branch: `install`.
Posture: [PDR-003](decisions/PDR-003-fork-install-posture.md) — this fork is an install; [PDR-004](decisions/PDR-004-public-maintained-install.md) — it is public and upstream is read-only.

## Execution order

| Pri | Ticket | Title | Status |
|-----|--------|-------|--------|
| P0 | #1625 | Episode job_status stuck running after command failed | done (this branch) |
| P0 | #1624 | POLS: never blank-while-refreshing (podcasts scope) | done (placeholderData; wider audit later) |
| P0 | #1626 | Visible ambient activity for async jobs (ADR-004) | done (this branch) |
| P0 | #1604 | Entity-first episode row at submit | done (this branch) |
| P0 | #1603 | Content-by-reference job payloads | done (this branch) |
| P1 | #1605 | Shared heartbeat + cancel contract | done (this branch) |
| P1 | #1608 | Retry double-submit race | done (this branch) |
| P1 | #1606 | Allowlist POST /api/commands/jobs | done (this branch) |
| P1 | #1609 | Disk artifacts containment pattern | done (this branch) |
| P1 | #1623 | Install-now for opt-in runtimes | done (this branch) |
| P2 | #1610 | Route submits through CommandService | done (this branch) |
| P2 | #1590 | Speaker reconciliation tie-break | done (podcast-creator) |
| P2 | #1592 | Per-segment transcript parse retry | done (podcast-creator) |
| P3 | #1591 | Solo speaker persona invention QA | parked (honorifics dropped; last solo run clean — reopen only if self-interview shows up) |
| P3 | #1601 | Train-length / morning show templates | deferred on owner (Product B; not mode-enums on deep-dive) |

## Decision anchors

- ADR-004 (amended): background work must be **visible**, not only observable → #1626
- ADR-015: reaper birth certificate → landed (8d559f1 / f4d97a7)
- ADR-014 / PDR-003: install-local indexing and budget tuning allowed
- ADR-008 amended: no word-budget / auto-regen loops

## Update rule

When a ticket ships: mark done here, close in Vikunja with a close note, push branch.
