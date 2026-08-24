# ADR-012: One audio element per episode card

- **Status**: Accepted
- **Date**: 2026-07
- **Related**: [frontend.md](../frontend.md), [podcasts.md](../podcasts.md)

## Context

`EpisodeCard` rendered two native `<audio controls>` elements for the same
episode — one on the list card and one inside the Details dialog — both bound
to the same `src`. Native media elements do not share playhead or play/pause
state, so both could run at once on different cadences while the modal was
open. That is a product defect, not a browser quirk.

## Decision

**An episode exposes exactly one media element for playback.**

Today that element lives on the **card** (list row). The Details dialog is
metadata only (summary / outline / transcript) and must not mount a second
`<audio>`.

If dual visible players are needed again later, the **only** allowed fix is a
**single shared media element** (one ref / one component instance) that both
surfaces attach to — never two independent `<audio>` tags for the same
episode. Cloning the element is out of bounds.

## Alternatives considered

- **A — Card only (chosen now)** — drop the dialog player. Cheapest, fixes the
  desync, matches list-primary playback.
- **B — Dialog only while open** — unmount the card player when Details opens.
  Still two mount points over time; easy to regress into two concurrent
  elements.
- **C — One shared element (only future path if A is insufficient)** — one
  `<audio>` (or equivalent) owned by a small player component; UI chrome can
  appear in more than one place only if it still drives that single element.

## Consequences

- Details no longer restarts or doubles playback; the list player is the
  source of truth.
- Reintroducing a second `<audio src={same}>` without consolidating ownership
  is a regression against this ADR.
- Cross-episode exclusivity (pausing other cards when one plays) is out of
  scope here; if needed, it builds on the single-element ownership rule.
