"""One-shot migration: copy source_embedding rows from SurrealDB to Qdrant.

Run inside the app container once, after enabling qdrant mode:

    uv run python -m open_notebook.vectorstore.backfill [--delete-surreal]

Reads every source_embedding row, upserts deterministic points (uuid5 of
source_id:order, so re-runs are idempotent), verifies the point count, and
only with --delete-surreal removes the surreal rows afterwards.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from loguru import logger

from open_notebook.database.repository import repo_query
from open_notebook.vectorstore.qdrant import (
    count_points,
    ensure_collection,
    upsert_chunks,
)

BATCH = 200


async def run(delete_surreal: bool) -> int:
    rows = await repo_query(
        "SELECT source, `order`, content, embedding FROM source_embedding "
        "WHERE embedding != NONE ORDER BY source, `order`"
    )
    if not rows:
        logger.info("No source_embedding rows to migrate")
        return 0

    dimension = len(rows[0]["embedding"])
    await ensure_collection(dimension)

    # Group rows per source so upsert_chunks' order-derived point ids match
    # the live write path exactly.
    by_source: dict[str, list[dict]] = {}
    for row in rows:
        by_source.setdefault(str(row["source"]), []).append(row)

    total = 0
    for source_id, source_rows in by_source.items():
        source_rows.sort(key=lambda r: r.get("order") or 0)
        chunks = [r["content"] for r in source_rows]
        embeddings = [r["embedding"] for r in source_rows]
        for start in range(0, len(chunks), BATCH):
            total += await upsert_chunks(
                source_id,
                chunks[start : start + BATCH],
                embeddings[start : start + BATCH],
                start_order=start,
            )

    points = await count_points()
    logger.info(
        f"Migrated {total} rows across {len(by_source)} sources; "
        f"collection now holds {points} points"
    )
    if points < total:
        logger.error("Point count below migrated rows — NOT deleting surreal rows")
        return 1

    if delete_surreal:
        await repo_query("DELETE source_embedding")
        logger.info("Deleted surreal source_embedding rows")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delete-surreal", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.delete_surreal)))


if __name__ == "__main__":
    main()
