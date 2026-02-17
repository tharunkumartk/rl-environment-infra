import sys
import os
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional
import asyncio

# Configure logger
logger = logging.getLogger(__name__)

# Add computer-use-preview to path
COMPUTER_USE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "computer-use-preview"
)
sys.path.insert(0, COMPUTER_USE_PATH)

from agent import BrowserAgent
from computers import PlaywrightComputer
from task_verifier import verify_task_output
import database
import docker_manager
from rollout_logger import RolloutLogger

# Thread pool for running rollouts
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "4"))
executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

PLAYWRIGHT_SCREEN_SIZE = (1440, 900)
METABASE_ACCESS_INFO = "Login to the account with Login info: daksh@deeptune.com, Daksh@123. Please only output the JSON output in the final response/step. "


def run_rollout_sync(
    rollout_id: str,
    task_id: str,
    task_text: str,
    expected_answer: Optional[str],
    metabase_port: int,
    pg_container: str,
    mb_container: str,
    model: str = "gemini-2.5-computer-use-preview-10-2025",
):
    """
    Synchronous function to run a rollout (executed in thread pool).
    This is a blocking function that runs the full agent loop.
    """
    log_path = None
    rollout_logger = None

    try:
        # Update status to running
        asyncio.run(database.update_rollout(rollout_id, status="running"))

        # Enable headless mode
        os.environ["PLAYWRIGHT_HEADLESS"] = "1"

        # Construct full query with Metabase access info
        full_query = METABASE_ACCESS_INFO + task_text

        # Initialize Playwright Computer (no video recording)
        initial_url = f"http://localhost:{metabase_port}/"
        env = PlaywrightComputer(
            screen_size=PLAYWRIGHT_SCREEN_SIZE,
            initial_url=initial_url,
            highlight_mouse=False,
            record_video_dir=None,  # Disable video recording
        )

        # Initialize JSON logger
        rollout_logger = RolloutLogger(rollout_id, task_id)
        rollout_logger.start()

        # Run the agent with JSON logging (suppress stdout/stderr)
        result = None

        # Update rollout with log path early so frontend can start polling
        # Path should be the full API path (mounted at /static/logs/)
        filename = os.path.relpath(
            rollout_logger.get_log_path(), os.path.join(COMPUTER_USE_PATH, "task_logs")
        )
        log_path = f"/static/logs/{filename}"
        asyncio.run(
            database.update_rollout(
                rollout_id,
                log_path=log_path,
            )
        )

        with env as browser_computer:
            agent = BrowserAgent(
                browser_computer=browser_computer,
                query=full_query,
                model_name=model,
                verbose=False,  # Disable verbose output
                logger=rollout_logger,
            )
            result = agent.agent_loop()

        # Get the log path (full API path)
        filename = os.path.relpath(
            rollout_logger.get_log_path(), os.path.join(COMPUTER_USE_PATH, "task_logs")
        )
        log_path = f"/static/logs/{filename}"

        # Verify task output
        parsed_json = None
        success = False
        if result:
            parsed_json, success = verify_task_output(
                task_text, result, expected_answer
            )
        
        # Log the final output step with reasoning and parsed JSON
        rollout_logger.log_final_output(
            final_reasoning=result,
            parsed_output=parsed_json
        )

        # Complete logging
        rollout_logger.complete()

        # Convert parsed_json to string for database storage
        parsed_json_str = json.dumps(parsed_json) if parsed_json else None

        # Update rollout with results (no video_path)
        # Use "success" or "failed" status based on verification
        final_status = "success" if success else "failed"
        asyncio.run(
            database.update_rollout(
                rollout_id,
                status=final_status,
                result=result,
                parsed_json=parsed_json_str,
                success=success,
                log_path=log_path,
                completed_at=datetime.utcnow().isoformat(),
            )
        )

        # Log to server logs
        logger.info(f"✓ Rollout {rollout_id} completed successfully")

    except Exception as e:
        error_msg = str(e)
        # Log to server logs
        logger.error(f"✗ Error in rollout {rollout_id}: {error_msg}")

        # Complete logging with error if rollout_logger exists
        if rollout_logger:
            rollout_logger.complete(error=error_msg)
            if not log_path:
                filename = os.path.relpath(
                    rollout_logger.get_log_path(), os.path.join(COMPUTER_USE_PATH, "task_logs")
                )
                log_path = f"/static/logs/{filename}"

        # Update rollout with error status (system error, not task failure)
        asyncio.run(
            database.update_rollout(
                rollout_id,
                status="error",
                error=error_msg,
                log_path=log_path,
                completed_at=datetime.utcnow().isoformat(),
            )
        )

    finally:
        # Always teardown the environment
        try:
            docker_manager.teardown_environment(
                rollout_id, pg_container, mb_container, metabase_port
            )
        except Exception as e:
            logger.error(f"Error tearing down environment for {rollout_id}: {e}")


async def spawn_rollout(
    rollout_id: str,
    task_id: str,
    model: str = "gemini-2.5-computer-use-preview-10-2025",
):
    """
    Spawn a new rollout (async function called from FastAPI).
    This provisions the environment and submits the work to the thread pool.
    """
    try:
        # Get task details
        task = await database.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        # Update status to provisioning
        await database.update_rollout(rollout_id, status="provisioning")

        # Provision Docker environment (this is blocking but necessary)
        # We run it in the default executor to not block the event loop
        loop = asyncio.get_event_loop()
        metabase_port, pg_container, mb_container = await loop.run_in_executor(
            None, docker_manager.provision_environment, rollout_id
        )

        # Update rollout with container info
        await database.update_rollout(
            rollout_id,
            metabase_port=metabase_port,
            container_pg=pg_container,
            container_mb=mb_container,
        )

        logger.info(
            f"⚙ Environment provisioned for rollout {rollout_id} on port {metabase_port}"
        )

        # Submit the actual rollout work to the thread pool
        executor.submit(
            run_rollout_sync,
            rollout_id,
            task_id,
            task["task"],
            task.get("answer"),
            metabase_port,
            pg_container,
            mb_container,
            model,
        )

    except Exception as e:
        error_msg = str(e)
        logger.error(f"✗ Error spawning rollout {rollout_id}: {error_msg}")

        # Update rollout with error status (provisioning/system error)
        await database.update_rollout(
            rollout_id,
            status="error",
            error=error_msg,
            completed_at=datetime.utcnow().isoformat(),
        )


def shutdown():
    """Shutdown the worker pool."""
    logger.info("Shutting down worker pool...")
    executor.shutdown(wait=True)
    logger.info("Worker pool shut down")
