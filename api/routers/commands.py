from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field
from surreal_commands import registry

from api.command_service import CommandService
from open_notebook.exceptions import OpenNotebookError

router = APIRouter()

# HTTP generic submit allowlist (#1606). Typed product routers (podcasts,
# sources, embeddings, transformations) validate fail-closed and submit via
# CommandService / submit_command internally. The product UI never POSTs here
# (only GET list/status). An empty allowlist fails closed so shared-password
# auth cannot enqueue arbitrary command+input. Extend deliberately if a
# public RPC client is introduced; do not mirror the full registry.
PUBLIC_HTTP_COMMAND_ALLOWLIST: FrozenSet[Tuple[str, str]] = frozenset()


class CommandExecutionRequest(BaseModel):
    command: str = Field(
        ..., description="Command function name (e.g., 'generate_podcast')"
    )
    app: str = Field(..., description="Application name (e.g., 'open_notebook')")
    input: Dict[str, Any] = Field(..., description="Arguments to pass to the command")


class CommandJobResponse(BaseModel):
    job_id: str
    status: str
    message: str


class CommandJobStatusResponse(BaseModel):
    job_id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    created: Optional[str] = None
    updated: Optional[str] = None
    progress: Optional[Dict[str, Any]] = None


class ActiveCommandJobResponse(BaseModel):
    job_id: str
    name: str = ""
    app: str = ""
    status: str
    error_message: Optional[str] = None
    created: Optional[str] = None
    updated: Optional[str] = None


@router.get("/commands/jobs", response_model=List[ActiveCommandJobResponse])
async def list_active_command_jobs(
    limit: int = Query(50, ge=1, le=100),
):
    """List non-terminal command jobs for the ambient activity indicator (#1626)."""
    try:
        jobs = await CommandService.list_active_jobs(limit=limit)
        return [ActiveCommandJobResponse(**job) for job in jobs]
    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error listing active command jobs: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to list active command jobs"
        )


@router.post("/commands/jobs", response_model=CommandJobResponse)
async def execute_command(request: CommandExecutionRequest):
    """Submit a command for background processing (allowlisted only).

    Product features must use typed routers that validate inputs. This
    generic RPC is locked to ``PUBLIC_HTTP_COMMAND_ALLOWLIST`` (#1606).
    GET status/list remain open for polling submitted work.
    """
    key = (request.app.strip(), request.command.strip())
    if key not in PUBLIC_HTTP_COMMAND_ALLOWLIST:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Command '{request.app}.{request.command}' is not allowed "
                f"via POST /commands/jobs. Use the typed API for this "
                f"operation (podcasts, sources, embeddings)."
            ),
        )

    try:
        job_id = await CommandService.submit_command_job(
            module_name=request.app,
            command_name=request.command,
            command_args=request.input,
        )

        return CommandJobResponse(
            job_id=job_id,
            status="submitted",
            message=f"Command '{request.command}' submitted successfully",
        )

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error submitting command: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to submit command"
        )


@router.get("/commands/jobs/{job_id}", response_model=CommandJobStatusResponse)
async def get_command_job_status(job_id: str):
    """Get the status of a specific command job"""
    try:
        status_data = await CommandService.get_command_status(job_id)
        return CommandJobStatusResponse(**status_data)

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error fetching job status: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to fetch job status"
        )


@router.delete("/commands/jobs/{job_id}")
async def cancel_command_job(job_id: str):
    """Cancel a running command job"""
    try:
        success = await CommandService.cancel_command_job(job_id)
        return {"job_id": job_id, "cancelled": success}

    except HTTPException:
        raise
    except OpenNotebookError:
        raise
    except Exception as e:
        logger.error(f"Error cancelling command job: {str(e)}")
        raise HTTPException(
            status_code=500, detail="Failed to cancel command job"
        )


@router.get("/commands/registry/debug")
async def debug_registry():
    """Debug endpoint to see what commands are registered"""
    try:
        # Get all registered commands
        all_items = registry.get_all_commands()

        # Create JSON-serializable data
        command_items = []
        for item in all_items:
            try:
                command_items.append(
                    {
                        "app_id": item.app_id,
                        "name": item.name,
                        "full_id": f"{item.app_id}.{item.name}",
                    }
                )
            except Exception as item_error:
                logger.error(f"Error processing item: {item_error}")

        # Get the basic command structure
        try:
            commands_dict: dict[str, list[str]] = {}
            for item in all_items:
                if item.app_id not in commands_dict:
                    commands_dict[item.app_id] = []
                commands_dict[item.app_id].append(item.name)
        except Exception:
            commands_dict = {}

        return {
            "total_commands": len(all_items),
            "commands_by_app": commands_dict,
            "command_items": command_items,
        }

    except Exception as e:
        logger.error(f"Error debugging registry: {str(e)}")
        return {
            "error": str(e),
            "total_commands": 0,
            "commands_by_app": {},
            "command_items": [],
        }
