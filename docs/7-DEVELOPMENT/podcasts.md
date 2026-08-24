# Podcast Subsystem

How podcast generation is modeled and executed: the two-tier profile system, the model-registry references, and the deliberate no-auto-retry policy.

## Two-tier profile system (`open_notebook/podcasts/models.py`)

- **SpeakerProfile** — voice configuration: a `voice_model` (`record<model>` reference for TTS) plus 1–4 speakers (name, voice_id, backstory, personality). Individual speakers can override the profile's `voice_model`. Voice IDs must be unique within a profile (podcast-creator contract).
- **EpisodeProfile** — generation settings: `outline_llm` / `transcript_llm` (`record<model>` references), `language` (BCP 47, e.g. `pt-BR`), segment count (**3–10**, matching podcast-creator), briefing template. It references a SpeakerProfile by record ID.
- **PodcastEpisode** — a generated episode. Links content, profiles and the async job (`command` field → surreal-commands RecordID).

## Model registry references, not strings

Profile fields reference `Model` records instead of raw provider/model strings. At generation time `open_notebook.ai.runtime.resolve_model_config(model_id)` loads the Model, resolves its linked credential (or falls back to provider env config), and returns `(provider, model_name, config)` for podcast-creator.

The legacy string fields (`tts_provider`, `outline_provider`, …) that predated the registry were dropped by SQL migration 22 (#1107). The migration best-effort maps any still-unresolved profile to an existing `model` record (provider + name + type) before dropping the columns; profiles with no matching record stay unresolved — the UI already flags them as needing model selection and the user re-picks once. The old startup data migration (`open_notebook/podcasts/migration.py`) is gone.

## Profile snapshots

`PodcastEpisode` stores `episode_profile` and `speaker_profile` as **dicts (snapshots)**, not references. Editing a profile never retroactively changes past episodes — that's intentional. Corollary: deleting a profile does not cascade to episodes.

## Notebook scope

When generation is started with `notebook_id`, the episode stores `notebook` (`record<notebook>`). List filter: `GET /podcasts/episodes?notebook_id=…`. Free-form content jobs and pre-migration-27 rows leave `notebook` empty.

Frontend: `usePodcastEpisodes({ notebookId })` wraps `listEpisodes` (never pass the API method bare as `queryFn` — React Query would feed it the query context). The notebook page shows a scoped episode section; the global podcasts page has a notebook filter and deep link `/podcasts?notebook_id=…`.

## Job lifecycle and the retry policy

Generation runs as a `generate_podcast_command` job on the surreal-commands worker. The command is a thin adapter; work lives in `open_notebook.podcasts.episode_generation.run_episode_generation`:

- The module resolves model configs and credentials for **only the job's episode and speaker profiles** (single-job payload — not the whole catalog), then injects them into podcast-creator via `configure()`. Configure runs under a process lock; job-local settings are materialised before the lock is released so concurrent workers do not clobber each other during LLM/TTS.
- **`max_attempts: 1` — no automatic retries.** A mid-generation retry would create duplicate episode records (records are created during execution). Failed episodes are marked `failed` with an error message; retry is explicitly user-initiated via `POST /podcasts/episodes/{id}/retry` (keeps the failed row as evidence and preserves briefing suffix). Retry uses a compare-and-swap claim on `episode.command` so a double-click cannot enqueue two generate jobs against the same row (#1608).
- Stale `running` commands (no heartbeat past `OPEN_NOTEBOOK_STALE_COMMAND_MINUTES`, default **90**) are reaped to `failed` so operators can retry after a worker death. Long jobs use the shared `long_job_pulse` contract (`open_notebook.jobs`): podcast pipeline ~10s, source/transform/embed ~30s — each pulse touches `command.updated` and observes cooperative cancel. Only the real worker process (`OPEN_NOTEBOOK_IS_WORKER=1` or `surreal-commands-worker` argv) stamps `data/worker.heartbeat` every 30s — API imports of `commands` must not forge readiness. Cancel marks the command failed rather than leaving a zombie.
- Inside that one command execution, transcript generation has a separate,
  bounded quality policy ([ADR-008](decisions/ADR-008-podcast-quality-gate.md)).
  The deterministic gate checks explicit word budgets, substantial passage
  duplication, and outline restarts **before TTS**. A rejected transcript gets one automatic
  regeneration with the failure reasons added to the briefing; a second
  rejection fails the job instead of publishing known-bad audio. This does not
  create a second episode record.
- Status tracking: `get_job_status()` / `get_job_detail()` query surreal-commands and return `"unknown"` on failure rather than raising. Listing endpoints use the batched `get_job_details_for_commands()` so N episodes cost one status query, not N. List endpoints use a SQL summary projection (no full transcript/content/outline blobs).
- Near-silent / too-small combined audio is rejected after generation (fail
  closed via byte floor + ffmpeg volumedetect mean volume / duration).
- Stock briefings carry an explicit word budget so ADR-008's length check
  applies without custom profile text (migration 24).
- Notebook context assembly fails closed when sources cannot be loaded; it never
  submits a placeholder `"Notebook ID: …"` string. `PODCAST_CONTEXT_MAX_CHARS`
  defaults to 120000 and bounds peak assembly.

## Generator dependency (git pin)

Outline/transcript/TTS/combine run inside **`podcast-creator`**. This repo pins a reviewed git revision in `pyproject.toml` (`[tool.uv.sources]`) so strict structured-output schemas and clip hygiene stay fixed until the same fixes ship on PyPI. Open Notebook’s staged pipeline imports `podcast_creator.nodes` so quality gates run **before** TTS; there is no silent fall-back to black-box `create_podcast` if those nodes are missing.

## Environment variables

`PODCAST_CONTEXT_MAX_CHARS`, `PODCAST_TTS_MAX_TOKENS`, and `OPEN_NOTEBOOK_STALE_COMMAND_MINUTES` are documented in [environment-reference.md](../5-CONFIGURATION/environment-reference.md#podcast-generation). Non-default or set values are logged at job submit / job start.
