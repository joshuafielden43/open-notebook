"""Notebook chat context token budget."""

from open_notebook.context.assembly import _trim_notebook_context_to_budget


def test_trim_drops_notes_then_sources_to_fit_budget():
    sources = [{"id": f"s{i}", "blob": "word " * 200} for i in range(5)]
    notes = [{"id": f"n{i}", "blob": "note " * 200} for i in range(5)]
    data = {"sources": sources, "notes": notes}
    trimmed, text = _trim_notebook_context_to_budget(data, max_tokens=50)
    assert len(trimmed["sources"]) + len(trimmed["notes"]) < 10
    assert "truncated" in text.lower() or len(trimmed["notes"]) < 5


def test_trim_unlimited_keeps_all():
    data = {
        "sources": [{"id": "s1", "t": "hello"}],
        "notes": [{"id": "n1", "t": "world"}],
    }
    trimmed, text = _trim_notebook_context_to_budget(data, max_tokens=None)
    assert len(trimmed["sources"]) == 1
    assert len(trimmed["notes"]) == 1
    assert "truncated" not in text.lower()
