"""Podcast job prep shared by API submit and Episode Generation.

Validation and briefing construction live here so API and worker do not
reimplement them. Full job execution is :mod:`episode_generation`.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from loguru import logger

from open_notebook.ai.runtime import resolve_model_config
from open_notebook.podcasts.models import EpisodeProfile, SpeakerProfile


def validate_profiles_ready(
    episode_profile: EpisodeProfile,
    speaker_profile: SpeakerProfile,
    *,
    check_placeholder_voices: bool = True,
) -> None:
    """Raise ValueError when profiles cannot produce a podcast."""
    if not episode_profile.outline_llm:
        raise ValueError(
            f"Episode profile '{episode_profile.name}' has no outline model configured. "
            "Please update the profile to select an outline model."
        )
    if not episode_profile.transcript_llm:
        raise ValueError(
            f"Episode profile '{episode_profile.name}' has no transcript model configured. "
            "Please update the profile to select a transcript model."
        )
    if not speaker_profile.voice_model:
        raise ValueError(
            f"Speaker profile '{speaker_profile.name}' has no voice model configured. "
            "Please update the profile to select a voice model."
        )
    if not check_placeholder_voices:
        return
    for speaker in speaker_profile.speakers or []:
        voice_id = str(speaker.get("voice_id") or "").strip()
        if not voice_id:
            raise ValueError(
                f"Speaker '{speaker.get('name', '?')}' is missing a voice_id"
            )
        if voice_id in {"voice_123", "voice_id", "TODO"}:
            raise ValueError(
                f"Speaker '{speaker.get('name', '?')}' still has a placeholder voice_id"
            )


def build_briefing(
    episode_profile: EpisodeProfile,
    briefing_suffix: Optional[str] = None,
) -> str:
    briefing = episode_profile.default_briefing or ""
    if briefing_suffix:
        briefing = f"{briefing}\n\nAdditional instructions: {briefing_suffix}"
    return briefing


async def load_profiles_for_job(
    episode_profile_name: str,
    speaker_profile_ref: Optional[str] = None,
) -> Tuple[EpisodeProfile, SpeakerProfile]:
    """Load episode + speaker profiles for a generation job.

    When speaker_profile_ref is omitted, uses the episode profile's configured
    speaker. Validates readiness (including placeholder voices).
    """
    episode_profile = await EpisodeProfile.get_by_name(episode_profile_name)
    if not episode_profile:
        raise ValueError(f"Episode profile '{episode_profile_name}' not found")

    speaker_ref = speaker_profile_ref or episode_profile.speaker_config
    if not speaker_ref:
        raise ValueError(
            f"Episode profile '{episode_profile.name}' has no speaker "
            "profile configured. Please update the profile to select a "
            "speaker profile."
        )
    speaker_profile = await SpeakerProfile.resolve(speaker_ref)
    if not speaker_profile:
        if speaker_profile_ref:
            raise ValueError(f"Speaker profile '{speaker_ref}' not found")
        raise ValueError(
            f"Episode profile '{episode_profile.name}' references a "
            "speaker profile that no longer exists. Please update the "
            "profile to select a speaker profile."
        )

    validate_profiles_ready(episode_profile, speaker_profile)
    return episode_profile, speaker_profile


def prepare_episode_profile_for_creator(
    episode_profile: EpisodeProfile,
    speaker_profile: SpeakerProfile,
    *,
    outline_provider: str,
    outline_model: str,
    outline_config: dict,
    transcript_provider: str,
    transcript_model: str,
    transcript_config: dict,
) -> dict[str, Any]:
    """Single-job episode profile dict in podcast-creator shape."""
    return {
        "name": episode_profile.name,
        "description": episode_profile.description,
        "speaker_config": speaker_profile.name,
        "outline_provider": outline_provider,
        "outline_model": outline_model,
        "outline_config": outline_config,
        "transcript_provider": transcript_provider,
        "transcript_model": transcript_model,
        "transcript_config": transcript_config,
        "default_briefing": episode_profile.default_briefing,
        "num_segments": min(int(episode_profile.num_segments or 5), 10),
        "language": episode_profile.language,
        "outline_llm": episode_profile.outline_llm,
        "transcript_llm": episode_profile.transcript_llm,
        "max_tokens": episode_profile.max_tokens,
    }


async def prepare_speaker_profile_for_creator(
    speaker_profile: SpeakerProfile,
    *,
    tts_provider: str,
    tts_model: str,
    tts_config: dict,
    tts_max_tokens: Optional[str] = None,
) -> dict[str, Any]:
    """Single-job speaker profile dict with resolved TTS configs."""
    conf = dict(tts_config)
    if tts_max_tokens:
        conf["max_tokens"] = int(tts_max_tokens)

    speakers: list[dict[str, Any]] = []
    for speaker in speaker_profile.speakers or []:
        row = dict(speaker)
        if row.get("voice_model"):
            try:
                prov, model, sp_conf = await resolve_model_config(
                    str(row["voice_model"])
                )
                if tts_max_tokens:
                    sp_conf = {**sp_conf, "max_tokens": int(tts_max_tokens)}
                row["tts_provider"] = prov
                row["tts_model"] = model
                row["tts_config"] = sp_conf
            except Exception as e:
                logger.warning(
                    f"Failed to resolve per-speaker TTS for '{row.get('name')}': {e}"
                )
        speakers.append(row)

    return {
        "name": speaker_profile.name,
        "description": speaker_profile.description,
        "voice_model": speaker_profile.voice_model,
        "tts_provider": tts_provider,
        "tts_model": tts_model,
        "tts_config": conf,
        "speakers": speakers,
    }
