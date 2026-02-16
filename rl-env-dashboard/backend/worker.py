import sys
import os
import traceback
from datetime import datetime
import asyncio

import database
import docker_manager


# Note: run_rollout_sync() function removed - agents now run inside containers


async def spawn_rollout(
    rollout_id: str,
    task_id: str,
    model: str = "gemini-2.5-computer-use-preview-10-2025",
):
    """
    Spawn a new rollout by starting a containerized agent.
    The agent runs inside Docker and reports results back via API.
    """
    try:
        # Get task details
        task = await database.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        # Generate agent token for authentication
        agent_token = docker_manager.generate_agent_token()

        # Update status to provisioning
        await database.update_rollout(
            rollout_id, 
            status="provisioning",
            agent_token=agent_token
        )

        # Provision Docker environment (runs in executor to not block event loop)
        # The container will start immediately and run the agent
        loop = asyncio.get_event_loop()
        metabase_port, container_name = await loop.run_in_executor(
            None,
            docker_manager.provision_environment,
            rollout_id,
            task_id,
            task["task"],
            task.get("answer"),
            agent_token,
            model,
        )

        # Update rollout with container info
        await database.update_rollout(
            rollout_id,
            metabase_port=metabase_port,
            container_name=container_name,
        )

        print(
            f"⚙ Container started for rollout {rollout_id} on port {metabase_port}"
        )
        print(f"   Agent will report results to backend via API")

        # Note: Agent runs inside container and reports back via API endpoints
        # No need to wait here - the container is self-contained

    except Exception as e:
        # Capture detailed error information
        error_type = type(e).__name__
        error_msg = str(e)
        stack_trace = traceback.format_exc()
        detailed_error = f"{error_type}: {error_msg}"
        
        print(f"✗ Error spawning rollout {rollout_id}: {detailed_error}")
        print(f"Stack trace:\n{stack_trace}")

        # Update rollout with error (truncate if too long)
        db_error = detailed_error if len(detailed_error) < 500 else detailed_error[:497] + "..."
        
        await database.update_rollout(
            rollout_id,
            status="error",
            error=db_error,
            completed_at=datetime.utcnow().isoformat(),
        )


def shutdown():
    """Shutdown worker (no-op in containerized mode)."""
    print("Worker shutdown complete")
