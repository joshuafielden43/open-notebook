# Plan: Full-corpus reports (ledger + STORM write)

**Status:** Plan for feedback — revised after deep critique (2026-07). Not a build ticket, not an ADR.

**Audience:** Anyone reviewing product/architecture direction before more implementation.

**Related:** [shaped-report-design.md](./shaped-report-design.md) (L1–L8), [shaped-report-roast.md](./shaped-report-roast.md) (GO 8.7), `open_notebook/reports/ledger.py` (L1 prototype).

**Date:** 2026-07

---

## 1. Problem (the only one this plan is about)

Open Notebook already holds the user’s stocked corpus. Report generation does **not** reliably use it.

**Measured fact (n8n notebook, live):** under the default ~80k token report pack, only on the order of **~15 of ~279** long-form blocks enter the generation prompt — roughly **5%** of the shelf. The rest never reaches the model. Order is effectively first-fit / load order, not user intent.

**What that means for a fat notebook (~300 sources):**

- The memo is written against a **silent sample**, not the notebook.
- The user **cannot usefully control** which 5% wins without hand-grooming inclusion — unacceptable at that scale.
- Improving prose quality on top of a packed string does not fix **coverage**.

**Non-problems (do not re-center the plan on these):**

- Whether UI offers quick vs deep *labels* (Deep is **opt-in**; settled in design — not reopened here).
- SurrealDB replacement (does not change model-read cost of a full shelf).
- Memo length for its own sake.

**One-line problem statement:**

Reports under-use a fat notebook by packing a small, uncontrolled subset of stocked blocks; the user will not groom hundreds of sources to compensate.

---

## 2. Vocabulary (do not blur these)

Calling “blocks offered to the mapper” **full-corpus coverage** overstates the result. Use:

| Term | Meaning |
|------|---------|
| **Eligible** | Body entered the mapping census (no pack cutoff as sole filter). |
| **Retained** | Verified evidence unit survived extract + L1 substring check. |
| **Allocated** | Evidence unit assigned to an outline section (or explicitly omitted with reason). |
| **Used** | Evidence unit actually supported generated prose (traceable in section write). |
| **Useful** | Evidence answered the **requested report intent** (instructions / question). |

Today’s prototype ledger only establishes **eligible** and a weak form of **retained** (1–3 generic snippets per doc). **Allocated / used / useful** do not exist until outline validation and section scoping ship. Success claims must name which layers they mean.

---

## 3. Success definition

On a notebook with on the order of **hundreds** of stocked sources/notes, the multi-stage path must:

1. **Eligible = full shelf** — every report-eligible body is in the census (no packing cutoff as the only filter). Collection failures and failed block IDs are counted, not silent.
2. **No groom tax** — no per-source include matrix to get a usable report.
3. **Write is inventory-constrained** — published memo is produced from retained units under a validated allocation, not from an opaque 80k pack as the only brain.
4. **Evidence-survival invariant** — every retained unit is either **allocated**, **explicitly omitted with a reason**, or the job **fails closed**. Outline is not trusted without a deterministic validator (unknown refs, empty sections, unassigned required units).
5. **Inspectable survival audit** — post-run: eligible / retained / allocated / used (and omit reasons); not only `sources_covered`.
6. **Durable jobs** — cache + checkpoints + resumability + **idempotent single-note publication** are prerequisites for shipping multi-stage as a product path, not “nice later.”
7. **Preflight** — operator sees rough cost/time/cache-hit estimate before paying for a full census on a fat shelf.
8. **Quality gate measures the right things** — not only “concrete tokens / 100 words.” Gate includes instruction relevance, nugget/evidence coverage, sentence support, provenance preservation, audit accuracy (see §10).

**Settled (do not re-litigate):**

- Multi-stage path is **opt-in Deep** (design). Standard pack path remains default.
- Insufficient section success under L7 → **fail closed** (no half-authoritative note as v1 default).

---

## 4. Fork that controls everything: what is the ledger?

**Unresolved in the first plan draft; now locked as a product decision:**

### Decision: intent-neutral inventory + intent-aware selection

| Layer | Intent? | Cache key | Job |
|-------|---------|-----------|-----|
| **Measurement (ledger)** | **No** — extract for reuse across reports | Content identity of body (+ insights if included), **not** report instructions | Rich retained units with provenance |
| **Selection / outline** | **Yes** — this report’s instructions | Ephemeral per job (or cache by content + instructions if we later want) | Rank, allocate, omit for *this* ask |

**Why not intent-aware extraction only:**

Three generic snippets optimized for one question cannot be both a **reusable cache** and support arbitrary later reports (“compare failure modes” vs “list integrations”). Intent in the map step forces either (a) re-map every report (destroy cache) or (b) wrong evidence for the next ask.

**Why not “1–3 generic snippets forever”:**

Eligible ≠ useful. Sampling **inside** each document recreates the 5% problem at chunk scale. The inventory must be **rich enough** that intent-aware selection has material to choose from (chunk- or claim-level units, not a handful of generic lines per source). Exact richness (nuggets per chunk, max units per source) is a design parameter; the plan requires it be **specified and bounded**, not left as “1–3 forever.”

**Claim-level provenance (required for cache + multi-source truth):**

Dedupe collapses **claim text**, not **supporting sources**. Same normalized claim from source A and B → one claim node, **refs = {A, B}**. Global first-source-wins that drops B is **wrong** (order-dependent cache, lost corroboration). Landed prototype behavior is a known defect relative to this plan.

---

## 5. Solution shape (two layers)

### Layer A — Measurement: full-shelf evidence ledger

**Role:** Census of stocked text. Replace pack-until-80k as the evidence source of truth for Deep.

**Mechanism (target contract; prototype is partial):**

- Enumerate the same long-form block set report assembly would consider.
- Partition **all** eligible blocks into extraction work (every block in exactly one map unit; no pack cutoff).
- Extractive model → candidate units; **L1** deterministic substring check on **owning** body only; no regen loops (ADR-008).
- Merge with multi-ref claim provenance (§4).
- Stats: eligible, retained, failed block IDs, collection failures — not only row counts.

**Live calibration (prototype, n8n):** ~279 eligible, ~43 batches, ~0.9M batch-body tokens, ~18 min sequential cloud chat — proves map cost scale; does **not** prove useful coverage or survival through write.

**What Layer A fixes:** silent **notebook-level** under-sampling.

**What it does not fix alone:** intent usefulness, allocation, used evidence, or a coherent memo.

### Layer B — Write: STORM-shaped manufacturing on the ledger

**Role:** Factorized write under partial context so the memo can be grounded in a wide inventory without one mega-prompt and without grooming 300 titles.

**STORM, stripped:** discover → outline → section populate → assemble.

**Here:** web discover/crawl **deleted**. Measurement is the ledger. STORM is **write control**, not inherited validation of Stanford STORM (which also used perspective discovery and question-driven research).

| Stage | Input | Output | Hard rule |
|-------|--------|--------|-----------|
| Merge | Raw extracts | Claim units + multi-refs | Rule-based; multi-source refs preserved |
| Intent select (optional pass) | Inventory + instructions | Working set for this report | May down-rank; must **not** silently drop without omit record if unit was required by policy |
| Outline | Working set | ≤ ~6 sections + assignments | Only known unit IDs; validated |
| Thesis | Working set + instructions | Frozen spine | Instructions constrained by inventory |
| Hard omit (L4) | Outline draft | Drop empty sections | No LLM section call on unstocked headings |
| **Allocation validator** | Outline + inventory | Accept / reject plan | Every unit: allocated \| omitted(reason) \| fail-closed |
| Section jobs | Thesis + TOC + **that section’s units only** | Markdown | Scoped; token-bounded inputs |
| Assemble | Sections + sources + audit | **One** AI note | Idempotent publish (see §8) |

**Evidence-survival invariant (minimum):**

After outline validation, there is no “orphaned retained unit” without an omit reason, and no section assignment that references a non-existent unit. Generation must not invent citations outside assigned units. Audit must be able to state survival counts without hand inspection.

---

## 6. One authoritative contract (reconcile plan / design / code)

Three authorities currently disagree. **This section is the reconciliation target.**

| Topic | Design (shaped-report) | Landed L1 prototype | **This plan (authoritative for next work)** |
|-------|------------------------|---------------------|-----------------------------------------------|
| Who enters map | Cap / top-N language remains in design § Stage 1 | All blocks, batched | **All eligible bodies** (no pack-style top-N); empty bodies skipped |
| Map shape | Per-item XML; concurrency 4 | Batched JSON; sequential | **Batched extract OK**; intermediate format = tolerant tags or JSON; **concurrency ≤4 default**, sequential acceptable for v1 |
| Completion / context | Local-model XML emphasis | 24k batch in, 4k completion fixed | **Hard bounds + adapt to runtime context** (§9); 24k is not a portable Ollama floor |
| Deep entry | Opt-in | N/A (no flag) | **Opt-in Deep** (settled) |
| Fail publication | L7 fail closed if below success target | N/A | **Fail closed** (settled) |
| Dedupe | Prefer longer on collision | First-source-wins drops peers | **Collapse text, keep all refs** |
| Cache | Not in design center | None | **Prerequisite** for multi-stage product path (§8) |

When design.md and this plan still diverge after implementation, **update design.md in the same change set** so L1–L8 stay the long-form contract and this plan stays the problem/success/ops spine. Do not leave a third silent variant in code.

---

## 7. Composition (end state)

```text
Stocked notebook
      │
      ▼
 run-level corpus manifest (eligible IDs + content hashes)
      │
      ▼
 build_ledger (map all eligible; L1; multi-ref merge)
      │  ◄── per-block cache by content hash
      ▼
 intent-aware selection (this report’s instructions)
      │
      ▼
 outline + thesis + L4 omit
      │
      ▼
 allocation validator ── fail closed if invariant broken
      │
      ▼
 section writes (scoped, bounded) ── L7 partial policy
      │
      ▼
 idempotent assemble → single AI note + survival audit
```

Standard one-shot pack path remains the **default cheap channel**. This plan is the **Deep** channel that refuses “answer fat-notebook coverage with a silent pack.”

---

## 8. Durability (parent-job contract — not optional)

Map cost is large (~0.9M tokens / ~18 min class on a fat notebook). Multi-stage **must not** ship as “retry the whole command five times from zero.”

**Prerequisites before calling Deep production-ready:**

| Concern | Requirement |
|---------|-------------|
| **Cache** | Per-block (or per-claim) extract cache keyed by content hash; map skips unchanged bodies |
| **Checkpoints** | Persist stage progress (manifest, ledger snapshot id, outline, completed section ids) under **one parent job** |
| **Resume** | Transient failure continues from last checkpoint; does not re-spend full census by default |
| **Cancel** | Parent cancel aborts cleanly; no orphan publishes |
| **Idempotent publish** | Note save + notebook attach is a single logical publish; retries must not create duplicate report notes (command `max_attempts` today is 5 on standard report — Deep needs a stricter publish contract) |
| **Preflight** | Estimate tokens/time and cache hit rate from manifest before map |

Without these, “full shelf” is a correct idea that operators will learn to fear.

---

## 9. Bounds (self-hosted target)

Hobbyist / local models are first-class. Fixed “24k in / 4k out” is not portable (Ollama often defaults small contexts on modest VRAM).

**Plan requirements:**

- Cap **per-item** body into the map (chunk long sources); oversize blocks must not unbounded-blow a single call.
- Bound **batch item count**, **completion tokens** as f(items), **outline input**, **section evidence input**.
- Bound **wall time** and memory expectations; adapt batch budget to **runtime context capability** when known.
- Document operator knobs; fail with a clear message when the notebook exceeds configured ceilings rather than thrashing.

---

## 10. Quality gate (offline ship proof)

L5-style dual-run remains: multi-stage does not market as “better” until offline proof on fixed real notebooks.

**Wrong sole metric:** concrete tokens per 100 words.

**Required dimensions (plan-level):**

| Dimension | Question |
|-----------|----------|
| Instruction relevance | Did the memo answer the asked report? |
| Nugget / evidence coverage | Did stocked material that should matter appear as retained→allocated→used? |
| Sentence support | Are claims supportable from assigned units? |
| Provenance preservation | Multi-ref and L1 intact through audit? |
| Audit accuracy | Do footer/stats match reality? |

Compare against standard pack path on the **same** notebooks and instruction set.

---

## 11. Privacy and trust (product + InfoSec)

- L1 proves **provenance** (snippet came from body), not **safety**. Imported source text remains untrusted prompt content.
- Deep **expands** cloud disclosure from “~80k pack” to “essentially the whole notebook” unless map stays local. Preflight and docs must say so.
- Prompt-injection containment for hostile sources is a known residual risk; do not claim “substring-verified ⇒ safe.”
- Duplicate-submit control on expensive Deep jobs (UI + server) belongs with durability.

---

## 12. Workstreams (plan-level, not tickets)

Ordered by dependency. Critique priorities folded in.

| # | Workstream | Outcome | Pri |
|---|------------|---------|-----|
| 0 | **L1 prototype** | All-eligible map, L1 verify, stats | Done (prototype; see gaps) |
| 0b | **Contract freeze** | Reconcile plan + design + code (§6); multi-ref dedupe; richer-than-1–3 inventory policy | Critical |
| 1 | **Intent model** | Intent-neutral cacheable measure + intent-aware selection API/shape (§4) | Critical |
| 2 | **Cache + checkpoints + resume + idempotent publish** | Durable parent job (§8) | Critical / High |
| 3 | **Manifest + coverage gate** | Run-level eligible set; min successful coverage; failed IDs | High |
| 4 | **Hard limits + runtime context adapt** | §9 | High |
| 5 | **Outline + thesis + L4 + allocation validator** | Survival invariant | Critical |
| 6 | **Section loop + assemble + audit** | Used evidence + survival footer | High |
| 7 | **Quality gate suite** | §10 metrics on fixed notebooks | High |
| 8 | **Product hook** | Opt-in Deep UI + preflight cost/time | Medium |
| 9 | **Privacy / preflight disclosure** | Whole-corpus disclosure copy; injection notes | Medium |

**Refuse:**

- Web crawl / multi-agent discourse / `knowledge-storm` package / new DB as part of this plan.
- Marketing “full-corpus” when only eligible+weak retained exist.

---

## 13. Risks (remaining)

- Outline remains load-bearing if validator is weak → silent loss after a “full” census.
- Richer inventory raises map cost; cache is mandatory to stay evening-budget honest.
- Local context ceilings force chunking → more map calls; must be visible in preflight.
- Section independence vs narrative glue (L8) still a tone tradeoff.
- Operator trust fails if audit only shows “sources covered.”

**Open questions still worth feedback (narrowed):**

1. Minimum **richness** of intent-neutral inventory (units per source / chunk policy) before selection is meaningful.
2. Map model class default: same as chat vs dedicated small/local.
3. Whether intent-aware selection is a separate LLM pass or folded into outline only.
4. Exact omit taxonomy (unallocated-by-intent, map-failed, L1-drop, section-dropped-L7, …).

**Removed as open (settled):** Deep opt-in vs always; fail-closed vs partial publish as v1 default.

---

## 14. What “done” looks like for this plan

- Fat-notebook Deep run: **eligible = full shelf**, retained units are **rich enough for intent**, allocation is **validated**, memo is **inventory-constrained**, audit reports **survival**, jobs are **durable**, preflight sets expectations.
- No silent 5% pack as the Deep evidence channel.
- No claim of “full-corpus coverage” that only means “we opened every file and took three random lines.”
- Plan, design, and code tell the **same** contract.

---

## 15. Critique trail

Deep critique (2026-07) accepted in substance: eligible≠covered; intent vs cache fork; survival invariant; three-authority conflict; durability; local bounds; multi-ref dedupe; quality-gate metrics; privacy disclosure; STORM as analogy not proof. Layer split kept. Web STORM / multi-agent / new DB still refused.

---

## 16. Pointers

| Doc / code | Why |
|------------|-----|
| This plan | Problem, vocabulary, intent/cache decision, survival, durability, reconciliation |
| [shaped-report-design.md](./shaped-report-design.md) | L1–L8 long-form; must be updated when §6 freezes |
| [shaped-report-roast.md](./shaped-report-roast.md) | Prior GO on dual-mode shape |
| `open_notebook/reports/ledger.py` | Landed prototype (gaps: multi-ref dedupe, richness, cache, intent) |
| `open_notebook/context/assembly.py` | Pack cutoff (standard path) |
| `commands/report_commands.py` | Retry/publish semantics to harden for Deep |

---

*Implementation work should cite §12 workstreams and use §2 vocabulary. Do not re-debate §1 without new measurements.*
