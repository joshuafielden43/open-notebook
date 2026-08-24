"""#1607 — for_report token-native packing and no N+1 full loads."""

from __future__ import annotations

from typing import Any, List
from unittest.mock import AsyncMock, patch

import pytest

from open_notebook.context.assembly import (
    DEFAULT_REPORT_CONTEXT_MAX_TOKENS,
    _pack_blocks_by_tokens,
    for_report,
    report_context_max_tokens,
)


def test_report_context_max_tokens_default(monkeypatch):
    monkeypatch.delenv("OPEN_NOTEBOOK_REPORT_CONTEXT_MAX_TOKENS", raising=False)
    assert report_context_max_tokens() == DEFAULT_REPORT_CONTEXT_MAX_TOKENS


def test_report_context_max_tokens_zero_is_unlimited(monkeypatch):
    monkeypatch.setenv("OPEN_NOTEBOOK_REPORT_CONTEXT_MAX_TOKENS", "0")
    assert report_context_max_tokens() is None


def test_pack_blocks_by_tokens_unlimited_keeps_all():
    blocks = ["## Source: A\n\nhello", "## Note: B\n\nworld"]
    assert _pack_blocks_by_tokens(blocks, None) == "\n\n".join(blocks)


def test_pack_blocks_by_tokens_drops_trailing_over_budget():
    # Each block is deliberately large so a tiny budget keeps only the first.
    blocks = [
        "## Source: first\n\n" + ("alpha " * 200),
        "## Source: second\n\n" + ("beta " * 200),
        "## Note: third\n\n" + ("gamma " * 200),
    ]
    packed = _pack_blocks_by_tokens(blocks, max_tokens=40)
    assert "first" in packed
    assert "second" not in packed
    assert "third" not in packed


def test_pack_blocks_truncates_single_oversized_block():
    huge = "## Source: big\n\n" + ("word " * 5000)
    packed = _pack_blocks_by_tokens([huge], max_tokens=50)
    assert "truncated" in packed.lower()
    assert "big" in packed
    # Must actually fit under budget (rough).
    from open_notebook.utils.token_utils import token_count

    assert token_count(packed) <= 50 + 5  # small slack for edge encode


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
    def __init__(self, sources: List[_FakeSource], notes: List[_FakeNote]):
        self.id = "notebook:n1"
        self._sources = sources
        self._notes = notes
        self.source_get_calls = 0
        self.include_full_text_calls: List[bool] = []
        self.include_content_calls: List[bool] = []

    async def get_sources(self, include_full_text: bool = False):
        self.include_full_text_calls.append(include_full_text)
        return list(self._sources)

    async def get_notes(self, include_content: bool = False):
        self.include_content_calls.append(include_content)
        return list(self._notes)


@pytest.mark.asyncio
async def test_for_report_loads_full_bodies_once_no_source_get():
    sources = [
        _FakeSource("source:1", "One", "content one " * 20),
        _FakeSource("source:2", "Two", "content two " * 20),
    ]
    notes = [_FakeNote("note:1", "Memo", "note body " * 10)]
    nb = _FakeNotebook(sources, notes)

    # Source.get must never be called for report assembly.
    async def boom(*_a, **_k):
        raise AssertionError("Source.get must not be used by for_report")

    with (
        patch(
            "open_notebook.context.assembly.SourceInsight.get_for_sources",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "open_notebook.context.assembly.Source.get",
            new=boom,
        ),
        patch(
            "open_notebook.context.assembly.Note.get",
            new=boom,
        ),
    ):
        text = await for_report(nb, max_tokens=10_000)  # type: ignore[arg-type]

    assert "One" in text and "Two" in text and "Memo" in text
    assert nb.include_full_text_calls == [True]
    assert nb.include_content_calls == [True]


@pytest.mark.asyncio
async def test_for_report_respects_token_budget():
    sources = [
        _FakeSource("source:1", "First", "alpha " * 400),
        _FakeSource("source:2", "Second", "beta " * 400),
    ]
    nb = _FakeNotebook(sources, [])

    with patch(
        "open_notebook.context.assembly.SourceInsight.get_for_sources",
        new=AsyncMock(return_value={}),
    ):
        text = await for_report(nb, max_tokens=30)  # type: ignore[arg-type]

    assert "First" in text
    # Second block should not fit under a tight budget.
    assert "Second" not in text
