"""Recover stuck command jobs after worker death.

surreal-commands has no cancel API we can rely on. When a worker dies mid-job
the command row stays `running` forever and retry is blocked. This module
marks stale running rows as failed so operators can retry.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger

from open_notebook.database.repository import ensure_record_id, repo_query

# Default: a command that has been "running" this long without a heartbeat
# (command.updated) is dead. Progress stages must touch the command row.
DEFAULT_STALE_RUNNING_MINUTES = 90
STALE_ERROR = (
    "Job marked failed: no progress for too long (worker likely restarted "
    "or died). Retry to start a new generation."
)


def stale_running_threshold_minutes() -> int:
    raw = os.getenv(
        "OPEN_NOTEBOOK_STALE_COMMAND_MINUTES",
        str(DEFAULT_STALE_RUNNING_MINUTES),
    )
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_STALE_RUNNING_MINUTES
    return max(5, value)


def is_stale_running(
    *,
    status: Optional[str],
    updated: Optional[object],
    now: Optional[datetime] = None,
    threshold_minutes: Optional[int] = None,
) -> bool:
    """Return True when a running/processing job has not updated within threshold."""
    if str(status or "").lower() not in {"running", "processing"}:
        return False
    if updated is None:
        # No heartbeat yet is a NEWBORN, not a zombie: surreal-commands never
        # writes `updated`, so a freshly-running job carries NONE until its
        # first touch_command_heartbeat (~hundreds of ms). Treating NONE as
        # stale let any status poll reap a healthy just-submitted job. The
        # reaper stamps NONE rows (birth certificate) so a true zombie still
        # dies one threshold after being first observed.
        return False
    threshold = threshold_minutes or stale_running_threshold_minutes()
    current = now or datetime.now(timezone.utc)
    if isinstance(updated, str):
        try:
            updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
        except ValueError:
            return True
    elif isinstance(updated, datetime):
        updated_dt = updated
    else:
        return True
    if updated_dt.tzinfo is None:
        updated_dt = updated_dt.replace(tzinfo=timezone.utc)
    return current - updated_dt >= timedelta(minutes=threshold)


async def fail_stale_running_commands(
    *,
    threshold_minutes: Optional[int] = None,
) -> int:
    """Mark stale running commands as failed. Returns count updated."""
    minutes = threshold_minutes or stale_running_threshold_minutes()
    try:
        # Phase A — birth certificate: a running row with no `updated` has
        # simply never heartbeat (surreal-commands doesn't write timestamps).
        # Stamp it now so its staleness clock starts at first observation;
        # never fail it on sight, or every newborn job dies in the window
        # between going `running` and its first heartbeat.
        await repo_query(
            """
            UPDATE command SET updated = time::now()
            WHERE status IN ['running', 'processing'] AND updated IS NONE
            """
        )
        # Phase B — reap only rows that had a clock and let it lapse.
        result = await repo_query(
            f"""
            UPDATE command SET
                status = 'failed',
                error_message = $error
            WHERE status IN ['running', 'processing']
              AND updated < time::now() - {int(minutes)}m
            RETURN AFTER
            """,
            {"error": STALE_ERROR},
        )
        count = len(result or [])
        if count:
            logger.warning(f"Marked {count} stale running command(s) as failed")
        return count
    except Exception as e:
        logger.warning(f"Bulk stale-command update failed, falling back: {e}")

    # Fallback: fetch running rows and update individually.
    try:
        rows = await repo_query(
            "SELECT id, status, updated FROM command WHERE status IN ['running', 'processing']"
        )
    except Exception as fetch_err:
        logger.error(f"Failed to list running commands for stale recovery: {fetch_err}")
        return 0

    updated = 0
    now = datetime.now(timezone.utc)
    for row in rows or []:
        if row.get("updated") is None:
            # Same birth-certificate rule as the bulk path: stamp, don't reap.
            try:
                await repo_query(
                    "UPDATE $id SET updated = time::now()",
                    {"id": ensure_record_id(str(row["id"]))},
                )
            except Exception:
                pass
            continue
        if not is_stale_running(
            status=row.get("status"),
            updated=row.get("updated"),
            now=now,
            threshold_minutes=minutes,
        ):
            continue
        try:
            await repo_query(
                """
                UPDATE $id SET
                    status = 'failed',
                    error_message = $error
                """,
                {
                    "id": ensure_record_id(str(row["id"])),
                    "error": STALE_ERROR,
                },
            )
            updated += 1
        except Exception as row_err:
            logger.warning(f"Failed to mark command {row.get('id')} stale: {row_err}")
    if updated:
        logger.warning(f"Marked {updated} stale running command(s) as failed (fallback)")
    return updated


async def touch_command_heartbeat(command_id: Optional[str]) -> None:
    """Bump command.updated so the stale reaper does not kill live long jobs.

    surreal-commands merge() does not always advance `updated` during
    execution. Podcast progress stages must call this explicitly.
    """
    if not command_id:
        return
    try:
        await repo_query(
            "UPDATE $id SET updated = time::now()",
            {"id": ensure_record_id(str(command_id))},
        )
        # Also stamp a host-local heartbeat so /health can prove a worker
        # process has run recently even when the queue is idle afterward.
        _write_worker_heartbeat_file()
    except Exception as e:
        logger.debug(f"Command heartbeat touch failed for {command_id}: {e}")


async def command_was_cancelled(command_id: Optional[str]) -> bool:
    """True when the command row was force-failed/cancelled by an operator."""
    if not command_id:
        return False
    try:
        rows = await repo_query(
            "SELECT status FROM $id",
            {"id": ensure_record_id(str(command_id))},
        )
        if not rows:
            return False
        status = str(rows[0].get("status") or "").lower()
        return status in {"failed", "error", "cancelled", "canceled"}
    except Exception:
        return False


def is_worker_process() -> bool:
    """True only in a surreal-commands worker process — never the API.

    Detection order:
    1. ``OPEN_NOTEBOOK_IS_WORKER=1`` (set by supervisord/Makefile for the worker).
    2. ``sys.argv`` contains ``surreal-commands-worker`` (CLI entrypoint).

    Importing ``commands`` from the API for submit_command validation must
    NOT count as a live worker (readiness false-positive).
    """
    import os
    import sys

    flag = os.environ.get("OPEN_NOTEBOOK_IS_WORKER", "").strip().lower()
    if flag in {"1", "true", "yes", "on"}:
        return True
    return any("surreal-commands-worker" in part for part in sys.argv)


def _write_worker_heartbeat_file() -> None:
    """Stamp the heartbeat file. Call only from a real worker process."""
    from pathlib import Path

    from open_notebook.config import DATA_FOLDER

    if not is_worker_process():
        # Defense in depth: API/import paths must never forge readiness.
        return

    path = Path(DATA_FOLDER) / "worker.heartbeat"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
    except Exception as e:
        logger.debug(f"Worker heartbeat file write failed: {e}")


# Worker process pulses every 30s. Treat the file as stale after a few missed
# pulses so a dead worker cannot look healthy for half an hour.
DEFAULT_HEARTBEAT_MAX_AGE_MINUTES = 2


def worker_heartbeat_file_fresh(
    *,
    max_age_minutes: int = DEFAULT_HEARTBEAT_MAX_AGE_MINUTES,
    now: Optional[datetime] = None,
) -> bool:
    """True when data/worker.heartbeat was written within max_age_minutes."""
    from pathlib import Path

    from open_notebook.config import DATA_FOLDER

    path = Path(DATA_FOLDER) / "worker.heartbeat"
    if not path.is_file():
        return False
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return False
    current = now or datetime.now(timezone.utc)
    return current - mtime <= timedelta(minutes=max_age_minutes)


async def mark_command_failed(
    command_id: str,
    error_message: str = "Job cancelled by operator",
) -> bool:
    """Force a command row into failed so retry is allowed."""
    if not command_id:
        return False
    try:
        await repo_query(
            """
            UPDATE $id SET
                status = 'failed',
                error_message = $error
            """,
            {
                "id": ensure_record_id(command_id),
                "error": error_message,
            },
        )
        return True
    except Exception as e:
        logger.error(f"Failed to mark command {command_id} failed: {e}")
        return False


def job_is_retryable(status: Optional[str]) -> bool:
    """Statuses from which a new generation job may be submitted."""
    return str(status or "").lower() in {
        "failed",
        "error",
        "cancelled",
        "canceled",
    }


def coerce_status_detail(detail: dict[str, Any]) -> dict[str, Any]:
    """If the detail looks stale-running, rewrite to failed for the client."""
    status = detail.get("status")
    if is_stale_running(status=status, updated=detail.get("updated")):
        return {
            **detail,
            "status": "failed",
            "error_message": detail.get("error_message") or STALE_ERROR,
            "stale_recovered": True,
        }
    return detail
