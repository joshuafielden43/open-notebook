import os
from typing import Any, Dict, Optional

from loguru import logger
from pydantic import BaseModel

from api.command_service import CommandService
from open_notebook.database.repository import ensure_record_id
from open_notebook.domain.notebook import Notebook
from open_notebook.exceptions import (
    ConfigurationError,
    DatabaseOperationError,
    InvalidInputError,
    NotFoundError,
    OpenNotebookError,
)
from open_notebook.podcasts.models import EpisodeProfile, PodcastEpisode, SpeakerProfile


class PodcastGenerationRequest(BaseModel):
    """Request model for podcast generation"""

    episode_profile: str
    speaker_profile: str
    episode_name: str
    content: Optional[str] = None
    notebook_id: Optional[str] = None
    briefing_suffix: Optional[str] = None


class PodcastGenerationResponse(BaseModel):
    """Response model for podcast generation"""

    job_id: str
    status: str
    message: str
    episode_profile: str
    episode_name: str
    worker_likely_ready: Optional[bool] = None


class PodcastService:
    """Service layer for podcast operations"""

    @staticmethod
    def _podcast_context_max_chars() -> Optional[int]:
        """Fail-fast parse of the shared context dial (#1630)."""
        from open_notebook.context.assembly import podcast_context_max_chars

        raw = os.getenv("PODCAST_CONTEXT_MAX_CHARS", "120000")
        try:
            int(raw)
        except ValueError as e:
            raise ConfigurationError(
                "PODCAST_CONTEXT_MAX_CHARS must be an integer"
            ) from e
        return podcast_context_max_chars()

    @staticmethod
    def _validate_profiles_ready(
        episode_profile: EpisodeProfile, speaker_profile: SpeakerProfile
    ) -> None:
        """Fail at submit time when models/voices are not configured."""
        from open_notebook.podcasts.orchestration import validate_profiles_ready

        validate_profiles_ready(episode_profile, speaker_profile)

    @staticmethod
    async def submit_generation_job(
        episode_profile_name: str,
        speaker_profile_name: str,
        episode_name: str,
        notebook_id: Optional[str] = None,
        content: Optional[str] = None,
        briefing_suffix: Optional[str] = None,
        existing_episode_id: Optional[str] = None,
    ) -> str:
        """Submit a podcast generation job for background processing"""
        try:
            # Fail fast on invalid env (worker also reads this when assembling).
            PodcastService._podcast_context_max_chars()

            # Validate episode profile exists
            episode_profile = await EpisodeProfile.get_by_name(episode_profile_name)
            if not episode_profile:
                raise ValueError(f"Episode profile '{episode_profile_name}' not found")

            # Resolve the user-facing speaker profile name to a record ID at
            # the API boundary (#630) - everything downstream works with IDs.
            speaker_profile = await SpeakerProfile.resolve(speaker_profile_name)
            if not speaker_profile:
                raise ValueError(f"Speaker profile '{speaker_profile_name}' not found")

            PodcastService._validate_profiles_ready(episode_profile, speaker_profile)

            # Content-by-reference (#1603): do not put full notebook text in
            # surreal-commands args. Worker assembles via for_podcast(notebook_id)
            # or loads free-form text from the episode row.
            freeform = (content or "").strip() if content else ""
            if freeform.startswith("Notebook ID:"):
                raise ValueError(
                    "Notebook context is empty or invalid; add sources before "
                    "generating a podcast"
                )
            if notebook_id:
                # Lightweight existence check only — no full assembly at submit.
                try:
                    notebook = await Notebook.get(notebook_id)
                    if not notebook:
                        raise ValueError(f"Notebook '{notebook_id}' not found")
                except ValueError:
                    raise
                except Exception as e:
                    logger.error(f"Failed to load notebook {notebook_id}: {e}")
                    raise ValueError(
                        f"Could not load notebook '{notebook_id}': {e}"
                    ) from e
            elif not freeform:
                raise ValueError(
                    "Content is required — provide either content or notebook_id"
                )

            # Entity-first (#1604): durable episode row exists before the
            # worker claims the job, so the list is never empty while queued.
            from open_notebook.podcasts.orchestration import build_briefing

            if not existing_episode_id:
                stub = PodcastEpisode(
                    name=episode_name,
                    episode_profile=episode_profile.model_dump(mode="json"),
                    speaker_profile=speaker_profile.model_dump(mode="json"),
                    briefing=build_briefing(episode_profile, briefing_suffix),
                    # Free-form text lives on the row; notebook jobs leave empty
                    # until the worker assembles under PODCAST_CONTEXT_MAX_CHARS.
                    content=freeform,
                    notebook=notebook_id,
                    command=None,
                    audio_file=None,
                    outline=None,
                    transcript=None,
                )
                await stub.save()
                existing_episode_id = str(stub.id)
                logger.info(
                    f"Created queued episode row {existing_episode_id} "
                    f"for '{episode_name}' before job submit"
                )
            elif freeform:
                # Retry / re-submit with updated free-form body.
                try:
                    episode_row = await PodcastEpisode.get(existing_episode_id)
                    if episode_row:
                        episode_row.content = freeform
                        await episode_row.save()
                except Exception as e:
                    logger.warning(
                        f"Could not refresh content on {existing_episode_id}: {e}"
                    )

            # Command args are references + knobs only — no multi-MB content blob.
            command_args = {
                "episode_profile": episode_profile_name,
                "speaker_profile": str(speaker_profile.id),
                "episode_name": episode_name,
                "briefing_suffix": briefing_suffix,
                "existing_episode_id": existing_episode_id,
            }
            if notebook_id:
                command_args["notebook_id"] = str(notebook_id)

            # All API job submits go through CommandService so command-module
            # imports stay in one place (#1610).
            job_id_str = await CommandService.submit_command_job(
                "open_notebook",
                "generate_podcast",
                command_args,
            )

            # Link command onto the episode so list filters (command OR audio)
            # show the card immediately, even before the worker starts.
            try:
                episode_row = await PodcastEpisode.get(existing_episode_id)
                if episode_row:
                    episode_row.command = job_id_str
                    await episode_row.save()
            except Exception as link_err:
                logger.warning(
                    f"Episode {existing_episode_id} created but command link "
                    f"failed ({link_err}); worker will attach command_id"
                )

            logger.info(
                f"Submitted podcast generation job: {job_id_str} for episode "
                f"'{episode_name}' ({existing_episode_id})"
            )
            return job_id_str

        except OpenNotebookError:
            raise
        except ValueError as e:
            # Surface validation errors as 400 so the UI can show the real reason.
            logger.warning(f"Podcast generation rejected: {e}")
            raise InvalidInputError(str(e)) from e
        except Exception as e:
            logger.error(f"Failed to submit podcast generation job: {e}")
            raise DatabaseOperationError(
                "Failed to submit podcast generation job"
            ) from e

    @staticmethod
    async def get_job_status(job_id: str) -> Dict[str, Any]:
        """Get status of a podcast generation job (shared with CommandService)."""
        try:
            return await CommandService.get_command_status(job_id)
        except Exception as e:
            logger.error(f"Failed to get podcast job status: {e}")
            raise DatabaseOperationError("Failed to get job status") from e

    @staticmethod
    def _require_notebook_record_id(notebook_id: str):
        """Parse notebook_id or raise 400 — bad ids must not become Surreal 500s."""
        try:
            rid = ensure_record_id(notebook_id)
        except (TypeError, ValueError) as e:
            raise InvalidInputError("Invalid notebook_id") from e
        if rid.table_name != "notebook" or not str(rid.id).strip():
            raise InvalidInputError("Invalid notebook_id")
        return rid

    @staticmethod
    async def list_episodes(
        *,
        summary: bool = True,
        limit: int = 50,
        offset: int = 0,
        notebook_id: Optional[str] = None,
    ) -> list:
        """List podcast episodes (summary projection omits fat blobs in SQL)."""
        # Reject garbage like notebook_id=[object Object] before SurrealDB sees it.
        if notebook_id is not None:
            PodcastService._require_notebook_record_id(notebook_id)
        try:
            if summary:
                return await PodcastEpisode.list_summary(
                    order_by="created desc",
                    limit=limit,
                    offset=offset,
                    notebook_id=notebook_id,
                )
            # detail=full must return outline/transcript blobs so the UI can
            # show them mid-run. A prior branch always used list_summary when
            # notebook_id was set, so the notebook page never got fat fields
            # even with detail=full (global podcasts page did via get_all).
            return await PodcastEpisode.list_full(
                order_by="created desc",
                limit=limit,
                offset=offset,
                notebook_id=notebook_id,
            )
        except OpenNotebookError:
            raise
        except Exception as e:
            logger.error(f"Failed to list podcast episodes: {e}")
            raise DatabaseOperationError("Failed to list episodes") from e

    @staticmethod
    async def get_episode(episode_id: str) -> PodcastEpisode:
        """Get a specific podcast episode"""
        try:
            episode = await PodcastEpisode.get(episode_id)
            if not episode:
                raise NotFoundError("Episode not found")
            return episode
        except OpenNotebookError:
            raise
        except Exception as e:
            logger.error(f"Failed to get podcast episode {episode_id}: {e}")
            raise NotFoundError("Episode not found") from e
