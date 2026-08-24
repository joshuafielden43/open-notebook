"""Long-job liveness contract for multi-minute surreal-commands work (#1605).

**Rule:** any command that can run longer than a few minutes MUST:

1. Call :func:`touch_command_heartbeat` at start (so the reaper's birth
   certificate is not the only stamp).
2. Pulse heartbeats while opaque work runs (LLM calls, graph invoke, bulk
   embed) — use :class:`LongJobPulse` as an async context manager.
3. Observe cooperative cancel via :func:`command_was_cancelled` on each
   pulse (or at stage boundaries). Cancel is operator-forced
   ``status=failed`` on the command row (ADR-004 / mark_command_failed).

Podcast stages already pulse (~10s) and check cancel. Source processing and
transformations historically only stamped at a few boundaries; wrap their
long ``ainvoke`` windows with this helper.

The global stale reaper (default 90 minutes without ``command.updated``)
kills zombies. Heartbeats keep live jobs off that path.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable, Optional

from loguru import logger

from open_notebook.jobs.recovery import command_was_cancelled, touch_command_heartbeat

# Source / transform graphs can run multi-minute with no intermediate writes.
DEFAULT_LONG_JOB_PULSE_SECONDS = 30.0
# Podcast outline/transcript/TTS already use a tighter pulse in generation.py.
PODCAST_PULSE_SECONDS = 10.0


class JobCancelledError(Exception):
    """Operator cancelled (or force-failed) this command while it was running."""

    def __init__(self, command_id: str):
        self.command_id = command_id
        super().__init__(f"Command {command_id} was cancelled")


@asynccontextmanager
async def long_job_pulse(
    command_id: Optional[str],
    *,
    interval_seconds: float = DEFAULT_LONG_JOB_PULSE_SECONDS,
    on_cancelled: Optional[Callable[[str], Exception]] = None,
) -> AsyncIterator[None]:
    """Pulse command.updated and observe cancel while awaiting long work.

    Usage::

        async with long_job_pulse(command_id):
            await source_graph.ainvoke(...)

    When ``command_id`` is missing, this is a no-op context (tests / local).
    """
    if not command_id:
        yield
        return

    await touch_command_heartbeat(command_id)
    if await command_was_cancelled(command_id):
        err_factory = on_cancelled or JobCancelledError
        raise err_factory(command_id)

    stop = asyncio.Event()
    cancelled = False

    async def _pulse() -> None:
        nonlocal cancelled
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
                return
            except asyncio.TimeoutError:
                pass
            try:
                await touch_command_heartbeat(command_id)
                if await command_was_cancelled(command_id):
                    cancelled = True
                    return
            except Exception as e:
                logger.debug(f"long_job_pulse tick failed for {command_id}: {e}")

    task = asyncio.create_task(_pulse())
    try:
        yield
    finally:
        stop.set()
        try:
            await task
        except Exception as e:
            logger.debug(f"long_job_pulse task end for {command_id}: {e}")
        if cancelled:
            err_factory = on_cancelled or JobCancelledError
            raise err_factory(command_id)
