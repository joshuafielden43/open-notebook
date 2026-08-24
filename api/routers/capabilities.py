"""Read-only runtime capability reporting."""

from fastapi import APIRouter

from api.models import CapabilitiesResponse
from open_notebook.jobs import get_worker_status
from open_notebook.utils.runtime_capabilities import (
    crawl4ai_local_ready,
    crawl4ai_remote_configured,
    docling_available,
)

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


@router.get("", response_model=CapabilitiesResponse)
async def get_capabilities():
    """Report actual extraction and worker capabilities without mutating them."""
    crawl4ai_remote = crawl4ai_remote_configured()
    crawl4ai_local = crawl4ai_local_ready()
    worker = await get_worker_status()
    return CapabilitiesResponse(
        docling_available=docling_available(),
        crawl4ai_available=crawl4ai_local or crawl4ai_remote,
        crawl4ai_remote_configured=crawl4ai_remote,
        worker_likely_ready=bool(worker.get("worker_likely_ready", True)),
        pending_command_count=int(worker.get("pending_command_count") or 0),
        running_command_count=int(worker.get("running_command_count") or 0),
    )
