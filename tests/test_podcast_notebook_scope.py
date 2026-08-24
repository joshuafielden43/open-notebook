"""Episodes remember the notebook they were generated from."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.podcast_service import PodcastService
from commands.podcast_commands import PodcastGenerationInput
from open_notebook.exceptions import InvalidInputError
from open_notebook.podcasts.episode_generation import EpisodeGenerationRequest
from open_notebook.podcasts.models import PodcastEpisode


def test_episode_model_accepts_notebook():
    ep = PodcastEpisode(
        name="scoped",
        episode_profile={"name": "solo"},
        speaker_profile={"name": "voice"},
        briefing="brief",
        content="body",
        notebook="notebook:abc",
    )
    assert str(ep.notebook) == "notebook:abc"
    data = ep._prepare_save_data()
    assert data["notebook"] is not None
    # Surreal TYPE option<record<notebook>> rejects bare strings.
    from surrealdb import RecordID

    assert isinstance(data["notebook"], RecordID)
    assert data["notebook"].table_name == "notebook"
    assert str(data["notebook"].id) == "abc"


def test_prepare_save_converts_notebook_and_command():
    """Regression: a shadowed _prepare_save_data only fixed command."""
    from surrealdb import RecordID

    ep = PodcastEpisode(
        name="scoped",
        episode_profile={"name": "solo"},
        speaker_profile={"name": "voice"},
        briefing="brief",
        content="body",
        notebook="notebook:abc",
        command="command:job1",
    )
    data = ep._prepare_save_data()
    assert isinstance(data["notebook"], RecordID)
    assert isinstance(data["command"], RecordID)


def test_command_input_accepts_notebook_id():
    inp = PodcastGenerationInput(
        episode_profile="solo_expert",
        episode_name="ep",
        content="text",
        notebook_id="notebook:xyz",
    )
    assert inp.notebook_id == "notebook:xyz"


def test_generate_podcast_submit_schema_accepts_args():
    """surreal-commands validates submit args via RunnableLambda input schema.

    content is optional (content-by-reference); notebook_id + existing_episode_id
    are enough for the worker to assemble or load text.
    """
    from surreal_commands.core.registry import registry

    import commands.podcast_commands  # noqa: F401 — register command

    item = registry.get_command_by_id("open_notebook.generate_podcast")
    assert item is not None
    schema = item.input_schema
    validated = schema(
        episode_profile="solo_expert",
        speaker_profile="speaker_profile:1",
        episode_name="ep",
        notebook_id="notebook:xyz",
        existing_episode_id="episode:stub",
    )
    dumped = validated.model_dump()
    # LangChain wraps the single param as root=... for some versions; accept either shape.
    payload = dumped.get("root", dumped) if isinstance(dumped, dict) else dumped
    if isinstance(payload, dict):
        assert payload.get("notebook_id") == "notebook:xyz" or (
            hasattr(payload, "notebook_id") and payload.notebook_id == "notebook:xyz"
        )
        assert payload.get("content") in (None, "")
    else:
        assert payload.notebook_id == "notebook:xyz"


def test_generation_request_carries_notebook_id():
    req = EpisodeGenerationRequest(
        episode_profile="solo_expert",
        episode_name="ep",
        notebook_id="notebook:xyz",
    )
    assert req.notebook_id == "notebook:xyz"
    assert req.content is None


@pytest.mark.asyncio
async def test_resolve_episode_content_assembles_from_notebook():
    from open_notebook.podcasts.episode_generation import resolve_episode_content

    req = EpisodeGenerationRequest(
        episode_profile="solo_expert",
        episode_name="ep",
        notebook_id="notebook:xyz",
    )
    notebook = MagicMock()
    with (
        patch(
            "open_notebook.domain.notebook.Notebook.get",
            new=AsyncMock(return_value=notebook),
        ),
        patch(
            "open_notebook.context.for_podcast",
            new=AsyncMock(return_value="assembled body"),
        ),
    ):
        text = await resolve_episode_content(req)
    assert text == "assembled body"


@pytest.mark.asyncio
async def test_resolve_episode_content_prefers_episode_row():
    from open_notebook.podcasts.episode_generation import resolve_episode_content

    req = EpisodeGenerationRequest(
        episode_profile="solo_expert",
        episode_name="ep",
        notebook_id="notebook:xyz",
    )
    existing = MagicMock()
    existing.id = "episode:1"
    existing.content = "stored freeform"
    text = await resolve_episode_content(req, existing)
    assert text == "stored freeform"


@pytest.mark.asyncio
async def test_submit_job_forwards_notebook_id():
    ep = MagicMock()
    ep.name = "solo_expert"
    ep.outline_llm = "model:o"
    ep.transcript_llm = "model:t"
    ep.default_briefing = "brief"
    ep.model_dump = MagicMock(return_value={"name": "solo_expert"})
    sp = MagicMock()
    sp.id = "speaker_profile:1"
    sp.name = "voice"
    sp.voice_model = "model:v"
    sp.speakers = [{"name": "A", "voice_id": "v1"}]
    sp.model_dump = MagicMock(return_value={"name": "voice"})

    submitted = {}
    saved = {}

    async def capture_submit(app, cmd, args, context=None):
        submitted["args"] = args
        return "command:job1"

    async def fake_save(self):
        if not self.id:
            self.id = "episode:stub1"
        saved["episode"] = self

    linked = MagicMock()
    linked.id = "episode:stub1"
    linked.command = None
    linked.save = AsyncMock()

    with (
        patch(
            "api.podcast_service.EpisodeProfile.get_by_name",
            new=AsyncMock(return_value=ep),
        ),
        patch(
            "api.podcast_service.SpeakerProfile.resolve",
            new=AsyncMock(return_value=sp),
        ),
        patch(
            "api.podcast_service.Notebook.get",
            new=AsyncMock(return_value=MagicMock()),
        ),
        patch.object(PodcastService, "_validate_profiles_ready"),
        patch(
            "api.podcast_service.CommandService.submit_command_job",
            side_effect=capture_submit,
        ),
        patch.object(PodcastEpisode, "save", fake_save),
        patch.object(PodcastEpisode, "get", new=AsyncMock(return_value=linked)),
        patch(
            "open_notebook.podcasts.orchestration.build_briefing",
            return_value="brief",
        ),
    ):
        job_id = await PodcastService.submit_generation_job(
            episode_profile_name="solo_expert",
            speaker_profile_name="voice",
            episode_name="from-nb",
            notebook_id="notebook:abc",
        )

    assert job_id == "command:job1"
    assert submitted["args"]["notebook_id"] == "notebook:abc"
    # Content-by-reference (#1603): no full text in command args
    assert "content" not in submitted["args"]
    assert submitted["args"]["existing_episode_id"] == "episode:stub1"
    assert linked.command == "command:job1"
    linked.save.assert_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_id",
    [
        "[object Object]",
        "not-a-record",
        "",
        "notebook:",
        "source:xyz",
    ],
)
async def test_list_episodes_rejects_malformed_notebook_id(bad_id):
    """Garbage notebook_id must be 400, not a SurrealDB-driven 500."""
    with pytest.raises(InvalidInputError) as exc_info:
        await PodcastService.list_episodes(notebook_id=bad_id)
    assert "notebook_id" in str(exc_info.value)


@pytest.mark.asyncio
async def test_list_episodes_accepts_valid_notebook_id():
    with patch(
        "api.podcast_service.PodcastEpisode.list_summary",
        new=AsyncMock(return_value=[]),
    ) as list_summary:
        result = await PodcastService.list_episodes(notebook_id="notebook:abc")
    assert result == []
    list_summary.assert_awaited_once()
    assert list_summary.await_args is not None
    assert list_summary.await_args.kwargs["notebook_id"] == "notebook:abc"


@pytest.mark.asyncio
async def test_list_episodes_full_detail_uses_list_full_not_summary():
    """Notebook-scoped detail=full must load outline/transcript blobs."""
    with (
        patch(
            "api.podcast_service.PodcastEpisode.list_full",
            new=AsyncMock(return_value=["full-row"]),
        ) as list_full,
        patch(
            "api.podcast_service.PodcastEpisode.list_summary",
            new=AsyncMock(return_value=["summary-row"]),
        ) as list_summary,
    ):
        result = await PodcastService.list_episodes(
            summary=False,
            notebook_id="notebook:abc",
        )
    assert result == ["full-row"]
    list_full.assert_awaited_once()
    assert list_full.await_args is not None
    assert list_full.await_args.kwargs["notebook_id"] == "notebook:abc"
    list_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_job_omits_notebook_when_freeform():
    ep = MagicMock()
    ep.name = "solo_expert"
    ep.outline_llm = "model:o"
    ep.transcript_llm = "model:t"
    ep.default_briefing = "brief"
    ep.model_dump = MagicMock(return_value={"name": "solo_expert"})
    sp = MagicMock()
    sp.id = "speaker_profile:1"
    sp.name = "voice"
    sp.voice_model = "model:v"
    sp.speakers = [{"name": "A", "voice_id": "v1"}]
    sp.model_dump = MagicMock(return_value={"name": "voice"})

    submitted = {}

    async def capture_submit(app, cmd, args, context=None):
        submitted["args"] = args
        return "command:job2"

    async def fake_save(self):
        if not self.id:
            self.id = "episode:stub2"

    linked = MagicMock()
    linked.save = AsyncMock()

    with (
        patch(
            "api.podcast_service.EpisodeProfile.get_by_name",
            new=AsyncMock(return_value=ep),
        ),
        patch(
            "api.podcast_service.SpeakerProfile.resolve",
            new=AsyncMock(return_value=sp),
        ),
        patch.object(PodcastService, "_validate_profiles_ready"),
        patch(
            "api.podcast_service.CommandService.submit_command_job",
            side_effect=capture_submit,
        ),
        patch.object(PodcastEpisode, "save", fake_save),
        patch.object(PodcastEpisode, "get", new=AsyncMock(return_value=linked)),
        patch(
            "open_notebook.podcasts.orchestration.build_briefing",
            return_value="brief",
        ),
    ):
        await PodcastService.submit_generation_job(
            episode_profile_name="solo_expert",
            speaker_profile_name="voice",
            episode_name="freeform",
            content="hello world",
        )

    assert "notebook_id" not in submitted["args"]
    assert "content" not in submitted["args"]
    assert submitted["args"]["existing_episode_id"] == "episode:stub2"
