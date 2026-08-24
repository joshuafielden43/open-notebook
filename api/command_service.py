from typing import Any, Dict, List, Optional

from loguru import logger
from surreal_commands import get_command_status, submit_command

from open_notebook.database.repository import repo_query

# Statuses that mean "still in the queue or running" for ambient UI.
ACTIVE_COMMAND_STATUSES = (
    "new",
    "pending",
    "submitted",
    "queued",
    "running",
    "processing",
)


class CommandService:
    """Generic service layer for command operations"""

    @staticmethod
    async def list_active_jobs(limit: int = 50) -> List[Dict[str, Any]]:
        """List command rows that are not yet terminal (for ambient activity UI)."""
        limit = max(1, min(int(limit), 100))
        try:
            # Bind statuses as a list; ORDER BY id keeps the query valid when
            # created/updated are NONE on fresh surreal-commands rows.
            result = await repo_query(
                f"""
                SELECT id, name, app, status, error_message, created, updated
                FROM command
                WHERE status IN $statuses
                ORDER BY id DESC
                LIMIT {limit}
                """,
                {"statuses": list(ACTIVE_COMMAND_STATUSES)},
            )
            jobs: List[Dict[str, Any]] = []
            for row in result or []:
                jobs.append(
                    {
                        "job_id": str(row.get("id") or ""),
                        "name": row.get("name") or "",
                        "app": row.get("app") or "",
                        "status": str(row.get("status") or "unknown").lower(),
                        "error_message": row.get("error_message") or None,
                        "created": str(row["created"])
                        if row.get("created") is not None
                        else None,
                        "updated": str(row["updated"])
                        if row.get("updated") is not None
                        else None,
                    }
                )
            return jobs
        except Exception as e:
            logger.error(f"Failed to list active command jobs: {e}")
            raise

    @staticmethod
    async def submit_command_job(
        module_name: str,  # Actually app_name for surreal-commands
        command_name: str,
        command_args: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Submit a generic command job for background processing"""
        try:
            # Ensure all command modules are registered before submit_command
            # validates against the local registry (#1631).
            from commands.register import ensure_commands_registered

            ensure_commands_registered()

            # surreal-commands expects: submit_command(app_name, command_name, args)
            cmd_id = submit_command(
                module_name,  # This is actually the app name (e.g., "open_notebook")
                command_name,  # Command name (e.g., "generate_podcast")
                command_args,  # Input data
            )
            # Convert RecordID to string if needed
            if not cmd_id:
                raise ValueError("Failed to get cmd_id from submit_command")
            cmd_id_str = str(cmd_id)
            logger.info(
                f"Submitted command job: {cmd_id_str} for {module_name}.{command_name}"
            )
            return cmd_id_str

        except Exception as e:
            logger.error(f"Failed to submit command job: {e}")
            raise

    @staticmethod
    async def get_command_status(job_id: str) -> Dict[str, Any]:
        """Get status of any command job"""
        try:
            status = await get_command_status(job_id)
            return {
                "job_id": job_id,
                "status": status.status if status else "unknown",
                "result": status.result if status else None,
                "error_message": getattr(status, "error_message", None)
                if status
                else None,
                "created": str(status.created)
                if status and hasattr(status, "created") and status.created
                else None,
                "updated": str(status.updated)
                if status and hasattr(status, "updated") and status.updated
                else None,
                "progress": getattr(status, "progress", None) if status else None,
            }
        except Exception as e:
            logger.error(f"Failed to get command status: {e}")
            raise

    @staticmethod
    async def cancel_command_job(job_id: str) -> bool:
        """Cancel a running/pending command by marking it failed in SurrealDB.

        surreal-commands has no cooperative cancel; forcing status=failed lets
        operators retry instead of leaving zombie running rows forever.
        """
        try:
            from open_notebook.jobs import mark_command_failed

            logger.info(f"Attempting to cancel job: {job_id}")
            return await mark_command_failed(
                job_id, error_message="Job cancelled by operator"
            )
        except Exception as e:
            logger.error(f"Failed to cancel command job: {e}")
            raise
