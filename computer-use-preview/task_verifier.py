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

import os
import json
from typing import Any, Optional
from google import genai
from google.genai.types import (
    Part,
    GenerateContentConfig,
    Content,
)


def compare_json_results(actual: Any, expected: Any) -> bool:
    """
    Deep comparison of two JSON structures.

    Args:
        actual: The actual parsed JSON result
        expected: The expected parsed JSON result

    Returns:
        True if the structures match, False otherwise
    """
    # Handle None cases
    if actual is None and expected is None:
        return True
    if actual is None or expected is None:
        return False

    # Handle numbers with tolerance for floating point comparison
    # Check this BEFORE strict type checking to allow int/float comparison
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        # Allow small floating point differences
        return abs(float(actual) - float(expected)) < 1e-9

    # Handle different types (strict check for non-numeric types)
    if type(actual) != type(expected):
        return False

    # Handle dictionaries
    if isinstance(actual, dict):
        if set(actual.keys()) != set(expected.keys()):
            return False
        return all(
            compare_json_results(actual[key], expected[key]) for key in actual.keys()
        )

    # Handle lists
    if isinstance(actual, list):
        if len(actual) != len(expected):
            return False
        return all(compare_json_results(a, e) for a, e in zip(actual, expected))

    # Handle all other types (strings, booleans, etc.)
    return actual == expected


def verify_task_output(
    task_input: str, task_output: str, expected_result: Optional[str] = None
) -> tuple[Optional[dict[str, Any]], bool]:
    """
    Verifies and extracts JSON from task output based on task input requirements,
    and optionally compares it with the expected result.

    Args:
        task_input: The original task description/requirements that specify the expected JSON format
        task_output: The output from the task execution that contains the JSON to extract
        expected_result: Optional JSON string containing the expected result to compare against

    Returns:
        A tuple containing:
        - The extracted and parsed JSON dictionary (or None if extraction fails)
        - A boolean indicating whether the result matches the expected result (True if no expected_result provided)
    """
    # Initialize Gemini client
    client = genai.Client(
        api_key=os.environ.get("GEMINI_API_KEY"),
        vertexai=os.environ.get("USE_VERTEXAI", "0").lower() in ["true", "1"],
        project=os.environ.get("VERTEXAI_PROJECT"),
        location=os.environ.get("VERTEXAI_LOCATION"),
    )

    # Create prompt for JSON extraction
    prompt = f"""Given the following task input and task output, please extract the JSON data in the exact format requested by the task input.

Task Input (specifies the expected JSON format):
{task_input}

Task Output (contains the data to extract):
{task_output}

Please extract and return ONLY the JSON object in the format specified by the task input. Do not include any explanations, markdown formatting, or additional text - just the raw JSON object."""

    # Prepare the content for the API call
    contents = [
        Content(
            role="user",
            parts=[Part(text=prompt)],
        )
    ]

    # Configure generation parameters
    config = GenerateContentConfig(
        temperature=0.1,  # Low temperature for more deterministic output
        top_p=0.95,
        max_output_tokens=8192,
    )

    try:
        # Call Gemini API
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=contents,
            config=config,
        )

        # Extract text from response
        if not response.candidates or not response.candidates[0].content:
            return None, False

        candidate = response.candidates[0]
        text_parts = []

        for part in candidate.content.parts:
            if part.text:
                text_parts.append(part.text)

        response_text = " ".join(text_parts).strip()

        if not response_text:
            return None, False

        # Try to parse the JSON from the response
        # Remove markdown code blocks if present
        if response_text.startswith("```"):
            # Find the actual JSON content between code blocks
            lines = response_text.split("\n")
            json_lines = []
            in_code_block = False

            for line in lines:
                if line.strip().startswith("```"):
                    in_code_block = not in_code_block
                    continue
                if in_code_block or (not line.strip().startswith("```")):
                    json_lines.append(line)

            response_text = "\n".join(json_lines).strip()

        # Parse JSON
        parsed_json = json.loads(response_text)

        # Compare with expected result if provided
        success = True
        if expected_result:
            try:
                expected_json = json.loads(expected_result)
                success = compare_json_results(parsed_json, expected_json)
            except json.JSONDecodeError as e:
                print(f"Error parsing expected result JSON: {e}")
                success = False

        return parsed_json, success

    except json.JSONDecodeError as e:
        print(f"Error parsing JSON from response: {e}")
        print(f"Response text: {response_text}")
        return None, False
    except Exception as e:
        print(f"Error calling Gemini API: {e}")
        return None, False


if __name__ == "__main__":
    task_input = "Retrieve product titles and ratings for products with ratings above '4.5' and price less than '40'. Return in the format {product_titles: list[str], ratings: list[number]}."
    task_output = 'I have evaluated step 19. The filters "Rating is greater than 4.5" AND "Price is less than 40" have been applied.\nI can see 3 products that match:\n1.  Title: Rustic Paper Wallet, Rating: 4.6\n2.  Title: Ergonomic Wool Bag, Rating: 5\n3.  Title: Lightweight Linen Hat, Rating: 5\n\nI have the product titles and ratings. I need to return them in the format {product\\_titles: list\\[str], ratings: list\\[number]}.\n\nproduct\\_titles = \\["Rustic Paper Wallet", "Ergonomic Wool Bag", "Lightweight Linen Hat"]\nratings = \\[4.6, 5, 5]\n\nI will now format the final answer.\n\n{"product_titles": ["Rustic Paper Wallet", "Ergonomic Wool Bag", "Lightweight Linen Hat"], "ratings": [4.6, 5, 5]}'
    expected_result = '{\n  "ratings": [\n    4.6,\n    5,\n    5\n  ],\n  "product_titles": [\n    "Rustic Paper Wallet",\n    "Ergonomic Wool Bag",\n    "Lightweight Linen Hat"\n  ]\n}'
    
    parsed_json, success = verify_task_output(task_input, task_output, expected_result)
    print(f"Parsed JSON: {parsed_json}")
    print(f"Success: {success}")
    if parsed_json:
        print(json.dumps(parsed_json, indent=4))
