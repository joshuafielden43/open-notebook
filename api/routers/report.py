"""Typed report-generation endpoint (ADR-004: not via raw POST /commands/jobs)."""

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.report_service import submit_report_job

router = APIRouter()


class ReportGenerateRequest(BaseModel):
    """Optional controls for a notebook report job."""

    instructions: Optional[str] = Field(
        default=None,
        description="Optional free-text guidance appended to the report prompt",
    )
    model_id: Optional[str] = Field(
        default=None,
        description="Optional model registry id; default chat model when omitted",
    )


class ReportGenerateResponse(BaseModel):
    """Accepted report job identifier."""

    job_id: str


@router.post(
    "/notebooks/{notebook_id}/report",
    response_model=ReportGenerateResponse,
)
async def generate_notebook_report(
    notebook_id: str,
    body: Optional[ReportGenerateRequest] = None,
) -> ReportGenerateResponse:
    """Enqueue markdown report generation for a notebook; return job id."""
    body = body or ReportGenerateRequest()
    job_id = await submit_report_job(
        notebook_id,
        instructions=body.instructions,
        model_id=body.model_id,
    )
    return ReportGenerateResponse(job_id=job_id)
