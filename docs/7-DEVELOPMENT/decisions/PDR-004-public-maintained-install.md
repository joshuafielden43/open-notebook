# PDR-004: Public maintained install with a read-only upstream boundary

- **Status**: Accepted
- **Date**: 2026-08
- **Related**: [PDR-003](PDR-003-fork-install-posture.md)

## Context

This repository carries one operator's maintained Open Notebook installation on
top of `lfnovo/open-notebook`. Keeping it private obscured the actual sharing
posture without improving the integration model. The operator accepts that the
code and its history are public, but does not want routine maintenance to create
work, notifications, issues, or pull requests for the upstream maintainer.

## Decision

The maintained repository is public. Its `main` branch mirrors upstream and its
default `install` branch contains the maintained installation changes. A daily
GitHub Actions workflow fetches upstream, fast-forwards the mirror when safe,
merges upstream into `install`, validates the result, and publishes only to this
repository.

The upstream remote is fetch-only and has a deliberately invalid push URL. No
automation may push upstream, open upstream issues or pull requests, or contact
upstream maintainers. Any future upstream contribution requires a separate,
explicit decision by the operator.

## Alternatives considered

- **Keep the repository private** — adds ambiguity without serving the desired
  public development stance.
- **Use an ordinary GitHub fork and contribute routinely upstream** — creates a
  social and notification path the operator does not want.
- **Stop reconciling upstream** — leaves the installation on an avoidably stale
  codebase.

## Consequences

- Source, commit history, Actions history, and logs are treated as public.
- Secrets and deployment data must never enter commits or CI output.
- The approved `claude-config/git-hardening` hook is checked in at
  `.githooks/pre-commit`, enabled through `core.hooksPath`, and self-tested by
  CI. GitHub secret-scanning push protection remains enabled.
- Upstream reconciliation remains automatic and failures create an issue in
  this repository in addition to GitHub's workflow notification.
- `install`, not an old feature branch, is the maintained deployment line.
