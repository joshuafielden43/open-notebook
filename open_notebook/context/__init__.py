"""Context assembly package — prefer these imports over utils.context_builder."""

from open_notebook.context.assembly import (
    build_notebook_context,
    build_source_context,
    for_chat,
    for_podcast,
    for_source_chat,
    format_source_context,
    podcast_context_max_chars,
)

__all__ = [
    "build_notebook_context",
    "build_source_context",
    "for_chat",
    "for_podcast",
    "for_source_chat",
    "format_source_context",
    "podcast_context_max_chars",
]
