"""Model runtime — small interface over selection + credential resolution.

Callers should use this module instead of wiring ModelManager + provision +
key_provider themselves:

- :func:`resolve_model_config` — model record id → (provider, name, config)
- :func:`chat_langchain` — language model as LangChain (graphs)
- :func:`get_embedding` — default embedding model
- :func:`get_tts` / :func:`get_stt` — speech defaults
- :func:`get_by_id` / :func:`get_default` — Esperanto model objects

Credential resolution prefers config dicts (no process-env mutation) via
:func:`open_notebook.ai.key_provider.resolve_provider_config`.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from esperanto import (
    EmbeddingModel,
    LanguageModel,
    SpeechToTextModel,
    TextToSpeechModel,
)
from langchain_core.language_models.chat_models import BaseChatModel
from loguru import logger

from open_notebook.ai.models import ModelType, model_manager
from open_notebook.exceptions import ConfigurationError
from open_notebook.utils import token_count

LARGE_CONTEXT_TOKEN_THRESHOLD = 105_000


async def resolve_model_config(
    model_id: str,
    *,
    max_tokens: Optional[int] = None,
    **kwargs: Any,
) -> Tuple[str, str, dict]:
    """Resolve a model registry id to a validated provider config triple (#1629).

    Loads the Model record, prefers its linked credential, else falls back to
    provider env config (no process-env mutation). Re-validates URL fields and
    normalizes anthropic-compatible base URLs — the same checks
    :meth:`ModelManager.get_model` uses before constructing Esperanto clients.

    Returns ``(provider, model_name, config_dict)`` where ``provider`` is the
    stored registry name (underscores). Esperanto hyphen rewrite happens only
    when building an Esperanto instance.

    This is the single config-resolution interface for podcasts, embeddings
    call sites that need a triple, and ModelManager.
    """
    from open_notebook.ai.connection_tester import (
        normalize_anthropic_compatible_base_url,
    )
    from open_notebook.ai.key_provider import resolve_provider_config
    from open_notebook.ai.models import Model, _revalidate_config_urls

    model = await Model.get(model_id)
    config: dict = {}
    if model.credential:
        credential = await model.get_credential_obj()
        if credential:
            config = credential.to_esperanto_config()
            await _revalidate_config_urls(config, model.provider)
            logger.debug(
                f"Using credential '{credential.name}' for model {model.name}"
            )
        else:
            logger.warning(
                f"Model {model.id} has credential {model.credential} but it "
                f"could not be loaded. Falling back to provider default / env."
            )
            config = await resolve_provider_config(model.provider) or {}
            if config:
                await _revalidate_config_urls(config, model.provider)
    else:
        config = await resolve_provider_config(model.provider) or {}
        if config:
            await _revalidate_config_urls(config, model.provider)

    if max_tokens is not None:
        config = {**config, "max_tokens": max_tokens}
    if kwargs:
        config = {**config, **kwargs}

    if model.provider == "anthropic_compatible" and (
        not str(config.get("api_key", "")).strip()
        or not str(config.get("base_url", "")).strip()
    ):
        raise ConfigurationError(
            "Anthropic-compatible models require a base URL and API key"
        )
    if model.provider == "anthropic_compatible":
        config = {
            **config,
            "base_url": normalize_anthropic_compatible_base_url(
                str(config["base_url"])
            ),
        }

    return (model.provider, model.name, config)


async def get_by_id(model_id: str, **kwargs: Any) -> Optional[ModelType]:
    """Resolve a stored model id to an Esperanto model instance."""
    return await model_manager.get_model(model_id, **kwargs)


async def get_default(model_type: str, **kwargs: Any) -> Optional[ModelType]:
    """Resolve a default model type (chat, embedding, large_context, …)."""
    return await model_manager.get_default_model(model_type, **kwargs)


async def get_embedding(**kwargs: Any) -> Optional[EmbeddingModel]:
    return await model_manager.get_embedding_model(**kwargs)


async def get_tts(**kwargs: Any) -> Optional[TextToSpeechModel]:
    return await model_manager.get_text_to_speech(**kwargs)


async def get_stt(**kwargs: Any) -> Optional[SpeechToTextModel]:
    return await model_manager.get_speech_to_text(**kwargs)


async def chat_langchain(
    content: Any,
    model_id: Optional[str],
    default_type: str = "chat",
    **kwargs: Any,
) -> BaseChatModel:
    """Select a language model and return its LangChain adapter.

    Selection: large_context if token estimate exceeds threshold; else explicit
    model_id; else default for ``default_type``.
    """
    tokens = token_count(content)
    model: Optional[ModelType] = None
    selection_reason = ""

    if tokens > LARGE_CONTEXT_TOKEN_THRESHOLD:
        selection_reason = f"large_context (content has {tokens} tokens)"
        logger.debug(
            f"Using large context model because the content has {tokens} tokens"
        )
        model = await get_default("large_context", **kwargs)
    elif model_id:
        selection_reason = f"explicit model_id={model_id}"
        model = await get_by_id(model_id, **kwargs)
    else:
        selection_reason = f"default for type={default_type}"
        model = await get_default(default_type, **kwargs)

    # Never repr the model object: provider clients carry api_key in their
    # repr, and DEBUG logs must not leak credentials.
    logger.debug(
        f"Using model: {getattr(model, 'name', None) or type(model).__name__} "
        f"(provider={getattr(model, 'provider', 'unknown')})"
    )

    if model is None:
        logger.error(
            f"Model provisioning failed: No model found. "
            f"Selection reason: {selection_reason}. "
            f"model_id={model_id}, default_type={default_type}. "
            f"Please check Settings → Models and ensure a default model is configured for '{default_type}'."
        )
        raise ConfigurationError(
            f"No model configured for {selection_reason}. "
            f"Please go to Settings → Models and configure a default model for '{default_type}'."
        )

    if not isinstance(model, LanguageModel):
        logger.error(
            f"Model type mismatch: Expected LanguageModel but got {type(model).__name__}. "
            f"Selection reason: {selection_reason}. "
            f"model_id={model_id}, default_type={default_type}."
        )
        raise ConfigurationError(
            f"Model is not a LanguageModel: {model}. "
            f"Please check that the model configured for '{default_type}' is a language model, not an embedding or speech model."
        )

    return model.to_langchain()


# Back-compat name used throughout graphs
provision_langchain_model = chat_langchain
