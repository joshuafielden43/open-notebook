"""Active command listing for ambient background-job UI (#1626)."""

from unittest.mock import AsyncMock, patch

import pytest

from api.command_service import CommandService


@pytest.mark.asyncio
async def test_list_active_jobs_maps_rows():
    rows = [
        {
            "id": "command:abc",
            "name": "generate_podcast",
            "app": "open_notebook",
            "status": "running",
            "error_message": None,
            "created": None,
            "updated": "2026-07-27T12:00:00Z",
        }
    ]
    with patch(
        "api.command_service.repo_query",
        new=AsyncMock(return_value=rows),
    ):
        jobs = await CommandService.list_active_jobs()

    assert len(jobs) == 1
    assert jobs[0]["job_id"] == "command:abc"
    assert jobs[0]["status"] == "running"
    assert jobs[0]["name"] == "generate_podcast"


@pytest.mark.asyncio
async def test_list_active_jobs_raises_on_db_error():
    with patch(
        "api.command_service.repo_query",
        new=AsyncMock(side_effect=RuntimeError("db")),
    ):
        with pytest.raises(RuntimeError):
            await CommandService.list_active_jobs()
