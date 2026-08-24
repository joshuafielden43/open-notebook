import re
from typing import Annotated, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel

from api.podcast_service import (
    PodcastGenerationRequest,
    PodcastGenerationResponse,
    PodcastService,
)
from open_notebook.ai.models import Model
from open_notebook.exceptions import OpenNotebookError
from open_notebook.jobs import get_worker_status
from open_notebook.podcasts.audio_paths import resolve_contained_audio_path
from open_notebook.podcasts.generation import derive_generation_stage
from open_notebook.podcasts.models import PodcastEpisode

router = APIRouter()

# Model reference fields stored in the denormalized profile snapshots on an
# episode, mapped to the resolved display fields the frontend renders
# ("provider / name" rows in EpisodeCard). Mirrors the speaker_config ->
# speaker_config_name precedent in api/routers/episode_profiles.py.
_EPISODE_PROFILE_MODEL_FIELDS = {
    "outline_llm": ("outline_model_provider", "outline_model_name"),
    "transcript_llm": ("transcript_model_provider", "transcript_model_name"),
}
_SPEAKER_PROFILE_MODEL_FIELDS = {
    "voice_model": ("voice_model_provider", "voice_model_name"),
}


def _collect_snapshot_model_ids(episodes: List[PodcastEpisode]) -> List[str]:
    """Collect the distinct model record IDs referenced by episode snapshots."""
    ids = set()
    for episode in episodes:
        for field in _EPISODE_PROFILE_MODEL_FIELDS:
            ref = (episode.episode_profile or {}).get(field)
            if ref:
                ids.add(str(ref))
        for field in _SPEAKER_PROFILE_MODEL_FIELDS:
            ref = (episode.speaker_profile or {}).get(field)
            if ref:
                ids.add(str(ref))
    return sorted(ids)


def _with_resolved_model_fields(
    snapshot: dict,
    field_map: dict,
    models_by_id: dict,
) -> dict:
    """Return a copy of a profile snapshot with resolved model display fields.

    Only sets the display fields when the reference resolves; unresolvable
    references (deleted model) and legacy snapshots without references are
    left untouched so the frontend can fall back to the historical
    provider/model strings, then to a placeholder.
    """
    enriched = dict(snapshot or {})
    for ref_field, (provider_field, name_field) in field_map.items():
        ref = enriched.get(ref_field)
        info = models_by_id.get(str(ref)) if ref else None
        if info:
            enriched[provider_field] = info["provider"]
            enriched[name_field] = info["name"]
    return enriched


async def _resolve_snapshot_models(
    episodes: List[PodcastEpisode],
) -> dict:
    """Batch-resolve every model reference in the episodes' snapshots.

    One query for the whole list (see Model.get_display_info_for_ids) - a
    failure degrades to no resolved fields rather than failing the request.
    """
    try:
        return await Model.get_display_info_for_ids(
            _collect_snapshot_model_ids(episodes)
        )
    except Exception as e:
        logger.warning(f"Error batch-resolving snapshot model references: {str(e)}")
        return {}


def _delete_episode_audio(episode: PodcastEpisode, episode_id: str) -> None:
    """Best-effort unlink of an episode's audio file, refusing invalid paths.

    Shared by the delete and retry endpoints. Legacy/escaping audio_file
    values (resolve_contained_audio_path -> None) are logged and skipped.
    """
    if not episode.audio_file:
        return
    audio_path = resolve_contained_audio_path(episode.audio_file)
    if audio_path is None:
        logger.warning(
            f"Refusing to delete audio file outside podcasts directory "
            f"for episode {episode_id}: {episode.audio_file}"
        )
    elif audio_path.exists():
        try:
            audio_path.unlink()
            logger.info(f"Deleted audio file: {audio_path}")
        except Exception as e:
            logger.warning(f"Failed to delete audio file {audio_path}: {e}")


class PodcastEpisodeResponse(BaseModel):
    id: str
    name: str
    episode_profile: dict
    speaker_profile: dict
    briefing: str
    notebook_id: Optional[str] = None
    audio_file: Optional[str] = None
    audio_url: Optional[str] = None
    transcript: Optional[dict] = None
    outline: Optional[dict] = None
    created: Optional[str] = None
    job_status: Optional[str] = None
    error_message: Optional[str] = None
    generation_stage: Optional[str] = None


def _has_outline(outline: object) -> bool:
    if not outline:
        return False
    if isinstance(outline, dict):
        segments = outline.get("segments")
        return bool(segments)
    return True


def _has_transcript(transcript: object) -> bool:
    if not transcript:
        return False
    if isinstance(transcript, dict):
        rows = transcript.get("transcript")
        return bool(rows)
    return True


def _episode_to_response(
    episode: PodcastEpisode,
    *,
    job_status: Optional[str],
    error_message: Optional[str],
    models_by_id: dict,
    include_content: bool,
) -> PodcastEpisodeResponse:
    audio_url = None
    audio_path = resolve_contained_audio_path(episode.audio_file)
    if audio_path is not None and audio_path.exists():
        audio_url = f"/api/podcasts/episodes/{episode.id}/audio"

    outline = episode.outline if include_content else None
    transcript = episode.transcript if include_content else None
    briefing = episode.briefing or ""
    if not include_content and len(briefing) > 240:
        briefing = briefing[:240] + "…"

    outline_present = getattr(episode, "has_outline", None)
    if outline_present is None:
        outline_present = _has_outline(episode.outline)
    transcript_present = getattr(episode, "has_transcript", None)
    if transcript_present is None:
        transcript_present = _has_transcript(episode.transcript)

    generation_stage = derive_generation_stage(
        job_status=job_status,
        has_outline=bool(outline_present),
        has_transcript=bool(transcript_present),
        has_audio=bool(audio_url or episode.audio_file),
    )

    notebook_id = None
    if episode.notebook is not None:
        notebook_id = str(episode.notebook)

    return PodcastEpisodeResponse(
        id=str(episode.id),
        name=episode.name,
        episode_profile=_with_resolved_model_fields(
            episode.episode_profile,
            _EPISODE_PROFILE_MODEL_FIELDS,
            models_by_id,
        ),
        speaker_profile=_with_resolved_model_fields(
            episode.speaker_profile,
            _SPEAKER_PROFILE_MODEL_FIELDS,
            models_by_id,
        ),
        briefing=briefing,
        notebook_id=notebook_id,
        audio_file=episode.audio_file,
        audio_url=audio_url,
        transcript=transcript,
        outline=outline,
        created=str(episode.created) if episode.created else None,
        job_status=job_status,
        error_message=error_message,
        generation_stage=generation_stage,
    )


@router.post("/podcasts/generate", response_model=PodcastGenerationResponse)
async def generate_podcast(request: PodcastGenerationRequest):
    """
    Generate a podcast episode using Episode Profiles.
    Returns immediately with job ID for status tracking.
    """
    try:
        job_id = await PodcastService.submit_generation_job(
            episode_profile_name=request.episode_profile,
            speaker_profile_name=request.speaker_profile,
            episode_name=request.episode_name,
            notebook_id=request.notebook_id,
            content=request.content,
            briefing_suffix=request.briefing_suffix,
        )
        worker = await get_worker_status()
        worker_ready = bool(worker.get("worker_likely_ready", True))
        message = (
            f"Podcast generation started for episode '{request.episode_name}'"
        )
        if not worker_ready:
            message = (
                f"Podcast job queued for '{request.episode_name}', but the "
                "background worker does not appear to be running. Start the "
                "worker (make worker-start) or jobs will sit pending forever."
            )

        return PodcastGenerationResponse(
            job_id=job_id,
            status="submitted",
            message=message,
            episode_profile=request.episode_profile,
            episode_name=request.episode_name,
            worker_likely_ready=worker_ready,
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error generating podcast: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to generate podcast"
        )


@router.get("/podcasts/jobs/{job_id}")
async def get_podcast_job_status(job_id: str):
    """Get the status of a podcast generation job"""
    try:
        status_data = await PodcastService.get_job_status(job_id)
        return status_data

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching podcast job status: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to fetch job status"
        )


@router.get("/podcasts/episodes", response_model=List[PodcastEpisodeResponse])
async def list_podcast_episodes(
    detail: Annotated[
        str,
        Query(
            description="summary omits fat outline/transcript payloads; full includes them",
        ),
    ] = "summary",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    notebook_id: Annotated[
        Optional[str],
        Query(description="When set, only episodes generated from this notebook"),
    ] = None,
):
    """List podcast episodes (paginated; default summary projection)."""
    try:
        include_content = detail == "full"
        # summary=True projects without fat content/transcript/outline in SQL
        # and applies LIMIT/START in the database.
        episodes = await PodcastService.list_episodes(
            summary=not include_content,
            limit=limit,
            offset=offset,
            notebook_id=notebook_id,
        )

        # Batch-fetch job status for every episode with a command in one
        # query instead of one round trip per episode (see
        # PodcastEpisode.get_job_details_for_commands docstring).
        try:
            details_by_command = await PodcastEpisode.get_job_details_for_commands(
                [episode.command for episode in episodes if episode.command]
            )
        except Exception as e:
            logger.warning(f"Error batch-fetching podcast job statuses: {str(e)}")
            details_by_command = {}

        # Batch-resolve the snapshots' model references (outline_llm,
        # transcript_llm, voice_model) to display fields in one query
        # instead of one lookup per episode.
        models_by_id = await _resolve_snapshot_models(episodes)

        response_episodes = []
        for episode in episodes:
            # Entity-first queued rows may briefly lack command; still show them.
            # Only skip empty shells with neither identity nor progress.
            if (
                not episode.command
                and not episode.audio_file
                and not (episode.content or "").strip()
            ):
                continue

            # Get job status and error message if available
            job_status = None
            error_message = None
            if episode.command:
                detail_row = details_by_command.get(
                    PodcastEpisode.command_status_key(episode.command) or ""
                )
                if detail_row is not None:
                    job_status = detail_row["status"]
                    error_message = detail_row["error_message"]
                else:
                    job_status = "unknown"
            elif episode.audio_file:
                # No command but has audio file = completed import
                job_status = "completed"
            else:
                # Queued entity-first row waiting for worker claim
                job_status = "pending"

            response_episodes.append(
                _episode_to_response(
                    episode,
                    job_status=job_status,
                    error_message=error_message,
                    models_by_id=models_by_id,
                    include_content=include_content,
                )
            )

        return response_episodes

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error listing podcast episodes: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to list podcast episodes"
        )


@router.get("/podcasts/episodes/{episode_id}", response_model=PodcastEpisodeResponse)
async def get_podcast_episode(episode_id: str):
    """Get a specific podcast episode"""
    try:
        episode = await PodcastService.get_episode(episode_id)

        # Get job status and error message if available
        job_status = None
        error_message = None
        if episode.command:
            try:
                detail = await episode.get_job_detail()
                job_status = detail["status"]
                error_message = detail["error_message"]
            except Exception:
                job_status = "unknown"
        else:
            # No command but has audio file = completed import
            job_status = "completed" if episode.audio_file else "unknown"

        models_by_id = await _resolve_snapshot_models([episode])

        return _episode_to_response(
            episode,
            job_status=job_status,
            error_message=error_message,
            models_by_id=models_by_id,
            include_content=True,
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching podcast episode: {str(e)}")
        raise HTTPException(status_code=404, detail="Episode not found")


def _episode_download_filename(episode_name: Optional[str], episode_id: str) -> str:
    """Human filename for the MP3: sanitized episode name, id-suffix fallback."""
    base = re.sub(r"[^\w\s.-]", "", episode_name or "", flags=re.UNICODE).strip()
    base = re.sub(r"\s+", " ", base)
    if not base:
        base = f"episode-{episode_id.split(':', 1)[-1]}"
    return f"{base}.mp3"


@router.api_route("/podcasts/episodes/{episode_id}/audio", methods=["GET", "HEAD"])
async def stream_podcast_episode_audio(episode_id: str, download: bool = False):
    """Stream the audio file associated with a podcast episode.

    Range requests are honored by FileResponse, so media elements can seek.
    ``?download=1`` switches Content-Disposition to attachment with a
    human-readable filename derived from the episode name.
    """
    try:
        episode = await PodcastService.get_episode(episode_id)
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching podcast episode for audio: {str(e)}")
        raise HTTPException(status_code=404, detail="Episode not found")

    if not episode.audio_file:
        raise HTTPException(status_code=404, detail="Episode has no audio file")

    audio_path = resolve_contained_audio_path(episode.audio_file)
    if audio_path is None:
        logger.warning(
            f"Blocked audio access outside podcasts directory for episode "
            f"{episode_id}: {episode.audio_file}"
        )
        raise HTTPException(status_code=403, detail="Access to file denied")

    if not audio_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found on disk")

    return FileResponse(
        audio_path,
        media_type="audio/mpeg",
        filename=_episode_download_filename(episode.name, episode_id),
        content_disposition_type="attachment" if download else "inline",
    )


def _extract_briefing_suffix(briefing: str, default_briefing: Optional[str]) -> Optional[str]:
    """Recover the one-off instructions appended after the profile default."""
    if not briefing:
        return None
    marker = "\n\nAdditional instructions: "
    if marker in briefing:
        return briefing.split(marker, 1)[1].strip() or None
    if default_briefing and briefing.startswith(default_briefing):
        suffix = briefing[len(default_briefing) :].strip()
        return suffix or None
    return None


@router.post("/podcasts/episodes/{episode_id}/retry")
async def retry_podcast_episode(episode_id: str):
    """Retry a failed podcast episode by submitting a new job with the same inputs.

    Reuses the failed episode row (outline/transcript evidence preserved until
    overwritten) and queues a new generation into the same episode — no twin.
    """
    try:
        episode = await PodcastService.get_episode(episode_id)

        # Retry only when the command row is *actually* failed in the DB after
        # a reaper pass — not when coerce_status_detail paints stale running
        # as failed while a worker may still be writing the episode.
        from open_notebook.database.repository import ensure_record_id, repo_query
        from open_notebook.jobs import (
            fail_stale_running_commands,
            job_is_retryable,
        )

        await fail_stale_running_commands()
        raw_status = None
        failed_command_id = str(episode.command) if episode.command else None
        if failed_command_id:
            rows = await repo_query(
                "SELECT status FROM $id",
                {"id": ensure_record_id(failed_command_id)},
            )
            if rows:
                raw_status = rows[0].get("status")
        if not failed_command_id or not job_is_retryable(raw_status):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Episode is not in a failed state "
                    f"(current: {raw_status}). Wait for the job to finish "
                    f"or be reaped before retrying."
                ),
            )

        # CAS claim before submit so a double-click cannot enqueue two jobs
        # against the same artifact row while both still see the failed
        # command (#1608).
        claimed = await PodcastEpisode.claim_for_retry(
            episode_id, failed_command_id
        )
        if not claimed:
            raise HTTPException(
                status_code=409,
                detail=(
                    "A retry is already in progress for this episode. "
                    "Refresh and wait for the new job to finish."
                ),
            )

        # Extract params for re-submission
        ep_profile_name = episode.episode_profile.get("name")
        sp_profile_name = episode.speaker_profile.get("name")
        episode_name = episode.name
        content = episode.content
        briefing_suffix = _extract_briefing_suffix(
            episode.briefing or "",
            (episode.episode_profile or {}).get("default_briefing"),
        )

        if not ep_profile_name or not sp_profile_name:
            await PodcastEpisode.restore_command_link(
                episode_id, failed_command_id
            )
            raise HTTPException(
                status_code=400,
                detail="Cannot retry: episode or speaker profile name missing from stored data",
            )

        notebook_id = str(episode.notebook) if episode.notebook else None

        # Submit a new job into the same episode row (no orphan twin).
        try:
            job_id = await PodcastService.submit_generation_job(
                episode_profile_name=ep_profile_name,
                speaker_profile_name=sp_profile_name,
                episode_name=episode_name,
                content=content,
                notebook_id=notebook_id,
                briefing_suffix=briefing_suffix,
                existing_episode_id=episode_id,
            )
        except (HTTPException, OpenNotebookError):
            await PodcastEpisode.restore_command_link(
                episode_id, failed_command_id
            )
            raise
        except Exception:
            await PodcastEpisode.restore_command_link(
                episode_id, failed_command_id
            )
            raise

        return {
            "job_id": job_id,
            "message": "Retry submitted successfully",
            "episode_id": episode_id,
        }

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error retrying podcast episode: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to retry episode"
        )


@router.delete("/podcasts/episodes/{episode_id}")
async def delete_podcast_episode(episode_id: str):
    """Delete a podcast episode and its associated audio file"""
    try:
        # Get the episode first to check if it exists and get the audio file path
        episode = await PodcastService.get_episode(episode_id)

        # Delete the physical audio file if it exists
        _delete_episode_audio(episode, episode_id)

        # Delete the episode from the database
        await episode.delete()

        logger.info(f"Deleted podcast episode: {episode_id}")
        return {"message": "Episode deleted successfully", "episode_id": episode_id}

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error deleting podcast episode: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to delete episode"
        )
