"""Worker command adapter for podcast generation.

Job lifecycle and generation live in
:mod:`open_notebook.podcasts.episode_generation`. This module only maps
command I/O onto that interface and shapes failure messages for operators.

Do not add ``from __future__ import annotations`` here. surreal-commands
validates submit args via LangChain's ``RunnableLambda.get_input_schema()``,
which builds a Pydantic model from the function annotations. With postponed
evaluation those annotations stay as ForwardRefs and submit raises
PydanticUserError (class-not-fully-defined) → API 500.
"""

from typing import Optional

from loguru import logger
from surreal_commands import CommandInput, CommandOutput, command

from open_notebook.podcasts.episode_generation import (
    EpisodeGenerationRequest,
    build_episode_output_dir,
    cleanup_failed_episode_dir,
    run_episode_generation,
)
from open_notebook.podcasts.generation import AudioQualityError, PodcastCancelledError

# Re-export path helpers so existing imports/tests keep working.
__all__ = [
    "PodcastGenerationInput",
    "PodcastGenerationOutput",
    "build_episode_output_dir",
    "cleanup_failed_episode_dir",
    "generate_podcast_command",
]


class PodcastGenerationInput(CommandInput):
    episode_profile: str
    # Speaker profile record ID or name (the API boundary resolves the
    # user-facing name to a record ID before submitting; both are accepted
    # here for robustness).
    speaker_profile: Optional[str] = None
    episode_name: str
    # Optional: prefer content-by-reference (#1603). When omitted, the worker
    # loads text from the episode row or assembles via for_podcast(notebook_id).
    content: Optional[str] = None
    briefing_suffix: Optional[str] = None
    # When set (retry path / entity-first submit), reuse this episode row.
    existing_episode_id: Optional[str] = None
    # Notebook that supplied context (worker assembles when content omitted).
    notebook_id: Optional[str] = None


class PodcastGenerationOutput(CommandOutput):
    success: bool
    episode_id: Optional[str] = None
    audio_file_path: Optional[str] = None
    transcript: Optional[dict] = None
    outline: Optional[dict] = None
    processing_time: float
    error_message: Optional[str] = None


@command("generate_podcast", app="open_notebook", retry={"max_attempts": 1})
async def generate_podcast_command(
    input_data: PodcastGenerationInput,
) -> PodcastGenerationOutput:
    """Run one podcast job via the Episode Generation module."""
    command_id = (
        str(input_data.execution_context.command_id)
        if input_data.execution_context
        else None
    )
    # Heartbeat before any setup: the row goes `running` with no timestamp
    # (surreal-commands writes none), and an unstamped running row must not
    # linger — belt to the reaper's birth-certificate suspenders.
    if command_id:
        from open_notebook.jobs import touch_command_heartbeat

        await touch_command_heartbeat(command_id)
    try:
        result = await run_episode_generation(
            EpisodeGenerationRequest(
                episode_profile=input_data.episode_profile,
                speaker_profile=input_data.speaker_profile,
                episode_name=input_data.episode_name,
                content=input_data.content,
                briefing_suffix=input_data.briefing_suffix,
                existing_episode_id=input_data.existing_episode_id,
                command_id=command_id,
                notebook_id=input_data.notebook_id,
            )
        )
        return PodcastGenerationOutput(
            success=True,
            episode_id=result.episode_id,
            audio_file_path=result.audio_file_path,
            transcript=result.transcript,
            outline=result.outline,
            processing_time=result.processing_time,
        )
    except AudioQualityError as e:
        logger.error(f"Podcast audio quality failed: {e}")
        raise RuntimeError(f"Podcast audio generation failed: {e}") from e
    except PodcastCancelledError as e:
        logger.warning(f"Podcast generation cancelled: {e}")
        raise RuntimeError(str(e)) from e
    except ValueError:
        raise
    except Exception as e:
        logger.error(f"Podcast generation failed: {e}")
        logger.exception(e)
        error_msg = str(e)
        if "Invalid json output" in error_msg or "Expecting value" in error_msg:
            error_msg += (
                "\n\nNOTE: This error commonly occurs with GPT-5 models that use "
                "extended thinking. The model may be putting all output inside "
                "<think> tags, leaving nothing to parse. Try using gpt-4o, "
                "gpt-4o-mini, or gpt-4-turbo instead in your episode profile."
            )
        raise RuntimeError(error_msg) from e
