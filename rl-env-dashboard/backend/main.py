from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os

import database
import docker_manager
import worker
from routes import router

# Paths to computer-use outputs (relative to this file)
COMPUTER_USE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "computer-use-preview"
)
LOG_PATH = os.path.join(COMPUTER_USE_PATH, "task_logs")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown."""
    # Startup
    print("Initializing database...")
    await database.init_db()
    print("Database initialized")

    yield

    # Shutdown
    print("Cleaning up Docker containers...")
    docker_manager.cleanup_all()
    print("Shutting down worker pool...")
    worker.shutdown()
    print("Shutdown complete")


app = FastAPI(title="RL Environment Dashboard API", lifespan=lifespan)

# Configure CORS - Allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router)

# Mount static files for log and screenshot serving
os.makedirs(LOG_PATH, exist_ok=True)
app.mount("/static/logs", StaticFiles(directory=LOG_PATH), name="logs")


@app.get("/")
def read_root():
    return {"message": "RL Environment Dashboard API"}


@app.get("/health")
def health_check():
    return {"status": "healthy"}
