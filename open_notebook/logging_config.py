"""Safe process-wide Loguru configuration."""

import sys

from loguru import logger

_configured = False


def configure_safe_logging() -> None:
    """Keep tracebacks useful without rendering frame-local secret values."""
    global _configured
    if _configured:
        return
    logger.remove()
    logger.add(sys.stderr, backtrace=True, diagnose=False)
    _configured = True
