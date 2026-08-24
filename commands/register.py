"""Single seam for surreal-commands registration (#1631).

Importing a command module runs its ``@command`` decorator and registers the
job in the process-local registry. Call :func:`ensure_commands_registered`
before any ``submit_command`` so product jobs, including reports, do not depend
on which router happened to import first.
"""

from __future__ import annotations

from loguru import logger

_registered = False


def ensure_commands_registered() -> None:
    """Idempotently import every worker command module once."""
    global _registered
    if _registered:
        return
    try:
        import commands.embedding_commands  # noqa: F401
        import commands.podcast_commands  # noqa: F401
        import commands.report_commands  # noqa: F401
        import commands.source_commands  # noqa: F401
    except ImportError as import_err:
        logger.error(f"Failed to import command modules: {import_err}")
        raise ValueError("Command modules not available") from import_err
    _registered = True
