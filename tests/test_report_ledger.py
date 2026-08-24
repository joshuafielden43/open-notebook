"""Extractive evidence ledger — batching, L1 filter, parse isolation, stats."""

from __future__ import annotations

import json
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_notebook.reports.ledger import (
    BATCH_INPUT_TOKEN_BUDGET,
    Ledger,
    _Block,
    _filter_and_merge_items,
    _parse_extraction_payload,
    build_ledger,
    is_verbatim_substring,
    normalize_ws,
    partition_blocks_by_token_budget,
)


def _block(item_id: str, title: str, body: str) -> _Block:
    kind = "Source" if item_id.startswith("source:") else "Note"
    text = f"## {kind}: {title}\n\n{body}"
    return _Block(item_id=item_id, item_title=title, text=text)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_normalize_ws_collapses_whitespace():
    assert normalize_ws("  a \n\t b  ") == "a b"


def test_is_verbatim_substring_ignores_whitespace_drift():
    ctx = "The pipeline  runs\non  Kubernetes pods."
    assert is_verbatim_substring("pipeline runs on Kubernetes", ctx)
    assert not is_verbatim_substring("pipeline runs on Nomad", ctx)


def test_partition_assigns_every_block_exactly_once():
    # Tiny budget forces multiple batches; every block still appears once.
    blocks = [
        _block("source:1", "One", "alpha " * 50),
        _block("source:2", "Two", "beta " * 50),
        _block("source:3", "Three", "gamma " * 50),
        _block("note:1", "Memo", "delta " * 50),
    ]
    # Very small budget → often one block per batch
    batches = partition_blocks_by_token_budget(blocks, budget=40)
    flat = [b.item_id for batch in batches for b in batch]
    assert flat == [b.item_id for b in blocks]
    assert sum(len(batch) for batch in batches) == len(blocks)
    assert len(batches) >= 1


def test_partition_oversized_block_is_solo_not_dropped():
    huge = _block("source:huge", "Huge", "word " * 5000)
    small = _block("source:s", "Small", "tiny body")
    batches = partition_blocks_by_token_budget([huge, small], budget=50)
    ids = [[b.item_id for b in batch] for batch in batches]
    assert ["source:huge"] in ids
    assert any("source:s" in batch for batch in ids)
    assert sum(len(b) for b in batches) == 2


def test_partition_empty():
    assert partition_blocks_by_token_budget([]) == []


def test_parse_extraction_payload_accepts_fenced_json():
    raw = """```json
{"items": [{"item_id": "source:1", "item_title": "A", "snippets": ["hello world"]}]}
```"""
    items = _parse_extraction_payload(raw)
    assert items[0]["item_id"] == "source:1"


def test_parse_extraction_payload_rejects_garbage():
    with pytest.raises(ValueError):
        _parse_extraction_payload("not json at all {{{")


def test_filter_drops_non_verbatim_and_counts():
    body = "Alpha beta gamma delta epsilon zeta eta theta."
    batch = [_block("source:1", "Doc", body)]
    proposed = [
        {
            "item_id": "source:1",
            "item_title": "Doc",
            "snippets": [
                "Alpha beta gamma delta",  # good
                "completely invented phrase about Mars",  # bad
            ],
        }
    ]
    seen: set[str] = set()
    rows, prop, verb, dropped = _filter_and_merge_items(
        proposed, batch, seen_snippets=seen
    )
    assert prop == 2
    assert verb == 1
    assert len(rows) == 1
    assert rows[0].snippets == ["Alpha beta gamma delta"]
    assert dropped == 0  # row kept because one snippet survived


def test_filter_drops_row_when_all_snippets_fail():
    batch = [_block("source:1", "Doc", "only real text here")]
    proposed = [
        {
            "item_id": "source:1",
            "item_title": "Doc",
            "snippets": ["fabricated claim", "also fake"],
        }
    ]
    rows, prop, verb, dropped = _filter_and_merge_items(
        proposed, batch, seen_snippets=set()
    )
    assert rows == []
    assert prop == 2
    assert verb == 0
    assert dropped == 1


def test_filter_dedupes_normalized_snippets_across_calls():
    body = "Shared phrase appears here in the document body."
    batch = [_block("source:1", "Doc", body)]
    proposed = [
        {
            "item_id": "source:1",
            "item_title": "Doc",
            "snippets": ["Shared phrase appears here"],
        }
    ]
    seen: set[str] = set()
    rows1, _, _, _ = _filter_and_merge_items(proposed, batch, seen_snippets=seen)
    # Second batch proposes same text with extra whitespace
    proposed2 = [
        {
            "item_id": "source:1",
            "item_title": "Doc",
            "snippets": ["Shared   phrase\nappears here"],
        }
    ]
    rows2, prop, verb, _ = _filter_and_merge_items(proposed2, batch, seen_snippets=seen)
    assert len(rows1) == 1
    assert verb == 1  # still counts as verbatim
    assert rows2 == [] or rows2[0].snippets == []  # deduped out
    # After dedupe with empty snippets, row may be omitted
    assert all(not r.snippets for r in rows2) or rows2 == []


# ---------------------------------------------------------------------------
# build_ledger (LLM mocked)
# ---------------------------------------------------------------------------


class _FakeSource:
    def __init__(self, sid: str, title: str, text: str):
        self.id = sid
        self.title = title
        self._text = text

    async def get_context(self, context_size: str = "long", insights: Any = None):
        return {
            "id": self.id,
            "title": self.title,
            "full_text": self._text,
            "insights": insights or [],
        }


class _FakeNote:
    def __init__(self, nid: str, title: str, content: str):
        self.id = nid
        self.title = title
        self._content = content

    def get_context(self, context_size: str = "long"):
        return {"id": self.id, "title": self.title, "content": self._content}


class _FakeNotebook:
    def __init__(
        self, sources: List[_FakeSource], notes: List[_FakeNote] | None = None
    ):
        self.id = "notebook:n1"
        self._sources = sources
        self._notes = notes or []

    async def get_sources(self, include_full_text: bool = False):
        return list(self._sources)

    async def get_notes(self, include_content: bool = False):
        return list(self._notes)


@pytest.mark.asyncio
async def test_build_ledger_partitions_all_blocks_and_stats_add_up():
    sources = [
        _FakeSource("source:1", "One", "Alpha beta gamma delta epsilon zeta."),
        _FakeSource("source:2", "Two", "Eta theta iota kappa lambda mu."),
        _FakeSource("source:3", "Three", "Nu xi omicron pi rho sigma."),
    ]
    nb = _FakeNotebook(sources)

    # Force one block per batch so multi-call coverage is exercised.
    call_ids: List[List[str]] = []

    def fake_partition(blocks, budget=BATCH_INPUT_TOKEN_BUDGET):
        batches = [[b] for b in blocks]
        for batch in batches:
            call_ids.append([b.item_id for b in batch])
        return batches

    chain = AsyncMock()

    async def ainvoke(payload):
        resp = MagicMock()
        text = "".join(getattr(msg, "content", "") or "" for msg in payload)
        for sid, snip in [
            ("source:1", "Alpha beta gamma delta"),
            ("source:2", "Eta theta iota kappa"),
            ("source:3", "Nu xi omicron pi"),
        ]:
            if f"item_id: {sid}" in text:
                resp.content = json.dumps(
                    {
                        "items": [
                            {
                                "item_id": sid,
                                "item_title": sid,
                                "snippets": [snip],
                            }
                        ]
                    }
                )
                return resp
        resp.content = json.dumps({"items": []})
        return resp

    chain.ainvoke = AsyncMock(side_effect=ainvoke)

    with (
        patch(
            "open_notebook.context.assembly.SourceInsight.get_for_sources",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "open_notebook.reports.ledger.partition_blocks_by_token_budget",
            side_effect=fake_partition,
        ),
        patch(
            "open_notebook.ai.runtime.chat_langchain",
            new=AsyncMock(return_value=chain),
        ),
    ):
        ledger = await build_ledger(nb)  # type: ignore[arg-type]

    assert isinstance(ledger, Ledger)
    assert ledger.stats.blocks_total == 3
    assert ledger.stats.batches == 3
    # Every block offered exactly once
    flat = [i for batch in call_ids for i in batch]
    assert sorted(flat) == ["source:1", "source:2", "source:3"]
    assert ledger.stats.blocks_failed == 0
    assert ledger.stats.rows_kept == 3
    assert ledger.stats.sources_covered == 3
    assert ledger.stats.pre_filter_verbatim_rate == 1.0
    assert chain.ainvoke.await_count == 3


@pytest.mark.asyncio
async def test_build_ledger_parse_failure_skips_batch_not_run():
    sources = [
        _FakeSource("source:1", "One", "Alpha beta gamma delta epsilon."),
        _FakeSource("source:2", "Two", "Zeta eta theta iota kappa."),
    ]
    nb = _FakeNotebook(sources)

    def fake_partition(blocks, budget=BATCH_INPUT_TOKEN_BUDGET):
        return [[b] for b in blocks]

    chain = AsyncMock()
    call_n = {"n": 0}

    async def ainvoke(payload):
        call_n["n"] += 1
        resp = MagicMock()
        text = "".join(getattr(m, "content", "") or "" for m in payload)
        if "source:1" in text and call_n["n"] == 1:
            resp.content = "NOT VALID JSON {{{"
            return resp
        resp.content = json.dumps(
            {
                "items": [
                    {
                        "item_id": "source:2",
                        "item_title": "Two",
                        "snippets": ["Zeta eta theta iota"],
                    }
                ]
            }
        )
        return resp

    chain.ainvoke = AsyncMock(side_effect=ainvoke)

    with (
        patch(
            "open_notebook.context.assembly.SourceInsight.get_for_sources",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "open_notebook.reports.ledger.partition_blocks_by_token_budget",
            side_effect=fake_partition,
        ),
        patch(
            "open_notebook.ai.runtime.chat_langchain",
            new=AsyncMock(return_value=chain),
        ),
    ):
        ledger = await build_ledger(nb)  # type: ignore[arg-type]

    assert ledger.stats.blocks_total == 2
    assert ledger.stats.blocks_failed == 1  # first batch lost
    assert ledger.stats.rows_kept == 1
    assert ledger.rows[0].item_id == "source:2"
    assert ledger.stats.pre_filter_verbatim_rate == 1.0


@pytest.mark.asyncio
async def test_build_ledger_drops_non_verbatim_in_stats():
    src = _FakeSource(
        "source:1",
        "Doc",
        "Real words live in this source document body.",
    )
    nb = _FakeNotebook([src])

    chain = AsyncMock()
    resp = MagicMock()
    resp.content = json.dumps(
        {
            "items": [
                {
                    "item_id": "source:1",
                    "item_title": "Doc",
                    "snippets": [
                        "Real words live in this",
                        "totally fabricated invention",
                    ],
                }
            ]
        }
    )
    chain.ainvoke = AsyncMock(return_value=resp)

    with (
        patch(
            "open_notebook.context.assembly.SourceInsight.get_for_sources",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "open_notebook.ai.runtime.chat_langchain",
            new=AsyncMock(return_value=chain),
        ),
    ):
        ledger = await build_ledger(nb)  # type: ignore[arg-type]

    assert ledger.stats.rows_kept == 1
    assert ledger.rows[0].snippets == ["Real words live in this"]
    # 1 of 2 snippets verified
    assert ledger.stats.pre_filter_verbatim_rate == pytest.approx(0.5)
    assert chain.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_build_ledger_passes_explicit_completion_budget():
    src = _FakeSource("source:1", "Doc", "Short real body text for extract.")
    nb = _FakeNotebook([src])

    chain = AsyncMock()
    resp = MagicMock()
    resp.content = json.dumps(
        {
            "items": [
                {
                    "item_id": "source:1",
                    "item_title": "Doc",
                    "snippets": ["Short real body text"],
                }
            ]
        }
    )
    chain.ainvoke = AsyncMock(return_value=resp)
    chat_mock = AsyncMock(return_value=chain)

    with (
        patch(
            "open_notebook.context.assembly.SourceInsight.get_for_sources",
            new=AsyncMock(return_value={}),
        ),
        patch("open_notebook.ai.runtime.chat_langchain", new=chat_mock),
    ):
        await build_ledger(nb)  # type: ignore[arg-type]

    assert chat_mock.await_count == 1
    assert chat_mock.await_args is not None
    kwargs = chat_mock.await_args.kwargs
    assert kwargs.get("max_tokens") == 4096


@pytest.mark.asyncio
async def test_build_ledger_dedupe_across_batches():
    s1 = _FakeSource(
        "source:1",
        "A",
        "Identical claim text appears in document A body here.",
    )
    s2 = _FakeSource(
        "source:2",
        "B",
        "Identical claim text appears in document B body here.",
    )
    nb = _FakeNotebook([s1, s2])

    def fake_partition(blocks, budget=BATCH_INPUT_TOKEN_BUDGET):
        return [[b] for b in blocks]

    chain = AsyncMock()

    async def ainvoke(payload):
        text = "".join(getattr(m, "content", "") or "" for m in payload)
        resp = MagicMock()
        if "source:1" in text:
            sid, title = "source:1", "A"
        else:
            sid, title = "source:2", "B"
        # Same normalized snippet content (appears in both bodies)
        resp.content = json.dumps(
            {
                "items": [
                    {
                        "item_id": sid,
                        "item_title": title,
                        "snippets": ["Identical claim text appears in document"],
                    }
                ]
            }
        )
        return resp

    chain.ainvoke = AsyncMock(side_effect=ainvoke)

    with (
        patch(
            "open_notebook.context.assembly.SourceInsight.get_for_sources",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "open_notebook.reports.ledger.partition_blocks_by_token_budget",
            side_effect=fake_partition,
        ),
        patch(
            "open_notebook.ai.runtime.chat_langchain",
            new=AsyncMock(return_value=chain),
        ),
    ):
        ledger = await build_ledger(nb)  # type: ignore[arg-type]

    # First source keeps the snippet; second loses it to dedupe → no row or empty
    all_snips = [s for r in ledger.rows for s in r.snippets]
    assert len(all_snips) == 1
    assert normalize_ws(all_snips[0]).startswith("Identical claim text")
