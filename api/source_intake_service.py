"""Worker-backed lifecycle shared by source creation and retry."""

from typing import Any

from loguru import logger

from api.command_service import CommandService
from api.models import AssetModel, SourceCreate, SourceResponse
from commands.source_commands import SourceProcessingInput
from open_notebook.database.repository import ensure_record_id
from open_notebook.domain.notebook import Asset, Source
from open_notebook.exceptions import DatabaseOperationError, OpenNotebookError


def source_to_response(
    source: Source, embedded_chunks: int = 0, **extras: Any
) -> SourceResponse:
    """Build a SourceResponse from a Source and endpoint-specific fields."""
    fields: dict[str, Any] = {
        "id": source.id or "",
        "title": source.title,
        "topics": source.topics or [],
        "asset": AssetModel(
            file_path=source.asset.file_path,
            url=source.asset.url,
        )
        if source.asset
        else None,
        "full_text": source.full_text,
        "embedded": embedded_chunks > 0,
        "embedded_chunks": embedded_chunks,
        "created": str(source.created),
        "updated": str(source.updated),
    }
    fields.update(extras)
    return SourceResponse(**fields)


async def default_transformation_ids() -> list[str]:
    """Ids of transformations marked apply_default.

    The Add Source UI reads apply_default to pre-check its boxes, but API
    callers that omit the transformations field used to silently get none —
    bulk ingests produced sources with zero insights. When the caller
    specifies nothing, honor the toggle the product already exposes.
    """
    try:
        from open_notebook.domain.transformation import Transformation

        transformations = await Transformation.get_all()
        return [
            str(t.id) for t in transformations if t.apply_default and t.id is not None
        ]
    except Exception as e:
        logger.warning(f"Could not load default transformations: {e}")
        return []


async def queue_source_processing(
    source: Source,
    *,
    content_state: dict[str, Any],
    notebook_ids: list[str],
    transformations: list[str],
    embed: bool,
) -> str:
    """Queue source processing and persist its command reference."""
    command_input = SourceProcessingInput(
        source_id=str(source.id),
        content_state=content_state,
        notebook_ids=notebook_ids,
        transformations=transformations,
        embed=embed,
    )
    command_id = await CommandService.submit_command_job(
        "open_notebook", "process_source", command_input.model_dump()
    )
    source.command = ensure_record_id(command_id)
    await source.save()
    return command_id


async def create_queued_source(
    source_data: SourceCreate,
    *,
    content_state: dict[str, Any],
    transformation_ids: list[str],
    file_path: str | None,
) -> SourceResponse:
    """Persist a source, associate it with notebooks, and queue processing."""
    source = Source(
        title=source_data.title or "Processing...",
        topics=[],
        asset=(
            Asset(url=source_data.url)
            if source_data.type == "link"
            else Asset(file_path=file_path or source_data.file_path)
            if source_data.type == "upload"
            else None
        ),
    )
    await source.save()

    for notebook_id in source_data.notebooks or []:
        await source.add_to_notebook(notebook_id)

    try:
        command_id = await queue_source_processing(
            source,
            content_state=content_state,
            notebook_ids=source_data.notebooks or [],
            transformations=transformation_ids,
            embed=source_data.embed,
        )
        return source_to_response(
            source,
            asset=None,
            full_text=None,
            embedded=False,
            embedded_chunks=0,
            command_id=command_id,
            status="queued",
            processing_info={"async": True, "queued": True},
        )
    except OpenNotebookError:
        try:
            await source.delete()
        except Exception:
            pass
        raise
    except Exception as e:
        logger.error(f"Failed to submit source processing command: {e}")
        try:
            await source.delete()
        except Exception:
            pass
        raise DatabaseOperationError("Failed to queue processing") from e
