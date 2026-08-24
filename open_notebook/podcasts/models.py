from typing import Any, ClassVar, Dict, List, Optional, Tuple, Union

from loguru import logger
from pydantic import ConfigDict, Field, field_validator
from surrealdb import RecordID

from open_notebook.ai.runtime import resolve_model_config
from open_notebook.database.repository import ensure_record_id, repo_query
from open_notebook.domain.base import ObjectModel

# Back-compat alias for tests and older imports.
_resolve_model_config = resolve_model_config


class EpisodeProfile(ObjectModel):
    """
    Episode Profile - Simplified podcast configuration.
    Replaces complex 15+ field configuration with user-friendly profiles.
    """

    table_name: ClassVar[str] = "episode_profile"
    nullable_fields: ClassVar[set[str]] = {
        "description",
        "speaker_config",
        "outline_llm",
        "transcript_llm",
        "language",
        "max_tokens",
    }

    name: str = Field(..., description="Unique profile name")
    description: Optional[str] = Field(None, description="Profile description")
    speaker_config: Optional[str] = Field(
        None,
        description=(
            "speaker_profile record ID this profile uses. None when the "
            "referenced speaker profile no longer exists (orphaned by "
            "migration 20 or a later deletion)."
        ),
    )

    # Model registry references
    outline_llm: Optional[str] = Field(
        None, description="Model record ID for outline generation"
    )
    transcript_llm: Optional[str] = Field(
        None, description="Model record ID for transcript generation"
    )
    language: Optional[str] = Field(
        None, description="Podcast language (BCP 47 locale code, e.g. pt-BR, en-US)"
    )

    default_briefing: str = Field(..., description="Default briefing template")
    num_segments: int = Field(default=5, description="Number of podcast segments")
    max_tokens: Optional[int] = Field(
        None,
        description="Max output tokens for outline/transcript generation (passed through to podcast_creator)",
    )

    @field_validator("num_segments")
    @classmethod
    def validate_segments(cls, v):
        # podcast-creator rejects >10 segments; keep the same ceiling here so
        # a saved profile cannot fail every generation at library validation.
        if not 3 <= v <= 10:
            raise ValueError("Number of segments must be between 3 and 10")
        return v

    def _prepare_save_data(self) -> dict:
        data = super()._prepare_save_data()
        if data.get("speaker_config"):
            data["speaker_config"] = ensure_record_id(data["speaker_config"])
        if data.get("outline_llm"):
            data["outline_llm"] = ensure_record_id(data["outline_llm"])
        if data.get("transcript_llm"):
            data["transcript_llm"] = ensure_record_id(data["transcript_llm"])
        return data

    async def resolve_outline_config(self) -> Tuple[str, str, dict]:
        """Resolve outline model -> (provider, model_name, config_dict)"""
        if not self.outline_llm:
            raise ValueError(
                f"Episode profile '{self.name}' has no outline model configured. "
                "Please update the profile to select an outline model."
            )
        return await resolve_model_config(self.outline_llm, max_tokens=self.max_tokens)

    async def resolve_transcript_config(self) -> Tuple[str, str, dict]:
        """Resolve transcript model -> (provider, model_name, config_dict)"""
        if not self.transcript_llm:
            raise ValueError(
                f"Episode profile '{self.name}' has no transcript model configured. "
                "Please update the profile to select a transcript model."
            )
        return await resolve_model_config(
            self.transcript_llm, max_tokens=self.max_tokens
        )

    @classmethod
    async def get_by_name(cls, name: str) -> Optional["EpisodeProfile"]:
        """Get episode profile by name"""
        result = await repo_query(
            "SELECT * FROM episode_profile WHERE name = $name", {"name": name}
        )
        if result:
            return cls(**result[0])
        return None


class SpeakerProfile(ObjectModel):
    """
    Speaker Profile - Voice and personality configuration.
    Supports 1-4 speakers for flexible podcast formats.
    """

    table_name: ClassVar[str] = "speaker_profile"
    nullable_fields: ClassVar[set[str]] = {
        "description",
        "voice_model",
    }

    name: str = Field(..., description="Unique profile name")
    description: Optional[str] = Field(None, description="Profile description")

    # Model registry reference
    voice_model: Optional[str] = Field(None, description="Model record ID for TTS")

    speakers: List[Dict[str, Any]] = Field(
        ..., description="Array of speaker configurations"
    )

    @field_validator("speakers")
    @classmethod
    def validate_speakers(cls, v):
        if not 1 <= len(v) <= 4:
            raise ValueError("Must have between 1 and 4 speakers")

        required_fields = ["name", "voice_id", "backstory", "personality"]
        for speaker in v:
            for field in required_fields:
                if field not in speaker:
                    raise ValueError(f"Speaker missing required field: {field}")
            if not str(speaker.get("voice_id") or "").strip():
                raise ValueError("Speaker voice_id cannot be empty")

        names = [str(speaker.get("name") or "").strip() for speaker in v]
        if len(names) != len(set(names)):
            raise ValueError("Speaker names must be unique")

        # podcast-creator requires unique voice IDs across the profile.
        voice_ids = [str(speaker.get("voice_id") or "").strip() for speaker in v]
        if len(voice_ids) != len(set(voice_ids)):
            raise ValueError("Speaker voice_ids must be unique")
        return v

    def _prepare_save_data(self) -> dict:
        data = super()._prepare_save_data()
        if data.get("voice_model"):
            data["voice_model"] = ensure_record_id(data["voice_model"])
        # Handle per-speaker voice_model overrides
        if data.get("speakers"):
            for speaker in data["speakers"]:
                if speaker.get("voice_model"):
                    speaker["voice_model"] = ensure_record_id(speaker["voice_model"])
        return data

    async def resolve_tts_config(self) -> Tuple[str, str, dict]:
        """Resolve TTS model -> (provider, model_name, config_dict)"""
        if not self.voice_model:
            raise ValueError(
                f"Speaker profile '{self.name}' has no voice model configured. "
                "Please update the profile to select a voice model."
            )
        return await resolve_model_config(self.voice_model)

    @classmethod
    async def get_by_name(cls, name: str) -> Optional["SpeakerProfile"]:
        """Get speaker profile by name"""
        result = await repo_query(
            "SELECT * FROM speaker_profile WHERE name = $name", {"name": name}
        )
        if result:
            return cls(**result[0])
        return None

    @classmethod
    async def resolve(cls, ref: Union[str, RecordID]) -> Optional["SpeakerProfile"]:
        """Resolve a speaker profile by record ID or by unique name.

        The API contract accepts speaker profiles by NAME (see
        POST /api/podcasts/generate), while episode_profile.speaker_config
        stores a record ID (migration 20). This resolves either form and
        returns None when the reference doesn't match anything.
        """
        ref_str = str(ref)
        if ref_str.startswith(f"{cls.table_name}:"):
            result = await repo_query(
                "SELECT * FROM $id", {"id": ensure_record_id(ref_str)}
            )
            if result:
                return cls(**result[0])
            return None
        return await cls.get_by_name(ref_str)


class PodcastEpisode(ObjectModel):
    """Enhanced PodcastEpisode with job tracking and metadata"""

    table_name: ClassVar[str] = "episode"
    nullable_fields: ClassVar[set[str]] = {
        "notebook",
        "audio_file",
        "transcript",
        "outline",
        "command",
    }

    name: str = Field(..., description="Episode name")
    episode_profile: Dict[str, Any] = Field(
        ..., description="Episode profile used (stored as object)"
    )
    speaker_profile: Dict[str, Any] = Field(
        ..., description="Speaker profile used (stored as object)"
    )
    briefing: str = Field(..., description="Full briefing used for generation")
    content: str = Field(default="", description="Source content")
    # notebook record id when generation was started from a notebook; None for
    # free-form content-only jobs and pre-migration 27 rows.
    notebook: Optional[Union[str, RecordID]] = Field(
        default=None,
        description="Notebook this episode was generated from (if any)",
    )
    audio_file: Optional[str] = Field(
        default=None,
        description=(
            "Path to the generated audio file, relative to PODCASTS_FOLDER "
            "(see open_notebook/podcasts/audio_paths.py). Absolute values "
            "are legacy rows migration 21 could not convert and are treated "
            "as invalid by the API."
        ),
    )
    transcript: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Generated transcript"
    )
    outline: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Generated outline"
    )
    command: Optional[Union[str, RecordID]] = Field(
        default=None, description="Link to surreal-commands job"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Optional list-only flags (not persisted) so generation_stage works without
    # shipping full outline/transcript blobs.
    has_outline: Optional[bool] = Field(default=None, exclude=True)
    has_transcript: Optional[bool] = Field(default=None, exclude=True)

    @classmethod
    async def list_full(
        cls,
        order_by: str = "created desc",
        *,
        limit: int = 50,
        offset: int = 0,
        notebook_id: Optional[str] = None,
    ) -> List["PodcastEpisode"]:
        """List full episode rows including outline/transcript (detail=full).

        Same filter/limit surface as list_summary so notebook-scoped polls get
        mid-run outline/transcript the same way the global list does.
        """
        validated = cls._validate_order_by(order_by)
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        where = ""
        params: Dict[str, Any] = {}
        if notebook_id:
            where = "WHERE notebook = $notebook "
            params["notebook"] = ensure_record_id(notebook_id)
        query = (
            f"SELECT * FROM {cls.table_name} {where}ORDER BY {validated} "
            f"LIMIT {limit} START {offset}"
        )
        result = await repo_query(query, params)
        objects: List[PodcastEpisode] = []
        for obj in result or []:
            try:
                objects.append(cls(**obj))
            except Exception as e:
                logger.critical(f"Error creating episode full object: {e}")
        return objects

    @classmethod
    async def list_summary(
        cls,
        order_by: str = "created desc",
        *,
        limit: int = 50,
        offset: int = 0,
        notebook_id: Optional[str] = None,
    ) -> List["PodcastEpisode"]:
        """List episodes without fat content/transcript/outline payloads.

        Used by the episodes list endpoint so the DB does not ship multi-MB
        blobs just to paint status cards. Projects cheap presence flags for
        generation_stage without loading the blobs.
        """
        validated = cls._validate_order_by(order_by)
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        where = ""
        params: Dict[str, Any] = {}
        if notebook_id:
            where = "WHERE notebook = $notebook "
            params["notebook"] = ensure_record_id(notebook_id)
        # Presence flags keep stage UI honest without fat payloads.
        query = (
            f"SELECT id, name, episode_profile, speaker_profile, briefing, "
            f"notebook, audio_file, command, created, updated, "
            f"(outline != NONE AND outline != {{}}) AS has_outline, "
            f"(transcript != NONE AND transcript != {{}}) AS has_transcript "
            f"FROM {cls.table_name} {where}ORDER BY {validated} "
            f"LIMIT {limit} START {offset}"
        )
        try:
            result = await repo_query(query, params)
        except Exception as e:
            # Older Surreal builds may reject the flag expressions; fall back.
            logger.warning(f"list_summary with flags failed, falling back: {e}")
            query = (
                f"SELECT id, name, episode_profile, speaker_profile, briefing, "
                f"notebook, audio_file, command, created, updated "
                f"FROM {cls.table_name} {where}ORDER BY {validated} "
                f"LIMIT {limit} START {offset}"
            )
            result = await repo_query(query, params)
        objects: List[PodcastEpisode] = []
        for obj in result or []:
            row = dict(obj)
            has_outline = row.pop("has_outline", None)
            has_transcript = row.pop("has_transcript", None)
            row.setdefault("content", "")
            row.setdefault("transcript", None)
            row.setdefault("outline", None)
            briefing = row.get("briefing") or ""
            if isinstance(briefing, str) and len(briefing) > 240:
                row["briefing"] = briefing[:240] + "…"
            try:
                episode = cls(**row)
                episode.has_outline = (
                    bool(has_outline) if has_outline is not None else None
                )
                episode.has_transcript = (
                    bool(has_transcript) if has_transcript is not None else None
                )
                objects.append(episode)
            except Exception as e:
                logger.critical(f"Error creating episode summary object: {e}")
        return objects

    @staticmethod
    def command_status_key(command_id: Union[str, RecordID, None]) -> Optional[str]:
        """Canonical map key for command ids (always `command:…` string)."""
        if not command_id:
            return None
        try:
            return str(ensure_record_id(command_id))
        except Exception:
            return str(command_id)

    @staticmethod
    def _normalize_command_status_detail(detail: dict) -> dict:
        """Terminal truth for UI: failed jobs must never present as running.

        surreal-commands can leave a non-empty error_message while status is
        still running in rare failure paths; treat that as failed. Always
        lower-case the status string so enum-like values compare cleanly.
        """
        status = str(detail.get("status") or "unknown").lower()
        error_message = detail.get("error_message")
        if error_message is not None:
            error_message = str(error_message).strip() or None
        if status in {"running", "processing"} and error_message:
            status = "failed"
        return {
            **detail,
            "status": status,
            "error_message": error_message,
        }

    async def get_job_status(self) -> Optional[str]:
        """Get the status of the associated command"""
        detail = await self.get_job_detail()
        return detail.get("status")

    @classmethod
    async def claim_for_retry(cls, episode_id: str, expected_command: str) -> bool:
        """Compare-and-swap claim so two retries cannot share one episode row.

        Succeeds only when ``episode.command`` still equals the failed command
        we verified as retryable. Clears the pointer (sets command to NONE) so
        a concurrent retry sees non-retryable state until the winner links its
        new job id. Returns True if this caller won the claim (#1608).
        """
        if not episode_id or not expected_command:
            return False
        try:
            rows = await repo_query(
                """
                UPDATE $id SET
                    command = NONE,
                    updated = time::now()
                WHERE command = $expected
                RETURN AFTER
                """,
                {
                    "id": ensure_record_id(episode_id),
                    "expected": ensure_record_id(expected_command),
                },
            )
            return bool(rows)
        except Exception as e:
            logger.error(
                f"Failed to claim episode {episode_id} for retry "
                f"(expected command {expected_command}): {e}"
            )
            return False

    @classmethod
    async def restore_command_link(cls, episode_id: str, command_id: str) -> None:
        """Restore the prior command link after a failed retry submit.

        Only writes when command is still NONE (the claimed state) so we never
        overwrite a newer winner's job id.
        """
        if not episode_id or not command_id:
            return
        try:
            await repo_query(
                """
                UPDATE $id SET
                    command = $command,
                    updated = time::now()
                WHERE command IS NONE
                """,
                {
                    "id": ensure_record_id(episode_id),
                    "command": ensure_record_id(command_id),
                },
            )
        except Exception as e:
            logger.warning(
                f"Could not restore command {command_id} on episode "
                f"{episode_id} after failed retry submit: {e}"
            )

    async def get_job_detail(self) -> dict:
        """Get status and error_message of the associated command.

        Reads the command table directly (same path as the batch list helper)
        so detail and list cannot disagree on status for the same command id.
        """
        if not self.command:
            return {"status": None, "error_message": None, "updated": None}

        try:
            from open_notebook.jobs import (
                coerce_status_detail,
                fail_stale_running_commands,
            )

            # Opportunistically reaping zombies keeps retry unblocked.
            try:
                await fail_stale_running_commands()
            except Exception as reap_err:
                logger.warning(f"Stale command reaper skipped: {reap_err}")

            result = await repo_query(
                "SELECT id, status, error_message, updated FROM command "
                "WHERE id = $command_id",
                {"command_id": ensure_record_id(self.command)},
            )
            if not result:
                return {"status": "unknown", "error_message": None, "updated": None}
            row = result[0]
            detail = self._normalize_command_status_detail(
                {
                    "status": row.get("status", "unknown"),
                    "error_message": row.get("error_message"),
                    "updated": str(row.get("updated"))
                    if row.get("updated") is not None
                    else None,
                }
            )
            return coerce_status_detail(detail)
        except Exception:
            return {"status": "unknown", "error_message": None, "updated": None}

    @classmethod
    async def get_job_details_for_commands(
        cls, command_ids: List[Union[str, RecordID]]
    ) -> Dict[str, dict]:
        """
        Batch-fetch {status, error_message, updated} for many commands in one query.

        Listing episodes otherwise calls get_job_detail() once per episode —
        O(n) round trips. Keys are always canonical `command:…` strings so
        router lookups via command_status_key() never miss a row because of
        RecordID vs str formatting.
        """
        ids = [cid for cid in command_ids if cid]
        grouped: Dict[str, dict] = {}
        if not ids:
            return grouped
        try:
            from open_notebook.jobs import (
                coerce_status_detail,
                fail_stale_running_commands,
            )

            try:
                await fail_stale_running_commands()
            except Exception as reap_err:
                logger.warning(f"Stale command reaper skipped: {reap_err}")
            result = await repo_query(
                "SELECT id, status, error_message, updated FROM command WHERE id IN $command_ids",
                {"command_ids": [ensure_record_id(cid) for cid in ids]},
            )
        except Exception as e:
            logger.error(f"Error batch-fetching command status: {e}")
            return grouped
        for row in result or []:
            detail = cls._normalize_command_status_detail(
                {
                    "status": row.get("status", "unknown"),
                    "error_message": row.get("error_message"),
                    "updated": str(row.get("updated"))
                    if row.get("updated") is not None
                    else None,
                }
            )
            key = cls.command_status_key(row.get("id"))
            if key:
                grouped[key] = coerce_status_detail(detail)
        return grouped

    @field_validator("command", mode="before")
    @classmethod
    def parse_command(cls, value):
        if isinstance(value, str):
            return ensure_record_id(value)
        return value

    def _prepare_save_data(self) -> dict:
        """Ensure record-typed fields stay RecordIDs for SurrealDB.

        A second earlier override only converted notebook and was shadowed by
        this method (command-only). That left notebook as a plain string on
        create, Surreal rejected option<record<notebook>>, and the job died
        before any episode row existed — so generate looked like a silent no-op.
        """
        data = super()._prepare_save_data()
        if data.get("command") is not None:
            data["command"] = ensure_record_id(data["command"])
        if data.get("notebook") is not None:
            data["notebook"] = ensure_record_id(data["notebook"])
        return data
