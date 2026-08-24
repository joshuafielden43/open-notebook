"""Embedding jobs — deep module for note/insight/source vectorization (#1632)."""

from open_notebook.embeddings.core import (
    embed_insight,
    embed_note,
    embed_source,
    run_embed_job,
)

__all__ = [
    "embed_insight",
    "embed_note",
    "embed_source",
    "run_embed_job",
]
