"""Compatibility shim — prefer ``open_notebook.conversations``."""

from open_notebook.conversations.runtime import (
    session_message_count as get_session_message_count,
)

__all__ = ["get_session_message_count"]
