"""Episode Generation — deep module for one PodcastEpisode job.

Interface: :func:`run_episode_generation` takes a job request and returns a
result. Callers (worker command, future API paths) do not orchestrate
profiles, configure lock, quality gate, or audio path conversion themselves.

Internals: profile prep, podcast-creator configure, staged pipeline with
quality retry (generation.py), episode persistence, workdir cleanup.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from open_notebook.config import PODCASTS_FOLDER
from open_notebook.database.repository import ensure_record_id
from open_notebook.podcasts.audio_paths import to_relative_audio_path
from open_notebook.podcasts.generation import (
    AudioQualityError,
    PodcastCancelledError,
    create_podcast_with_quality_retry,
)
from open_notebook.podcasts.models import PodcastEpisode
from open_notebook.podcasts.orchestration import (
    build_briefing,
    load_profiles_for_job,
    prepare_episode_profile_for_creator,
    prepare_speaker_profile_for_creator,
)
from open_notebook.utils.model_utils import full_model_dump

try:
    from podcast_creator import configure
except ImportError as e:
    logger.error(f"Failed to import podcast_creator: {e}")
    raise ValueError("podcast_creator library not available") from e


@dataclass(frozen=True)
class EpisodeGenerationRequest:
    """Everything needed to run or retry one episode generation job.

    Prefer content-by-reference (#1603): omit ``content`` and pass
    ``notebook_id`` and/or ``existing_episode_id`` so the worker loads or
    assembles text under budget. Explicit ``content`` remains for free-form
    and legacy command rows that still carry the blob.
    """

    episode_profile: str
    episode_name: str
    content: Optional[str] = None
    speaker_profile: Optional[str] = None
    briefing_suffix: Optional[str] = None
    existing_episode_id: Optional[str] = None
    command_id: Optional[str] = None
    notebook_id: Optional[str] = None


@dataclass(frozen=True)
class EpisodeGenerationResult:
    """Successful publication payload for one episode."""

    episode_id: str
    audio_file_path: Optional[str]
    transcript: Optional[dict]
    outline: Optional[dict]
    processing_time: float


def build_episode_output_dir(podcasts_folder: str = PODCASTS_FOLDER) -> tuple[str, Path]:
    """Build a filesystem-safe output directory under PODCASTS_FOLDER/episodes."""
    import uuid

    episode_dir_name = str(uuid.uuid4())
    output_dir = Path(podcasts_folder) / "episodes" / episode_dir_name
    return episode_dir_name, output_dir


def cleanup_failed_episode_dir(
    output_dir: Optional[Path],
    podcasts_folder: str = PODCASTS_FOLDER,
) -> bool:
    """Best-effort remove a failed episode work tree under episodes root."""
    import shutil

    if output_dir is None:
        return False
    try:
        episodes_root = (Path(podcasts_folder) / "episodes").resolve()
        target = output_dir.resolve()
        if not target.is_dir():
            return False
        if target == episodes_root or episodes_root not in target.parents:
            logger.warning(
                f"Refusing to clean episode dir outside episodes root: {target}"
            )
            return False
        shutil.rmtree(target)
        logger.info(f"Removed failed podcast work directory: {target}")
        return True
    except Exception as e:
        logger.warning(f"Failed to clean podcast work directory {output_dir}: {e}")
        return False


async def resolve_episode_content(
    request: EpisodeGenerationRequest,
    existing: Optional[PodcastEpisode] = None,
) -> str:
    """Resolve generation text without requiring it in the command payload.

    Order: request.content → episode.content → for_podcast(notebook_id).
    """
    if request.content and str(request.content).strip():
        text = str(request.content).strip()
    elif existing and existing.content and str(existing.content).strip():
        text = str(existing.content).strip()
        logger.info(
            f"Using content stored on episode {existing.id} "
            f"({len(text)} chars) — content-by-reference"
        )
    elif request.notebook_id:
        from open_notebook.context import for_podcast
        from open_notebook.context.assembly import podcast_context_max_chars
        from open_notebook.domain.notebook import Notebook

        max_chars = podcast_context_max_chars()
        if max_chars is None:
            logger.info("PODCAST_CONTEXT_MAX_CHARS=0 (unlimited notebook context)")
        notebook = await Notebook.get(request.notebook_id)
        if not notebook:
            raise ValueError(f"Notebook '{request.notebook_id}' not found")
        text = (await for_podcast(notebook, max_chars=max_chars) or "").strip()
        logger.info(
            f"Assembled podcast content from notebook {request.notebook_id} "
            f"({len(text)} chars, cap={max_chars})"
        )
    else:
        text = ""

    if not text:
        raise ValueError(
            "Content is required — provide content, store it on the episode "
            "row, or pass notebook_id for worker assembly"
        )
    if text.startswith("Notebook ID:"):
        raise ValueError(
            "Notebook context is empty or invalid; add sources before "
            "generating a podcast"
        )
    return text


async def run_episode_generation(
    request: EpisodeGenerationRequest,
) -> EpisodeGenerationResult:
    """Run one episode job to a published result or raise.

    Raises:
        ValueError: permanent validation failures (no worker retry).
        RuntimeError: generation/audio failures (command maps to failed job).
        PodcastCancelledError: operator cancelled mid-run.
        AudioQualityError: finished audio failed quality checks.
    """
    start_time = time.time()
    tts_max_tokens = os.getenv("PODCAST_TTS_MAX_TOKENS")
    output_dir: Optional[Path] = None

    try:
        logger.info(
            f"Starting podcast generation for episode: {request.episode_name}"
        )
        logger.info(f"Using episode profile: {request.episode_profile}")

        episode_profile, speaker_profile = await load_profiles_for_job(
            request.episode_profile,
            request.speaker_profile,
        )

        logger.info(f"Loaded episode profile: {episode_profile.name}")
        logger.info(f"Loaded speaker profile: {speaker_profile.name}")

        outline_provider, outline_model_name, outline_config = (
            await episode_profile.resolve_outline_config()
        )
        transcript_provider, transcript_model_name, transcript_config = (
            await episode_profile.resolve_transcript_config()
        )
        tts_provider, tts_model_name, tts_config = (
            await speaker_profile.resolve_tts_config()
        )

        logger.info(
            f"Resolved models - outline: {outline_provider}/{outline_model_name}, "
            f"transcript: {transcript_provider}/{transcript_model_name}, "
            f"tts: {tts_provider}/{tts_model_name}"
        )

        episode_profile_dict = prepare_episode_profile_for_creator(
            episode_profile,
            speaker_profile,
            outline_provider=outline_provider,
            outline_model=outline_model_name,
            outline_config=outline_config,
            transcript_provider=transcript_provider,
            transcript_model=transcript_model_name,
            transcript_config=transcript_config,
        )
        speaker_profile_dict = await prepare_speaker_profile_for_creator(
            speaker_profile,
            tts_provider=tts_provider,
            tts_model=tts_model_name,
            tts_config=tts_config,
            tts_max_tokens=tts_max_tokens,
        )

        briefing = build_briefing(episode_profile, request.briefing_suffix)

        command_ref = (
            ensure_record_id(request.command_id) if request.command_id else None
        )
        existing: Optional[PodcastEpisode] = None
        if request.existing_episode_id:
            existing = await PodcastEpisode.get(request.existing_episode_id)
            if not existing:
                raise ValueError(
                    f"Existing episode '{request.existing_episode_id}' not found"
                )

        content = await resolve_episode_content(request, existing)

        if existing:
            episode = existing
            episode.name = request.episode_name
            episode.episode_profile = episode_profile.model_dump(mode="json")
            episode.speaker_profile = speaker_profile.model_dump(mode="json")
            episode.command = command_ref
            episode.briefing = briefing
            episode.content = content
            episode.audio_file = None
            # Keep prior notebook scope unless this retry names a new one.
            if request.notebook_id:
                episode.notebook = request.notebook_id
        else:
            episode = PodcastEpisode(
                name=request.episode_name,
                episode_profile=episode_profile.model_dump(mode="json"),
                speaker_profile=speaker_profile.model_dump(mode="json"),
                command=command_ref,
                briefing=briefing,
                content=content,
                notebook=request.notebook_id,
                audio_file=None,
                transcript=None,
                outline=None,
            )
        await episode.save()
        if episode.command:
            from open_notebook.jobs import touch_command_heartbeat

            await touch_command_heartbeat(str(episode.command))

        from open_notebook.jobs import stale_running_threshold_minutes

        logger.info(
            "Podcast dials: "
            f"PODCAST_TTS_MAX_TOKENS={tts_max_tokens or 'unset'}, "
            f"OPEN_NOTEBOOK_STALE_COMMAND_MINUTES="
            f"{stale_running_threshold_minutes()}"
        )

        # SECURITY: do not configure("templates", ...) with user text (SSTI).
        # File-based prompts/podcast/*.jinja only.

        def configure_this_job() -> None:
            configure(
                "speakers_config",
                {"profiles": {speaker_profile.name: speaker_profile_dict}},
            )
            configure(
                "episode_config",
                {"profiles": {episode_profile.name: episode_profile_dict}},
            )

        logger.info("Configured podcast-creator with single-job profiles")
        logger.info(f"Generated briefing (length: {len(briefing)} chars)")

        episode_dir_name, output_dir = build_episode_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Created output directory: {output_dir}")

        logger.info("Starting podcast generation with podcast-creator...")

        async def persist_generation_progress(stage: str, value: object) -> None:
            from open_notebook.jobs import touch_command_heartbeat

            if episode.command:
                await touch_command_heartbeat(str(episode.command))

            if stage == "outline":
                episode.outline = full_model_dump(value)
                await episode.save()
                logger.info(f"Persisted podcast outline checkpoint: {episode.id}")
            elif stage == "transcript":
                episode.transcript = {"transcript": full_model_dump(value)}
                await episode.save()
                logger.info(f"Persisted podcast transcript checkpoint: {episode.id}")
            elif stage in ("tts", "combine"):
                logger.info(f"Podcast stage: {stage} for {episode.id}")
            else:
                raise ValueError(f"Unexpected podcast progress stage: {stage}")

        result, effective_briefing = await create_podcast_with_quality_retry(
            briefing=briefing,
            create_kwargs={
                "content": content,
                "episode_name": episode_dir_name,
                "output_dir": str(output_dir),
                "speaker_config": speaker_profile.name,
                "episode_profile": episode_profile.name,
                "_command_id": str(episode.command) if episode.command else None,
            },
            progress_callback=persist_generation_progress,
            configure_profiles=configure_this_job,
        )
        episode.briefing = effective_briefing

        raw_audio_path = result.get("final_output_file_path") if result else None
        audio_error: Optional[str] = None
        if raw_audio_path is not None and str(raw_audio_path).startswith("ERROR:"):
            audio_error = str(raw_audio_path)
            raw_audio_path = None

        audio_file_rel = (
            to_relative_audio_path(raw_audio_path) if raw_audio_path else None
        )
        episode.audio_file = audio_file_rel
        episode.transcript = {
            "transcript": full_model_dump(result["transcript"]) if result else None
        }
        episode.outline = full_model_dump(result["outline"]) if result else None
        await episode.save()

        if audio_error:
            cleanup_failed_episode_dir(output_dir)
            raise RuntimeError(f"Podcast audio generation failed: {audio_error}")

        processing_time = time.time() - start_time
        logger.info(
            f"Successfully generated podcast episode: {episode.id} "
            f"in {processing_time:.2f}s"
        )

        return EpisodeGenerationResult(
            episode_id=str(episode.id),
            audio_file_path=audio_file_rel,
            transcript={"transcript": full_model_dump(result["transcript"])}
            if result.get("transcript")
            else None,
            outline=full_model_dump(result["outline"])
            if result.get("outline")
            else None,
            processing_time=processing_time,
        )

    except AudioQualityError:
        cleanup_failed_episode_dir(output_dir)
        raise
    except PodcastCancelledError:
        cleanup_failed_episode_dir(output_dir)
        raise
    except ValueError:
        cleanup_failed_episode_dir(output_dir)
        raise
    except Exception:
        cleanup_failed_episode_dir(output_dir)
        raise
