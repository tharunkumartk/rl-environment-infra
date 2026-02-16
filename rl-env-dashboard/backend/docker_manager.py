import subprocess
import sys
import time
import os
import requests
from typing import Tuple, Set
from pathlib import Path

# Port allocation tracking
_used_ports: Set[int] = set()
_next_port = 8100

# Path to SQL dump (relative to backend directory)
SQL_DUMP_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "metabase_envdata.sql"
)

# Shared Postgres container name
SHARED_PG_CONTAINER = "metabase-shared-postgres"
SHARED_PG_PORT = 5433  # Different from default to avoid conflicts


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


def ensure_shared_postgres():
    """
    Ensure the shared Postgres container exists and has the data loaded.
    This is called once at startup or when needed.
    """
    # Check if container already exists and is running
    result = subprocess.run(
        ["docker", "ps", "-q", "-f", f"name={SHARED_PG_CONTAINER}"],
        capture_output=True,
        text=True,
    )

    if result.stdout.strip():
        print(f"Shared Postgres container {SHARED_PG_CONTAINER} is already running")
        return

    # Check if container exists but is stopped
    result = subprocess.run(
        ["docker", "ps", "-aq", "-f", f"name={SHARED_PG_CONTAINER}"],
        capture_output=True,
        text=True,
    )

    if result.stdout.strip():
        print(f"Starting existing Postgres container {SHARED_PG_CONTAINER}...")
        subprocess.run(["docker", "start", SHARED_PG_CONTAINER], check=True)
        time.sleep(3)
        return

    # Create new shared Postgres container
    print(f"Creating shared Postgres container {SHARED_PG_CONTAINER}...")

    # Create Docker network if it doesn't exist
    subprocess.run(
        ["docker", "network", "create", "rollout-net"],
        capture_output=True,
        check=False,
    )

    # Start Postgres container (version 16 to match the dump format)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            SHARED_PG_CONTAINER,
            "--network",
            "rollout-net",
            "-e",
            "POSTGRES_USER=metabase",
            "-e",
            "POSTGRES_PASSWORD=metabase_password",
            "-e",
            "POSTGRES_DB=postgres",
            "-p",
            f"{SHARED_PG_PORT}:5432",
            "postgres:16",
        ],
        check=True,
        capture_output=True,
    )

    # Wait for Postgres to be ready
    print(f"Waiting for Postgres to be ready...")
    max_retries = 30
    for i in range(max_retries):
        result = subprocess.run(
            ["docker", "exec", SHARED_PG_CONTAINER, "pg_isready", "-U", "metabase"],
            capture_output=True,
        )
        if result.returncode == 0:
            break
        time.sleep(1)
    else:
        raise Exception("Postgres failed to start in time")

    # Load SQL dump into Postgres
    print(f"Loading SQL data into Postgres...")
    if not os.path.exists(SQL_DUMP_PATH):
        raise Exception(f"SQL dump not found at {SQL_DUMP_PATH}")

    # Copy SQL dump into container
    subprocess.run(
        [
            "docker",
            "cp",
            SQL_DUMP_PATH,
            f"{SHARED_PG_CONTAINER}:/tmp/metabase_envdata.sql",
        ],
        check=True,
        capture_output=True,
    )

    # Check if root_db already exists
    result = subprocess.run(
        [
            "docker",
            "exec",
            SHARED_PG_CONTAINER,
            "psql",
            "-U",
            "metabase",
            "-d",
            "postgres",
            "-tAc",
            "SELECT 1 FROM pg_database WHERE datname = 'root_db';",
        ],
        capture_output=True,
        text=True,
    )

    if result.stdout.strip() == "1":
        print(f"root_db already exists, skipping restore")
    else:
        # Restore the dump (creates root_db database)
        # Use --no-owner to avoid ownership conflicts (dump was created with different user)
        print(f"Restoring SQL dump into root_db...")
        result = subprocess.run(
            [
                "docker",
                "exec",
                SHARED_PG_CONTAINER,
                "pg_restore",
                "-U",
                "metabase",
                "-d",
                "postgres",
                "-C",
                "--no-owner",
                "--no-acl",
                "/tmp/metabase_envdata.sql",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"pg_restore stderr: {result.stderr}")
            raise Exception(f"Failed to restore SQL dump: {result.stderr}")

        print(f"SQL dump restored successfully")

    print(f"Shared Postgres container ready with data loaded")


def provision_environment(rollout_id: str) -> Tuple[int, str, str]:
    """
    Provision a Metabase environment for a rollout.
    Each rollout gets its own isolated database within the shared Postgres container.

    Returns:
        Tuple of (metabase_port, postgres_container_name, metabase_container_name)
    """
    short_id = rollout_id[:8]
    mb_container = f"rollout-mb-{short_id}"
    db_name = f"rollout_db_{short_id}"  # Unique database name for this rollout

    # Allocate port for Metabase
    metabase_port = allocate_port()

    try:
        # Ensure shared Postgres is running
        ensure_shared_postgres()

        # Create a new database for this rollout within the shared Postgres
        print(f"Creating isolated database {db_name} for rollout {short_id}...")

        # Copy SQL dump into container (if not already there)
        if os.path.exists(SQL_DUMP_PATH):
            subprocess.run(
                [
                    "docker",
                    "cp",
                    SQL_DUMP_PATH,
                    f"{SHARED_PG_CONTAINER}:/tmp/metabase_envdata.sql",
                ],
                check=True,
                capture_output=True,
            )

        # Create a new database and restore the dump into it
        # First create the database
        subprocess.run(
            [
                "docker",
                "exec",
                SHARED_PG_CONTAINER,
                "psql",
                "-U",
                "metabase",
                "-d",
                "postgres",
                "-c",
                f"CREATE DATABASE {db_name};",
            ],
            check=True,
            capture_output=True,
        )

        # Restore the dump into the new database
        # Note: The dump contains "CREATE DATABASE root_db", so we need to handle this
        # We'll restore it and it will create root_db, then we can rename or use root_db directly
        subprocess.run(
            [
                "docker",
                "exec",
                SHARED_PG_CONTAINER,
                "pg_restore",
                "-U",
                "metabase",
                "-d",
                "postgres",
                "--clean",
                "--if-exists",
                "-C",
                "/tmp/metabase_envdata.sql",
            ],
            check=False,  # Don't fail if database already exists
            capture_output=True,
        )

        # Now root_db exists with the data. We need to clone it to our rollout-specific database
        # Drop the rollout db we just created and recreate it as a template from root_db
        subprocess.run(
            [
                "docker",
                "exec",
                SHARED_PG_CONTAINER,
                "psql",
                "-U",
                "metabase",
                "-d",
                "postgres",
                "-c",
                f"DROP DATABASE IF EXISTS {db_name};",
            ],
            check=True,
            capture_output=True,
        )

        subprocess.run(
            [
                "docker",
                "exec",
                SHARED_PG_CONTAINER,
                "psql",
                "-U",
                "metabase",
                "-d",
                "postgres",
                "-c",
                f"CREATE DATABASE {db_name} WITH TEMPLATE root_db;",
            ],
            check=True,
            capture_output=True,
        )

        print(f"Database {db_name} created with isolated copy of data")

        # Start Metabase container connected to this specific database
        print(f"Starting Metabase container {mb_container}...")
        subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                mb_container,
                "--network",
                "rollout-net",
                "-e",
                "MB_DB_TYPE=postgres",
                "-e",
                f"MB_DB_DBNAME={db_name}",
                "-e",
                "MB_DB_PORT=5432",
                "-e",
                "MB_DB_USER=metabase",
                "-e",
                "MB_DB_PASS=metabase_password",
                "-e",
                f"MB_DB_HOST={SHARED_PG_CONTAINER}",
                "-p",
                f"{metabase_port}:3000",
                "metabase/metabase",
            ],
            check=True,
            capture_output=True,
        )

        # Wait for Metabase to be ready (health check)
        print(f"Waiting for Metabase to be ready on port {metabase_port}...")
        max_retries = 120  # Metabase can take up to 2 minutes
        for i in range(max_retries):
            try:
                response = requests.get(
                    f"http://localhost:{metabase_port}/api/health", timeout=5
                )
                if response.status_code == 200:
                    print(f"Metabase is ready!")
                    break
            except:
                pass
            if i % 10 == 0:
                try:
                    print(
                        f"Still waiting for Metabase... ({i}/{max_retries})",
                        file=sys.__stdout__,
                    )
                except (ValueError, OSError):
                    # stdout might be closed, use __stdout__ directly
                    sys.__stdout__.write(
                        f"Still waiting for Metabase... ({i}/{max_retries})\n"
                    )
                    sys.__stdout__.flush()
            time.sleep(1)
        else:
            raise Exception("Metabase failed to start in time")

        # Return shared Postgres container name for tracking
        return metabase_port, SHARED_PG_CONTAINER, mb_container

    except Exception as e:
        # Cleanup on failure
        try:
            print(f"Error provisioning environment: {e}", file=sys.__stderr__)
        except (ValueError, OSError):
            # stderr might be closed, use __stderr__ directly
            sys.__stderr__.write(f"Error provisioning environment: {e}\n")
            sys.__stderr__.flush()
        teardown_environment(rollout_id, None, mb_container, metabase_port)
        raise


def teardown_environment(
    rollout_id: str,
    pg_container: str = None,
    mb_container: str = None,
    port: int = None,
):
    """
    Teardown a Metabase environment.
    Removes the Metabase container and drops the rollout-specific database.
    The shared Postgres container stays running for other rollouts.
    """
    short_id = rollout_id[:8]
    if not mb_container:
        mb_container = f"rollout-mb-{short_id}"
    db_name = f"rollout_db_{short_id}"

    # Stop and remove the Metabase container
    try:
        subprocess.run(
            ["docker", "stop", mb_container],
            capture_output=True,
            timeout=30,
            check=False,
        )
        subprocess.run(["docker", "rm", mb_container], capture_output=True, check=False)
        try:
            print(f"Removed container {mb_container}", file=sys.__stdout__)
        except (ValueError, OSError):
            sys.__stdout__.write(f"Removed container {mb_container}\n")
            sys.__stdout__.flush()
    except Exception as e:
        try:
            print(f"Error removing container {mb_container}: {e}", file=sys.__stderr__)
        except (ValueError, OSError):
            sys.__stderr__.write(f"Error removing container {mb_container}: {e}\n")
            sys.__stderr__.flush()

    # Drop the rollout-specific database to free up space
    try:
        # Check if shared Postgres container is running
        result = subprocess.run(
            ["docker", "ps", "-q", "-f", f"name={SHARED_PG_CONTAINER}"],
            capture_output=True,
            text=True,
        )

        if result.stdout.strip():
            subprocess.run(
                [
                    "docker",
                    "exec",
                    SHARED_PG_CONTAINER,
                    "psql",
                    "-U",
                    "metabase",
                    "-d",
                    "postgres",
                    "-c",
                    f"DROP DATABASE IF EXISTS {db_name};",
                ],
                capture_output=True,
                check=False,
            )
            try:
                print(f"Dropped database {db_name}", file=sys.__stdout__)
            except (ValueError, OSError):
                sys.__stdout__.write(f"Dropped database {db_name}\n")
                sys.__stdout__.flush()
    except Exception as e:
        try:
            print(f"Error dropping database {db_name}: {e}", file=sys.__stderr__)
        except (ValueError, OSError):
            sys.__stderr__.write(f"Error dropping database {db_name}: {e}\n")
            sys.__stderr__.flush()

    # Release port
    if port:
        release_port(port)


def cleanup_all():
    """
    Cleanup all rollout Metabase containers and their databases (called on shutdown).
    Note: The shared Postgres container is preserved for future use.
    Use cleanup_shared_postgres() to remove it if needed.
    """
    try:
        # List all Metabase containers with the rollout prefix
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "name=rollout-mb-",
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
                # Extract rollout ID from container name (rollout-mb-<short_id>)
                if container.startswith("rollout-mb-"):
                    short_id = container.replace("rollout-mb-", "")
                    db_name = f"rollout_db_{short_id}"

                    # Drop the database
                    subprocess.run(
                        [
                            "docker",
                            "exec",
                            SHARED_PG_CONTAINER,
                            "psql",
                            "-U",
                            "metabase",
                            "-d",
                            "postgres",
                            "-c",
                            f"DROP DATABASE IF EXISTS {db_name};",
                        ],
                        capture_output=True,
                        check=False,
                    )
                    print(f"Dropped database {db_name}")

                # Stop and remove container
                subprocess.run(
                    ["docker", "stop", container],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                subprocess.run(
                    ["docker", "rm", container], capture_output=True, check=False
                )
                print(f"Cleaned up container {container}")
            except Exception as e:
                print(f"Error cleaning up container {container}: {e}")

    except Exception as e:
        print(f"Error during cleanup: {e}")


def cleanup_shared_postgres():
    """
    Stop and remove the shared Postgres container.
    This will delete all Metabase application data and rollout databases!
    Only call this when you want to completely reset the system.
    """
    try:
        # First drop all rollout databases
        result = subprocess.run(
            [
                "docker",
                "exec",
                SHARED_PG_CONTAINER,
                "psql",
                "-U",
                "metabase",
                "-d",
                "postgres",
                "-t",
                "-c",
                "SELECT datname FROM pg_database WHERE datname LIKE 'rollout_db_%';",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode == 0:
            databases = [
                db.strip() for db in result.stdout.strip().split("\n") if db.strip()
            ]
            for db_name in databases:
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        SHARED_PG_CONTAINER,
                        "psql",
                        "-U",
                        "metabase",
                        "-d",
                        "postgres",
                        "-c",
                        f"DROP DATABASE IF EXISTS {db_name};",
                    ],
                    capture_output=True,
                    check=False,
                )
                print(f"Dropped database {db_name}")

        # Now stop and remove the container
        print(
            f"Stopping and removing shared Postgres container {SHARED_PG_CONTAINER}..."
        )
        subprocess.run(
            ["docker", "stop", SHARED_PG_CONTAINER],
            capture_output=True,
            timeout=30,
            check=False,
        )
        subprocess.run(
            ["docker", "rm", SHARED_PG_CONTAINER],
            capture_output=True,
            check=False,
        )
        print(f"Shared Postgres container removed")
    except Exception as e:
        print(f"Error removing shared Postgres: {e}")
