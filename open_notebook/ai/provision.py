"""Compatibility shim — prefer ``open_notebook.ai.runtime``.

``provision_langchain_model`` remains the historical import path for graphs.
"""

from open_notebook.ai.runtime import (  # noqa: F401
    LARGE_CONTEXT_TOKEN_THRESHOLD,
    chat_langchain,
    provision_langchain_model,
)
