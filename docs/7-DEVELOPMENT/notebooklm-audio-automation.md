# NotebookLM Audio Overview — automation options (free / self-host only)

**Goal:** Unattended “sources → learning audio” without hand-driving Google NotebookLM.

**Not the goal:** Privacy branding, cloud SaaS, paid TTS APIs.

## Finding

There is no free drop-in NotebookLM Audio Overview with a real public automation API.

What exists that fits free/self-host:

| Option | Role |
|---|---|
| n8n + Speaches/Kokoro + LLM + ffmpeg | Full pipeline you own; templates for multi-speaker TTS exist |
| Mozilla document-to-podcast | OSS blueprint, two speakers, local models |
| Open Notebook + podcast-creator | This repo; jobs/API; reliability is the app shell around the library |

## Field evidence (GitHub / Reddit / web)

**podcast-creator (lfnovo):** small project; open bugs include hard max_tokens, thinking-token parse failures, Speaches/Kokoro choppy audio; open “add tests”; almost no independent Reddit/HN reliability discourse.

**Open Notebook issues:** generation/JSON failures, Speaches choppy (upstream), config/UX friction — users hit the pipeline through ON, not the library alone.

**n8n:** public multi-speaker / newsletter→dialogue workflows prove the pattern without podcast-creator.

**Reddit:** NotebookLM alternatives asked often; no consensus free app that “just works unattended.”

## podcast-creator alone

Generator (outline → transcript → TTS → combine). Not a product reliability system. Concurrent `configure()`, no progress hooks in stock API, structural script quality not owned in-library. Clip-level silence checks exist in current pin; structural ADR-008-style gates do not.

## Decision frame for this fork

Automate learning audio on our iron. Evaluate n8n pipeline vs hardening ON on that criterion only.
