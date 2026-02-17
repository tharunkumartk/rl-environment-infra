# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import argparse
import json
import os
from typing import Any

from agent import BrowserAgent
from computers import BrowserbaseComputer, PlaywrightComputer
from task_verifier import verify_task_output


PLAYWRIGHT_SCREEN_SIZE = (1440, 900)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the browser agent with a query.")

    # Create mutually exclusive group for query and tasks_file
    query_group = parser.add_mutually_exclusive_group(required=True)
    query_group.add_argument(
        "--query",
        type=str,
        help="The query for the browser agent to execute.",
    )
    query_group.add_argument(
        "--tasks_file",
        type=str,
        help="Path to JSON file containing tasks to execute iteratively.",
    )

    parser.add_argument(
        "--env",
        type=str,
        choices=("playwright", "browserbase"),
        default="playwright",
        help="The computer use environment to use.",
    )
    parser.add_argument(
        "--initial_url",
        type=str,
        default="http://localhost:3000/",
        help="The inital URL loaded for the computer.",
    )
    parser.add_argument(
        "--highlight_mouse",
        action="store_true",
        default=False,
        help="If possible, highlight the location of the mouse.",
    )
    parser.add_argument(
        "--model",
        default="gemini-2.5-computer-use-preview-10-2025",
        help="Set which main model to use.",
        choices=[
            "gemini-2.5-computer-use-preview-10-2025",
            "gemini-3-flash-preview",
            "gemini-3-pro-preview",
        ],
    )
    args = parser.parse_args()

    # Metabase access information to prepend to each task
    METABASE_ACCESS_INFO = "Login to the account with Login info: daksh@deeptune.com, Daksh@123. Please only output the JSON output in the final response/step. "

    # Handle tasks file if provided
    if args.tasks_file:
        return run_tasks_from_file(args, METABASE_ACCESS_INFO)
    else:
        # Single query execution
        return run_single_query(args, args.query)


def run_single_query(args: Any, query: str) -> int:
    """Run a single query with the browser agent."""
    if args.env == "playwright":
        env = PlaywrightComputer(
            screen_size=PLAYWRIGHT_SCREEN_SIZE,
            initial_url=args.initial_url,
            highlight_mouse=args.highlight_mouse,
        )
    elif args.env == "browserbase":
        env = BrowserbaseComputer(
            screen_size=PLAYWRIGHT_SCREEN_SIZE, initial_url=args.initial_url
        )
    else:
        raise ValueError("Unknown environment: ", args.env)

    with env as browser_computer:
        agent = BrowserAgent(
            browser_computer=browser_computer,
            query=query,
            model_name=args.model,
        )
        agent.agent_loop()
    return 0


def run_tasks_from_file(args: Any, metabase_access_info: str) -> int:
    """Run multiple tasks from a JSON file and collect outputs."""
    # Enable headless mode for batch task processing
    os.environ["PLAYWRIGHT_HEADLESS"] = "1"

    # Create video recordings directory
    recordings_dir = "task_recordings"
    os.makedirs(recordings_dir, exist_ok=True)

    # Read tasks from JSON file
    with open(args.tasks_file, "r") as f:
        tasks = json.load(f)

    # Initialize output collection
    tasks_output = []
    output_file = "tasks_output.json"

    print(f"\nRunning in headless mode for batch task processing.")
    print(f"Video recordings will be saved to {recordings_dir}/")
    print(f"Found {len(tasks)} tasks to execute.")
    print(f"Outputs will be written to {output_file}\n")

    # Process each task
    for i, task_obj in enumerate(tasks, 1):
        task_id = task_obj.get("id", f"task_{i}")
        task_description = task_obj.get("task", "")
        expected_answer = task_obj.get("answer")

        print(f"\n{'='*80}")
        print(f"Processing Task {i}/{len(tasks)}: {task_id}")
        print(f"Description: {task_description}")
        print(f"{'='*80}\n")

        # Prepend Metabase access information to task
        full_query = metabase_access_info + task_description

        # Run the browser agent for this task
        video_path = None
        try:
            if args.env == "playwright":
                # Create a task-specific subdirectory for this recording
                task_video_dir = os.path.join(recordings_dir, task_id)
                os.makedirs(task_video_dir, exist_ok=True)

                env = PlaywrightComputer(
                    screen_size=PLAYWRIGHT_SCREEN_SIZE,
                    initial_url=args.initial_url,
                    highlight_mouse=args.highlight_mouse,
                    record_video_dir=task_video_dir,
                )
            elif args.env == "browserbase":
                env = BrowserbaseComputer(
                    screen_size=PLAYWRIGHT_SCREEN_SIZE, initial_url=args.initial_url
                )
            else:
                raise ValueError("Unknown environment: ", args.env)

            with env as browser_computer:
                agent = BrowserAgent(
                    browser_computer=browser_computer,
                    query=full_query,
                    model_name=args.model,
                )
                result = agent.agent_loop()

            # Get video path if available (only for Playwright)
            if args.env == "playwright" and hasattr(env, "get_video_path"):
                video_path = env.get_video_path()

            # Verify and extract JSON from task output
            parsed_json = None
            success = False
            if result:
                print(f"\n🔍 Verifying task output and extracting JSON...")
                parsed_json, success = verify_task_output(
                    task_description, result, expected_answer
                )
                if parsed_json:
                    print(f"✓ Successfully extracted JSON")
                    if expected_answer:
                        if success:
                            print(f"✓ Result matches expected answer")
                        else:
                            print(f"✗ Result does NOT match expected answer")
                else:
                    print(f"⚠ Could not extract JSON from output")

            # Collect task output
            task_output = {
                "id": task_id,
                "task": task_description,
                "status": "completed",
                "result": result,
                "parsed_json": parsed_json,
                "success": success,
                "video_path": video_path,
            }

        except Exception as e:
            print(f"\n❌ Error processing task {task_id}: {str(e)}\n")
            task_output = {
                "id": task_id,
                "task": task_description,
                "status": "failed",
                "error": str(e),
                "parsed_json": None,
                "success": False,
                "video_path": video_path,
            }

        # Add to outputs
        tasks_output.append(task_output)

        # Write updated output to file after each task
        with open(output_file, "w") as f:
            json.dump(tasks_output, f, indent=2)

        video_msg = f" Video: {video_path}" if video_path else ""
        print(
            f"\n✓ Task {task_id} completed. Output written to {output_file}{video_msg}"
        )

    print(f"\n{'='*80}")
    print(f"All {len(tasks)} tasks completed!")
    print(f"Results saved to {output_file}")
    print(f"Videos saved to {recordings_dir}/")
    print(f"{'='*80}\n")

    return 0


if __name__ == "__main__":
    main()
