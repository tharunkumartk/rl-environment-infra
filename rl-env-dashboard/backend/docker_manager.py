import subprocess
import time
import os
import requests
import secrets
from typing import Tuple, Set, Dict

# Port allocation tracking
_used_ports: Set[int] = set()
_next_port = 8100

# Path to SQL dump (relative to backend directory)
SQL_DUMP_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "metabase_envdata.sql"
)

# Path to agent-docker directory
AGENT_DOCKER_PATH = os.path.join(os.path.dirname(__file__), "..", "agent-docker")

# Docker image names
AGENT_IMAGE_NAME = "rollout-agent:latest"
METABASE_IMAGE_NAME = "metabase/metabase:latest"
POSTGRES_IMAGE_NAME = "postgres:16"

# Backend URL for agent communication (use host.docker.internal on Mac/Windows)
BACKEND_URL = os.environ.get("BACKEND_URL", "http://host.docker.internal:8000")

# Database credentials
DB_USER = "metabase"
DB_PASSWORD = "metabase_password"
DB_NAME = "metabasedb"


def allocate_port() -> int:
    """Allocate the next available port."""
    global _next_port
    while _next_port in _used_ports:
        _next_port += 1
    port = _next_port
    _used_ports.add(port)
    _next_port += 1
    return port


def release_port(port: int):
    """Release a port back to the pool."""
    _used_ports.discard(port)


def build_agent_image() -> bool:
    """
    Build the custom Docker image containing Metabase, Postgres, and the agent.
    Returns True if image exists or was built successfully.
    """
    try:
        # Check if image already exists
        result = subprocess.run(
            ["docker", "images", "-q", AGENT_IMAGE_NAME],
            capture_output=True,
            text=True,
            check=True,
        )
        
        if result.stdout.strip():
            print(f"Docker image {AGENT_IMAGE_NAME} already exists")
            return True
        
        # Build the image from the project root (parent of rl-env-dashboard)
        # This allows us to COPY both computer-use-preview/ and metabase_envdata.sql
        project_root = os.path.join(os.path.dirname(__file__), "..", "..")
        project_root = os.path.abspath(project_root)
        
        print(f"Building Docker image {AGENT_IMAGE_NAME}...")
        print(f"Build context: {project_root}")
        
        # Create a build context that includes:
        # - rl-env-dashboard/agent-docker/* (Dockerfile, scripts, etc.)
        # - computer-use-preview/ (agent code)
        # - metabase_envdata.sql (database dump)
        
        build_result = subprocess.run(
            [
                "docker",
                "build",
                "-t",
                AGENT_IMAGE_NAME,
                "-f",
                "rl-env-dashboard/agent-docker/Dockerfile",
                ".",  # Build context is project root
            ],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        
        if build_result.returncode != 0:
            print(f"Error building Docker image:")
            print(f"STDOUT: {build_result.stdout}")
            print(f"STDERR: {build_result.stderr}")
            return False
        
        print(f"Successfully built Docker image {AGENT_IMAGE_NAME}")
        return True
        
    except Exception as e:
        print(f"Error checking/building Docker image: {e}")
        return False


def generate_agent_token() -> str:
    """Generate a secure random token for agent authentication."""
    return secrets.token_urlsafe(32)


def _start_postgres_container(
    short_id: str, network_name: str, sql_dump_path: str
) -> str:
    """
    Start PostgreSQL container with SQL dump loaded.
    Returns container name.
    """
    container_name = f"rollout-postgres-{short_id}"
    
    # Ensure SQL dump exists
    if not os.path.exists(sql_dump_path):
        raise Exception(f"SQL dump not found at {sql_dump_path}")
    
    abs_dump_path = os.path.abspath(sql_dump_path)
    
    # Start PostgreSQL container
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--network",
            network_name,
            "-e",
            f"POSTGRES_USER={DB_USER}",
            "-e",
            f"POSTGRES_PASSWORD={DB_PASSWORD}",
            "-e",
            f"POSTGRES_DB={DB_NAME}",
            "-v",
            f"{abs_dump_path}:/docker-entrypoint-initdb.d/dump.sql:ro",
            POSTGRES_IMAGE_NAME,
        ],
        check=True,
        capture_output=True,
    )
    
    # Wait for PostgreSQL to be ready
    print(f"Waiting for PostgreSQL {container_name} to be ready...")
    max_retries = 30
    for i in range(max_retries):
        try:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    container_name,
                    "pg_isready",
                    "-U",
                    DB_USER,
                ],
                capture_output=True,
                timeout=5,
            )
            if result.returncode == 0:
                print(f"PostgreSQL {container_name} is ready")
                break
        except Exception:
            pass
        
        if i == max_retries - 1:
            raise Exception(f"PostgreSQL {container_name} failed to start in time")
        time.sleep(1)
    
    # Load SQL dump manually using pg_restore
    print(f"Loading SQL dump into {container_name}...")
    subprocess.run(
        [
            "docker",
            "exec",
            container_name,
            "pg_restore",
            "-U",
            DB_USER,
            "-d",
            DB_NAME,
            "--no-owner",
            "--no-acl",
            "/docker-entrypoint-initdb.d/dump.sql",
        ],
        capture_output=True,
        check=False,  # May have warnings
    )
    print(f"SQL dump loaded into {container_name}")
    
    return container_name


def _start_metabase_container(
    short_id: str, network_name: str, postgres_container: str, metabase_port: int
) -> str:
    """
    Start Metabase container connected to PostgreSQL.
    Returns container name.
    """
    container_name = f"rollout-metabase-{short_id}"
    
    # Start Metabase container
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--network",
            network_name,
            "-e",
            "MB_DB_TYPE=postgres",
            "-e",
            f"MB_DB_DBNAME={DB_NAME}",
            "-e",
            f"MB_DB_PORT=5432",
            "-e",
            f"MB_DB_USER={DB_USER}",
            "-e",
            f"MB_DB_PASS={DB_PASSWORD}",
            "-e",
            f"MB_DB_HOST={postgres_container}",
            "-p",
            f"{metabase_port}:3000",  # Expose for debugging
            METABASE_IMAGE_NAME,
        ],
        check=True,
        capture_output=True,
    )
    
    # Wait for Metabase to be ready
    print(f"Waiting for Metabase {container_name} to be ready...")
    max_retries = 120  # 2 minutes
    for i in range(max_retries):
        try:
            response = requests.get(
                f"http://localhost:{metabase_port}/api/health",
                timeout=2,
            )
            if response.status_code == 200:
                print(f"Metabase {container_name} is ready")
                return container_name
        except Exception:
            pass
        
        if i == max_retries - 1:
            raise Exception(f"Metabase {container_name} failed to start in time")
        time.sleep(1)
    
    return container_name


def _start_agent_container(
    short_id: str,
    network_name: str,
    metabase_container: str,
    rollout_id: str,
    task_id: str,
    task_text: str,
    expected_answer: str,
    agent_token: str,
    gemini_api_key: str,
    model_name: str,
) -> str:
    """
    Start agent container connected to Metabase.
    Returns container name.
    """
    container_name = f"rollout-agent-{short_id}"
    metabase_url = f"http://{metabase_container}:3000"
    
    # Start agent container
    # Add --add-host to ensure host.docker.internal works on custom networks
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--network",
            network_name,
            "--add-host", "host.docker.internal:host-gateway",  # Ensure backend access works
            "-e",
            f"ROLLOUT_ID={rollout_id}",
            "-e",
            f"TASK_ID={task_id}",
            "-e",
            f"TASK_TEXT={task_text}",
            "-e",
            f"EXPECTED_ANSWER={expected_answer or ''}",
            "-e",
            f"BACKEND_URL={BACKEND_URL}",
            "-e",
            f"AGENT_TOKEN={agent_token}",
            "-e",
            f"GEMINI_API_KEY={gemini_api_key}",
            "-e",
            f"MODEL_NAME={model_name}",
            "-e",
            f"METABASE_URL={metabase_url}",
            AGENT_IMAGE_NAME,
        ],
        check=True,
        capture_output=True,
    )
    
    print(f"Started agent container {container_name}")
    return container_name


def provision_environment(
    rollout_id: str,
    task_id: str,
    task_text: str,
    expected_answer: str,
    agent_token: str,
    model_name: str = "gemini-2.5-computer-use-preview-10-2025",
) -> Tuple[int, str]:
    """
    Provision a multi-container environment with separate PostgreSQL, Metabase, and Agent containers.
    Each rollout gets its own Docker network and 3 containers.

    Returns:
        Tuple of (metabase_port, agent_container_name)
    """
    # Ensure the Docker image is built
    if not build_agent_image():
        raise Exception("Failed to build agent Docker image")
    
    short_id = rollout_id[:8]
    network_name = f"rollout-net-{short_id}"
    
    # Allocate port for Metabase (for debugging/access)
    metabase_port = allocate_port()
    
    postgres_container = None
    metabase_container = None
    agent_container = None

    try:
        # Get Gemini API key from environment
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise Exception("GEMINI_API_KEY environment variable not set")
        
        # 1. Create Docker network
        print(f"Creating Docker network {network_name}...")
        subprocess.run(
            ["docker", "network", "create", network_name],
            check=True,
            capture_output=True,
        )
        print(f"Created network {network_name}")
        
        # 2. Start PostgreSQL container with SQL dump
        postgres_container = _start_postgres_container(
            short_id, network_name, SQL_DUMP_PATH
        )
        
        # 3. Start Metabase container
        metabase_container = _start_metabase_container(
            short_id, network_name, postgres_container, metabase_port
        )
        
        # 4. Start Agent container
        agent_container = _start_agent_container(
            short_id,
            network_name,
            metabase_container,
            rollout_id,
            task_id,
            task_text,
            expected_answer,
            agent_token,
            gemini_api_key,
            model_name,
        )
        
        print(f"Environment provisioned successfully for rollout {short_id}")
        print(f"  - Network: {network_name}")
        print(f"  - PostgreSQL: {postgres_container}")
        print(f"  - Metabase: {metabase_container} (port {metabase_port})")
        print(f"  - Agent: {agent_container}")
        
        return metabase_port, agent_container

    except Exception as e:
        print(f"Error provisioning environment: {e}")
        # Cleanup on failure
        teardown_environment(rollout_id, agent_container, metabase_port)
        raise


def teardown_environment(
    rollout_id: str,
    container_name: str = None,
    port: int = None,
):
    """
    Teardown a multi-container environment.
    Removes all containers (agent, metabase, postgres) and the network.
    """
    short_id = rollout_id[:8]
    network_name = f"rollout-net-{short_id}"
    
    containers = [
        f"rollout-agent-{short_id}",
        f"rollout-metabase-{short_id}",
        f"rollout-postgres-{short_id}",
    ]
    
    # Stop and remove all containers
    for container in containers:
        try:
            subprocess.run(
                ["docker", "stop", container],
                capture_output=True,
                timeout=30,
                check=False,
            )
            subprocess.run(
                ["docker", "rm", container],
                capture_output=True,
                check=False,
            )
            print(f"Removed container {container}")
        except Exception:
            pass
    
    # Remove network
    try:
        subprocess.run(
            ["docker", "network", "rm", network_name],
            capture_output=True,
            check=False,
        )
        print(f"Removed network {network_name}")
    except Exception:
        pass

    # Release port
    if port:
        release_port(port)


def is_container_running(container_name: str) -> bool:
    """
    Check if a container is currently running.
    
    Returns:
        True if container exists and is running, False otherwise
    """
    try:
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        
        # If container doesn't exist, inspect returns non-zero exit code
        if result.returncode != 0:
            return False
        
        # Check if the container is running
        return result.stdout.strip().lower() == "true"
        
    except Exception:
        return False


def cleanup_all():
    """
    Cleanup all rollout containers and networks (called on shutdown).
    """
    try:
        # Cleanup all containers with rollout prefix
        for prefix in ["rollout-agent-", "rollout-metabase-", "rollout-postgres-"]:
            result = subprocess.run(
                [
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    f"name={prefix}",
                    "--format",
                    "{{.Names}}",
                ],
                capture_output=True,
                text=True,
                check=True,
            )

            containers = result.stdout.strip().split("\n")
            containers = [c for c in containers if c]  # Filter empty strings

            for container in containers:
                try:
                    subprocess.run(
                        ["docker", "stop", container],
                        capture_output=True,
                        timeout=30,
                        check=False,
                    )
                    subprocess.run(
                        ["docker", "rm", container],
                        capture_output=True,
                        check=False,
                    )
                    print(f"Cleaned up container {container}")
                except Exception:
                    pass
        
        # Cleanup all networks with rollout prefix
        result = subprocess.run(
            [
                "docker",
                "network",
                "ls",
                "--filter",
                "name=rollout-net-",
                "--format",
                "{{.Name}}",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        
        networks = result.stdout.strip().split("\n")
        networks = [n for n in networks if n]
        
        for network in networks:
            try:
                subprocess.run(
                    ["docker", "network", "rm", network],
                    capture_output=True,
                    check=False,
                )
                print(f"Cleaned up network {network}")
            except Exception:
                pass

    except Exception as e:
        print(f"Error during cleanup: {e}")
