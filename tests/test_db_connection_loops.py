"""Cross-event-loop safety of db_connection (chat 502 regression).

The pool's Queue belongs to the loop that created it. Sync LangGraph nodes
run on throwaway loops via run_coroutine_sync; handing them pooled
connections raised "got Future attached to a different loop" and 502'd every
chat turn once credential resolution moved to DB reads.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from open_notebook.database import repository


class FakeConn:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_off_home_loop_gets_direct_connection():
    fake = FakeConn()
    sentinel_loop = object()  # anything that is not the running loop
    pool: asyncio.Queue = asyncio.Queue()
    with (
        patch.object(repository, "_pool", pool),
        patch.object(repository, "_pool_loop", sentinel_loop),
        patch.object(repository, "_open_connection", new=AsyncMock(return_value=fake)),
    ):
        async with repository.db_connection() as db:
            assert db is fake
        assert fake.closed, "direct connection must be closed, not pooled"
        assert pool.empty(), "pool must not be touched from a foreign loop"


@pytest.mark.asyncio
async def test_home_loop_uses_pool():
    fake = FakeConn()
    pool: asyncio.Queue = asyncio.Queue()
    await pool.put(fake)
    with (
        patch.object(repository, "_pool", pool),
        patch.object(repository, "_pool_loop", asyncio.get_running_loop()),
    ):
        async with repository.db_connection() as db:
            assert db is fake
            assert pool.empty()
        assert not fake.closed, "healthy pooled connection is returned, not closed"
        assert pool.qsize() == 1
