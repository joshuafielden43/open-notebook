"""Heuristic readiness of the surreal-commands worker.

Liveness is proven by a fresh ``data/worker.heartbeat`` file written only by
the real worker process (see job_recovery / commands package). Command-table
activity is a secondary signal when jobs are in flight.

Probe failures fail closed (worker_likely_ready=False) so operators are not
told the queue is healthy when we cannot prove it. Cold installs with no
heartbeat file are NOT ready — the worker must start and stamp the file.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from open_notebook.database.repository import repo_query


def evaluate_worker_ready(
    *,
    pending: int,
    running: int,
    recent_activity: int,
    probe_error: bool = False,
    heartbeat_fresh: bool = False,
    stale_running: int = 0,
) -> bool:
    """Pure readiness rule used by get_worker_status and unit tests.

    - Probe failure → not ready (fail closed).
    - Fresh worker heartbeat → ready (idle or busy).
    - Pending/running work without a heartbeat → ready only if there is
      effective live running capacity or very recent command activity
      (worker may have crashed between heartbeats but still be processing).
    - Idle queue with no heartbeat → not ready (worker never started / dead).
    - Running rows that are all stale do not count as live capacity.
    """
    if probe_error:
        return False
    if heartbeat_fresh:
        return True
    effective_running = max(0, int(running) - max(0, int(stale_running)))
    if pending > 0 or effective_running > 0:
        return effective_running > 0 or recent_activity > 0
    return False


async def get_worker_status() -> dict[str, Any]:
    """Return pending/running counts and a fail-closed worker_likely_ready flag."""
    from open_notebook.jobs.recovery import (
        is_stale_running,
        worker_heartbeat_file_fresh,
    )

    pending = 0
    running = 0
    recent_activity = 0
    stale_running = 0
    try:
        rows = await repo_query(
            """
            SELECT status, count() AS c FROM command
            WHERE status IN ['pending', 'new', 'queued', 'running', 'completed', 'failed']
            GROUP BY status
            """
        )
        for row in rows or []:
            status = str(row.get("status") or "").lower()
            count = int(row.get("c") or 0)
            if status in {"pending", "new", "queued"}:
                pending += count
            elif status == "running":
                running += count

        try:
            running_rows = await repo_query(
                "SELECT id, status, updated FROM command WHERE status IN ['running', 'processing']"
            )
            for row in running_rows or []:
                if is_stale_running(
                    status=row.get("status"), updated=row.get("updated")
                ):
                    stale_running += 1
        except Exception:
            pass

        try:
            recent = await repo_query(
                """
                SELECT count() AS c FROM command
                WHERE status IN ['running', 'completed', 'failed']
                  AND updated > time::now() - 15m
                GROUP ALL
                """
            )
            if recent:
                recent_activity = int(recent[0].get("c") or 0)
        except Exception as window_err:
            logger.warning(f"Worker recent-activity window unavailable: {window_err}")
            recent_activity = 0
    except Exception as e:
        logger.warning(f"Worker status probe failed: {e}")
        return {
            "worker_likely_ready": False,
            "pending_command_count": 0,
            "running_command_count": 0,
            "probe_error": str(e),
        }

    heartbeat_fresh = worker_heartbeat_file_fresh()
    ready = evaluate_worker_ready(
        pending=pending,
        running=running,
        recent_activity=recent_activity,
        probe_error=False,
        heartbeat_fresh=heartbeat_fresh,
        stale_running=stale_running,
    )

    return {
        "worker_likely_ready": ready,
        "pending_command_count": pending,
        "running_command_count": running,
        "stale_running_count": stale_running,
        "worker_heartbeat_fresh": heartbeat_fresh,
    }
