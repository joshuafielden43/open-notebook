"""Source application module — status and list projection helpers.

Routers stay thin: Surreal list rows and command status become API-shaped
data here, not inline in ``api/routers/sources.py``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _truncate_error(error: Optional[str], max_len: int = 500) -> Optional[str]:
    if not error:
        return None
    text = str(error).strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def command_fields_from_row(
    command: Any,
) -> Tuple[Optional[str], Optional[str], Optional[Dict[str, Any]]]:
    """Extract command_id, status, processing_info from a FETCH-ed command."""
    if command and isinstance(command, dict):
        command_id = str(command.get("id")) if command.get("id") else None
        status = command.get("status")
        result_data = command.get("result")
        execution_metadata = (
            result_data.get("execution_metadata", {})
            if isinstance(result_data, dict)
            else {}
        )
        processing_info = {
            "started_at": execution_metadata.get("started_at"),
            "completed_at": execution_metadata.get("completed_at"),
            "error": _truncate_error(command.get("error_message")),
        }
        return command_id, status, processing_info
    if command:
        return str(command), "unknown", None
    return None, None, None


def status_message_for_source(
    status: Optional[str],
    processing_info: Optional[Dict[str, Any]] = None,
) -> str:
    """Human-readable status line; failures surface the real error when known."""
    error_text = None
    if isinstance(processing_info, dict):
        raw_err = processing_info.get("error")
        if raw_err:
            error_text = str(raw_err).strip() or None

    if status == "completed":
        return "Source processing completed successfully"
    if status == "failed":
        return error_text or "Source processing failed"
    if status == "running":
        return "Source processing in progress"
    if status == "queued":
        return "Source processing queued"
    if status == "unknown":
        return "Source processing status unknown"
    if status is None:
        return "Legacy source (completed before async processing)"
    return f"Source processing status: {status}"
