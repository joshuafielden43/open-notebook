# Design: Shaped reports for Open Notebook

**Customer:** Hobbyist nerd / adjacent self-hoster — home lab or small VPS, Open Notebook + worker, local or cheap chat models. Privacy-first. Already curates sources into notebooks. Wants **dense, trustworthy written memos** from that corpus without crawl farms, n8n, or multi-agent babysitting. Evening time budget.

**Edge:** report v1, `for_report`, jobs/pulse, podcast segment discipline, evidence-as-budget prompt, multi-model defaults.

**Roast:** average **8.7** (GO). See [shaped-report-roast.md](./shaped-report-roast.md).

**Amended:** post-critique (context scale, cohesion, partial resilience, local-model I/O, omit transparency); fine-tune (L7 small-N math, map concurrency, tolerant tag parse).

## Product promise

Open Notebook produces real **notebook-grounded reports** as AI notes. Shaping means: structure from evidence, section-scoped writes when needed, hard omit of fluff, optional mermaid — **not** web reinjection.

Two modes, one API family:

| Mode | When | Mechanism |
|------|------|-----------|
| **Standard** (default) | Always available | Today’s path: `for_report` → one chat call → AI note (evidence-as-budget prompt). Fast path. |
| **Deep** (opt-in flag `mode=deep` or UI toggle) | User asks for denser structure | Evidence-first multi-stage below. Costs more tokens/time; status labels on **one parent job**. |

Web crawl is never inside either mode. Thin notebooks stay thin (honest fail or short stocked note).

## Deep mode pipeline (STORM-shaped, notebook RM)

```text
per-source ledger map  →  merge/dedupe  →  outline (partition ≤6)  →  thesis from ledger
        → section jobs (scoped evidence + thesis + TOC)  →  assemble note (+ audit footer)
```

### Stage 1 — Ledger (L1): per-source map, not one corpus pass

**Problem:** 30–100 sources cannot fit a single extract call (overflow + needle-in-haystack on local models).

**Spec:**

1. **Map (parallel or batched):** For each source (and optionally each note) in the notebook, run a **bounded** extractive call on that document’s long context only (title + full_text / note body, capped per item). Emit rows as XML (see Format below), each with:
   - `source_ref` or `note_ref`
   - `title`
   - one or more `snippet` elements (verbatim / substring-backed; no free generative facts)
2. **Cap map work:** Max items processed (e.g. top N by recency or `for_report` pack order); skip empty bodies.
3. **Map concurrency throttle:** Bound concurrent map LLM calls so a local Ollama/vLLM box or small VPS is not flooded (e.g. notebooks with 50+ sources). Spec default: `asyncio.Semaphore` with **concurrency = 4** for local / small workers; raise only when the operator’s stack is known to be multi-slot (env or setting later). Never unbounded fan-out.
4. **Merge / dedupe:** Deterministic join of map outputs:
   - Collapse near-duplicate snippets (normalize whitespace; optional simple hash/Jaccard).
   - Prefer longer snippet when two refs collide on the same claim span.
   - Do **not** use a second “summarize the whole ledger” LLM if a rule-based merge suffices.
5. **Collapse (L6):** If merged ledger row count &lt; threshold (e.g. 5), fall back to **standard** one-shot or return clear “not enough concrete material” — no outline/section theater on empty inventory.

Status label: `ledger` (with subprogress `map k/n` when useful).

### Stage 2 — Outline + thesis (L2–L4)

- Outline is **compression of the merged ledger** only (not a free wish-list).
- Partition: each ledger item → at most one primary section; max-6 **merges** stocked clusters; unused unforced.
- **Hard omit early (L4):** proposed sections with no linked ledger rows are dropped **before** any section write.
- Thesis is produced **at outline time** from ledger themes (+ user instructions constrained by ledger), then frozen for all section jobs.

### Stage 3 — Section jobs

- Sequential by default (simpler cancel/pulse); optional small parallel batch later.
- Each call gets:
  - frozen **thesis**
  - full **outline TOC** (titles only)
  - **only** that section’s ledger allotment
  - under **L8**, either strict self-containment or a short preceding-section digest (see Contracts)
- Caps: ≤6 sections; mermaid 0–2 with syntax validate/drop.

### Stage 4 — Assemble

- Concatenate completed sections; append `## Sources` from ledger.
- Always append **audit footer** (see Delivery).
- Save one AI note (same as v1).

## Contracts

| ID | Rule |
|----|------|
| **L1 Extractive ledger** | Rows = source/note id + title + verbatim/substring-backed snippets. No free generative “facts.” Built by **per-item map + merge**, not a single corpus-wide extract. |
| **L2 Partition** | Each ledger item → at most one primary section. Max-6 **merges** stocked clusters; never throw away stocked items without merge. Unused unforced (no pad). |
| **L3 Thesis timing** | Thesis formed at outline from ledger themes (or user instructions constrained by ledger). Injected into every section write. |
| **L4 Hard omit early** | Drop unstockable sections at **outline** time — never burn a section LLM call then omit. |
| **L5 Density gate = offline ship proof** | Multi-stage **feature** ships only after dual-run metric on real notebooks beats or matches standard one-shot (concrete tokens/100w, zero zero-specific sections). **Not** dual-run on every user click. |
| **L6 Collapse** | If ledger items &lt; threshold (e.g. 5), deep mode auto-falls back to standard one-shot or returns clear “not enough concrete material.” |
| **L7 Partial resilience** | Each failed section is retried up to **2×** (transient only: rate limit, timeout, worker blip); then dropped with an audit footer line. Do **not** re-run ledger/outline for a single section failure. Cancel still aborts the parent. **Completion target** for a deep job with *N* planned sections (after L4 omit): **required successes = min(N − 1, ⌈0.75 × N⌉)** when *N* ≥ 2; when *N* = 1, require 1. Worked examples: *N*=2 → 2/2; *N*=3 → 2/3; *N*=4 → 3/4; *N*=6 → 5/6 (⌈0.75×6⌉=5, min(5,5)=5). Rationale: pure ≥75% fails a 3-section outline if one times out (2/3≈66.7%). If successes &lt; required, job fails permanently with measured stage detail (user can re-submit). |
| **L8 Section independence** | Default: **strict self-containment** — prompts forbid cross-references (“as above”, “Section 2…”) and ban re-introducing the global thesis at length; each section assumes a reader with only the TOC + that section’s evidence. Optional upgrade (same job flag or later): inject a **≤150-token digest of prior completed section titles + one-line claims** (not full prose) to reduce duplicate background. Never pass full prior section bodies (context bloat + drift). |

## Shared section write (prompt package)

| Field | Content |
|-------|---------|
| Thesis | One frozen paragraph from outline stage |
| TOC | Section titles in order |
| Evidence | This section’s ledger rows only (XML) |
| Prior digest | Empty under strict L8; or short title+claim list if digest mode |
| Output shape | Markdown body for this section only; optional mermaid fence if `diagram` |

## Format / schema (local-model reliability)

**Mandate simple delimited / XML-style tags** for intermediate model I/O — not deep nested JSON schemas — for 8B–14B Ollama-class models:

```xml
<ledger>
  <item ref="source:abc" title="…">
    <snippet>verbatim or tight extract…</snippet>
  </item>
</ledger>

<outline>
  <thesis>…</thesis>
  <section id="1" title="…" diagram="false">
    <item_ref>source:abc</item_ref>
  </section>
</outline>

<section id="1">
…markdown body…
</section>
```

**Parse with tolerant tag extraction, not strict DOM XML.** Smaller LLMs emit unescaped `&`, raw `<` inside markdown snippets, and broken nesting — `xml.etree.ElementTree` will `ParseError` on dirty output. Spec:

- Prefer **regex / tag-sliced** extraction (e.g. match `<snippet>(.*?)</snippet>` with `re.DOTALL`, same for `item`, `section`, `thesis`) or a **lenient HTML/XML** library (e.g. BeautifulSoup) if already in tree.
- Do **not** require well-formed document-wide XML for success.
- On total extract failure for a unit: one repair retry with a “close your tags / escape special chars in snippets” hint; then fail that unit under L7 (or skip map item).

## Delivery

One AI note (same as v1). Job status: `ledger (map k/n) | outline | section k/n | assemble`.

**Required audit footer** (plain markdown after `## Sources`, or a collapsed `## Report audit` section):

| Field | Example |
|-------|---------|
| Mode | deep |
| Ledger items | 42 (mapped from 18 sources, 3 notes; 6 deduped) |
| Outline sections planned / written / omitted | 5 / 4 / 1 |
| **Omitted (L4)** | `Implementation Benchmarks` — no direct evidence in notebook sources |
| **Dropped mid-run (L7)** | `Risks` — failed after 2 retries (timeout) |
| Model | id / name used |
| Fallback | none \| collapsed to standard (ledger &lt; 5) |

Omits must look intentional, not like a generation bug.

## Failure matrix (deep)

| Event | Behavior |
|-------|----------|
| Map item fails | Skip item; continue map; count in audit |
| Ledger &lt; threshold | L6 collapse to standard or hard fail with message |
| Outline parse fail | 1 repair retry; then fail job |
| Section fail | Up to 2 retries; then drop section; continue if remaining can still hit L7 target |
| Below L7 required successes | Fail job; no partial note (or optional future: partial note only if product flips this) |
| Cancel | Parent cancel; no orphan publishes |

**Default v1 deep:** if successes &lt; L7 required target → **no note** (fail closed). Avoids shipping half-reports that look authoritative. Audit details live in job error/status.

## What we steal (no install)

- STORM: pre-write then write; outline tree; section populate; citation objects
- Podcast: bounded segment jobs + shared structure
- ADR-008 lesson: **no** metric-driven auto-regen loops that pad
- Mermaid over Excalidraw for diagrams

## What we refuse

- `pip install knowledge-storm`
- n8n / SearXNG / Firecrawl as report brain
- Excalidraw
- Per-request dual-run cost tax
- Multi-agent discourse UI
- Single corpus-wide LLM extract over the whole notebook

## Implementation order

1. Keep improving standard path (packing + prompt) — measure residual falloff.
2. Per-source ledger map + merge/dedupe + XML parse.
3. Outline-from-ledger + L4 early omit + thesis + audit footer fields.
4. Deep section loop + L7/L8 + shared thesis (behind `mode=deep`).
5. Offline density gate suite on fixed notebooks before recommending deep.
6. Mermaid last.

## Success metrics

1. Standard path remains best default for thin/medium notebooks.
2. Deep path wins offline density gate on stocked notebooks before marketing as “better.”
3. Hobbyist can run deep with only existing worker + chat model.
4. No new services.
5. Large notebooks (30+ sources) complete ledger map without one mega-context extract.
6. Audit footer always explains omits/drops when deep succeeds.

## Stance

**Build deep as opt-in manufacturing; keep standard one-shot.** Shape over crawl. STORM is the map, not the package. Quiet Portfolio density for this customer means **better use of owned sources under hard structure**, not a second research OS.

## Critique trail

Architectural critique absorbed: L1 scale (per-source map), narrative cohesion (L8), worker partial failure (L7), local-model XML I/O, L4 omit transparency in the footer.

Fine-tune absorbed: L7 small-N completion formula `min(N−1, ⌈0.75×N⌉)`; map concurrency semaphore (default 4); tolerant regex/HTML tag parse over strict ElementTree.
