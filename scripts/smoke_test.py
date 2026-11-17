"""Checks that the deployed application answers one real request.

    FUNCTION_KEY=... python -m scripts.smoke_test --url https://<host>/api/assess

Inputs:  the /api/assess URL and a function key in FUNCTION_KEY.
Output:  exit code 0 if the application returned a matched, valid-looking assessment for a
         sample machine, 1 if it did not within the attempts allowed.

One passing request makes one agent call. Uses only the standard library, so the rollback
workflow can run it without installing anything.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Optional

# A machine in the sample data, named by its ID, so matching does not depend on the agent.
REQUEST = {"equipment_id": "CAT320-SE-1042", "request_text": "The boom raises slowly when the machine is warm."}
ATTEMPTS = 5
SECONDS_BETWEEN_ATTEMPTS = 20
REQUEST_TIMEOUT_SECONDS = 120


def problem_with(status: int, body: str) -> Optional[str]:
    """What is wrong with this response, or None if it is a good assessment."""
    if status != 200:
        return f"HTTP {status}: {body[:500]}"
    try:
        assessment = json.loads(body)
    except ValueError:
        return f"HTTP 200, but the body is not JSON: {body[:200]}"
    if not isinstance(assessment, dict) or assessment.get("match_status") != "matched":
        return f"HTTP 200, but the sample machine was not matched: {body[:500]}"
    if not assessment.get("recommendations"):
        return f"HTTP 200, but the assessment has no recommendations: {body[:500]}"
    return None


def post(url: str, key: str) -> tuple[int, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(REQUEST).encode(),
        headers={"Content-Type": "application/json", "x-functions-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, f"no response: {exc}"


def run(send: Callable[[], tuple[int, str]], sleep: Callable[[float], None] = time.sleep) -> bool:
    """Retries because the application restarts when a new package is deployed."""
    for attempt in range(1, ATTEMPTS + 1):
        problem = problem_with(*send())
        if problem is None:
            print(f"Smoke test passed on attempt {attempt}.")
            return True
        print(f"Attempt {attempt} of {ATTEMPTS}: {problem}", flush=True)
        if attempt < ATTEMPTS:
            sleep(SECONDS_BETWEEN_ATTEMPTS)
    return False


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", required=True, help="the deployed /api/assess URL")
    args = parser.parse_args(argv)

    key = os.environ.get("FUNCTION_KEY")
    if not key:
        parser.error("set FUNCTION_KEY to a function key for the application")
    return 0 if run(lambda: post(args.url, key)) else 1


if __name__ == "__main__":
    sys.exit(main())
