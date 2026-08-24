"""Compatibility shim — prefer ``open_notebook.context``."""

from open_notebook.context.assembly import (  # noqa: F401
    SOURCE_TRUNCATION_NOTICE,
    _truncate_source_to_token_budget,
    build_notebook_context,
    build_source_context,
    for_chat,
    for_podcast,
    for_report,
    for_source_chat,
    format_source_context,
    report_context_max_tokens,
)
