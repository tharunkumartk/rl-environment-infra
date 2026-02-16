from fastapi import APIRouter, UploadFile, File, HTTPException, Query, BackgroundTasks, Header
from pydantic import BaseModel
from typing import Optional, List
import json
import uuid
from datetime import datetime

import database
import worker
import docker_manager

router = APIRouter(prefix="/api")


async def check_and_update_container_status(rollout: dict) -> dict:
    """
    Check if a rollout's container is still running and update status if it died unexpectedly.
    
    Args:
        rollout: The rollout dictionary from the database
        
    Returns:
        Updated rollout dictionary
    """
    # Only check containers that should be running
    if rollout["status"] in ["provisioning", "running"]:
        container_name = rollout.get("container_name")
        
        if container_name:
            # Check if container is actually running
            if not docker_manager.is_container_running(container_name):
                # Container died unexpectedly - update status to error
                error_msg = f"Container {container_name} stopped unexpectedly"
                await database.update_rollout(
                    rollout["id"],
                    status="error",
                    error=error_msg,
                    completed_at=datetime.utcnow().isoformat()
                )
                # Update the rollout dict to reflect the change
                rollout["status"] = "error"
                rollout["error"] = error_msg
                rollout["completed_at"] = datetime.utcnow().isoformat()
                
    return rollout

# Request/Response models


class RolloutCreate(BaseModel):
    task_id: str
    model: Optional[str] = "gemini-2.5-computer-use-preview-10-2025"
    attempts: Optional[int] = 1


class JobResponse(BaseModel):
    id: str
    task_id: str
    created_at: str
    rollout_count: int
    success_count: int
    completed_count: int


class RolloutResponse(BaseModel):
    id: str
    task_id: str
    job_id: Optional[str] = None
    status: str
    result: Optional[str] = None
    parsed_json: Optional[str] = None
    success: Optional[bool] = None
    error: Optional[str] = None
    log_path: Optional[str] = None
    metabase_port: Optional[int] = None
    created_at: str
    completed_at: Optional[str] = None


class TaskResponse(BaseModel):
    id: str
    task: str
    answer: Optional[str] = None
    created_at: str
    job_count: Optional[int] = 0
    rollout_count: Optional[int] = 0
    success_count: Optional[int] = 0
    completed_count: Optional[int] = 0


# Task endpoints


@router.post("/tasks/upload")
async def upload_tasks(file: UploadFile = File(...)):
    """Upload a tasks.json file and insert/update tasks in the database."""
    try:
        # Read and parse JSON file
        contents = await file.read()
        tasks_data = json.loads(contents)

        if not isinstance(tasks_data, list):
            raise HTTPException(
                status_code=400, detail="JSON must be an array of tasks"
            )

        # Upsert each task
        inserted_count = 0
        for task_obj in tasks_data:
            task_id = task_obj.get("id")
            task_text = task_obj.get("task")
            answer = task_obj.get("answer")

            if not task_id or not task_text:
                continue  # Skip invalid tasks

            await database.upsert_task(task_id, task_text, answer)
            inserted_count += 1

        return {
            "message": f"Successfully uploaded {inserted_count} tasks",
            "count": inserted_count,
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tasks", response_model=List[TaskResponse])
async def list_tasks():
    """List all tasks with rollout statistics."""
    tasks = await database.get_all_tasks()
    return tasks


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str):
    """Get a single task by ID."""
    task = await database.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Get job count
    jobs = await database.get_jobs(task_id)
    task["job_count"] = len(jobs)

    # Get rollout counts
    rollouts = await database.get_rollouts(task_id=task_id)
    
    # Check container status for each rollout
    updated_rollouts = []
    for rollout in rollouts:
        updated_rollout = await check_and_update_container_status(rollout)
        updated_rollouts.append(updated_rollout)
    
    task["rollout_count"] = len(updated_rollouts)
    task["completed_count"] = sum(1 for r in updated_rollouts if r["status"] in ["success", "failed", "error"])
    task["success_count"] = sum(1 for r in updated_rollouts if r["status"] == "success")

    return task


# Job endpoints


@router.get("/tasks/{task_id}/jobs", response_model=List[JobResponse])
async def list_jobs(task_id: str):
    """List all jobs for a task."""
    jobs = await database.get_jobs(task_id)
    return jobs


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    """Get a single job by ID."""
    job = await database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get rollout stats for this job
    rollouts = await database.get_rollouts(job_id=job_id)
    
    # Check container status for each rollout
    updated_rollouts = []
    for rollout in rollouts:
        updated_rollout = await check_and_update_container_status(rollout)
        updated_rollouts.append(updated_rollout)
    
    job["rollout_count"] = len(updated_rollouts)
    job["completed_count"] = sum(1 for r in updated_rollouts if r["status"] in ["success", "failed", "error"])
    job["success_count"] = sum(1 for r in updated_rollouts if r["status"] == "success")

    return job


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str):
    """Delete a job and all its associated rollouts."""
    job = await database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    # Get all rollouts for this job
    rollouts = await database.get_rollouts(job_id=job_id)
    
    # Teardown any running rollouts
    for rollout in rollouts:
        if rollout["status"] in ["pending", "provisioning", "running"]:
            try:
                docker_manager.teardown_environment(
                    rollout["id"],
                    rollout.get("container_name"),
                    rollout.get("metabase_port"),
                )
            except Exception as e:
                print(f"Error tearing down environment for rollout {rollout['id']}: {e}")

    # Delete from database (this also deletes associated rollouts)
    await database.delete_job(job_id)

    return {"message": "Job deleted successfully"}


# Rollout endpoints


@router.post("/rollouts", response_model=List[RolloutResponse])
async def create_rollout(
    rollout_data: RolloutCreate, background_tasks: BackgroundTasks
):
    """Spawn one or more rollouts for a task, grouped into a new job."""
    # Verify task exists
    task = await database.get_task(rollout_data.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Create a new job for this run
    job_id = str(uuid.uuid4())
    await database.create_job(job_id, rollout_data.task_id)

    created_rollouts = []
    attempts = max(1, rollout_data.attempts or 1)

    for _ in range(attempts):
        # Generate rollout ID
        rollout_id = str(uuid.uuid4())

        # Create rollout in database with job_id
        await database.create_rollout(
            rollout_id, rollout_data.task_id, job_id=job_id, status="pending"
        )

        # Spawn the rollout asynchronously in background (non-blocking)
        background_tasks.add_task(
            worker.spawn_rollout, rollout_id, rollout_data.task_id, rollout_data.model
        )

        # Get the created rollout
        rollout = await database.get_rollout(rollout_id)
        if rollout:
            created_rollouts.append(rollout)

    return created_rollouts


@router.get("/rollouts", response_model=List[RolloutResponse])
async def list_rollouts(
    task_id: Optional[str] = Query(None),
    job_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
):
    """List rollouts with optional filters."""
    rollouts = await database.get_rollouts(
        task_id=task_id, job_id=job_id, status=status
    )
    
    # Check container status for each rollout
    updated_rollouts = []
    for rollout in rollouts:
        updated_rollout = await check_and_update_container_status(rollout)
        updated_rollouts.append(updated_rollout)
    
    return updated_rollouts


@router.get("/rollouts/{rollout_id}", response_model=RolloutResponse)
async def get_rollout(rollout_id: str):
    """Get a single rollout by ID."""
    rollout = await database.get_rollout(rollout_id)
    if not rollout:
        raise HTTPException(status_code=404, detail="Rollout not found")
    
    # Check if container is still running and update status if needed
    rollout = await check_and_update_container_status(rollout)
    
    return rollout


@router.delete("/rollouts/{rollout_id}")
async def delete_rollout(rollout_id: str):
    """Cancel and delete a rollout."""
    rollout = await database.get_rollout(rollout_id)
    if not rollout:
        raise HTTPException(status_code=404, detail="Rollout not found")

    # If rollout is still running, try to teardown the environment
    if rollout["status"] in ["pending", "provisioning", "running"]:
        try:
            docker_manager.teardown_environment(
                rollout_id,
                rollout.get("container_name"),
                rollout.get("metabase_port"),
            )
        except Exception as e:
            print(f"Error tearing down environment: {e}")

    # Delete from database
    await database.delete_rollout(rollout_id)

    return {"message": "Rollout deleted successfully"}


# Compute statistics endpoint


class ComputeStats(BaseModel):
    total_rollouts: int
    pending: int
    provisioning: int
    running: int
    completed: int
    failed: int
    error: int
    success_count: int
    in_progress_rollouts: List[RolloutResponse]
    pending_rollouts: List[RolloutResponse]
    recent_completed_rollouts: List[RolloutResponse]
    recent_failed_rollouts: List[RolloutResponse]
    all_rollouts: List[RolloutResponse]


@router.get("/compute/stats", response_model=ComputeStats)
async def get_compute_stats(
    task_id: Optional[str] = Query(None, description="Filter by task ID"),
    status: Optional[str] = Query(None, description="Filter by status"),
):
    """Get current compute usage statistics with optional filters."""
    # Get all rollouts with optional filters
    all_rollouts = await database.get_rollouts(task_id=task_id, status=status)
    
    # Check container status for all rollouts
    updated_rollouts = []
    for rollout in all_rollouts:
        updated_rollout = await check_and_update_container_status(rollout)
        updated_rollouts.append(updated_rollout)
    
    all_rollouts = updated_rollouts
    
    # Count by status
    pending = sum(1 for r in all_rollouts if r["status"] == "pending")
    provisioning = sum(1 for r in all_rollouts if r["status"] == "provisioning")
    running = sum(1 for r in all_rollouts if r["status"] == "running")
    success = sum(1 for r in all_rollouts if r["status"] == "success")
    failed = sum(1 for r in all_rollouts if r["status"] == "failed")
    error = sum(1 for r in all_rollouts if r["status"] == "error")
    success_count = success  # Alias for backward compatibility
    
    # Get in-progress rollouts (provisioning + running)
    in_progress = [r for r in all_rollouts if r["status"] in ["provisioning", "running"]]
    pending_list = [r for r in all_rollouts if r["status"] == "pending"]
    
    # Get recent success and failed rollouts (last 20)
    success_rollouts = [r for r in all_rollouts if r["status"] == "success"]
    success_rollouts.sort(key=lambda x: x.get("completed_at") or x.get("created_at"), reverse=True)
    recent_success = success_rollouts[:20]
    
    failed_rollouts = [r for r in all_rollouts if r["status"] in ["failed", "error"]]
    failed_rollouts.sort(key=lambda x: x.get("completed_at") or x.get("created_at"), reverse=True)
    recent_failed = failed_rollouts[:20]
    
    return {
        "total_rollouts": len(all_rollouts),
        "pending": pending,
        "provisioning": provisioning,
        "running": running,
        "completed": success + failed + error,  # For backward compatibility
        "failed": failed,
        "error": error,
        "success_count": success_count,
        "in_progress_rollouts": in_progress,
        "pending_rollouts": pending_list,
        "recent_completed_rollouts": recent_success,  # Renamed but keeping key for backward compatibility
        "recent_failed_rollouts": recent_failed,
        "all_rollouts": all_rollouts[:50],  # Limit to 50 for performance
    }


# Agent API endpoints (called by containerized agents)


class AgentStatusUpdate(BaseModel):
    status: str


class AgentLogEntry(BaseModel):
    log_data: dict


class AgentResult(BaseModel):
    result: Optional[str] = None
    parsed_json: Optional[dict] = None
    success: bool
    error: Optional[str] = None


async def verify_agent_token(agent_token: str = Header(..., alias="X-Agent-Token")):
    """Verify agent token and return associated rollout."""
    rollout = await database.get_rollout_by_token(agent_token)
    if not rollout:
        raise HTTPException(status_code=401, detail="Invalid agent token")
    return rollout


@router.post("/agent/status")
async def agent_update_status(
    status_update: AgentStatusUpdate,
    agent_token: str = Header(..., alias="X-Agent-Token")
):
    """Agent reports status update."""
    rollout = await verify_agent_token(agent_token)
    
    await database.update_rollout(
        rollout["id"],
        status=status_update.status
    )
    
    return {"message": "Status updated", "rollout_id": rollout["id"]}


@router.post("/agent/log")
async def agent_send_log(
    log_entry: AgentLogEntry,
    agent_token: str = Header(..., alias="X-Agent-Token")
):
    """Agent sends log entry (live streaming)."""
    rollout = await verify_agent_token(agent_token)
    
    # Extract log data
    log_data = log_entry.log_data
    step_number = log_data.get("step_number")
    reasoning = log_data.get("reasoning")
    function_calls = log_data.get("function_calls")  # Should be JSON string or list
    screenshot_base64 = log_data.get("screenshot_base64")
    
    # Convert function_calls to JSON string if it's a list
    if isinstance(function_calls, list):
        function_calls = json.dumps(function_calls)
    
    # Store step log in database
    await database.create_step_log(
        rollout_id=rollout["id"],
        step_number=step_number,
        reasoning=reasoning,
        function_calls=function_calls,
        screenshot_base64=screenshot_base64
    )
    
    return {"message": "Log received", "rollout_id": rollout["id"], "step_number": step_number}


@router.post("/agent/result")
async def agent_post_result(
    result_data: AgentResult,
    agent_token: str = Header(..., alias="X-Agent-Token")
):
    """Agent posts final results."""
    rollout = await verify_agent_token(agent_token)
    
    # Determine final status based on success and error
    if result_data.error:
        final_status = "failed"
    elif result_data.success:
        final_status = "success"
    else:
        final_status = "failed"
    
    # Convert parsed_json to string for database storage
    parsed_json_str = json.dumps(result_data.parsed_json) if result_data.parsed_json else None
    
    await database.update_rollout(
        rollout["id"],
        status=final_status,
        result=result_data.result,
        parsed_json=parsed_json_str,
        success=result_data.success,
        error=result_data.error,
        completed_at=datetime.utcnow().isoformat()
    )
    
    return {"message": "Result recorded", "rollout_id": rollout["id"], "status": final_status}


# Step logs endpoints


class StepLogResponse(BaseModel):
    id: int
    rollout_id: str
    step_number: int
    timestamp: str
    reasoning: Optional[str] = None
    function_calls: Optional[str] = None
    screenshot_base64: Optional[str] = None


@router.get("/rollouts/{rollout_id}/logs", response_model=List[StepLogResponse])
async def get_rollout_logs(rollout_id: str, include_screenshots: bool = Query(True)):
    """Get all step logs for a rollout."""
    rollout = await database.get_rollout(rollout_id)
    if not rollout:
        raise HTTPException(status_code=404, detail="Rollout not found")
    
    logs = await database.get_step_logs(rollout_id)
    
    # Optionally exclude screenshots to reduce payload size
    if not include_screenshots:
        for log in logs:
            log["screenshot_base64"] = None
    
    return logs


@router.get("/rollouts/{rollout_id}/logs/latest", response_model=StepLogResponse)
async def get_latest_rollout_log(rollout_id: str, include_screenshot: bool = Query(True)):
    """Get the most recent step log for a rollout."""
    rollout = await database.get_rollout(rollout_id)
    if not rollout:
        raise HTTPException(status_code=404, detail="Rollout not found")
    
    log = await database.get_latest_step_log(rollout_id)
    if not log:
        raise HTTPException(status_code=404, detail="No logs found for this rollout")
    
    # Optionally exclude screenshot to reduce payload size
    if not include_screenshot:
        log["screenshot_base64"] = None
    
    return log
