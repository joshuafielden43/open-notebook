"""Notebook application module — list/projection queries and helpers.

SurrealQL for notebook lists and view stamps lives here, not in routers.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import InvalidInputError, NotFoundError

_NOTEBOOK_ORDER_FIELDS = frozenset({"name", "created", "updated"})
_ORDER_DIRS = frozenset({"asc", "desc"})


def validate_notebook_order_by(order_by: str) -> str:
    """Allowlist order_by for notebook list queries."""
    parts = order_by.strip().lower().split()
    if len(parts) == 1:
        if parts[0] not in _NOTEBOOK_ORDER_FIELDS:
            raise InvalidInputError(
                f"Invalid order_by field: '{order_by}'. "
                f"Allowed: {', '.join(sorted(_NOTEBOOK_ORDER_FIELDS))}"
            )
        return parts[0]
    if len(parts) == 2:
        if parts[0] not in _NOTEBOOK_ORDER_FIELDS or parts[1] not in _ORDER_DIRS:
            raise InvalidInputError(
                f"Invalid order_by: '{order_by}'. "
                f"Allowed fields: {', '.join(sorted(_NOTEBOOK_ORDER_FIELDS))}"
            )
        return f"{parts[0]} {parts[1]}"
    raise InvalidInputError(
        f"Invalid order_by format: '{order_by}'. Expected 'field' or 'field direction'"
    )


async def list_notebooks_with_counts(
    *,
    archived: Optional[bool] = None,
    order_by: str = "updated desc",
) -> list[dict[str, Any]]:
    """List notebooks with source/note counts (router-ready dict rows)."""
    validated = validate_notebook_order_by(order_by)
    query = f"""
        SELECT *,
        count(<-reference.in) as source_count,
        count(<-artifact.in) as note_count
        FROM notebook
        ORDER BY {validated}
    """
    result = await repo_query(query)
    if archived is not None:
        result = [nb for nb in result if nb.get("archived") == archived]
    return result or []


async def get_notebook_row_with_counts(notebook_id: str) -> Optional[dict[str, Any]]:
    """Single notebook row with source/note counts, or None if missing."""
    result = await repo_query(
        """
        SELECT *,
        count(<-reference.in) as source_count,
        count(<-artifact.in) as note_count
        FROM $notebook_id
        """,
        {"notebook_id": ensure_record_id(notebook_id)},
    )
    if not result:
        return None
    return result[0]


async def stamp_notebook_view(notebook_id: str) -> None:
    """Best-effort last_viewed_at stamp; never raises."""
    try:
        await repo_query(
            "UPDATE $notebook_id SET last_viewed_at = time::now();",
            {"notebook_id": ensure_record_id(notebook_id)},
        )
    except Exception as e:
        logger.warning(
            f"Failed to stamp last_viewed_at for notebook {notebook_id}: {e}"
        )


async def list_recently_viewed_rows(limit: int) -> tuple[list[dict], list[dict]]:
    """Return (notebook_rows, source_rows) for recently-viewed merge."""
    notebooks = await repo_query(
        """
        SELECT id, name AS title, last_viewed_at
        FROM notebook
        WHERE last_viewed_at != NONE AND last_viewed_at != NULL
        ORDER BY last_viewed_at DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )
    sources = await repo_query(
        """
        SELECT id, title, last_viewed_at
        FROM source
        WHERE last_viewed_at != NONE AND last_viewed_at != NULL
        ORDER BY last_viewed_at DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )
    return notebooks or [], sources or []


async def require_notebook(notebook_id: str) -> Notebook:
    notebook = await Notebook.get(notebook_id)
    if not notebook:
        raise NotFoundError(f"Notebook {notebook_id} not found")
    return notebook


async def notebook_exists(notebook_id: str) -> bool:
    try:
        await require_notebook(notebook_id)
        return True
    except NotFoundError:
        return False


def notebook_summary_dict(notebook: Notebook) -> dict[str, Any]:
    return {
        "id": notebook.id,
        "name": notebook.name,
        "description": getattr(notebook, "description", None),
        "created": str(notebook.created) if notebook.created else None,
        "updated": str(notebook.updated) if notebook.updated else None,
        "archived": getattr(notebook, "archived", False),
    }
