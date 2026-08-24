"""Unit tests for stale-running recovery and readiness pure rules."""

from datetime import datetime, timedelta, timezone

import pytest

from open_notebook.jobs import (
    STALE_ERROR,
    coerce_status_detail,
    evaluate_worker_ready,
    is_stale_running,
    job_is_retryable,
)


def test_is_stale_running_true_after_threshold():
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(minutes=50)
    assert is_stale_running(
        status="running",
        updated=old.isoformat(),
        now=now,
        threshold_minutes=45,
    )


def test_is_stale_running_false_when_fresh():
    now = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)
    recent = now - timedelta(minutes=5)
    assert not is_stale_running(
        status="running",
        updated=recent.isoformat(),
        now=now,
        threshold_minutes=45,
    )


def test_is_stale_running_false_when_not_running():
    assert not is_stale_running(
        status="completed",
        updated=None,
        threshold_minutes=45,
    )


def test_newborn_running_without_heartbeat_is_not_stale():
    """Regression: command:h8xu... — surreal-commands never writes `updated`,
    so a just-submitted running job carries NONE until its first heartbeat.
    Treating NONE as stale let any status poll reap a healthy newborn ~300ms
    after submit ("was cancelled; aborting generation")."""
    assert not is_stale_running(
        status="running",
        updated=None,
        threshold_minutes=45,
    )


def test_coerce_status_detail_keeps_newborn_running():
    detail = coerce_status_detail(
        {"status": "running", "error_message": None, "updated": None}
    )
    assert detail["status"] == "running"
    assert "stale_recovered" not in detail


@pytest.mark.asyncio
async def test_reaper_stamps_newborns_and_reaps_only_lapsed():
    """Bulk reaper: phase A stamps NONE-updated running rows (birth
    certificate); phase B fails only rows whose stamped clock lapsed."""
    from unittest.mock import patch

    from open_notebook.jobs import recovery

    queries = []

    async def fake_repo(query_str, vars=None):
        queries.append(" ".join(query_str.split()))
        if "RETURN AFTER" in query_str:
            return []  # nothing lapsed
        return []

    with patch.object(recovery, "repo_query", new=fake_repo):
        count = await recovery.fail_stale_running_commands(threshold_minutes=45)

    assert count == 0
    stamp = queries[0]
    assert "SET updated = time::now()" in stamp
    assert "updated IS NONE" in stamp
    assert "status = 'failed'" not in stamp
    reap = queries[1]
    assert "status = 'failed'" in reap
    assert "IS NONE" not in reap


def test_coerce_status_detail_rewrites_stale_running():
    now = datetime.now(timezone.utc)
    old = (now - timedelta(hours=2)).isoformat()
    detail = coerce_status_detail(
        {"status": "running", "error_message": None, "updated": old}
    )
    assert detail["status"] == "failed"
    assert STALE_ERROR in (detail.get("error_message") or "")


def test_job_is_retryable_statuses():
    assert job_is_retryable("failed")
    assert job_is_retryable("error")
    assert not job_is_retryable("running")
    assert not job_is_retryable("pending")


def test_evaluate_worker_ready_fail_closed_on_probe_error():
    assert not evaluate_worker_ready(
        pending=0, running=0, recent_activity=0, probe_error=True
    )


def test_evaluate_worker_ready_pending_without_activity_is_not_ready():
    assert not evaluate_worker_ready(
        pending=3, running=0, recent_activity=0, probe_error=False
    )


def test_evaluate_worker_ready_pending_with_running_is_ready():
    assert evaluate_worker_ready(
        pending=3, running=1, recent_activity=0, probe_error=False
    )


def test_evaluate_worker_ready_idle_requires_heartbeat_or_cold_boot_signal():
    # Idle without a fresh heartbeat is not ready (worker process missing).
    assert not evaluate_worker_ready(
        pending=0,
        running=0,
        recent_activity=0,
        probe_error=False,
        heartbeat_fresh=False,
    )
    assert evaluate_worker_ready(
        pending=0,
        running=0,
        recent_activity=0,
        probe_error=False,
        heartbeat_fresh=True,
    )


def test_evaluate_worker_ready_pending_with_only_stale_running_needs_heartbeat():
    assert not evaluate_worker_ready(
        pending=2,
        running=2,
        recent_activity=0,
        probe_error=False,
        heartbeat_fresh=False,
        stale_running=2,
    )
    assert evaluate_worker_ready(
        pending=2,
        running=2,
        recent_activity=0,
        probe_error=False,
        heartbeat_fresh=True,
        stale_running=2,
    )


def test_evaluate_worker_ready_heartbeat_alone_is_ready_when_idle():
    assert evaluate_worker_ready(
        pending=0,
        running=0,
        recent_activity=0,
        heartbeat_fresh=True,
    )


def test_evaluate_worker_ready_idle_without_heartbeat_never_ready():
    """Cold install must not claim ready; worker must stamp heartbeat."""
    assert not evaluate_worker_ready(
        pending=0,
        running=0,
        recent_activity=0,
        heartbeat_fresh=False,
    )


def test_touch_command_heartbeat_exported():
    from open_notebook.jobs import (
        command_was_cancelled,
        is_worker_process,
        touch_command_heartbeat,
    )

    assert callable(touch_command_heartbeat)
    assert callable(command_was_cancelled)
    assert callable(is_worker_process)


def test_is_worker_process_false_for_api_style_argv(monkeypatch):
    """API process (uvicorn / run_api) must not count as the worker."""
    import sys

    from open_notebook.jobs import is_worker_process

    monkeypatch.delenv("OPEN_NOTEBOOK_IS_WORKER", raising=False)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "api.main:app", "--port", "5055"])
    assert is_worker_process() is False


def test_is_worker_process_true_for_cli_worker_argv(monkeypatch):
    import sys

    from open_notebook.jobs import is_worker_process

    monkeypatch.delenv("OPEN_NOTEBOOK_IS_WORKER", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["surreal-commands-worker", "--import-modules", "commands"],
    )
    assert is_worker_process() is True


def test_is_worker_process_true_for_env_flag(monkeypatch):
    import sys

    from open_notebook.jobs import is_worker_process

    monkeypatch.setenv("OPEN_NOTEBOOK_IS_WORKER", "1")
    monkeypatch.setattr(sys, "argv", ["python", "-c", "import commands"])
    assert is_worker_process() is True


def test_write_worker_heartbeat_skipped_outside_worker(monkeypatch, tmp_path):
    """API-side import/call must not forge data/worker.heartbeat."""
    from pathlib import Path

    import open_notebook.jobs.recovery as recovery

    monkeypatch.setattr(recovery, "is_worker_process", lambda: False)
    monkeypatch.setattr("open_notebook.config.DATA_FOLDER", str(tmp_path))
    recovery._write_worker_heartbeat_file()
    assert not (Path(tmp_path) / "worker.heartbeat").exists()


def test_write_worker_heartbeat_writes_inside_worker(monkeypatch, tmp_path):
    from pathlib import Path

    import open_notebook.jobs.recovery as recovery

    monkeypatch.setattr(recovery, "is_worker_process", lambda: True)
    monkeypatch.setattr("open_notebook.config.DATA_FOLDER", str(tmp_path))
    recovery._write_worker_heartbeat_file()
    assert (Path(tmp_path) / "worker.heartbeat").is_file()


def test_api_style_commands_import_does_not_start_heartbeat_thread(monkeypatch):
    """Importing commands the way the API does must not start a pulse thread.

    The API imports command modules for submit_command validation. That path
    used to start a daemon that forged worker.heartbeat and made /health lie.
    """
    import importlib
    import sys
    import threading

    monkeypatch.delenv("OPEN_NOTEBOOK_IS_WORKER", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "5055"],
    )

    # Force re-import with API-style argv (module may already be loaded).
    sys.modules.pop("commands", None)
    # Keep submodules if present; only re-exec package init logic we care about.
    import commands as commands_pkg

    importlib.reload(commands_pkg)

    assert commands_pkg._heartbeat_started is False
    names = [t.name for t in threading.enumerate()]
    assert "open-notebook-worker-heartbeat" not in names


@pytest.mark.asyncio
async def test_command_was_cancelled_true_on_failed_row():
    from unittest.mock import AsyncMock, patch

    from open_notebook.jobs import command_was_cancelled

    with patch(
        "open_notebook.jobs.recovery.repo_query",
        new=AsyncMock(return_value=[{"status": "failed"}]),
    ):
        assert await command_was_cancelled("command:abc")


@pytest.mark.asyncio
async def test_command_was_cancelled_false_on_running():
    from unittest.mock import AsyncMock, patch

    from open_notebook.jobs import command_was_cancelled

    with patch(
        "open_notebook.jobs.recovery.repo_query",
        new=AsyncMock(return_value=[{"status": "running"}]),
    ):
        assert not await command_was_cancelled("command:abc")
