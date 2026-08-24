"""Application service for submitting notebook report jobs."""

from typing import Optional

from api.command_service import CommandService
from open_notebook.database.repository import ensure_record_id
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import (
    DatabaseOperationError,
    InvalidInputError,
    NotFoundError,
    OpenNotebookError,
)


def require_notebook_record_id(notebook_id: str) -> str:
    """Validate and normalize a notebook record ID.

    Args:
        notebook_id: Candidate SurrealDB notebook record ID.

    Returns:
        Normalized notebook record ID.

    Raises:
        InvalidInputError: If the value is not a non-empty notebook ID.
    """
    try:
        record_id = ensure_record_id(notebook_id)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError("Invalid notebook_id") from exc
    if record_id.table_name != "notebook" or not str(record_id.id).strip():
        raise InvalidInputError("Invalid notebook_id")
    return str(record_id)


async def submit_report_job(
    notebook_id: str,
    *,
    instructions: Optional[str] = None,
    model_id: Optional[str] = None,
) -> str:
    """Validate a notebook and submit its report-generation job.

    Args:
        notebook_id: Notebook record ID.
        instructions: Optional user guidance for report generation.
        model_id: Optional chat-model record ID.

    Returns:
        Submitted command ID.

    Raises:
        InvalidInputError: If the notebook ID is malformed.
        NotFoundError: If the notebook does not exist.
        DatabaseOperationError: If command submission fails unexpectedly.
    """
    normalized_id = require_notebook_record_id(notebook_id)
    notebook = await Notebook.get(normalized_id)
    if not notebook:
        # Notebook.get normally raises NotFoundError; retain fail-closed behavior
        # for test doubles or alternate repositories that return None.
        raise NotFoundError("Notebook not found")

    command_args = {"notebook_id": str(notebook.id or normalized_id)}
    normalized_instructions = (instructions or "").strip()
    normalized_model_id = (model_id or "").strip()
    if normalized_instructions:
        command_args["instructions"] = normalized_instructions
    if normalized_model_id:
        command_args["model_id"] = normalized_model_id

    try:
        return await CommandService.submit_command_job(
            "open_notebook",
            "generate_report",
            command_args,
        )
    except Exception as exc:
        if isinstance(exc, OpenNotebookError):
            raise
        raise DatabaseOperationError("Failed to submit report generation job") from exc
