"""Conversation runtime: async-safe graph invoke, provision bridge, checkpointer.

Sync LangGraph nodes and async FastAPI meet here so call sites do not each
invent ThreadPoolExecutor / new_event_loop glue.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import sqlite3
from functools import lru_cache
from typing import Any, Callable, Coroutine, Optional, TypeVar

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from loguru import logger

from open_notebook.config import LANGGRAPH_CHECKPOINT_FILE

T = TypeVar("T")


def run_coroutine_sync(coro_factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run an async factory from sync code (LangGraph nodes).

    If a loop is already running, execute in a worker thread with a fresh
    loop; otherwise use asyncio.run.
    """

    def _in_new_loop() -> T:
        new_loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(new_loop)
            return new_loop.run_until_complete(coro_factory())
        finally:
            new_loop.close()
            asyncio.set_event_loop(None)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_factory())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_in_new_loop).result()


@lru_cache(maxsize=1)
def get_sqlite_checkpointer() -> SqliteSaver:
    """Process-wide SqliteSaver for LangGraph chat graphs.

    Single API process only: concurrent graph_invoke/get_state use this
    connection from worker threads. WAL + busy_timeout reduce
    ``database is locked`` under tab polls and dual chat panels.
    """
    conn = sqlite3.connect(
        LANGGRAPH_CHECKPOINT_FILE,
        check_same_thread=False,
        timeout=30.0,
    )
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error as e:
        logger.warning(f"SQLite checkpointer PRAGMA setup failed: {e}")
    return SqliteSaver(conn)


def thread_config(session_id: str, **extra: Any) -> RunnableConfig:
    """Build a RunnableConfig for a chat_session thread id."""
    configurable = {"thread_id": session_id, **extra}
    return RunnableConfig(configurable=configurable)


async def graph_get_state(graph: Any, session_id: str) -> Any:
    """Async-safe graph.get_state (SqliteSaver is sync)."""
    return await asyncio.to_thread(
        graph.get_state,
        config=thread_config(session_id),
    )


async def graph_invoke(
    graph: Any,
    input_state: dict[str, Any],
    session_id: str,
    *,
    configurable: Optional[dict[str, Any]] = None,
) -> Any:
    """Async-safe graph.invoke for conversation turns."""
    cfg_extra = dict(configurable or {})
    config = thread_config(session_id, **cfg_extra)

    def _invoke() -> Any:
        return graph.invoke(input_state, config=config)

    return await asyncio.to_thread(_invoke)


async def session_message_count(graph: Any, session_id: str) -> int:
    """Message count from LangGraph state; 0 on error."""
    try:
        thread_state = await graph_get_state(graph, session_id)
        if (
            thread_state
            and thread_state.values
            and "messages" in thread_state.values
        ):
            return len(thread_state.values["messages"])
    except Exception as e:
        logger.warning(f"Could not fetch message count for session {session_id}: {e}")
    return 0
