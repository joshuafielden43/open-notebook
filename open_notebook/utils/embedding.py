"""
Unified embedding utilities for Open Notebook.

Provides centralized embedding generation with support for:
- Single text embedding (with automatic chunking and mean pooling for large texts)
- Batch text embedding (multiple texts with automatic batching)
- Mean pooling for combining multiple embeddings into one

All embedding operations in the application should use these functions
to ensure consistent behavior and proper handling of large content.
"""

import asyncio
import math
import os
from typing import List, Optional
from weakref import WeakKeyDictionary

from loguru import logger

from .chunking import CHUNK_SIZE, ContentType, chunk_text
from .token_utils import token_count


def _get_embedding_batch_size() -> int:
    """
    Read the embedding batch size from the environment.

    This is intentionally configurable because provider limits vary widely, and
    CPU-only local embedding endpoints often need smaller batches than cloud APIs.
    """
    raw = os.getenv("OPEN_NOTEBOOK_EMBEDDING_BATCH_SIZE", "50").strip()
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
        if value != 50:
            logger.info(f"Using OPEN_NOTEBOOK_EMBEDDING_BATCH_SIZE={value}")
        return value
    except ValueError:
        logger.warning(
            "Invalid OPEN_NOTEBOOK_EMBEDDING_BATCH_SIZE='{}'; falling back to 50",
            raw,
        )
        return 50


EMBEDDING_BATCH_SIZE = _get_embedding_batch_size()
EMBEDDING_MAX_RETRIES = 3
EMBEDDING_RETRY_DELAY = 2  # seconds


def _get_embedding_concurrency() -> int:
    """
    Max provider requests in flight across ALL jobs in this process.

    Default 1: a serial local endpoint (e.g. single MLX server) answers N
    interleaved requests slower than the same N in single file — measured 2x
    worse at N=4 — so the pipeline overlaps provider calls with DB writes but
    never with each other. Raise only for providers that truly parallelize.
    """
    raw = os.getenv("OPEN_NOTEBOOK_EMBEDDING_CONCURRENCY", "1").strip()
    try:
        value = int(raw)
        if value < 1:
            raise ValueError
        if value != 1:
            logger.info(f"Using OPEN_NOTEBOOK_EMBEDDING_CONCURRENCY={value}")
        return value
    except ValueError:
        logger.warning(
            "Invalid OPEN_NOTEBOOK_EMBEDDING_CONCURRENCY='{}'; falling back to 1",
            raw,
        )
        return 1


EMBEDDING_CONCURRENCY = _get_embedding_concurrency()

# Semaphores are bound to an event loop; the API and the worker each run their
# own. Keyed weakly so a discarded loop does not pin its gate in memory.
_provider_gates: "WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    WeakKeyDictionary()
)


def _provider_gate() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    gate = _provider_gates.get(loop)
    if gate is None:
        gate = asyncio.Semaphore(EMBEDDING_CONCURRENCY)
        _provider_gates[loop] = gate
    return gate


def _l2_norm(vec: List[float]) -> float:
    return math.sqrt(sum(x * x for x in vec))


def _normalize(vec: List[float]) -> List[float]:
    norm = _l2_norm(vec)
    if norm <= 0:
        return list(vec)
    return [x / norm for x in vec]


async def mean_pool_embeddings(embeddings: List[List[float]]) -> List[float]:
    """
    Combine multiple embeddings into a single embedding using mean pooling.

    Algorithm:
    1. Normalize each embedding to unit length
    2. Compute element-wise mean
    3. Normalize the result to unit length
    """
    if not embeddings:
        raise ValueError("Cannot mean pool empty list of embeddings")

    dim = len(embeddings[0])
    if dim == 0:
        raise ValueError("Cannot mean pool zero-dimension embeddings")
    for emb in embeddings:
        if len(emb) != dim:
            raise ValueError("All embeddings must have the same dimension")

    if len(embeddings) == 1:
        return _normalize(list(embeddings[0]))

    normalized = [_normalize(list(emb)) for emb in embeddings]
    mean = [sum(row[i] for row in normalized) / len(normalized) for i in range(dim)]
    return _normalize(mean)


async def generate_embeddings(
    texts: List[str], command_id: Optional[str] = None
) -> List[List[float]]:
    """
    Generate embeddings for multiple texts with automatic batching and retry.

    Texts are split into batches of EMBEDDING_BATCH_SIZE to avoid exceeding
    provider payload limits. Each batch is retried up to EMBEDDING_MAX_RETRIES
    times on transient failures.

    Args:
        texts: List of text strings to embed
        command_id: Optional command ID for error logging context

    Returns:
        List of embedding vectors, one per input text

    Raises:
        ValueError: If no embedding model is configured
        RuntimeError: If embedding generation fails
    """
    if not texts:
        return []

    # Lazy import to avoid circular dependency
    from open_notebook.ai.models import model_manager

    embedding_model = await model_manager.get_embedding_model()
    if not embedding_model:
        raise ValueError(
            "No embedding model configured. Please configure one in the Models section."
        )

    model_name = getattr(embedding_model, "model_name", "unknown")

    # Log text sizes for debugging
    metrics: tuple[int, int, int, int] | None = None

    def _get_size_metrics() -> tuple[int, int, int, int]:
        nonlocal metrics
        if metrics is None:
            token_sizes = [token_count(t) for t in texts]
            metrics = (
                min(token_sizes),
                max(token_sizes),
                sum(token_sizes),
                sum(len(t) for t in texts),
            )
        return metrics

    logger.opt(lazy=True).debug(
        "Generating embeddings for {} texts "
        "(tokens: min={}, max={}, total={}; chars: total={})",
        lambda: len(texts),
        lambda: _get_size_metrics()[0],
        lambda: _get_size_metrics()[1],
        lambda: _get_size_metrics()[2],
        lambda: _get_size_metrics()[3],
    )

    all_embeddings: List[List[float]] = []
    total_batches = (len(texts) + EMBEDDING_BATCH_SIZE - 1) // EMBEDDING_BATCH_SIZE

    for batch_idx in range(total_batches):
        start = batch_idx * EMBEDDING_BATCH_SIZE
        end = start + EMBEDDING_BATCH_SIZE
        batch = texts[start:end]

        for attempt in range(1, EMBEDDING_MAX_RETRIES + 1):
            try:
                async with _provider_gate():
                    batch_embeddings = await embedding_model.aembed(batch)
                all_embeddings.extend(batch_embeddings)
                break
            except Exception as e:
                cmd_context = f" (command: {command_id})" if command_id else ""
                if attempt < EMBEDDING_MAX_RETRIES:
                    logger.debug(
                        f"Embedding batch {batch_idx + 1}/{total_batches} "
                        f"attempt {attempt}/{EMBEDDING_MAX_RETRIES} failed "
                        f"using model '{model_name}'{cmd_context}: {e}. Retrying..."
                    )
                    await asyncio.sleep(EMBEDDING_RETRY_DELAY)
                else:
                    logger.debug(
                        f"Embedding batch {batch_idx + 1}/{total_batches} "
                        f"failed after {EMBEDDING_MAX_RETRIES} attempts "
                        f"using model '{model_name}'{cmd_context}: {e}"
                    )
                    raise RuntimeError(
                        f"Failed to generate embeddings using model '{model_name}' "
                        f"(batch {batch_idx + 1}/{total_batches}, "
                        f"{len(batch)} texts): {e}"
                    ) from e

    logger.debug(f"Generated {len(all_embeddings)} embeddings in {total_batches} batch(es)")
    return all_embeddings


async def generate_embedding(
    text: str,
    content_type: Optional[ContentType] = None,
    file_path: Optional[str] = None,
    command_id: Optional[str] = None,
) -> List[float]:
    """
    Generate a single embedding for text, handling large content via chunking and mean pooling.

    For short text (<= CHUNK_SIZE tokens):
        - Embeds directly and returns the embedding

    For long text (> CHUNK_SIZE tokens):
        - Chunks the text using appropriate splitter for content type
        - Embeds all chunks in batches
        - Combines embeddings via mean pooling

    Args:
        text: The text to embed
        content_type: Optional explicit content type for chunking
        file_path: Optional file path for content type detection
        command_id: Optional command ID for error logging context

    Returns:
        Single embedding vector (list of floats)

    Raises:
        ValueError: If text is empty or no embedding model configured
        RuntimeError: If embedding generation fails
    """
    if not text or not text.strip():
        raise ValueError("Cannot generate embedding for empty text")

    text = text.strip()
    text_tokens = token_count(text)

    # Check if chunking is needed
    if text_tokens <= CHUNK_SIZE:
        # Short text - embed directly
        logger.debug(f"Embedding short text ({text_tokens} tokens) directly")
        embeddings = await generate_embeddings([text], command_id=command_id)
        return embeddings[0]

    # Long text - chunk and mean pool
    logger.debug(f"Text exceeds chunk size ({text_tokens} tokens), chunking...")

    chunks = chunk_text(text, content_type=content_type, file_path=file_path)

    if not chunks:
        raise ValueError("Text chunking produced no chunks")

    if len(chunks) == 1:
        # Single chunk after splitting
        embeddings = await generate_embeddings(chunks, command_id=command_id)
        return embeddings[0]

    logger.debug(f"Embedding {len(chunks)} chunks and mean pooling")

    # Embed all chunks in batches
    embeddings = await generate_embeddings(chunks, command_id=command_id)

    # Mean pool to get single embedding
    pooled = await mean_pool_embeddings(embeddings)

    logger.debug(f"Mean pooled {len(embeddings)} embeddings into single vector")
    return pooled
