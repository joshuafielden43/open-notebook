"""Context assembly — single module for chat, source-chat, podcast, and report.

Purpose-specific entry points:

- :func:`for_chat` / :func:`build_notebook_context` — notebook chat inclusion config
- :func:`for_source_chat` / :func:`build_source_context` — one source + insights
- :func:`for_podcast` — long-form char-budgeted string for podcast generation
- :func:`for_report` — long-form token-budgeted string for report generation

Domain models expose only field getters; orchestration lives here.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from loguru import logger

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import (
    Note,
    Notebook,
    Source,
    SourceInsight,
)
from open_notebook.exceptions import DatabaseOperationError, NotFoundError
from open_notebook.utils.token_utils import token_count

# Default notebook-chat context budget (tokens). 0 = unlimited.
DEFAULT_CHAT_CONTEXT_MAX_TOKENS = 100_000
# Report generation: token-native budget (not char packing). 0 = unlimited.
DEFAULT_REPORT_CONTEXT_MAX_TOKENS = 80_000
# Sentinel so for_report(max_tokens=None) means unlimited, while omitting
# max_tokens means "use OPEN_NOTEBOOK_REPORT_CONTEXT_MAX_TOKENS".
_REPORT_BUDGET_UNSET: object = object()

# Source-chat budgeting (ported from upstream a7de90d, #1226).
SOURCE_TRUNCATION_NOTICE = (
    "\n\n[Source content truncated to fit the context token budget.]"
)
SOURCE_INSIGHT_BUDGET_RATIO = 0.2
TOKEN_PREFIX_VALIDATION_WINDOW = 16
_TOKENIZER_UNSET = object()


def chat_context_max_tokens() -> Optional[int]:
    """Token budget for for_chat; None means unlimited."""
    raw = os.getenv(
        "OPEN_NOTEBOOK_CHAT_CONTEXT_MAX_TOKENS",
        str(DEFAULT_CHAT_CONTEXT_MAX_TOKENS),
    )
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CHAT_CONTEXT_MAX_TOKENS
    if value <= 0:
        return None
    return value


def report_context_max_tokens() -> Optional[int]:
    """Token budget for for_report; None means unlimited."""
    raw = os.getenv(
        "OPEN_NOTEBOOK_REPORT_CONTEXT_MAX_TOKENS",
        str(DEFAULT_REPORT_CONTEXT_MAX_TOKENS),
    )
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_REPORT_CONTEXT_MAX_TOKENS
    if value <= 0:
        return None
    return value


# Default podcast long-form char budget. 0 / unset-as-zero = unlimited.
DEFAULT_PODCAST_CONTEXT_MAX_CHARS = 120_000


def podcast_context_max_chars() -> Optional[int]:
    """Char budget for for_podcast; None means unlimited.

    Single dial for API submit fail-fast and worker assembly (#1630).
    Env ``PODCAST_CONTEXT_MAX_CHARS`` (default 120_000; ``0`` = unlimited).
    """
    raw = os.getenv(
        "PODCAST_CONTEXT_MAX_CHARS",
        str(DEFAULT_PODCAST_CONTEXT_MAX_CHARS),
    )
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_PODCAST_CONTEXT_MAX_CHARS
    if value <= 0:
        return None
    return value


def _ensure_prefix(table: str, record_id: str) -> str:
    """Ensure a record ID carries its table prefix (`table:id`)."""
    prefix = f"{table}:"
    return record_id if record_id.startswith(prefix) else f"{prefix}{record_id}"


def format_source_context(context_data: Dict[str, Any]) -> str:
    """Render the exact source/insight Markdown supplied to Source Chat."""
    context_parts = []

    if context_data.get("sources"):
        context_parts.append("## SOURCE CONTENT")
        for source in context_data["sources"]:
            if isinstance(source, dict):
                context_parts.append(f"**Source ID:** {source.get('id', 'Unknown')}")
                context_parts.append(f"**Title:** {source.get('title', 'No title')}")
                full_text = source.get("full_text")
                if isinstance(full_text, str) and full_text.strip():
                    context_parts.append(f"**Content:**\n{full_text}")
                else:
                    context_parts.append(
                        "**Content:**\n[Source text is unavailable in this context.]"
                    )
                context_parts.append("")

    if context_data.get("insights"):
        context_parts.append("## SOURCE INSIGHTS")
        for insight in context_data["insights"]:
            if isinstance(insight, dict):
                context_parts.append(f"**Insight ID:** {insight.get('id', 'Unknown')}")
                context_parts.append(
                    f"**Type:** {insight.get('insight_type', 'Unknown')}"
                )
                context_parts.append(
                    f"**Content:** {insight.get('content', 'No content')}"
                )
                context_parts.append("")

    return "\n".join(context_parts)


def _rendered_source_context_tokens(
    source: Optional[Dict[str, Any]],
    insights: list[Dict[str, Any]],
    encoding: Any = _TOKENIZER_UNSET,
) -> int:
    """Count the same Markdown payload that Source Chat sends to its prompt."""
    rendered = format_source_context(
        {
            "sources": [source] if source is not None else [],
            "insights": insights,
        }
    )
    if encoding is _TOKENIZER_UNSET:
        return token_count(rendered)
    if encoding is None:
        return int(len(rendered.split()) * 1.3)
    return len(encoding.encode(rendered, disallowed_special=()))


def _truncate_source_to_token_budget(
    source_context: Dict[str, Any],
    max_tokens: int,
    insights: Optional[list[Dict[str, Any]]] = None,
    *,
    encoding: Any = _TOKENIZER_UNSET,
    source_tokens: Optional[list[int]] = None,
    source_is_over_budget: bool = False,
) -> tuple[Optional[Dict[str, Any]], bool]:
    """Truncate source text to a token budget while retaining source metadata.

    A near-maximal token-aligned prefix is retained with bounded validation for
    local BPE non-monotonicity. The truncation notice is part of the budget so
    downstream formatters never need a second size policy.

    Args:
        source_context: Long source context containing ``full_text``.
        max_tokens: Maximum tokens available for rendered source context.
        insights: Already selected insights that share the rendered budget.
        encoding: Reusable tokenizer, ``None`` for the word-count fallback, or
            the internal sentinel when the helper should resolve it itself.
        source_tokens: Precomputed tokens for ``full_text``.
        source_is_over_budget: Skip recounting the full rendered source when
            the caller has already established that it exceeds ``max_tokens``.

    Returns:
        The budgeted source context (or ``None`` when even an explicit notice
        cannot fit) and whether its text was truncated.
    """
    selected_insights = insights or []
    if not source_is_over_budget and (
        _rendered_source_context_tokens(
            source_context,
            selected_insights,
            encoding,
        )
        <= max_tokens
    ):
        return source_context, False

    full_text = source_context.get("full_text")
    if not isinstance(full_text, str) or not full_text.strip():
        return None, False

    def candidate(prefix: str) -> Dict[str, Any]:
        return {
            **source_context,
            "full_text": prefix + SOURCE_TRUNCATION_NOTICE,
        }

    # A very small budget may not even fit source metadata plus the notice.
    # Omit the item; the caller records an explicit status in context metadata.
    notice_only = candidate("")
    if (
        _rendered_source_context_tokens(
            notice_only,
            selected_insights,
            encoding,
        )
        > max_tokens
    ):
        return None, True

    def find_fitting_prefix(
        upper_bound: int,
        prefix_for_count,
        validation_window: int,
    ) -> Optional[Dict[str, Any]]:
        """Find a large fitting prefix with bounded candidate re-encoding."""
        evaluated: Dict[int, tuple[Dict[str, Any], bool]] = {}

        def evaluate(prefix_count: int) -> tuple[Dict[str, Any], bool]:
            if prefix_count not in evaluated:
                prefix = prefix_for_count(prefix_count)
                truncated = candidate(prefix)
                evaluated[prefix_count] = (
                    truncated,
                    bool(prefix.strip())
                    and _rendered_source_context_tokens(
                        truncated,
                        selected_insights,
                        encoding,
                    )
                    <= max_tokens,
                )
            return evaluated[prefix_count]

        low = 1
        high = upper_bound
        best_count = 0
        best: Optional[Dict[str, Any]] = None
        while low <= high:
            midpoint = (low + high) // 2
            truncated, fits = evaluate(midpoint)
            if fits:
                best_count = midpoint
                best = truncated
                low = midpoint + 1
            else:
                high = midpoint - 1

        # BPE counts can be locally non-monotonic. Check a bounded window past
        # the binary-search result without stopping at an over-budget candidate.
        validation_start = best_count + 1 if best_count else 1
        validation_end = min(
            upper_bound,
            best_count + validation_window,
        )
        for prefix_count in range(validation_start, validation_end + 1):
            truncated, fits = evaluate(prefix_count)
            if fits and prefix_count > best_count:
                best_count = prefix_count
                best = truncated

        return best

    try:
        if encoding is _TOKENIZER_UNSET:
            import tiktoken

            encoding = tiktoken.get_encoding("o200k_base")
        if encoding is None:
            raise ImportError
        if source_tokens is None:
            source_tokens = encoding.encode(full_text, disallowed_special=())

        def token_prefix(prefix_token_count: int) -> str:
            return encoding.decode_bytes(source_tokens[:prefix_token_count]).decode(
                "utf-8", errors="ignore"
            )

        best = find_fitting_prefix(
            min(len(source_tokens), max_tokens),
            token_prefix,
            TOKEN_PREFIX_VALIDATION_WINDOW,
        )
        if best is not None:
            return best, True
    except (ImportError, OSError):
        # Match token_count's offline fallback with deterministic word-boundary
        # prefixes. Its split-based estimate is monotonic, so binary search does
        # not need the tokenizer path's validation window.
        word_ends = [match.end() for match in re.finditer(r"\S+", full_text)]

        def word_prefix(word_count: int) -> str:
            return full_text[: word_ends[word_count - 1]]

        best = find_fitting_prefix(
            min(len(word_ends), max_tokens),
            word_prefix,
            0,
        )
        if best is not None:
            return best, True

    # A notice without any source characters is not useful source context.
    # Omit it so callers can report ``omitted_budget`` honestly.
    return None, True


def _trim_notebook_context_to_budget(
    context_data: Dict[str, list],
    max_tokens: Optional[int],
) -> Tuple[Dict[str, list], str]:
    """Drop trailing notes then sources until under max_tokens."""
    sources = list(context_data.get("sources") or [])
    notes = list(context_data.get("notes") or [])

    def joined() -> str:
        parts = [str(s) for s in sources] + [str(n) for n in notes]
        return "".join(parts)

    if max_tokens is None:
        text = joined()
        return {"sources": sources, "notes": notes}, text

    text = joined()
    total = token_count(text)
    dropped = 0
    while total > max_tokens and (notes or sources):
        if notes:
            notes.pop()
        elif sources:
            sources.pop()
        dropped += 1
        text = joined()
        total = token_count(text)

    if dropped:
        logger.warning(
            f"Notebook chat context trimmed: dropped {dropped} item(s) "
            f"to fit ~{max_tokens} tokens (now ~{total})"
        )
        # Operator-visible marker in the count string path
        text = (
            text + f"\n\n[context truncated: dropped {dropped} item(s) "
            f"to fit token budget ~{max_tokens}]"
        )
    return {"sources": sources, "notes": notes}, text


async def build_notebook_context(
    notebook: Notebook,
    context_config: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, list], str]:
    """Assemble source/note context for a notebook.

    With a config, each entry's status string decides inclusion: "not in"
    skips it, "insights" includes the short source context, "full content"
    includes the long context (notes only support "full content"). Without a
    config, every source and note is included with its short context.

    Failures on individual items are logged and skipped — one broken record
    never fails the whole request. Result is trimmed to
    OPEN_NOTEBOOK_CHAT_CONTEXT_MAX_TOKENS (default 100k; 0 = unlimited).

    Returns:
        ({"sources": [...], "notes": [...]}, concatenated str() of every
        included context dict — used for token/char counting).
    """
    context_data: Dict[str, list] = {"sources": [], "notes": []}
    total_content = ""

    if context_config:
        # Batch-load included sources once, then attach insights in one query
        # instead of Source.get + get_insights per id (N+1 on every chat turn).
        source_entries: list[tuple[str, str]] = []
        for source_id, status in context_config.get("sources", {}).items():
            if "not in" in status:
                continue
            if "insights" not in status and "full content" not in status:
                continue
            source_entries.append((_ensure_prefix("source", source_id), status))

        sources_by_id: dict[str, Source] = {}
        if source_entries:
            try:
                ids = [ensure_record_id(sid) for sid, _ in source_entries]
                rows = await repo_query(
                    "SELECT * FROM source WHERE id IN $ids",
                    {"ids": ids},
                )
                for row in rows or []:
                    try:
                        src = Source(**row)
                        if src.id:
                            sources_by_id[str(src.id)] = src
                            # Also index without re-prefix variance
                            bare = str(src.id).split(":", 1)[-1]
                            sources_by_id[bare] = src
                    except Exception as e:
                        logger.warning(f"Skip malformed source row: {e}")
            except Exception as e:
                logger.warning(f"Batch source fetch failed, falling back: {e}")

        # De-dupe while preserving objects (sources_by_id has dual keys).
        seen_src: set[str] = set()
        unique_source_ids: list[str] = []
        for src in sources_by_id.values():
            if not src.id or src.id in seen_src:
                continue
            seen_src.add(src.id)
            unique_source_ids.append(src.id)
        try:
            insights_by_source = await SourceInsight.get_for_sources(unique_source_ids)
        except Exception as e:
            logger.warning(f"Error batch-fetching source insights: {str(e)}")
            insights_by_source = {}

        for source_id, status in source_entries:
            try:
                source = sources_by_id.get(source_id) or sources_by_id.get(
                    source_id.split(":", 1)[-1]
                )
                if source is None:
                    try:
                        source = await Source.get(source_id)
                    except Exception:
                        continue
                if source is None:
                    continue

                size: Literal["short", "long"] = (
                    "long" if "full content" in status else "short"
                )
                source_context = await source.get_context(
                    context_size=size,
                    insights=insights_by_source.get(source.id or "", []),
                )
                context_data["sources"].append(source_context)
                total_content += str(source_context)
            except Exception as e:
                logger.warning(f"Error processing source {source_id}: {str(e)}")
                continue

        note_entries: list[str] = []
        for note_id, status in context_config.get("notes", {}).items():
            if "not in" in status:
                continue
            if "full content" not in status:
                continue
            note_entries.append(_ensure_prefix("note", note_id))

        notes_by_id: dict[str, Note] = {}
        if note_entries:
            try:
                ids = [ensure_record_id(nid) for nid in note_entries]
                rows = await repo_query(
                    "SELECT * FROM note WHERE id IN $ids",
                    {"ids": ids},
                )
                for row in rows or []:
                    try:
                        parsed_note = Note(**row)
                        if parsed_note.id:
                            notes_by_id[str(parsed_note.id)] = parsed_note
                            notes_by_id[str(parsed_note.id).split(":", 1)[-1]] = (
                                parsed_note
                            )
                    except Exception as e:
                        logger.warning(f"Skip malformed note row: {e}")
            except Exception as e:
                logger.warning(f"Batch note fetch failed, falling back: {e}")

        for note_id in note_entries:
            try:
                note: Optional[Note] = notes_by_id.get(note_id) or notes_by_id.get(
                    note_id.split(":", 1)[-1]
                )
                if note is None:
                    note = await Note.get(note_id)
                if not note:
                    continue
                note_context = note.get_context(context_size="long")
                context_data["notes"].append(note_context)
                total_content += str(note_context)
            except Exception as e:
                logger.warning(f"Error processing note {note_id}: {str(e)}")
                continue
    else:
        # Default behavior - include all sources and notes with short context
        sources = await notebook.get_sources()
        try:
            insights_by_source = await SourceInsight.get_for_sources(
                [source.id for source in sources if source.id]
            )
        except Exception as e:
            # Match the per-source fallback below: a hiccup fetching
            # insights shouldn't fail the whole context request.
            logger.warning(f"Error batch-fetching source insights: {str(e)}")
            insights_by_source = {}
        for source in sources:
            try:
                source_context = await source.get_context(
                    context_size="short",
                    insights=insights_by_source.get(source.id or "", []),
                )
                context_data["sources"].append(source_context)
                total_content += str(source_context)
            except Exception as e:
                logger.warning(f"Error processing source {source.id}: {str(e)}")
                continue

        notes = await notebook.get_notes()
        for note in notes:
            try:
                note_context = note.get_context(context_size="short")
                context_data["notes"].append(note_context)
                total_content += str(note_context)
            except Exception as e:
                logger.warning(f"Error processing note {note.id}: {str(e)}")
                continue

    return _trim_notebook_context_to_budget(context_data, chat_context_max_tokens())


async def build_source_context(
    source_id: str, max_tokens: Optional[int] = None
) -> Dict[str, Any]:
    """Assemble a single source's full text plus its insights.

    Used by the source-chat graph. If ``max_tokens`` is given, the source text
    is kept in full when it fits, then insights are retained in fetch order
    while space remains. When the source alone exceeds the budget, a bounded
    share is reserved for insights and the source is explicitly truncated into
    the remaining space instead of being dropped.

    Returns a dict with "sources", "notes" (always empty), "insights",
    "total_tokens", "total_items" and per-type counts in "metadata".
    """
    try:
        sources: list = []
        insights: list = []
        source_truncated = False
        source_text_status = "not_found"

        try:
            full_source_id = _ensure_prefix("source", source_id)
            source = await Source.get(full_source_id)
        except NotFoundError:
            source = None

        if source:
            insight_objects = await source.get_insights()
            source_context = await source.get_context(
                context_size="long",
                insights=insight_objects,
            )
            # Insights have their own budgeted items below. Keeping the nested
            # copy would double-count them while the formatter ignores it.
            source_context = {**source_context, "insights": []}
            budgeted_source: Optional[Dict[str, Any]] = source_context

            insight_items: list[Dict[str, Any]] = []
            for insight in insight_objects:
                insight_content = {
                    "id": insight.id,
                    "source_id": source.id,
                    "insight_type": insight.insight_type,
                    "content": insight.content,
                }
                insight_items.append(insight_content)

            try:
                import tiktoken

                encoding = tiktoken.get_encoding("o200k_base")
            except (ImportError, OSError):
                encoding = None

            rendered_source_tokens = _rendered_source_context_tokens(
                source_context,
                [],
                encoding,
            )

            if max_tokens is None:
                insights.extend(insight_items)
            elif rendered_source_tokens > max_tokens:
                # Large documents would otherwise consume the entire budget and
                # silently remove every insight. Reserve a bounded share for
                # insights, then give all unused space back to the source text.
                insight_budget = int(max_tokens * SOURCE_INSIGHT_BUDGET_RATIO)
                for insight_content in insight_items:
                    candidate_insights = [*insights, insight_content]
                    if (
                        _rendered_source_context_tokens(
                            None,
                            candidate_insights,
                            encoding,
                        )
                        > insight_budget
                    ):
                        continue
                    insights.append(insight_content)

                full_text = source_context.get("full_text")
                encoded_source = (
                    encoding.encode(full_text, disallowed_special=())
                    if encoding is not None and isinstance(full_text, str)
                    else None
                )

                budgeted_source, source_truncated = _truncate_source_to_token_budget(
                    source_context,
                    max_tokens,
                    insights,
                    encoding=encoding,
                    source_tokens=encoded_source,
                    source_is_over_budget=True,
                )
            else:
                for insight_content in insight_items:
                    candidate_insights = [*insights, insight_content]
                    if (
                        _rendered_source_context_tokens(
                            source_context,
                            candidate_insights,
                            encoding,
                        )
                        > max_tokens
                    ):
                        continue
                    insights.append(insight_content)

            if budgeted_source is None:
                source_text_status = "omitted_budget"
            else:
                full_text = budgeted_source.get("full_text")
                if isinstance(full_text, str) and full_text.strip():
                    source_text_status = (
                        "truncated" if source_truncated else "available"
                    )
                else:
                    source_text_status = "missing"

            if budgeted_source is not None:
                sources.append(budgeted_source)
            total_tokens = _rendered_source_context_tokens(
                budgeted_source,
                insights,
                encoding,
            )
        else:
            logger.warning(f"Source {source_id} not found")
            total_tokens = 0

        total_items = len(sources) + len(insights)
        logger.info(f"Built context with {total_items} items, {total_tokens} tokens")

        return {
            "sources": sources,
            "notes": [],
            "insights": insights,
            "total_tokens": total_tokens,
            "total_items": total_items,
            "metadata": {
                "source_count": len(sources),
                "note_count": 0,
                "insight_count": len(insights),
                "source_text_status": source_text_status,
                "source_truncated": source_truncated,
            },
        }
    except Exception as e:
        logger.error(f"Error building context: {str(e)}")
        raise DatabaseOperationError(f"Failed to build context: {str(e)}")


# Public purpose aliases (architecture: one Context module, clear interface).
for_chat = build_notebook_context
for_source_chat = build_source_context


def _format_source_long_block(
    source: Source,
    source_context: Any,
) -> Optional[str]:
    """Render a long-form markdown block for one source (+ insights)."""
    if isinstance(source_context, dict):
        title = source_context.get("title") or source.title or "Untitled source"
        full_text = source_context.get("full_text")
        insights = source_context.get("insights") or []

        content_parts: List[str] = []
        if full_text:
            content_parts.append(str(full_text))

        insight_lines: List[str] = []
        for insight in insights:
            if not isinstance(insight, dict):
                continue
            insight_content = insight.get("content")
            if not insight_content:
                continue
            insight_type = insight.get("insight_type") or "Insight"
            insight_lines.append(f"- {insight_type}: {insight_content}")

        if insight_lines:
            content_parts.append("Insights:\n" + "\n".join(insight_lines))

        content = "\n\n".join(content_parts).strip()
    else:
        title = source.title or "Untitled source"
        content = str(source_context).strip()

    if not content:
        return None
    return f"## Source: {title}\n\n{content}"


def _format_note_long_block(note: Note, note_context: Any) -> Optional[str]:
    """Render a long-form markdown block for one note."""
    if isinstance(note_context, dict):
        title = note_context.get("title") or note.title or "Untitled note"
        content = note_context.get("content")
        content = str(content).strip() if content else ""
    else:
        title = note.title or "Untitled note"
        content = str(note_context).strip()

    if not content:
        return None
    return f"## Note: {title}\n\n{content}"


def _pack_blocks_by_tokens(
    blocks: List[str],
    max_tokens: Optional[int],
) -> str:
    """Greedily pack whole blocks under a token budget."""
    if not blocks:
        return ""
    if max_tokens is None:
        return "\n\n".join(blocks)

    packed: List[str] = []
    used = 0
    separator = "\n\n"
    separator_tokens = token_count(separator)

    for block in blocks:
        cost = token_count(block)
        extra = separator_tokens if packed else 0
        if used + extra + cost <= max_tokens:
            packed.append(block)
            used += extra + cost
            continue
        if packed:
            break

        # The first block alone is too large. Truncate to an approximate fit,
        # then tighten until the rendered block is actually under budget.
        approximate_chars = max(64, max_tokens * 3)
        truncated = block[:approximate_chars].rstrip()
        marker = (
            f"\n\n[context truncated: first block alone exceeded "
            f"token budget ~{max_tokens}]"
        )
        while truncated and token_count(truncated + marker) > max_tokens:
            truncated = truncated[: max(0, len(truncated) // 2)].rstrip()
            if len(truncated) < 64:
                break
        packed.append(truncated + marker)
        break

    if len(packed) < len(blocks):
        logger.warning(
            f"Report context packed {len(packed)}/{len(blocks)} block(s) "
            f"under ~{max_tokens} tokens"
        )
    return "\n\n".join(packed)


def _pack_blocks_by_chars(
    blocks: List[str],
    max_chars: Optional[int],
) -> str:
    """Greedy pack whole blocks under a character budget (podcast).

    Oversized middle blocks are **skipped** so later smaller blocks can still
    fit (same as the historical for_podcast fits() loop).
    """
    if not blocks:
        return ""
    if max_chars is None:
        return "\n\n".join(blocks)

    packed: List[str] = []
    used = 0
    skipped = 0
    for block in blocks:
        cost = len(block) + (2 if packed else 0)
        if used + cost > max_chars:
            if not packed:
                # First block alone exceeds budget — hard-truncate once.
                truncated = block[: max(0, max_chars - 64)].rstrip()
                marker = (
                    f"\n\n[context truncated: first block alone exceeded "
                    f"char budget ~{max_chars}]"
                )
                while truncated and len(truncated + marker) > max_chars:
                    truncated = truncated[: max(0, len(truncated) // 2)].rstrip()
                    if len(truncated) < 64:
                        break
                packed.append(truncated + marker)
                used = len(packed[0])
            else:
                skipped += 1
            continue
        packed.append(block)
        used += cost

    if not packed:
        return ""
    if skipped or len(packed) < len(blocks):
        logger.warning(
            f"Podcast context packed {len(packed)}/{len(blocks)} block(s) "
            f"under ~{max_chars} chars"
        )
    return "\n\n".join(packed)


async def _collect_long_form_blocks(
    notebook: Notebook,
    *,
    skip_item_errors: bool = True,
) -> List[str]:
    """Load full sources/notes once and format long-form blocks (#1630).

    Shared by podcast and report generation. Budget packing happens after
    collection. ``skip_item_errors`` controls whether malformed individual
    items are omitted or propagated.
    """
    sources = await notebook.get_sources(include_full_text=True)
    notes = await notebook.get_notes(include_content=True)

    source_ids = [source.id for source in sources if source.id]
    try:
        insights_by_source = await SourceInsight.get_for_sources(source_ids)
    except Exception as e:
        if not skip_item_errors:
            raise
        logger.warning(f"Error batch-fetching insights for long-form context: {e}")
        insights_by_source = {}

    blocks: List[str] = []
    for source in sources:
        try:
            source_context = await source.get_context(
                context_size="long",
                insights=insights_by_source.get(source.id or "", []),
            )
            block = _format_source_long_block(source, source_context)
            if block:
                blocks.append(block)
        except Exception as e:
            if not skip_item_errors:
                raise
            logger.warning(
                f"Skip source {getattr(source, 'id', '?')} in long-form context: {e}"
            )

    for note in notes:
        try:
            note_context = note.get_context(context_size="long")
            block = _format_note_long_block(note, note_context)
            if block:
                blocks.append(block)
        except Exception as e:
            if not skip_item_errors:
                raise
            logger.warning(
                f"Skip note {getattr(note, 'id', '?')} in long-form context: {e}"
            )

    return blocks


async def for_podcast(
    notebook: Notebook,
    max_chars: Optional[int] = None,
) -> str:
    """Long-form notebook context string for podcast generation.

    ``max_chars`` bounds whole blocks by **characters**; ``None`` returns the
    full assembly.

    Loads full source/note bodies once (#1630 / #1607); no N+1 under a cap.
    """
    blocks = await _collect_long_form_blocks(notebook, skip_item_errors=False)
    return _pack_blocks_by_chars(blocks, max_chars)


async def for_report(
    notebook: Notebook,
    max_tokens: Any = _REPORT_BUDGET_UNSET,
) -> str:
    """Long-form notebook context for report generation.

    The default token budget comes from
    ``OPEN_NOTEBOOK_REPORT_CONTEXT_MAX_TOKENS`` (80,000; ``0`` means
    unlimited). Passing ``max_tokens=None`` explicitly also means unlimited.
    """
    budget: Optional[int]
    if max_tokens is _REPORT_BUDGET_UNSET:
        budget = report_context_max_tokens()
    else:
        budget = max_tokens  # type: ignore[assignment]

    blocks = await _collect_long_form_blocks(notebook, skip_item_errors=True)
    return _pack_blocks_by_tokens(blocks, budget)
