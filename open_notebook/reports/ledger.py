"""Extractive evidence ledger for deep-mode reports (design stage 1 / L1).

Internal machinery only — no API, UI, persistence, or deep-mode flag.

Coverage: every long-form block that :func:`for_report` would consider is
offered to exactly one extraction batch (no packing cutoff). Block text is
built with the same format helpers and load path as
:func:`open_notebook.context.assembly._collect_long_form_blocks` so the set
matches report packing; we re-derive here (instead of calling that private
helper alone) because the helper returns bare strings and ledger rows need
source/note ids.

L1 enforcement is deterministic post-filter (whitespace-normalized substring),
not a second model call (ADR-008: no regen loops).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from open_notebook.domain.notebook import Notebook, SourceInsight
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.text_utils import extract_text_content
from open_notebook.utils.token_utils import token_count

# Per-call *input* budget for batching blocks into extraction calls.
#
# Justification:
# - for_report packs under 80k total; we deliberately partition so every block
#   is offered (no pack cutoff). Each call still needs a hard ceiling so local
#   32k–128k models and cloud APIs stay reliable.
# - 24_000 tokens leaves headroom for the system/user prompt framing and a
#   generous completion budget while staying well under the 105k large_context
#   auto-upgrade threshold (so ledger map stays on the chosen chat model).
# - Oversized single blocks still form a solo batch (offered in full).
BATCH_INPUT_TOKEN_BUDGET = 24_000

# Completion budget for extractive JSON. The live probe inherited a default
# that clipped around ~850 tokens and truncated multi-item extracts — never
# silent-inherit that. 4096 is ample for ~dozens of short snippets per batch.
EXTRACTION_MAX_COMPLETION_TOKENS = 4096

_JSON_FENCE_RE = re.compile(
    r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```",
    re.DOTALL | re.IGNORECASE,
)

_SYSTEM_PROMPT = """\
You extract EXTRACTIVE evidence snippets from notebook documents.

Rules (strict):
- Output JSON only. No markdown prose outside JSON.
- For each document, copy 1–3 snippets VERBATIM from that document's text.
- Each snippet: 8–40 words, continuous substring of the source (no paraphrase).
- Prefer concrete facts, claims, numbers, definitions over fluff.
- If a document has no usable concrete text, omit it from the array.
- Never invent facts that are not in the document text.

Schema:
{
  "items": [
    {
      "item_id": "<exactly the id given for the document>",
      "item_title": "<title string>",
      "snippets": ["verbatim substring...", "..."]
    }
  ]
}
"""


@dataclass(frozen=True)
class LedgerRow:
    """One evidence row: a source or note with verified verbatim snippets."""

    item_id: str
    item_title: str
    snippets: List[str]


@dataclass(frozen=True)
class LedgerStats:
    """Coverage and filter metrics for one ledger build."""

    blocks_total: int
    batches: int
    rows_kept: int
    rows_dropped_unverified: int
    sources_covered: int
    pre_filter_verbatim_rate: float
    blocks_failed: int


@dataclass
class Ledger:
    """Extractive evidence inventory for a notebook."""

    rows: List[LedgerRow] = field(default_factory=list)
    stats: LedgerStats = field(
        default_factory=lambda: LedgerStats(
            blocks_total=0,
            batches=0,
            rows_kept=0,
            rows_dropped_unverified=0,
            sources_covered=0,
            pre_filter_verbatim_rate=0.0,
            blocks_failed=0,
        )
    )


@dataclass(frozen=True)
class _Block:
    """One long-form report block with identity (internal)."""

    item_id: str
    item_title: str
    text: str


def normalize_ws(text: str) -> str:
    """Collapse whitespace for substring checks and dedupe keys."""
    return " ".join((text or "").split())


def is_verbatim_substring(snippet: str, context: str) -> bool:
    """True when whitespace-normalized snippet is a substring of context."""
    snip = normalize_ws(snippet)
    if not snip:
        return False
    return snip in normalize_ws(context)


def partition_blocks_by_token_budget(
    blocks: Sequence[_Block],
    budget: int = BATCH_INPUT_TOKEN_BUDGET,
) -> List[List[_Block]]:
    """Partition blocks into batches by token budget.

    Every block is assigned to exactly one batch. A block that alone exceeds
    ``budget`` becomes a solo batch (still offered in full — no drops).
    """
    if not blocks:
        return []
    if budget <= 0:
        return [[b] for b in blocks]

    batches: List[List[_Block]] = []
    current: List[_Block] = []
    used = 0
    sep_tokens = token_count("\n\n")

    for block in blocks:
        cost = token_count(block.text)
        if not current:
            current = [block]
            used = cost
            if cost > budget:
                batches.append(current)
                current = []
                used = 0
            continue

        extra = sep_tokens
        if used + extra + cost <= budget:
            current.append(block)
            used += extra + cost
            continue

        batches.append(current)
        current = [block]
        used = cost
        if cost > budget:
            batches.append(current)
            current = []
            used = 0

    if current:
        batches.append(current)
    return batches


def _parse_extraction_payload(raw: str) -> List[Dict[str, Any]]:
    """Parse model JSON into a list of item dicts. Raises ValueError on failure."""
    text = clean_thinking_content(extract_text_content(raw) if raw else "").strip()
    if not text:
        raise ValueError("empty model response")

    candidates: List[str] = [text]
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        candidates.insert(0, fence.group(1).strip())
    # Brace-slice fallback
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    start_a = text.find("[")
    end_a = text.rfind("]")
    if start_a != -1 and end_a > start_a:
        candidates.append(text[start_a : end_a + 1])

    last_err: Optional[Exception] = None
    for cand in candidates:
        try:
            data = json.loads(cand)
        except json.JSONDecodeError as e:
            last_err = e
            continue
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            items = data.get("items")
            if isinstance(items, list):
                return [x for x in items if isinstance(x, dict)]
            # Single item object
            if "item_id" in data or "snippets" in data:
                return [data]
        last_err = ValueError(f"unexpected JSON shape: {type(data).__name__}")
    raise ValueError(f"JSON parse failed: {last_err}")


async def _collect_report_blocks(notebook: Notebook) -> List[_Block]:
    """Enumerate every long-form block for_report would consider, with ids.

    Imports format helpers from assembly and uses the same load path as
    ``_collect_long_form_blocks`` (full sources + notes + batch insights,
    skip item errors). Re-derive (not the bare-string helper alone) so each
    block carries ``item_id`` for ledger rows.
    """
    # Local import keeps assembly optional for pure unit tests of partition/filter.
    from open_notebook.context.assembly import (
        _format_note_long_block,
        _format_source_long_block,
    )

    sources = await notebook.get_sources(include_full_text=True)
    notes = await notebook.get_notes(include_content=True)

    source_ids = [source.id for source in sources if source.id]
    try:
        insights_by_source = await SourceInsight.get_for_sources(source_ids)
    except Exception as e:
        logger.warning(f"Error batch-fetching insights for ledger blocks: {e}")
        insights_by_source = {}

    blocks: List[_Block] = []
    for source in sources:
        try:
            source_context = await source.get_context(
                context_size="long",
                insights=insights_by_source.get(source.id or "", []),
            )
            text = _format_source_long_block(source, source_context)
            if not text:
                continue
            item_id = str(source.id) if source.id else f"source:unknown"
            title = source.title or "Untitled source"
            if isinstance(source_context, dict):
                title = source_context.get("title") or title
            blocks.append(_Block(item_id=item_id, item_title=str(title), text=text))
        except Exception as e:
            logger.warning(
                f"Skip source {getattr(source, 'id', '?')} in ledger collect: {e}"
            )

    for note in notes:
        try:
            note_context = note.get_context(context_size="long")
            text = _format_note_long_block(note, note_context)
            if not text:
                continue
            item_id = str(note.id) if note.id else "note:unknown"
            title = note.title or "Untitled note"
            if isinstance(note_context, dict):
                title = note_context.get("title") or title
            blocks.append(_Block(item_id=item_id, item_title=str(title), text=text))
        except Exception as e:
            logger.warning(
                f"Skip note {getattr(note, 'id', '?')} in ledger collect: {e}"
            )

    return blocks


def _format_batch_user_message(batch: Sequence[_Block]) -> str:
    parts: List[str] = [
        "Extract verbatim snippets from the following documents.\n"
        "Return JSON only matching the schema.\n"
    ]
    for i, block in enumerate(batch, start=1):
        parts.append(
            f"### Document {i}\n"
            f"item_id: {block.item_id}\n"
            f"item_title: {block.item_title}\n\n"
            f"{block.text}"
        )
    return "\n\n".join(parts)


def _filter_and_merge_items(
    proposed: Sequence[Dict[str, Any]],
    batch: Sequence[_Block],
    *,
    seen_snippets: set[str],
) -> Tuple[List[LedgerRow], int, int, int]:
    """Verify snippets against batch context; dedupe; return rows + counters.

    Returns:
        (rows_kept, snippets_proposed, snippets_verbatim, rows_dropped_unverified)
    """
    by_id = {b.item_id: b for b in batch}

    snippets_proposed = 0
    snippets_verbatim = 0
    rows_dropped = 0
    kept: List[LedgerRow] = []

    for item in proposed:
        item_id = str(item.get("item_id") or "").strip()
        item_title = str(item.get("item_title") or "").strip()
        raw_snips = item.get("snippets") or []
        if not isinstance(raw_snips, list):
            raw_snips = []

        # Resolve to a batch block — L1 checks that block only (never the whole
        # batch: cross-doc matches would attach foreign text to the wrong id).
        block = by_id.get(item_id)
        if block is None:
            for b in batch:
                if item_title and normalize_ws(b.item_title) == normalize_ws(
                    item_title
                ):
                    block = b
                    item_id = b.item_id
                    item_title = b.item_title
                    break
        if block is None:
            n = len([s for s in raw_snips if isinstance(s, str) and s.strip()])
            snippets_proposed += n
            rows_dropped += 1
            continue

        item_title = item_title or block.item_title
        check_ctx = block.text

        verified: List[str] = []
        any_proposed = False
        for snip in raw_snips:
            if not isinstance(snip, str):
                continue
            s = snip.strip()
            if not s:
                continue
            any_proposed = True
            snippets_proposed += 1
            if not is_verbatim_substring(s, check_ctx):
                continue
            snippets_verbatim += 1
            key = normalize_ws(s).lower()
            if key in seen_snippets:
                continue
            seen_snippets.add(key)
            verified.append(s)

        if verified:
            kept.append(
                LedgerRow(
                    item_id=item_id,
                    item_title=item_title or item_id,
                    snippets=verified[:3],
                )
            )
        elif any_proposed:
            rows_dropped += 1

    return kept, snippets_proposed, snippets_verbatim, rows_dropped


async def _extract_batch(
    batch: Sequence[_Block],
    model_id: Optional[str],
) -> List[Dict[str, Any]]:
    """One sequential extractive LLM call for a batch. Raises on parse failure."""
    from open_notebook.ai.runtime import chat_langchain

    payload = [
        SystemMessage(content=_SYSTEM_PROMPT),
        HumanMessage(content=_format_batch_user_message(batch)),
    ]
    # max_tokens → Esperanto language config (completion budget).
    chain = await chat_langchain(
        str(payload),
        model_id,
        "chat",
        max_tokens=EXTRACTION_MAX_COMPLETION_TOKENS,
    )
    response = await chain.ainvoke(payload)
    content = extract_text_content(response.content)
    return _parse_extraction_payload(content)


async def build_ledger(
    notebook: Notebook,
    model_id: Optional[str] = None,
) -> Ledger:
    """Build an extractive evidence ledger for ``notebook``.

    Sequential batch extraction, deterministic L1 post-filter, no retries on
    failed batches (log + skip; blocks counted in ``stats.blocks_failed``).

    When later wired into a job, wrap the await in ``long_job_pulse``; this
    function stays a plain awaitable.
    """
    blocks = await _collect_report_blocks(notebook)
    batches = partition_blocks_by_token_budget(blocks)

    all_rows: List[LedgerRow] = []
    seen_snippets: set[str] = set()
    snippets_proposed = 0
    snippets_verbatim = 0
    rows_dropped_unverified = 0
    blocks_failed = 0

    for i, batch in enumerate(batches):
        try:
            proposed = await _extract_batch(batch, model_id)
        except Exception as e:
            logger.warning(
                f"Ledger batch {i + 1}/{len(batches)} failed "
                f"({len(batch)} block(s)): {e}"
            )
            blocks_failed += len(batch)
            continue

        kept, prop, verb, dropped = _filter_and_merge_items(
            proposed,
            batch,
            seen_snippets=seen_snippets,
        )
        snippets_proposed += prop
        snippets_verbatim += verb
        rows_dropped_unverified += dropped
        all_rows.extend(kept)

    # Merge rows that share item_id across batches (same source split? rare)
    merged: Dict[str, LedgerRow] = {}
    for row in all_rows:
        existing = merged.get(row.item_id)
        if existing is None:
            merged[row.item_id] = row
            continue
        combined = list(existing.snippets)
        for s in row.snippets:
            key = normalize_ws(s).lower()
            if key not in {normalize_ws(x).lower() for x in combined}:
                combined.append(s)
        merged[row.item_id] = LedgerRow(
            item_id=row.item_id,
            item_title=existing.item_title or row.item_title,
            snippets=combined[:6],  # soft cap after merge
        )

    rows = list(merged.values())
    # Surreal source record ids are `source:…`; notes are `note:…`.
    sources_covered = sum(1 for r in rows if str(r.item_id).startswith("source:"))

    rate = (
        float(snippets_verbatim) / float(snippets_proposed)
        if snippets_proposed > 0
        else 0.0
    )

    stats = LedgerStats(
        blocks_total=len(blocks),
        batches=len(batches),
        rows_kept=len(rows),
        rows_dropped_unverified=rows_dropped_unverified,
        sources_covered=sources_covered,
        pre_filter_verbatim_rate=rate,
        blocks_failed=blocks_failed,
    )
    logger.info(
        f"Ledger built: blocks_total={stats.blocks_total} batches={stats.batches} "
        f"rows_kept={stats.rows_kept} sources_covered={stats.sources_covered} "
        f"pre_filter_verbatim_rate={stats.pre_filter_verbatim_rate:.2%} "
        f"blocks_failed={stats.blocks_failed}"
    )
    return Ledger(rows=rows, stats=stats)
