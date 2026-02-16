"""
Logging utilities for rollout execution.
Captures agent steps in JSON format with screenshots.
"""

import os
import sys
import json
from datetime import datetime
import base64
from typing import Optional, Any
from contextlib import contextmanager


class RolloutLogger:
    """
    Logger to capture agent execution steps in JSON format with screenshots.
    """

    def __init__(
        self,
        rollout_id: str,
        task_id: str,
        log_dir: str = None,
        screenshot_dir: str = None,
    ):
        self.rollout_id = rollout_id
        self.task_id = task_id
        self.step_counter = 0
        self.steps = []
        self.started_at = None
        self.completed_at = None
        self.error = None

        # Default log directory
        if log_dir is None:
            log_dir = os.path.join(
                os.path.dirname(__file__),
                "..",
                "..",
                "computer-use-preview",
                "task_logs",
            )

        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

        # Create screenshot directory
        if screenshot_dir is None:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            screenshot_dir = os.path.join(
                self.log_dir,
                "screenshots",
                f"{task_id}_rollout_{rollout_id[:8]}_{timestamp}",
            )

        self.screenshot_dir = screenshot_dir
        os.makedirs(self.screenshot_dir, exist_ok=True)

        # Create log file path
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        log_filename = f"{task_id}_rollout_{rollout_id[:8]}_{timestamp}.json"
        self.log_path = os.path.join(self.log_dir, log_filename)

    def start(self):
        """Mark the start of the rollout."""
        self.started_at = datetime.utcnow().isoformat()
        self._write_log()

    def log_step(
        self,
        reasoning: Optional[str],
        function_calls: list[dict[str, Any]],
        screenshot_data: Optional[bytes] = None,
    ):
        """
        Log a single agent step with reasoning, function calls, and screenshot.

        Args:
            reasoning: The agent's reasoning for this step
            function_calls: List of function calls with their parameters
            screenshot_data: Screenshot as bytes (PNG format)
        """
        self.step_counter += 1

        # Save screenshot if provided
        screenshot_path = None
        if screenshot_data:
            screenshot_filename = f"step_{self.step_counter:03d}.png"
            screenshot_path = os.path.join(self.screenshot_dir, screenshot_filename)

            with open(screenshot_path, "wb") as f:
                f.write(screenshot_data)

            # Store relative path for the JSON log
            screenshot_path = os.path.relpath(screenshot_path, self.log_dir)

        step_data = {
            "step_number": self.step_counter,
            "timestamp": datetime.utcnow().isoformat(),
            "reasoning": reasoning,
            "function_calls": function_calls,
            "screenshot_path": screenshot_path,
        }

        self.steps.append(step_data)
        self._write_log()

    def log_final_output(
        self,
        final_reasoning: Optional[str],
        parsed_output: Optional[Any] = None,
    ):
        """
        Log the final step with reasoning and parsed output.
        
        Args:
            final_reasoning: The agent's final reasoning/response
            parsed_output: The parsed JSON output from the agent's response
        """
        self.step_counter += 1
        
        step_data = {
            "step_number": self.step_counter,
            "timestamp": datetime.utcnow().isoformat(),
            "reasoning": final_reasoning,
            "function_calls": [],
            "screenshot_path": None,
            "parsed_output": parsed_output,
            "is_final": True,
        }
        
        self.steps.append(step_data)
        self._write_log()

    def complete(self, error: Optional[str] = None):
        """Mark the completion of the rollout."""
        self.completed_at = datetime.utcnow().isoformat()
        self.error = error
        self._write_log()

    def _write_log(self):
        """Write the complete log to a JSON file."""
        log_data = {
            "rollout_id": self.rollout_id,
            "task_id": self.task_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "total_steps": self.step_counter,
            "steps": self.steps,
        }

        with open(self.log_path, "w") as f:
            json.dump(log_data, f, indent=2)

    def get_log_path(self):
        """Get the path to the log file."""
        return self.log_path

    def get_screenshot_dir(self):
        """Get the path to the screenshot directory."""
        return self.screenshot_dir


@contextmanager
def suppress_stdout_stderr():
    """
    Context manager to suppress all stdout and stderr output.
    Useful for silencing noisy agent/library output.
    """
    # Save original stdout/stderr
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    # Redirect to devnull
    devnull = open(os.devnull, "w")
    sys.stdout = devnull
    sys.stderr = devnull

    try:
        yield
    finally:
        # Restore original stdout/stderr
        sys.stdout = original_stdout
        sys.stderr = original_stderr
        devnull.close()
