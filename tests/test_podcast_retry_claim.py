"""#1608 — episode retry CAS claim prevents double-submit on one row."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from open_notebook.podcasts.models import PodcastEpisode


@pytest.mark.asyncio
async def test_claim_for_retry_wins_when_command_matches():
    async def fake_repo(query_str, vars=None):
        assert "WHERE command = $expected" in query_str
        assert vars is not None
        assert "id" in vars and "expected" in vars
        return [{"id": "episode:abc", "command": None}]

    with patch(
        "open_notebook.podcasts.models.repo_query", new=fake_repo
    ):
        assert await PodcastEpisode.claim_for_retry(
            "episode:abc", "command:failed1"
        )


@pytest.mark.asyncio
async def test_claim_for_retry_loses_when_already_claimed():
    async def fake_repo(query_str, vars=None):
        # Surreal returns empty when WHERE does not match.
        return []

    with patch(
        "open_notebook.podcasts.models.repo_query", new=fake_repo
    ):
        assert not await PodcastEpisode.claim_for_retry(
            "episode:abc", "command:failed1"
        )


@pytest.mark.asyncio
async def test_claim_for_retry_rejects_empty_ids():
    assert not await PodcastEpisode.claim_for_retry("", "command:x")
    assert not await PodcastEpisode.claim_for_retry("episode:x", "")


@pytest.mark.asyncio
async def test_restore_command_link_only_when_none():
    calls = []

    async def fake_repo(query_str, vars=None):
        calls.append(" ".join(query_str.split()))
        assert vars is not None
        assert "command" in vars
        return []

    with patch(
        "open_notebook.podcasts.models.repo_query", new=fake_repo
    ):
        await PodcastEpisode.restore_command_link(
            "episode:abc", "command:failed1"
        )

    assert calls
    assert "WHERE command IS NONE" in calls[0]


@pytest.mark.asyncio
async def test_retry_endpoint_second_claim_returns_409():
    """Two concurrent retries: first claims, second gets 409 without submit."""
    from fastapi import HTTPException

    from api.routers import podcasts as podcasts_router

    episode = AsyncMock()
    episode.command = "command:old-failed"
    episode.episode_profile = {"name": "ep", "default_briefing": "hi"}
    episode.speaker_profile = {"name": "sp"}
    episode.name = "Show"
    episode.content = "body"
    episode.briefing = "hi"
    episode.notebook = None

    claim_results = [True, False]  # first wins, second loses
    submit = AsyncMock(return_value="command:new")

    async def fake_claim(episode_id, expected_command):
        return claim_results.pop(0)

    with (
        patch.object(
            podcasts_router.PodcastService,
            "get_episode",
            new=AsyncMock(return_value=episode),
        ),
        patch(
            "open_notebook.jobs.fail_stale_running_commands",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "open_notebook.database.repository.repo_query",
            new=AsyncMock(return_value=[{"status": "failed"}]),
        ),
        patch.object(
            PodcastEpisode, "claim_for_retry", new=fake_claim
        ),
        patch.object(
            podcasts_router.PodcastService,
            "submit_generation_job",
            new=submit,
        ),
    ):
        first = await podcasts_router.retry_podcast_episode("episode:abc")
        assert first["job_id"] == "command:new"
        assert submit.await_count == 1

        with pytest.raises(HTTPException) as ei:
            await podcasts_router.retry_podcast_episode("episode:abc")
        assert ei.value.status_code == 409
        assert submit.await_count == 1  # second never submits


@pytest.mark.asyncio
async def test_retry_endpoint_restores_command_when_submit_fails():
    from fastapi import HTTPException

    from api.routers import podcasts as podcasts_router

    episode = AsyncMock()
    episode.command = "command:old-failed"
    episode.episode_profile = {"name": "ep", "default_briefing": "hi"}
    episode.speaker_profile = {"name": "sp"}
    episode.name = "Show"
    episode.content = "body"
    episode.briefing = "hi"
    episode.notebook = None

    restore = AsyncMock()

    with (
        patch.object(
            podcasts_router.PodcastService,
            "get_episode",
            new=AsyncMock(return_value=episode),
        ),
        patch(
            "open_notebook.jobs.fail_stale_running_commands",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "open_notebook.database.repository.repo_query",
            new=AsyncMock(return_value=[{"status": "failed"}]),
        ),
        patch.object(
            PodcastEpisode, "claim_for_retry", new=AsyncMock(return_value=True)
        ),
        patch.object(
            PodcastEpisode, "restore_command_link", new=restore
        ),
        patch.object(
            podcasts_router.PodcastService,
            "submit_generation_job",
            new=AsyncMock(
                side_effect=HTTPException(status_code=400, detail="nope")
            ),
        ),
    ):
        with pytest.raises(HTTPException) as ei:
            await podcasts_router.retry_podcast_episode("episode:abc")
        assert ei.value.status_code == 400

    restore.assert_awaited_once_with("episode:abc", "command:old-failed")
