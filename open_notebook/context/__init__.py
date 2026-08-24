"""Context assembly package — prefer these imports over utils.context_builder."""

from open_notebook.context.assembly import (
    build_notebook_context,
    build_source_context,
    for_chat,
    for_podcast,
    for_report,
    for_source_chat,
    format_source_context,
    podcast_context_max_chars,
    report_context_max_tokens,
)

__all__ = [
    "build_notebook_context",
    "build_source_context",
    "for_chat",
    "for_podcast",
    "for_report",
    "for_source_chat",
    "format_source_context",
    "podcast_context_max_chars",
    "report_context_max_tokens",
]
