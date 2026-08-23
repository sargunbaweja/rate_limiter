"""Load test script: hits a live, running FastAPI server (not the limiter
classes directly) and compares all four rate-limiting algorithms.

Requires Redis to be running and reachable via REDIS_URL (defaults to
redis://localhost:6379/0). For each algorithm this script launches its own
`uvicorn` server process configured with that algorithm via env vars, fires
the same paced burst of requests at it over real HTTP, then tears the server
down before moving to the next algorithm.
"""

import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PORT = 8123
BASE_URL = f"http://127.0.0.1:{PORT}"
REQUEST_COUNT = 20
DELAY_SECONDS = 0.125

ALGORITHMS = ["fixed_window", "sliding_window", "sliding_window_counter", "token_bucket"]

COMMON_ENV = {
    "RATE_LIMIT_LIMIT": "5",
    "RATE_LIMIT_WINDOW_SECONDS": "1",
    "RATE_LIMIT_CAPACITY": "5",
    "RATE_LIMIT_REFILL_RATE": "5.0",
}


def start_server(algo: str) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(COMMON_ENV)
    env["RATE_LIMIT_ALGO"] = algo
    env.setdefault("REDIS_URL", "redis://localhost:6379/0")

    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def wait_for_health(timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{BASE_URL}/health", timeout=0.5)
            if resp.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Server did not become healthy in time")


def stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def run_burst(client_key: str) -> list[tuple[float, int]]:
    results = []
    start = time.time()

    with httpx.Client(headers={"X-API-Key": client_key}) as client:
        for _ in range(REQUEST_COUNT):
            resp = client.get(f"{BASE_URL}/search", params={"q": "test"})
            elapsed = time.time() - start
            results.append((elapsed, resp.status_code))
            time.sleep(DELAY_SECONDS)

    return results


def print_results(algo: str, results: list[tuple[float, int]]) -> None:
    print(f"\n--- {algo} ---")
    allowed = 0
    for i, (elapsed, status) in enumerate(results, start=1):
        ok = status == 200
        allowed += ok
        label = "ALLOW" if ok else f"REJECT ({status})"
        print(f"[{elapsed:6.3f}s] request {i:2d}: {label}")
    print(f"total allowed: {allowed}/{len(results)}")


def run_load_test():
    all_results = {}

    for algo in ALGORITHMS:
        print(f"\n=== starting server with RATE_LIMIT_ALGO={algo} ===")
        proc = start_server(algo)
        try:
            wait_for_health()
            client_key = f"loadtest-{uuid.uuid4().hex[:8]}"
            results = run_burst(client_key)
            all_results[algo] = results
            print_results(algo, results)
        finally:
            stop_server(proc)

    print("\n=== summary (allowed / total, 8 req/s paced burst) ===")
    for algo, results in all_results.items():
        allowed = sum(1 for _, status in results if status == 200)
        print(f"{algo:24s} {allowed}/{len(results)}")


if __name__ == "__main__":
    run_load_test()
