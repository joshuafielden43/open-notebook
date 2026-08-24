"""Podcast generation helpers: staged pipeline, config isolation, audio checks.

podcast-creator's process-global configure() is not re-entrant. Concurrent
worker tasks serialize *configure only*; generation runs outside the lock once
speaker/episode settings are materialised into job-local kwargs. Quality checks
run after transcript and before TTS so a failed gate does not pay for audio twice.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from open_notebook.podcasts.quality import transcript_quality_issues

# Serialize configure() only: podcast-creator stores profiles in a
# process-global manager that concurrent jobs would otherwise clobber.
_PODCAST_CONFIG_LOCK = asyncio.Lock()

# Combined mute/near-empty clips from TTS fallbacks are often a few hundred
# bytes of header/silence; reject anything under this floor.
MIN_FINAL_AUDIO_BYTES = 2048
# mean_volume at or below this (ffmpeg volumedetect) is treated as silence.
SILENCE_MEAN_VOLUME_DB = -45.0
# Minimum playable duration; pure silence/null encodes are often 0–2s.
MIN_AUDIO_DURATION_SECONDS = 1.5

PodcastProgressCallback = Callable[[str, object], Awaitable[None]]
MAX_TRANSCRIPT_QUALITY_ATTEMPTS = 2

_MEAN_VOLUME_RE = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", re.I)
_DURATION_RE = re.compile(
    r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", re.I
)


class TranscriptQualityError(ValueError):
    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


class AudioQualityError(RuntimeError):
    """Raised when finished audio must not be published (not a ValueError)."""

    def __init__(self, issues: list[str]):
        self.issues = issues
        super().__init__("; ".join(issues))


class PodcastCancelledError(RuntimeError):
    """Raised when the operator cancelled the command mid-generation."""

    def __init__(self, command_id: str):
        self.command_id = command_id
        super().__init__(
            f"Podcast job {command_id} was cancelled; aborting generation"
        )


def quality_repair_briefing(briefing: str, issues: list[str]) -> str:
    failures = "\n".join(f"- {issue}" for issue in issues)
    return (
        f"{briefing}\n\n"
        "AUTOMATIC QUALITY REPAIR: The prior transcript was rejected for:\n"
        f"{failures}\n"
        "Regenerate from scratch. Keep each outline segment in exclusive "
        "scope and never restart or recap an earlier segment."
    )


def ffmpeg_available() -> bool:
    """True when ffmpeg is on PATH (required for post-TTS audio verification)."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0 or bool(result.stdout or result.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def require_ffmpeg_for_publish() -> None:
    """Fail before TTS when audio cannot be verified after generation."""
    if not ffmpeg_available():
        raise AudioQualityError(
            [
                "ffmpeg is not available on PATH; refusing to run TTS because "
                "finished audio cannot be quality-checked. Install ffmpeg "
                "(required for Docker and from-source podcast generation)."
            ]
        )


def _probe_ffmpeg_audio(path: Path) -> tuple[Optional[float], Optional[float]]:
    """Return (duration_seconds, mean_volume_db) via ffmpeg when available."""
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-i",
                str(path),
                "-af",
                "volumedetect",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.debug(f"ffmpeg audio probe unavailable: {e}")
        return None, None

    stderr = result.stderr or ""
    mean_volume: Optional[float] = None
    duration: Optional[float] = None
    match = _MEAN_VOLUME_RE.search(stderr)
    if match:
        mean_volume = float(match.group(1))
    dmatch = _DURATION_RE.search(stderr)
    if dmatch:
        hours, minutes, seconds = dmatch.groups()
        duration = (
            int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        )
    return duration, mean_volume


def audio_file_quality_issues(path: object) -> list[str]:
    """Return reasons a finished audio path must not be published as success."""
    if path is None:
        return ["audio file path is missing"]
    raw = str(path)
    if raw.startswith("ERROR:"):
        return [f"audio combination failed: {raw}"]
    audio_path = Path(raw)
    if not audio_path.is_file():
        return [f"audio file does not exist: {raw}"]
    size = audio_path.stat().st_size
    if size < MIN_FINAL_AUDIO_BYTES:
        return [
            f"audio file is too small to be playable dialogue "
            f"({size} bytes; minimum {MIN_FINAL_AUDIO_BYTES})"
        ]

    duration, mean_volume = _probe_ffmpeg_audio(audio_path)
    # Require both metrics. Partial parse (e.g. Duration only) is not enough
    # to publish; mute-with-header must not slip through.
    if duration is None or mean_volume is None:
        return [
            "could not verify audio quality (ffmpeg volume probe incomplete); "
            "refusing to publish unverified audio"
        ]
    issues: list[str] = []
    if duration < MIN_AUDIO_DURATION_SECONDS:
        issues.append(
            f"audio duration is too short for dialogue "
            f"({duration:.2f}s; minimum {MIN_AUDIO_DURATION_SECONDS}s)"
        )
    if mean_volume <= SILENCE_MEAN_VOLUME_DB:
        issues.append(
            f"audio is near-silent (mean_volume {mean_volume:.1f} dB; "
            f"threshold {SILENCE_MEAN_VOLUME_DB} dB)"
        )
    return issues


def materialize_job_local_kwargs(
    create_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Resolve global configure state into job-local kwargs under the lock.

    After this returns, generation can run without re-reading process-global
    speaker/episode maps (so the configure lock can be released).
    """
    from podcast_creator.episodes import load_episode_config
    from podcast_creator.speakers import load_speaker_config

    kwargs = dict(create_kwargs)
    speaker_name = kwargs.get("speaker_config")
    episode_name = kwargs.get("episode_profile")

    if episode_name and not kwargs.get("_resolved_episode"):
        episode_config = load_episode_config(episode_name)
        kwargs["_resolved_episode"] = episode_config
        kwargs["speaker_config"] = (
            speaker_name or episode_config.speaker_config
        )
        kwargs.setdefault("outline_provider", episode_config.outline_provider)
        kwargs.setdefault("outline_model", episode_config.outline_model)
        kwargs.setdefault(
            "transcript_provider", episode_config.transcript_provider
        )
        kwargs.setdefault("transcript_model", episode_config.transcript_model)
        kwargs.setdefault("num_segments", episode_config.num_segments)
        if kwargs.get("outline_config") is None:
            kwargs["outline_config"] = episode_config.outline_config
        if kwargs.get("transcript_config") is None:
            kwargs["transcript_config"] = episode_config.transcript_config
        if kwargs.get("language") is None:
            kwargs["language"] = episode_config.language
        kwargs["speaker_config"] = kwargs.get("speaker_config") or speaker_name

    speaker_key = kwargs.get("speaker_config")
    if speaker_key and not kwargs.get("_resolved_speaker"):
        kwargs["_resolved_speaker"] = load_speaker_config(speaker_key)

    return kwargs


async def create_podcast_with_quality_retry(
    *,
    briefing: str,
    create_kwargs: dict[str, Any],
    progress_callback: Optional[PodcastProgressCallback] = None,
    configure_profiles: Optional[Callable[[], None]] = None,
) -> tuple[dict[str, Any], str]:
    """Generate outline/transcript (with repair) before TTS; never publish mute audio.

    configure_profiles runs under the process lock and job-local kwargs are
    materialised there; the long LLM/TTS work runs outside the lock.
    """
    effective_briefing = briefing

    for attempt in range(1, MAX_TRANSCRIPT_QUALITY_ATTEMPTS + 1):
        async with _PODCAST_CONFIG_LOCK:
            if configure_profiles is not None:
                configure_profiles()
            job_kwargs = materialize_job_local_kwargs(create_kwargs)

        try:
            result = await _run_podcast_until_quality(
                briefing=effective_briefing,
                create_kwargs=job_kwargs,
                progress_callback=progress_callback,
            )
            issues: list[str] = []
        except TranscriptQualityError as exc:
            result = None
            issues = exc.issues

        if not issues and result is not None:
            audio_issues = audio_file_quality_issues(
                result.get("final_output_file_path")
            )
            if audio_issues:
                raise AudioQualityError(audio_issues)
            return result, effective_briefing

        logger.warning(
            f"Podcast transcript failed quality attempt {attempt}/"
            f"{MAX_TRANSCRIPT_QUALITY_ATTEMPTS}: {'; '.join(issues)}"
        )
        if attempt == MAX_TRANSCRIPT_QUALITY_ATTEMPTS:
            raise ValueError(
                "Podcast transcript failed automatic quality checks after "
                f"{MAX_TRANSCRIPT_QUALITY_ATTEMPTS} attempts: "
                f"{'; '.join(issues)}"
            )
        effective_briefing = quality_repair_briefing(briefing, issues)

    raise AssertionError("unreachable")


async def _run_podcast_until_quality(
    *,
    briefing: str,
    create_kwargs: dict[str, Any],
    progress_callback: Optional[PodcastProgressCallback],
) -> dict[str, Any]:
    """Staged outline → transcript → quality → TTS (pin required for nodes)."""
    try:
        return await _run_staged_podcast(
            briefing=briefing,
            create_kwargs=create_kwargs,
            progress_callback=progress_callback,
        )
    except ImportError as e:
        raise ImportError(
            "Staged podcast pipeline requires podcast-creator nodes "
            "(git pin in pyproject.toml). Quality-before-TTS is not available "
            "on a stock create_podcast-only install."
        ) from e


async def _run_staged_podcast(
    *,
    briefing: str,
    create_kwargs: dict[str, Any],
    progress_callback: Optional[PodcastProgressCallback],
) -> dict[str, Any]:
    """Outline → transcript → quality gate → TTS → combine."""
    import json as _json
    from pathlib import Path as _Path

    from langchain_core.runnables import RunnableConfig
    from podcast_creator.language import resolve_language_name
    from podcast_creator.nodes import (
        combine_audio_node,
        generate_all_audio_node,
        generate_outline_node,
        generate_transcript_node,
    )

    content = create_kwargs["content"]
    episode_name = create_kwargs["episode_name"]
    output_dir = create_kwargs["output_dir"]
    speaker_profile = create_kwargs.get("_resolved_speaker")
    if speaker_profile is None:
        # Last resort: library global (caller should have materialised).
        from podcast_creator.speakers import load_speaker_config

        speaker_key = create_kwargs.get("speaker_config")
        if not speaker_key:
            raise ValueError("speaker_config is required")
        speaker_profile = load_speaker_config(speaker_key)

    if not episode_name or not output_dir:
        raise ValueError("episode_name and output_dir are required")

    outline_provider = create_kwargs.get("outline_provider")
    outline_model = create_kwargs.get("outline_model")
    transcript_provider = create_kwargs.get("transcript_provider")
    transcript_model = create_kwargs.get("transcript_model")
    num_segments = create_kwargs.get("num_segments")
    outline_config = create_kwargs.get("outline_config")
    transcript_config = create_kwargs.get("transcript_config")
    language = create_kwargs.get("language")
    retry_max_attempts = create_kwargs.get("retry_max_attempts")
    retry_wait_multiplier = create_kwargs.get("retry_wait_multiplier")

    resolved_episode = create_kwargs.get("_resolved_episode")
    if resolved_episode is not None:
        outline_provider = outline_provider or resolved_episode.outline_provider
        outline_model = outline_model or resolved_episode.outline_model
        transcript_provider = (
            transcript_provider or resolved_episode.transcript_provider
        )
        transcript_model = transcript_model or resolved_episode.transcript_model
        num_segments = num_segments or resolved_episode.num_segments
        if outline_config is None:
            outline_config = resolved_episode.outline_config
        if transcript_config is None:
            transcript_config = resolved_episode.transcript_config
        language = language or resolved_episode.language

    output_path = _Path(output_dir)
    output_path.mkdir(exist_ok=True, parents=True)
    resolved_language = resolve_language_name(language) if language else None

    state: dict[str, Any] = {
        "content": content,
        "briefing": briefing,
        "num_segments": num_segments or 3,
        "language": resolved_language,
        "outline": None,
        "transcript": [],
        "audio_clips": [],
        "final_output_file_path": None,
        "output_dir": output_path,
        "episode_name": episode_name,
        "speaker_profile": speaker_profile,
    }

    configurable: dict[str, Any] = {
        "outline_provider": outline_provider or "openai",
        "outline_model": outline_model or "gpt-4o-mini",
        "transcript_provider": transcript_provider or "anthropic",
        "transcript_model": transcript_model or "claude-3-5-sonnet-latest",
        "outline_config": outline_config,
        "transcript_config": transcript_config,
    }
    if retry_max_attempts is not None:
        configurable["retry_max_attempts"] = retry_max_attempts
    if retry_wait_multiplier is not None:
        configurable["retry_wait_multiplier"] = retry_wait_multiplier

    config: RunnableConfig = {"configurable": configurable}
    command_id = create_kwargs.get("_command_id")
    cid = str(command_id) if command_id else None

    async def _abort_if_cancelled() -> None:
        """Stage-boundary cancel check (cooperative; does not kill in-flight LLM)."""
        if not cid:
            return
        from open_notebook.jobs import command_was_cancelled, touch_command_heartbeat

        await touch_command_heartbeat(cid)
        if await command_was_cancelled(cid):
            raise PodcastCancelledError(cid)

    # Shared long-job pulse (#1605): heartbeat whole pipeline so the reaper
    # does not treat multi-minute outline/transcript/TTS as a zombie; cancel
    # is observed each pulse (~10s) and raises PodcastCancelledError on exit.
    from open_notebook.jobs import PODCAST_PULSE_SECONDS, long_job_pulse

    async with long_job_pulse(
        cid,
        interval_seconds=PODCAST_PULSE_SECONDS,
        on_cancelled=PodcastCancelledError,
    ):
        await _abort_if_cancelled()
        outline_update = await generate_outline_node(state, config)  # type: ignore[arg-type]
        state.update(outline_update)
        if progress_callback:
            await progress_callback("outline", state.get("outline"))

        await _abort_if_cancelled()
        transcript_update = await generate_transcript_node(state, config)  # type: ignore[arg-type]
        state.update(transcript_update)
        if progress_callback:
            await progress_callback("transcript", state.get("transcript"))

        issues = transcript_quality_issues(
            briefing, state.get("outline"), state.get("transcript")
        )
        if issues:
            raise TranscriptQualityError(issues)

        require_ffmpeg_for_publish()
        await _abort_if_cancelled()

        if progress_callback:
            await progress_callback("tts", None)

        audio_update = await generate_all_audio_node(state, config)  # type: ignore[arg-type]
        state.update(audio_update)
        await _abort_if_cancelled()
        combine_update = await combine_audio_node(state, config)  # type: ignore[arg-type]
        state.update(combine_update)

    if state.get("outline"):
        outline_obj = state["outline"]
        outline_path = output_path / "outline.json"
        if hasattr(outline_obj, "model_dump_json"):
            outline_path.write_text(outline_obj.model_dump_json())
        else:
            outline_path.write_text(_json.dumps(outline_obj, default=str))

    if state.get("transcript"):
        transcript_path = output_path / "transcript.json"
        rows = state["transcript"]
        payload = [
            d.model_dump() if hasattr(d, "model_dump") else d for d in rows
        ]
        transcript_path.write_text(_json.dumps(payload, indent=2, default=str))

    if progress_callback:
        await progress_callback("combine", state.get("final_output_file_path"))

    return {
        "outline": state.get("outline"),
        "transcript": state.get("transcript"),
        "final_output_file_path": state.get("final_output_file_path"),
        "audio_clips_count": len(state.get("audio_clips") or []),
        "output_dir": output_path,
    }


def derive_generation_stage(
    *,
    job_status: Optional[str],
    has_outline: bool,
    has_transcript: bool,
    has_audio: bool,
) -> str:
    """Map durable episode fields + job status to a coarse pipeline stage."""
    status = (job_status or "").lower()
    if status in ("failed", "error"):
        return "failed"
    if status in ("completed",) or (
        has_audio
        and status not in ("running", "processing", "pending", "submitted")
    ):
        if has_audio:
            return "completed"
    if has_audio:
        return "completed"
    if has_transcript and status in (
        "running",
        "processing",
        "pending",
        "submitted",
        "unknown",
        "",
    ):
        return "tts"
    if has_outline and status in (
        "running",
        "processing",
        "pending",
        "submitted",
        "unknown",
        "",
    ):
        return "transcript"
    if status in ("pending", "submitted"):
        return "queued"
    if status in ("running", "processing"):
        return "outline"
    if status in ("completed",) and not has_audio:
        return "failed"
    return status or "unknown"
