"""Report Generation — deep module for one notebook report job (#1628).

Interface: :func:`run_report_generation` takes a request and returns a result.
The worker command is a thin adapter (heartbeat, I/O mapping, permanent errors).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from ai_prompter import Prompter
from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from open_notebook.domain.notebook import Note, Notebook
from open_notebook.exceptions import NotFoundError
from open_notebook.utils import clean_thinking_content
from open_notebook.utils.text_utils import extract_text_content

_SOURCE_TITLE_RE = re.compile(r"^## Source:\s*(.+?)\s*$", re.MULTILINE)

_HUMAN_MESSAGE = (
    "Write the markdown report now. Evidence is the budget: "
    "every body section must rest on concrete specifics from "
    "the context; when unused evidence runs thin, write fewer "
    "denser sections and stop. Follow the output contract "
    "exactly (including the final ## Sources section)."
)


@dataclass(frozen=True)
class ReportGenerationRequest:
    """Everything needed to run one report job."""

    notebook_id: str
    instructions: Optional[str] = None
    model_id: Optional[str] = None
    command_id: Optional[str] = None


@dataclass(frozen=True)
class ReportGenerationResult:
    """Successful report publication as an AI note."""

    note_id: str
    processing_time: float


def source_titles_from_context(context: str) -> List[str]:
    """Titles of source blocks actually present in the packed for_report string."""
    return [m.group(1).strip() for m in _SOURCE_TITLE_RE.finditer(context or "")]


async def run_report_generation(
    request: ReportGenerationRequest,
) -> ReportGenerationResult:
    """Assemble notebook context, generate markdown, save as AI note in notebook."""
    from open_notebook.ai.runtime import chat_langchain
    from open_notebook.context.assembly import for_report
    from open_notebook.jobs import (
        JobCancelledError,
        command_was_cancelled,
        long_job_pulse,
    )

    start_time = time.time()

    if not request.notebook_id or not str(request.notebook_id).strip():
        raise ValueError("notebook_id is required")

    try:
        notebook = await Notebook.get(request.notebook_id)
    except NotFoundError as e:
        raise ValueError(f"Notebook '{request.notebook_id}' not found") from e
    if not notebook:
        raise ValueError(f"Notebook '{request.notebook_id}' not found")

    notebook_name = (notebook.name or "Untitled notebook").strip()
    instructions = (request.instructions or "").strip() or None

    async with long_job_pulse(request.command_id):
        context = await for_report(notebook)
        if not (context or "").strip():
            raise ValueError(
                "Notebook context is empty; add sources or notes before "
                "generating a report"
            )

        source_titles = source_titles_from_context(context)
        system_prompt = Prompter(prompt_template="report/generate").render(
            data={
                "notebook_name": notebook_name,
                "context": context,
                "source_titles": source_titles,
                "instructions": instructions,
            }
        )

        payload = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=_HUMAN_MESSAGE),
        ]
        chain = await chat_langchain(
            str(payload),
            request.model_id,
            "chat",
        )
        response = await chain.ainvoke(payload)
        response_content = extract_text_content(response.content)
        markdown = clean_thinking_content(response_content).strip()

        if not markdown:
            raise ValueError("Model returned empty report content")

    # The pulse context raises on cancellation before publication. Check once
    # more at the stage boundary so cancellation during a short final LLM
    # window cannot publish an orphan note or enter the retry loop.
    if request.command_id and await command_was_cancelled(request.command_id):
        raise JobCancelledError(request.command_id)

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    title = f"Report: {notebook_name} {date_str}"
    note = Note(
        title=title,
        content=markdown,
        note_type="ai",
    )
    # Note.save() auto-submits embed_note — intentional.
    await note.save()
    if notebook.id:
        await note.add_to_notebook(str(notebook.id))

    processing_time = time.time() - start_time
    note_id = str(note.id) if note.id else ""
    logger.info(
        f"Report generated for notebook {request.notebook_id} "
        f"as note {note_id} in {processing_time:.2f}s"
    )
    return ReportGenerationResult(
        note_id=note_id,
        processing_time=processing_time,
    )
