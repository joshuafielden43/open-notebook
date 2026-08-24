"""Report generation v1 — command + typed notebook route."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from commands.report_commands import (
    REPORT_RETRY_CONFIG,
    ReportGenerationInput,
    generate_report_command,
)
from open_notebook.exceptions import NotFoundError
from open_notebook.jobs import JobCancelledError

SAMPLE_MARKDOWN = """# Research Brief on Widget Systems

## Overview

Widgets are central to the notebook material.

## Key findings

The sources describe three widget types and their trade-offs.

## Sources

- Widget Spec 2024
- Field Notes
"""


def _make_input(
    notebook_id: str = "notebook:abc",
    instructions: str | None = "Focus on risks",
    model_id: str | None = None,
) -> ReportGenerationInput:
    return ReportGenerationInput(
        notebook_id=notebook_id,
        instructions=instructions,
        model_id=model_id,
    )


@asynccontextmanager
async def _noop_pulse(_command_id=None):
    yield


@pytest.mark.asyncio
async def test_generate_report_saves_ai_note_with_title_and_sources():
    """Command generates markdown (# title, ## Sources) and saves an AI note."""
    notebook = MagicMock()
    notebook.id = "notebook:abc"
    notebook.name = "Widgets"

    context = (
        "## Source: Widget Spec 2024\n\nSpec body.\n\n"
        "## Source: Field Notes\n\nNotes body."
    )

    mock_chain = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = SAMPLE_MARKDOWN
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)

    saved_notes: list = []

    class FakeNote:
        def __init__(self, title=None, content=None, note_type=None):
            self.title = title
            self.content = content
            self.note_type = note_type
            self.id = "note:report1"

        async def save(self):
            saved_notes.append(self)
            return "command:embed1"

        async def add_to_notebook(self, notebook_id: str):
            self.notebook_id = notebook_id

    with (
        patch(
            "open_notebook.domain.notebook.Notebook.get",
            new=AsyncMock(return_value=notebook),
        ),
        patch(
            "open_notebook.context.assembly.for_report",
            new=AsyncMock(return_value=context),
        ) as for_report_mock,
        patch(
            "open_notebook.ai.runtime.chat_langchain",
            new=AsyncMock(return_value=mock_chain),
        ),
        patch("open_notebook.jobs.long_job_pulse", _noop_pulse),
        patch("open_notebook.reports.generation.Note", FakeNote),
    ):
        result = await generate_report_command(_make_input())

    assert result.success is True
    assert result.note_id == "note:report1"
    assert len(saved_notes) == 1
    note = saved_notes[0]
    assert note.note_type == "ai"
    assert note.title is not None
    assert note.title.startswith("Report: Widgets ")
    assert note.content is not None
    assert "# Research Brief on Widget Systems" in note.content
    assert "## Sources" in note.content
    assert "Widget Spec 2024" in note.content
    for_report_mock.assert_awaited_once()
    await_args = for_report_mock.await_args
    assert await_args is not None
    assert await_args.args[0] is notebook


@pytest.mark.asyncio
async def test_generate_report_calls_for_report_not_for_podcast():
    notebook = MagicMock()
    notebook.id = "notebook:xyz"
    notebook.name = "N"

    mock_chain = AsyncMock()
    mock_response = MagicMock()
    mock_response.content = SAMPLE_MARKDOWN
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)

    class FakeNote:
        def __init__(self, **kwargs):
            self.id = "note:1"
            self.__dict__.update(kwargs)

        async def save(self):
            return None

        async def add_to_notebook(self, _nid):
            return None

    with (
        patch(
            "open_notebook.domain.notebook.Notebook.get",
            new=AsyncMock(return_value=notebook),
        ),
        patch(
            "open_notebook.context.assembly.for_report",
            new=AsyncMock(return_value="## Source: A\n\ntext"),
        ) as for_report_mock,
        patch(
            "open_notebook.context.assembly.for_podcast",
            new=AsyncMock(return_value="SHOULD NOT BE USED"),
        ) as for_podcast_mock,
        patch(
            "open_notebook.ai.runtime.chat_langchain",
            new=AsyncMock(return_value=mock_chain),
        ),
        patch("open_notebook.jobs.long_job_pulse", _noop_pulse),
        patch("open_notebook.reports.generation.Note", FakeNote),
    ):
        await generate_report_command(_make_input(notebook_id="notebook:xyz"))

    for_report_mock.assert_awaited_once()
    for_podcast_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_report_instructions_reach_prompt():
    """User instructions are rendered into the system prompt (verbatim path)."""
    notebook = MagicMock()
    notebook.id = "notebook:abc"
    notebook.name = "Widgets"

    captured_payloads: list = []

    mock_chain = AsyncMock()

    async def capture_ainvoke(payload):
        captured_payloads.append(payload)
        resp = MagicMock()
        resp.content = SAMPLE_MARKDOWN
        return resp

    mock_chain.ainvoke = capture_ainvoke

    class FakeNote:
        def __init__(self, **kwargs):
            self.id = "note:1"
            self.__dict__.update(kwargs)

        async def save(self):
            return None

        async def add_to_notebook(self, _nid):
            return None

    secret = "UNIQUE_INSTRUCTION_TOKEN_xyzzy"
    with (
        patch(
            "open_notebook.domain.notebook.Notebook.get",
            new=AsyncMock(return_value=notebook),
        ),
        patch(
            "open_notebook.context.assembly.for_report",
            new=AsyncMock(return_value="## Source: Only\n\nbody"),
        ),
        patch(
            "open_notebook.ai.runtime.chat_langchain",
            new=AsyncMock(return_value=mock_chain),
        ),
        patch("open_notebook.jobs.long_job_pulse", _noop_pulse),
        patch("open_notebook.reports.generation.Note", FakeNote),
    ):
        await generate_report_command(
            _make_input(instructions=secret),
        )

    assert captured_payloads, "expected LLM payload"
    system_msg = captured_payloads[0][0]
    assert secret in system_msg.content
    assert "Additional instructions from the user" in system_msg.content


@pytest.fixture
def client():
    from api.main import app

    return TestClient(app)


def test_report_retry_stops_on_permanent_errors() -> None:
    """Cancellation and configuration failures are normalized to ValueError."""
    assert REPORT_RETRY_CONFIG["stop_on"] == [ValueError]


@pytest.mark.parametrize(
    "bad_id",
    [
        "[object Object]",
        "not-a-record",
        "notebook:",
        "source:xyz",
    ],
)
def test_report_router_400_on_malformed_notebook_id(bad_id, client):
    response = client.post(f"/api/notebooks/{bad_id}/report", json={})
    assert response.status_code == 400
    assert "notebook_id" in response.json()["detail"]


def test_report_router_404_on_missing_notebook(client):
    with patch(
        "api.report_service.Notebook.get",
        new=AsyncMock(side_effect=NotFoundError("Notebook not found")),
    ):
        response = client.post("/api/notebooks/notebook:missing/report", json={})
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_report_router_200_returns_job_id(client):
    notebook = MagicMock()
    notebook.id = "notebook:abc"
    notebook.name = "Widgets"

    with (
        patch(
            "api.report_service.Notebook.get",
            new=AsyncMock(return_value=notebook),
        ),
        patch(
            "api.report_service.CommandService.submit_command_job",
            new=AsyncMock(return_value="command:job99"),
        ) as submit,
    ):
        response = client.post(
            "/api/notebooks/notebook:abc/report",
            json={"instructions": "Be concise", "model_id": "model:chat1"},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == "command:job99"
    submit.assert_awaited_once()
    await_args = submit.await_args
    assert await_args is not None
    args = await_args.args
    assert args[0] == "open_notebook"
    assert args[1] == "generate_report"
    assert args[2]["notebook_id"] == "notebook:abc"
    assert args[2]["instructions"] == "Be concise"
    assert args[2]["model_id"] == "model:chat1"


def test_report_router_forwards_instructions_to_job(client):
    notebook = MagicMock()
    notebook.id = "notebook:n1"
    notebook.name = "N"

    with (
        patch(
            "api.report_service.Notebook.get",
            new=AsyncMock(return_value=notebook),
        ),
        patch(
            "api.report_service.CommandService.submit_command_job",
            new=AsyncMock(return_value="command:1"),
        ) as submit,
    ):
        response = client.post(
            "/api/notebooks/notebook:n1/report",
            json={"instructions": "List every risk"},
        )

    assert response.status_code == 200
    await_args = submit.await_args
    assert await_args is not None
    assert await_args.args[2]["instructions"] == "List every risk"


@pytest.mark.asyncio
async def test_cancelled_report_does_not_publish_note() -> None:
    """Cancellation observed after generation aborts before note publication."""
    notebook = MagicMock(id="notebook:cancel", name="Cancelled")
    mock_chain = AsyncMock()
    mock_response = MagicMock(content=SAMPLE_MARKDOWN)
    mock_chain.ainvoke = AsyncMock(return_value=mock_response)
    saved_notes: list[object] = []

    class FakeNote:
        """Record any attempted note publication."""

        def __init__(self, **kwargs: object) -> None:
            self.id = "note:must-not-exist"
            self.__dict__.update(kwargs)

        async def save(self) -> None:
            saved_notes.append(self)

        async def add_to_notebook(self, _notebook_id: str) -> None:
            return None

    @asynccontextmanager
    async def cancelled_on_exit(command_id: str | None = None):
        yield
        raise JobCancelledError(command_id or "command:cancel")

    report_input = _make_input(notebook_id="notebook:cancel")
    report_input.execution_context = MagicMock(command_id="command:cancel")

    with (
        patch(
            "open_notebook.domain.notebook.Notebook.get",
            new=AsyncMock(return_value=notebook),
        ),
        patch(
            "open_notebook.context.assembly.for_report",
            new=AsyncMock(return_value="## Source: A\n\ntext"),
        ),
        patch(
            "open_notebook.ai.runtime.chat_langchain",
            new=AsyncMock(return_value=mock_chain),
        ),
        patch("open_notebook.jobs.long_job_pulse", cancelled_on_exit),
        patch("open_notebook.reports.generation.Note", FakeNote),
    ):
        with pytest.raises(ValueError, match="cancelled"):
            await generate_report_command(report_input)

    assert saved_notes == []
