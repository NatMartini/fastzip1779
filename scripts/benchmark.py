"""Run a fixed workload with 1, 2, and 4 workers; retain real measured results."""

import argparse
import json
import math
import secrets
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=12)
    parser.add_argument("--workers", type=int, required=True, help="Actual configured worker count")
    parser.add_argument("--accounts-file", type=Path, default=Path("work/benchmark-accounts.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.jobs < 1 or args.workers < 1:
        parser.error("Jobs and workers must be positive")
    content = args.file.read_bytes()
    accounts = json.loads(args.accounts_file.read_text()) if args.accounts_file.exists() else []
    clients, jobs = [], []
    try:
        # Reuse accounts between runs so fair comparisons do not hit registration limits.
        for index in range(math.ceil(args.jobs / 3)):
            client = httpx.Client(base_url=args.base_url, timeout=90)
            clients.append(client)
            client.headers["X-CSRF-Token"] = client.get("/api/auth/csrf").json()["csrf_token"]
            existing = index < len(accounts)
            credentials = (
                accounts[index]
                if existing
                else {
                    "email": f"bench-{secrets.token_hex(8)}@example.com",
                    "password": secrets.token_urlsafe(24),
                }
            )
            response = client.post(
                "/api/auth/" + ("login" if existing else "register"), json=credentials
            )
            response.raise_for_status()
            if not existing:
                accounts.append(credentials)
                args.accounts_file.parent.mkdir(parents=True, exist_ok=True)
                args.accounts_file.touch(mode=0o600, exist_ok=True)
                args.accounts_file.write_text(json.dumps(accounts))
        started = time.monotonic()
        for index in range(args.jobs):
            client = clients[index // 3]
            response = client.post(
                "/api/jobs", data={"preset": "balanced"}, files={"file": (args.file.name, content)}
            )
            response.raise_for_status()
            jobs.append((client, response.json()["id"]))
        deadline = time.monotonic() + max(600, args.jobs * 330)
        while True:
            results = []
            for client, identifier in jobs:
                response = client.get(f"/api/jobs/{identifier}")
                response.raise_for_status()
                results.append(response.json())
            if all(j["status"] in {"SUCCEEDED", "FAILED", "EXPIRED"} for j in results):
                break
            if time.monotonic() > deadline:
                raise TimeoutError("Benchmark did not finish")
            time.sleep(2)
        elapsed = time.monotonic() - started
        succeeded = sum(j["status"] == "SUCCEEDED" for j in results)
        report = {
            "workers": args.workers,
            "jobs": args.jobs,
            "succeeded": succeeded,
            "elapsed_seconds": round(elapsed, 3),
            "jobs_per_minute": round(succeeded / elapsed * 60, 3),
            "processing_seconds": [j["processing_seconds"] for j in results],
            "attempts": [j["attempts"] for j in results],
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        for client in clients:
            client.close()


if __name__ == "__main__":
    main()
