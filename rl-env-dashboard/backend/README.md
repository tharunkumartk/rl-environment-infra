# RL Environment Dashboard - Backend

This is the backend API for the RL Environment Dashboard, built with FastAPI.

## Getting Started

First, create a virtual environment and install dependencies:

```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Then, run the development server:

```bash
uvicorn main:app --reload
```

The API will be available at [http://localhost:8000](http://localhost:8000)

## API Documentation

Once the server is running, you can access:
- Interactive API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Alternative API docs: [http://localhost:8000/redoc](http://localhost:8000/redoc)

## Tech Stack

- **FastAPI** - Modern, fast web framework
- **Uvicorn** - ASGI server
