"""resolve_provider_config must not mutate os.environ."""

import os
from unittest.mock import AsyncMock, patch

import pytest

from open_notebook.ai.key_provider import resolve_provider_config


@pytest.mark.asyncio
async def test_resolve_reads_env_without_writing():
    before = dict(os.environ)
    os.environ["OPENAI_API_KEY"] = "sk-test-from-env-only"
    try:
        with patch(
            "open_notebook.ai.key_provider._get_default_credential",
            new=AsyncMock(return_value=None),
        ):
            config = await resolve_provider_config("openai")
        assert config.get("api_key") == "sk-test-from-env-only"
        # No new keys introduced by resolve (OPENAI_API_KEY already there)
        assert os.environ.get("OPENAI_API_KEY") == "sk-test-from-env-only"
        # Must not have invented other provider keys
        for k in os.environ:
            if k.startswith("ANTHROPIC_") or k.startswith("AZURE_"):
                assert k in before or os.environ[k] == before.get(k)
    finally:
        if "OPENAI_API_KEY" in before:
            os.environ["OPENAI_API_KEY"] = before["OPENAI_API_KEY"]
        else:
            os.environ.pop("OPENAI_API_KEY", None)


@pytest.mark.asyncio
async def test_resolve_prefers_credential_config():
    cred = type(
        "C",
        (),
        {
            "to_esperanto_config": lambda self: {
                "api_key": "sk-from-db",
                "base_url": "http://local",
            },
        },
    )()
    with patch(
        "open_notebook.ai.key_provider._get_default_credential",
        new=AsyncMock(return_value=cred),
    ):
        config = await resolve_provider_config("openai")
    assert config == {"api_key": "sk-from-db", "base_url": "http://local"}


@pytest.mark.asyncio
async def test_chat_langchain_raises_configuration_error():
    from open_notebook.ai.runtime import chat_langchain
    from open_notebook.exceptions import ConfigurationError

    with patch(
        "open_notebook.ai.runtime.get_default",
        new=AsyncMock(return_value=None),
    ):
        with pytest.raises(ConfigurationError):
            await chat_langchain("hi", model_id=None, default_type="chat")
