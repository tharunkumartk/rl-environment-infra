from fastapi import APIRouter, UploadFile, File, HTTPException, Query, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import json
import uuid

import database
import worker
import docker_manager

router = APIRouter(prefix="/api")

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
    task["rollout_count"] = len(rollouts)
    task["completed_count"] = sum(1 for r in rollouts if r["status"] == "completed")
    task["success_count"] = sum(
        1 for r in rollouts if r["status"] == "completed" and r["success"]
    )

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
    job["rollout_count"] = len(rollouts)
    job["completed_count"] = sum(1 for r in rollouts if r["status"] == "completed")
    job["success_count"] = sum(
        1 for r in rollouts if r["status"] == "completed" and r["success"]
    )

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
                    rollout.get("container_pg"),
                    rollout.get("container_mb"),
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
    return rollouts


@router.get("/rollouts/{rollout_id}", response_model=RolloutResponse)
async def get_rollout(rollout_id: str):
    """Get a single rollout by ID."""
    rollout = await database.get_rollout(rollout_id)
    if not rollout:
        raise HTTPException(status_code=404, detail="Rollout not found")
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
                rollout.get("container_pg"),
                rollout.get("container_mb"),
                rollout.get("metabase_port"),
            )
        except Exception as e:
            print(f"Error tearing down environment: {e}")

    # Delete from database
    await database.delete_rollout(rollout_id)

    return {"message": "Rollout deleted successfully"}
