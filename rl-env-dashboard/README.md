# RL Environment Dashboard

A full-stack web application for managing and monitoring parallel rollouts of browser agent tasks with interactive Metabase environments.

## Architecture

- **Backend**: FastAPI (Python) - API server with SQLite database, Docker orchestration, and worker thread pool
- **Frontend**: Next.js (React/TypeScript) - Interactive dashboard for task management and rollout monitoring
- **Infrastructure**: Docker containers (Postgres + Metabase) dynamically provisioned per rollout
- **Agent**: Browser automation using Playwright and Gemini Computer Use

## Features

- 📤 Upload `tasks.json` files to define tasks
- 🚀 Spawn rollouts interactively per task
- 🔄 Real-time polling of rollout status
- 🎥 Video recordings of each agent execution
- ✅ Automatic verification against expected answers
- 🐳 Dynamic Docker environment provisioning
- 📊 Success rate tracking per task

## Setup

### Prerequisites

- Python 3.10+
- Node.js 18+
- Docker
- Playwright with Chromium

### Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export GEMINI_API_KEY="your-api-key"
export MAX_WORKERS=4  # Optional, defaults to 4

# Run the server
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Run the development server
npm run dev
```

The frontend will be available at `http://localhost:3000`

## Usage

1. **Start both servers** (backend on port 8000, frontend on port 3000)

2. **Upload tasks**: Click "Upload JSON" and select your `tasks.json` file
   ```json
   [
     {
       "id": "problem1",
       "task": "Retrieve product titles...",
       "answer": "{\"product_titles\": [...]}"
     }
   ]
   ```

3. **View tasks**: Tasks appear in the left sidebar with rollout counts and success rates

4. **Spawn rollouts**: Select a task and click "+ New Rollout"
   - The system will:
     - Provision a Postgres container and load the SQL data
     - Start a Metabase instance on a unique port
     - Wait for Metabase to be healthy
     - Run the browser agent to complete the task
     - Record a video of the execution
     - Verify the output against the expected answer
     - Tear down the containers

5. **Monitor progress**: Rollout cards update every 3 seconds showing:
   - Status: pending → provisioning → running → completed/failed
   - Parsed JSON results
   - Success/failure indicators
   - Video recordings
   - Error messages (if any)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/tasks/upload` | Upload tasks.json file |
| `GET` | `/api/tasks` | List all tasks with stats |
| `GET` | `/api/tasks/{id}` | Get single task details |
| `POST` | `/api/rollouts` | Spawn a new rollout |
| `GET` | `/api/rollouts` | List rollouts (filterable) |
| `GET` | `/api/rollouts/{id}` | Get rollout details |
| `DELETE` | `/api/rollouts/{id}` | Cancel/delete rollout |

## Architecture Details

### Backend Components

- **`database.py`**: SQLite with tasks and rollouts tables, async CRUD operations
- **`docker_manager.py`**: Container lifecycle management (provision/teardown)
- **`worker.py`**: Thread pool executor running agent jobs
- **`routes.py`**: FastAPI endpoints for task and rollout management
- **`main.py`**: Application entry point with lifespan management

### Frontend Components

- **`page.tsx`**: Main dashboard layout
- **`TaskUpload.tsx`**: File upload component
- **`TaskList.tsx`**: Sidebar task list with stats
- **`TaskDetail.tsx`**: Task info and rollout management
- **`RolloutCard.tsx`**: Individual rollout status display
- **`VideoPlayer.tsx`**: Video playback component

### Rollout Lifecycle

```
pending → provisioning → running → completed/failed
```

1. **Pending**: Rollout created in DB, waiting for worker thread
2. **Provisioning**: Docker containers starting, SQL data loading, Metabase health check
3. **Running**: Agent executing the task via Playwright
4. **Completed/Failed**: Task finished, containers torn down, results stored

### Resource Management

- **Port allocation**: Metabase instances use ports 3001+, automatically tracked
- **Container naming**: `rollout-pg-{id}` and `rollout-mb-{id}`
- **Video storage**: `computer-use-preview/task_recordings/{task_id}/rollout_{id}/`
- **Cleanup**: Automatic teardown on completion or shutdown

## Configuration

Environment variables:

```bash
# Backend
GEMINI_API_KEY=your-key          # Required
MAX_WORKERS=4                     # Optional, default 4
PLAYWRIGHT_HEADLESS=1             # Automatically set by worker

# Paths (auto-configured)
SQL_DUMP_PATH=../../metabase_envdata.sql
COMPUTER_USE_PATH=../../computer-use-preview
```

## Development

### Backend

```bash
# Run with auto-reload
uvicorn main:app --reload --port 8000

# Run tests (if any)
pytest

# Check logs
# Docker container logs: docker logs rollout-mb-{id}
# Worker output: printed to uvicorn console
```

### Frontend

```bash
# Development mode
npm run dev

# Build for production
npm run build
npm start

# Lint
npm run lint
```

## Troubleshooting

### Metabase not starting
- Ensure Docker is running
- Check if port is already in use: `lsof -i :3001`
- Increase timeout in `docker_manager.py` if needed

### Video not playing
- Ensure video path exists in `computer-use-preview/task_recordings/`
- Check browser console for CORS errors
- Verify static file mount in `main.py`

### Agent failing
- Check Gemini API key is set
- Verify Playwright and Chromium are installed
- Check Docker logs for Metabase errors
- Ensure SQL dump file exists at correct path

### Database locked
- SQLite doesn't handle high concurrency well; if you see "database is locked" errors, consider reducing `MAX_WORKERS` or migrating to Postgres

## Future Enhancements

- [ ] Batch rollout spawning (spawn N rollouts at once)
- [ ] WebSocket streaming for real-time updates
- [ ] Rollout comparison view
- [ ] Export results to CSV/JSON
- [ ] Authentication and user management
- [ ] Deployment to cloud with container orchestration
- [ ] Result caching and deduplication
- [ ] Advanced filtering and search
- [ ] Model selection per rollout
- [ ] Rollout history and analytics

## License

Same as the parent project.
