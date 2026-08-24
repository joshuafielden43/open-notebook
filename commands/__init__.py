"""Surreal-commands integration for Open Notebook"""

from open_notebook.logging_config import configure_safe_logging

configure_safe_logging()

# The worker starts via `surreal-commands-worker --import-modules commands`,
# so this package is imported before the worker connects to SurrealDB. Inject
# the internal DB hosts into no_proxy first so the DB websocket is never
# tunnelled through a configured HTTP proxy (issue #1160).
from open_notebook.utils.proxy import ensure_internal_no_proxy

ensure_internal_no_proxy()

# Stamp data/worker.heartbeat ONLY in the real worker process. The API also
# imports command modules for submit_command validation; if we started a
# heartbeat thread on every import, /health would lie that the worker is up.
import threading
import time

_heartbeat_started = False


def _worker_heartbeat_loop() -> None:
    while True:
        try:
            from open_notebook.jobs.recovery import _write_worker_heartbeat_file

            _write_worker_heartbeat_file()
        except Exception:
            pass
        time.sleep(30)


def _ensure_worker_heartbeat_thread() -> None:
    global _heartbeat_started
    if _heartbeat_started:
        return
    from open_notebook.jobs import is_worker_process

    if not is_worker_process():
        return
    _heartbeat_started = True
    threading.Thread(
        target=_worker_heartbeat_loop,
        name="open-notebook-worker-heartbeat",
        daemon=True,
    ).start()


_ensure_worker_heartbeat_thread()

from .embedding_commands import (
    embed_insight_command,
    embed_note_command,
    embed_source_command,
    rebuild_embeddings_command,
)
from .podcast_commands import generate_podcast_command
from .source_commands import process_source_command

__all__ = [
    # Embedding commands
    "embed_note_command",
    "embed_insight_command",
    "embed_source_command",
    "rebuild_embeddings_command",
    # Other commands
    "generate_podcast_command",
    "process_source_command",
]
