"""Tests for the shared long-job heartbeat + cancel contract (#1605)."""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from open_notebook.jobs import (
    DEFAULT_LONG_JOB_PULSE_SECONDS,
    PODCAST_PULSE_SECONDS,
    JobCancelledError,
    long_job_pulse,
)


def test_contract_exports():
    assert callable(long_job_pulse)
    assert issubclass(JobCancelledError, Exception)
    assert DEFAULT_LONG_JOB_PULSE_SECONDS == 30.0
    assert PODCAST_PULSE_SECONDS == 10.0


@pytest.mark.asyncio
async def test_long_job_pulse_noop_without_command_id():
    """Missing command_id (tests / local) must not touch recovery helpers."""
    ran = False
    async with long_job_pulse(None):
        ran = True
    assert ran


@pytest.mark.asyncio
async def test_long_job_pulse_heartbeats_and_completes(monkeypatch):
    touches: List[str] = []

    async def fake_touch(command_id):
        touches.append(command_id)

    async def fake_cancelled(command_id):
        return False

    monkeypatch.setattr(
        "open_notebook.jobs.contract.touch_command_heartbeat", fake_touch
    )
    monkeypatch.setattr(
        "open_notebook.jobs.contract.command_was_cancelled", fake_cancelled
    )

    async with long_job_pulse("command:abc", interval_seconds=0.05):
        await asyncio.sleep(0.12)

    # Initial touch + at least one interval tick.
    assert touches[0] == "command:abc"
    assert len(touches) >= 2


@pytest.mark.asyncio
async def test_long_job_pulse_raises_when_cancelled_at_entry(monkeypatch):
    async def fake_touch(command_id):
        return None

    async def fake_cancelled(command_id):
        return True

    monkeypatch.setattr(
        "open_notebook.jobs.contract.touch_command_heartbeat", fake_touch
    )
    monkeypatch.setattr(
        "open_notebook.jobs.contract.command_was_cancelled", fake_cancelled
    )

    with pytest.raises(JobCancelledError) as ei:
        async with long_job_pulse("command:dead"):
            pytest.fail("body must not run when already cancelled")
    assert ei.value.command_id == "command:dead"


@pytest.mark.asyncio
async def test_long_job_pulse_raises_custom_error_on_mid_run_cancel(monkeypatch):
    class CustomCancel(Exception):
        def __init__(self, command_id: str):
            self.command_id = command_id
            super().__init__(command_id)

    n_checks = 0

    async def fake_touch(command_id):
        return None

    async def fake_cancelled(command_id):
        nonlocal n_checks
        n_checks += 1
        # First check is at entry (still running); subsequent mid-pulse cancel.
        return n_checks > 1

    monkeypatch.setattr(
        "open_notebook.jobs.contract.touch_command_heartbeat", fake_touch
    )
    monkeypatch.setattr(
        "open_notebook.jobs.contract.command_was_cancelled", fake_cancelled
    )

    with pytest.raises(CustomCancel) as ei:
        async with long_job_pulse(
            "command:mid",
            interval_seconds=0.05,
            on_cancelled=CustomCancel,
        ):
            await asyncio.sleep(0.15)
    assert ei.value.command_id == "command:mid"


@pytest.mark.asyncio
async def test_job_cancelled_error_message():
    err = JobCancelledError("command:xyz")
    assert err.command_id == "command:xyz"
    assert "command:xyz" in str(err)
