"""Deep embed operations free of surreal-commands adapters (#1632).

Commands wrap these with retry config and CommandInput/Output types.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from loguru import logger

from open_notebook.database.repository import ensure_record_id, repo_insert, repo_query
from open_notebook.database.vector_index import (
    DIMENSION_ERROR_MARKER,
    drop_vector_indexes,
    reindex_if_drained,
)
from open_notebook.domain.notebook import Note, Source, SourceInsight
from open_notebook.utils.chunking import ContentType, chunk_text, detect_content_type
from open_notebook.utils.embedding import generate_embedding, generate_embeddings


async def embed_markdown_record(
    *,
    label: str,
    record_id: str,
    loader: Callable[[str], Awaitable[Any]],
    command_id: Optional[str] = None,
) -> None:
    """Load a note/insight, embed content, UPSERT embedding on the record."""
    record = await loader(record_id)
    if not record:
        raise ValueError(f"{label} '{record_id}' not found")

    if not record.content or not record.content.strip():
        raise ValueError(f"{label} '{record_id}' has no content to embed")

    embedding = await generate_embedding(
        record.content, content_type=ContentType.MARKDOWN, command_id=command_id
    )
    await repo_query(
        "UPDATE $record_id SET embedding = $embedding",
        {
            "record_id": ensure_record_id(record_id),
            "embedding": embedding,
        },
    )


async def embed_note(note_id: str, *, command_id: Optional[str] = None) -> None:
    await embed_markdown_record(
        label="Note",
        record_id=note_id,
        loader=Note.get,
        command_id=command_id,
    )


async def embed_insight(insight_id: str, *, command_id: Optional[str] = None) -> None:
    await embed_markdown_record(
        label="Insight",
        record_id=insight_id,
        loader=SourceInsight.get,
        command_id=command_id,
    )


async def embed_source(source_id: str, *, command_id: Optional[str] = None) -> int:
    """Embed a source as chunk rows. Returns chunks_created."""
    source = await Source.get(source_id)
    if not source:
        raise ValueError(f"Source '{source_id}' not found")

    if not source.full_text or not source.full_text.strip():
        raise ValueError(f"Source '{source_id}' has no text to embed")

    logger.debug(f"Deleting existing embeddings for source {source_id}")
    await repo_query(
        "DELETE source_embedding WHERE source = $source_id",
        {"source_id": ensure_record_id(source_id)},
    )

    file_path = source.asset.file_path if source.asset else None
    content_type = detect_content_type(source.full_text, file_path)
    logger.debug(f"Detected content type: {content_type.value}")

    chunks = chunk_text(source.full_text, content_type=content_type)
    total_chunks = len(chunks)
    chunk_sizes = [len(c) for c in chunks]
    logger.info(
        f"Created {total_chunks} chunks for source {source_id} "
        f"(sizes: min={min(chunk_sizes) if chunk_sizes else 0}, "
        f"max={max(chunk_sizes) if chunk_sizes else 0}, "
        f"avg={sum(chunk_sizes) // len(chunk_sizes) if chunk_sizes else 0} chars)"
    )
    if total_chunks == 0:
        raise ValueError("No chunks created after splitting text")

    logger.debug(f"Generating embeddings for {total_chunks} chunks")
    embeddings = await generate_embeddings(chunks, command_id=command_id)
    if len(embeddings) != len(chunks):
        raise ValueError(
            f"Embedding count mismatch: got {len(embeddings)} embeddings "
            f"for {len(chunks)} chunks"
        )

    from open_notebook.vectorstore import (
        delete_source_points,
        ensure_collection,
        qdrant_enabled,
        upsert_chunks,
    )

    if qdrant_enabled():
        # ADR-016: chunk vectors bypass SurrealDB's single writer thread so
        # bulk ingest stops degrading every read. The surreal rows for this
        # source were already deleted above, keeping the legacy table empty.
        canonical_id = str(source.id or source_id)
        await ensure_collection(len(embeddings[0]))
        await delete_source_points(canonical_id)
        await upsert_chunks(canonical_id, chunks, embeddings)
        return total_chunks

    records = [
        {
            "source": ensure_record_id(source_id),
            "order": idx,
            "content": chunk,
            "embedding": embedding,
        }
        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]
    logger.debug(f"Inserting {len(records)} source_embedding records")
    await repo_insert("source_embedding", records)
    return total_chunks


async def run_embed_job(
    *,
    kind: str,
    record_id: str,
    command_id: Optional[str],
    embed: Callable[[], Awaitable[Tuple[Dict[str, Any], str]]],
) -> Tuple[Optional[Dict[str, Any]], float, Optional[str]]:
    """Pulse + embed + post-drain reindex; shared by embed_* commands.

    Returns (extra_fields, processing_time, error_message).
    Permanent ValueError failures return error_message; transient re-raise.
    """
    from open_notebook.jobs import JobCancelledError, long_job_pulse

    start_time = time.time()
    try:
        logger.info(f"Starting embedding for {kind}: {record_id}")
        async with long_job_pulse(command_id):
            extra_fields, log_detail = await embed()

        processing_time = time.time() - start_time
        logger.info(
            f"Successfully embedded {kind} {record_id}{log_detail} "
            f"in {processing_time:.2f}s"
        )
        try:
            outcome = await reindex_if_drained()
            if outcome:
                logger.info(f"Vector index provisioning after embed drain: {outcome}")
        except Exception as provision_error:
            logger.warning(f"Vector index provisioning check failed: {provision_error}")

        return extra_fields, processing_time, None

    except JobCancelledError as e:
        processing_time = time.time() - start_time
        logger.warning(f"Embedding cancelled for {kind} {record_id}: {e}")
        return None, processing_time, str(e)
    except ValueError as e:
        processing_time = time.time() - start_time
        logger.error(f"Failed to embed {kind} {record_id}: {e}")
        return None, processing_time, str(e)
    except Exception as e:
        if DIMENSION_ERROR_MARKER in str(e):
            logger.warning(
                f"Embedding dimension changed ({kind} {record_id}); dropping "
                "vector indexes so re-embedding can proceed (ADR-014)"
            )
            try:
                await drop_vector_indexes()
            except Exception as drop_error:
                logger.error(f"Failed to drop vector indexes: {drop_error}")
        logger.debug(f"Transient error embedding {kind} {record_id}: {e}")
        raise
