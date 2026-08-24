# Creating Podcasts - Turn Research into Audio

Podcasts let you consume your research as multi-speaker audio. This guide matches the current app: episode profiles, speaker profiles, async generation, and stage badges.

---

## Prerequisites

```
✓ Sources added and processed (not stuck Queued/Failed)
✓ Background worker running (Docker image includes it; from-source: make worker-start)
✓ At least one speaker profile and one episode profile
✓ Models configured for outline, transcript, and TTS (Settings)
✓ ffmpeg available (included in the official Docker image)
```

If the UI shows a **background worker looks offline** banner, jobs will stay pending until the worker is started.

---

## Quick start

```
1. Open Podcasts (or Generate Podcast from a notebook)
2. Create a speaker profile if you have none (voices + TTS model)
3. Create an episode profile if you have none (outline/transcript models, briefing, segment count, linked speaker profile)
4. Choose content (notebook sources/notes or paste)
5. Select episode + speaker profiles, name the episode, Generate
6. Watch stage badges: outline → transcript → tts → completed (or failed with an error)
7. Download the MP3 when completed
```

Typical wall time is several minutes depending on models and length. Generation is non-blocking.

---

## Profiles (required today)

The app does **not** ship a fixed gallery of named presets like “Debate Format” in the UI. Stock data may seed a few named profiles (for example tech discussion / solo expert / business panel) after migrations — use those if present, or create your own.

### Speaker profile

- Speaker names and roles
- TTS voice IDs for your provider
- TTS model assignment

### Episode profile

- Outline model + transcript model
- Default briefing (instructions for the episode; may include a word range such as `800 to 1400 words` for the quality gate)
- Segment count
- Linked speaker profile
- Language (curated BCP-47 list)

Without profiles, Generate shows that an episode profile is required first.

---

## What happens during generation

```
1. Job queued (needs a live worker)
2. Outline generated and checkpointed on the episode
3. Transcript generated and checkpointed
4. Structural quality checks (word range, repetition, outline restarts)
   - One automatic repair pass if the first draft fails
5. TTS + combine (only after quality checks pass)
6. Audio quality check (ffmpeg) — then published
```

Failed jobs show the error on the episode card. **Retry** reuses the same episode row (no twin) with the same inputs.

---

## Content selection

- Prefer a focused set of sources (roughly a handful of primary pieces)
- Empty notebooks or context that cannot be assembled will be rejected before queueing
- Optional briefing suffix adds one-off instructions for a single run

---

## Operator dials

See [environment reference](../5-CONFIGURATION/environment-reference.md) for:

- `OPEN_NOTEBOOK_STALE_COMMAND_MINUTES` — when a stuck job is marked failed
- `PODCAST_TTS_MAX_TOKENS` / context caps — provider limits
- Quality-related briefing patterns (ADR-008)

---

## Troubleshooting

| Symptom | What to check |
|--------|----------------|
| Episode stays Pending | Worker offline banner; `make worker-start` / Docker worker process |
| Failed: transcript quality | Briefing word range, reduce segments, clearer instructions, Retry |
| Failed: ffmpeg | Install ffmpeg (Docker image already has it) |
| Failed mid-TTS | Provider keys, TTS model, voice IDs on the speaker profile |
| Cancel does not stop spend immediately | Cancel is cooperative between stages; long TTS may finish the current clip batch |

---

## Related

- [Podcasts explained](../2-CORE-CONCEPTS/podcasts-explained.md)
- [Development: podcasts](../7-DEVELOPMENT/podcasts.md)
- [ADR-008 quality gate](../7-DEVELOPMENT/decisions/ADR-008-podcast-quality-gate.md)
