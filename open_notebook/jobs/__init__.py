"""Jobs module: command lifecycle for surreal-commands workers.

Deep module for async work — submit/status/cancel, stale recovery, worker
readiness, and heartbeat contract. Callers should import from here rather
than ``open_notebook.utils.job_recovery`` / ``worker_status`` (those remain
compatibility shims).
"""

from open_notebook.jobs.contract import (
    DEFAULT_LONG_JOB_PULSE_SECONDS,
    PODCAST_PULSE_SECONDS,
    JobCancelledError,
    long_job_pulse,
)
from open_notebook.jobs.recovery import (
    DEFAULT_HEARTBEAT_MAX_AGE_MINUTES,
    DEFAULT_STALE_RUNNING_MINUTES,
    STALE_ERROR,
    coerce_status_detail,
    command_was_cancelled,
    fail_stale_running_commands,
    is_stale_running,
    is_worker_process,
    job_is_retryable,
    mark_command_failed,
    stale_running_threshold_minutes,
    touch_command_heartbeat,
    worker_heartbeat_file_fresh,
)
from open_notebook.jobs.status import evaluate_worker_ready, get_worker_status

__all__ = [
    "DEFAULT_HEARTBEAT_MAX_AGE_MINUTES",
    "DEFAULT_LONG_JOB_PULSE_SECONDS",
    "DEFAULT_STALE_RUNNING_MINUTES",
    "JobCancelledError",
    "PODCAST_PULSE_SECONDS",
    "STALE_ERROR",
    "coerce_status_detail",
    "command_was_cancelled",
    "evaluate_worker_ready",
    "fail_stale_running_commands",
    "get_worker_status",
    "is_stale_running",
    "is_worker_process",
    "job_is_retryable",
    "long_job_pulse",
    "mark_command_failed",
    "stale_running_threshold_minutes",
    "touch_command_heartbeat",
    "worker_heartbeat_file_fresh",
]
