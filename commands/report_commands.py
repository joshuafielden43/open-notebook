"""Worker command adapter for report generation.

Job lifecycle and generation live in
:mod:`open_notebook.reports.generation`. This module only maps command I/O
onto that interface.

Do not add ``from __future__ import annotations`` here. surreal-commands
validates submit args via LangChain's ``RunnableLambda.get_input_schema()``,
which builds a Pydantic model from the function annotations. With postponed
evaluation those annotations stay as ForwardRefs and submit raises
PydanticUserError (class-not-fully-defined) → API 500.
"""

from typing import Optional

from loguru import logger
from surreal_commands import CommandInput, CommandOutput, command

from open_notebook.exceptions import ConfigurationError
from open_notebook.jobs import JobCancelledError
from open_notebook.reports.generation import (
    ReportGenerationRequest,
    run_report_generation,
)

__all__ = [
    "ReportGenerationInput",
    "ReportGenerationOutput",
    "generate_report_command",
]

REPORT_RETRY_CONFIG = {
    "max_attempts": 5,
    "wait_strategy": "exponential_jitter",
    "wait_min": 1,
    "wait_max": 60,
    "stop_on": [ValueError],
    "retry_log_level": "debug",
}


class ReportGenerationInput(CommandInput):
    """Serializable inputs for the report worker command."""

    notebook_id: str
    instructions: Optional[str] = None
    model_id: Optional[str] = None


class ReportGenerationOutput(CommandOutput):
    """Successful report command result."""

    success: bool
    note_id: Optional[str] = None
    processing_time: float
    error_message: Optional[str] = None


@command("generate_report", app="open_notebook", retry=REPORT_RETRY_CONFIG)
async def generate_report_command(
    input_data: ReportGenerationInput,
) -> ReportGenerationOutput:
    """Run one report job via the Report Generation module."""
    command_id = (
        str(input_data.execution_context.command_id)
        if input_data.execution_context
        else None
    )
    try:
        result = await run_report_generation(
            ReportGenerationRequest(
                notebook_id=input_data.notebook_id,
                instructions=input_data.instructions,
                model_id=input_data.model_id,
                command_id=command_id,
            )
        )
        return ReportGenerationOutput(
            success=True,
            note_id=result.note_id or None,
            processing_time=result.processing_time,
        )
    except JobCancelledError as e:
        logger.warning(f"Report generation cancelled: {e}")
        raise ValueError(str(e)) from e
    except ValueError as e:
        logger.error(f"Report generation failed (permanent): {e}")
        raise
    except ConfigurationError as e:
        logger.error(f"Report generation failed (configuration): {e}")
        raise ValueError(str(e)) from e
    except Exception as e:
        logger.error(f"Report generation failed: {e}")
        logger.exception(e)
        raise
