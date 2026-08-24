"""#1606 — POST /commands/jobs is allowlist-locked."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from api.routers import commands as commands_router
from api.routers.commands import CommandExecutionRequest


@pytest.mark.asyncio
async def test_execute_command_rejects_unknown_command():
    submit = AsyncMock(return_value="command:should-not-run")
    with patch.object(
        commands_router.CommandService, "submit_command_job", new=submit
    ):
        with pytest.raises(HTTPException) as ei:
            await commands_router.execute_command(
                CommandExecutionRequest(
                    command="generate_podcast",
                    app="open_notebook",
                    input={"episode_name": "x"},
                )
            )
    assert ei.value.status_code == 403
    assert "not allowed" in str(ei.value.detail).lower()
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_command_rejects_foreign_app():
    with pytest.raises(HTTPException) as ei:
        await commands_router.execute_command(
            CommandExecutionRequest(
                command="anything",
                app="other_app",
                input={},
            )
        )
    assert ei.value.status_code == 403


@pytest.mark.asyncio
async def test_execute_command_allows_allowlisted(monkeypatch):
    monkeypatch.setattr(
        commands_router,
        "PUBLIC_HTTP_COMMAND_ALLOWLIST",
        frozenset({("open_notebook", "rebuild_embeddings")}),
    )
    submit = AsyncMock(return_value="command:ok")
    with patch.object(
        commands_router.CommandService, "submit_command_job", new=submit
    ):
        resp = await commands_router.execute_command(
            CommandExecutionRequest(
                command="rebuild_embeddings",
                app="open_notebook",
                input={"mode": "all"},
            )
        )
    assert resp.job_id == "command:ok"
    assert resp.status == "submitted"
    submit.assert_awaited_once()


def test_default_allowlist_is_empty():
    assert commands_router.PUBLIC_HTTP_COMMAND_ALLOWLIST == frozenset()
