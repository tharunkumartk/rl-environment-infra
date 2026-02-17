import aiosqlite
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "rollout_dashboard.db")


async def init_db():
    """Initialize database and create tables if they don't exist."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Create tasks table
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                answer TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Create rollouts table
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                task_id TEXT REFERENCES tasks(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )

        # Create rollouts table
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS rollouts (
                id TEXT PRIMARY KEY,
                task_id TEXT REFERENCES tasks(id),
                job_id TEXT REFERENCES jobs(id),
                status TEXT DEFAULT 'pending',
                result TEXT,
                parsed_json TEXT,
                success BOOLEAN,
                error TEXT,
                log_path TEXT,
                container_pg TEXT,
                container_mb TEXT,
                metabase_port INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP
            )
        """
        )

        await db.commit()


async def get_db():
    """Get database connection."""
    return await aiosqlite.connect(DB_PATH)


# Task CRUD operations


async def upsert_task(task_id: str, task_text: str, answer: Optional[str] = None):
    """Insert or update a task."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO tasks (id, task, answer)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                task = excluded.task,
                answer = excluded.answer
        """,
            (task_id, task_text, answer),
        )
        await db.commit()


async def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    """Get a single task by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_all_tasks() -> List[Dict[str, Any]]:
    """Get all tasks with rollout statistics."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT 
                t.*,
                COUNT(DISTINCT j.id) as job_count,
                COUNT(r.id) as rollout_count,
                SUM(CASE WHEN r.status = 'success' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN r.status IN ('success', 'failed') THEN 1 ELSE 0 END) as completed_count
            FROM tasks t
            LEFT JOIN jobs j ON t.id = j.task_id
            LEFT JOIN rollouts r ON t.id = r.task_id
            GROUP BY t.id
            ORDER BY t.created_at DESC
        """
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


# Job CRUD operations


async def create_job(job_id: str, task_id: str) -> str:
    """Create a new job."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO jobs (id, task_id) VALUES (?, ?)",
            (job_id, task_id),
        )
        await db.commit()
    return job_id


async def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """Get a single job by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_jobs(task_id: str) -> List[Dict[str, Any]]:
    """Get all jobs for a task with rollout statistics."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT 
                j.*,
                COUNT(r.id) as rollout_count,
                SUM(CASE WHEN r.status = 'success' THEN 1 ELSE 0 END) as success_count,
                SUM(CASE WHEN r.status IN ('success', 'failed') THEN 1 ELSE 0 END) as completed_count
            FROM jobs j
            LEFT JOIN rollouts r ON j.id = r.job_id
            WHERE j.task_id = ?
            GROUP BY j.id
            ORDER BY j.created_at DESC
        """,
            (task_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


# Rollout CRUD operations


async def create_rollout(
    rollout_id: str, task_id: str, job_id: Optional[str] = None, status: str = "pending"
) -> str:
    """Create a new rollout."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO rollouts (id, task_id, job_id, status)
            VALUES (?, ?, ?, ?)
        """,
            (rollout_id, task_id, job_id, status),
        )
        await db.commit()
    return rollout_id


async def update_rollout(
    rollout_id: str,
    status: Optional[str] = None,
    result: Optional[str] = None,
    parsed_json: Optional[str] = None,
    success: Optional[bool] = None,
    error: Optional[str] = None,
    log_path: Optional[str] = None,
    container_pg: Optional[str] = None,
    container_mb: Optional[str] = None,
    metabase_port: Optional[int] = None,
    completed_at: Optional[str] = None,
):
    """Update a rollout with new information."""
    updates = []
    params = []

    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if result is not None:
        updates.append("result = ?")
        params.append(result)
    if parsed_json is not None:
        updates.append("parsed_json = ?")
        params.append(parsed_json)
    if success is not None:
        updates.append("success = ?")
        params.append(1 if success else 0)
    if error is not None:
        updates.append("error = ?")
        params.append(error)
    if log_path is not None:
        updates.append("log_path = ?")
        params.append(log_path)
    if container_pg is not None:
        updates.append("container_pg = ?")
        params.append(container_pg)
    if container_mb is not None:
        updates.append("container_mb = ?")
        params.append(container_mb)
    if metabase_port is not None:
        updates.append("metabase_port = ?")
        params.append(metabase_port)
    if completed_at is not None:
        updates.append("completed_at = ?")
        params.append(completed_at)

    if not updates:
        return

    params.append(rollout_id)
    query = f"UPDATE rollouts SET {', '.join(updates)} WHERE id = ?"

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(query, params)
        await db.commit()


async def get_rollout(rollout_id: str) -> Optional[Dict[str, Any]]:
    """Get a single rollout by ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM rollouts WHERE id = ?", (rollout_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def get_rollouts(
    task_id: Optional[str] = None,
    job_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get rollouts with optional filters."""
    query = "SELECT * FROM rollouts WHERE 1=1"
    params = []

    if task_id:
        query += " AND task_id = ?"
        params.append(task_id)
    if job_id:
        query += " AND job_id = ?"
        params.append(job_id)
    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY created_at DESC"

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]


async def delete_rollout(rollout_id: str):
    """Delete a rollout."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM rollouts WHERE id = ?", (rollout_id,))
        await db.commit()


async def delete_job(job_id: str):
    """Delete a job and all its associated rollouts."""
    async with aiosqlite.connect(DB_PATH) as db:
        # First delete all rollouts associated with this job
        await db.execute("DELETE FROM rollouts WHERE job_id = ?", (job_id,))
        # Then delete the job itself
        await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await db.commit()
