"""A real HTTP upload → queue → worker → signed download smoke test."""

import argparse
import io
import json
import secrets
import time
from pathlib import Path

import httpx
from PIL import Image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--file", type=Path)
    args = parser.parse_args()
    started = time.monotonic()
    with httpx.Client(base_url=args.base_url, timeout=45) as client:
        deadline = time.monotonic() + args.timeout
        while True:
            try:
                client.get("/api/health/ready").raise_for_status()
                break
            except httpx.HTTPError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(2)
        token = client.get("/api/auth/csrf").json()["csrf_token"]
        client.headers["X-CSRF-Token"] = token
        response = client.post(
            "/api/auth/register",
            json={
                "email": f"smoke-{secrets.token_hex(8)}@example.com",
                "password": secrets.token_urlsafe(24),
            },
        )
        response.raise_for_status()
        if args.file:
            name, content = args.file.name, args.file.read_bytes()
        else:
            buffer = io.BytesIO()
            Image.new("RGB", (800, 600), (94, 144, 61)).save(buffer, "PNG")
            name, content = "smoke.png", buffer.getvalue()
        response = client.post(
            "/api/jobs", data={"preset": "balanced"}, files={"file": (name, content)}
        )
        response.raise_for_status()
        identifier = response.json()["id"]
        deadline = time.monotonic() + args.timeout
        while True:
            response = client.get(f"/api/jobs/{identifier}")
            response.raise_for_status()
            job = response.json()
            if job["status"] == "SUCCEEDED":
                break
            if job["status"] in {"FAILED", "EXPIRED"} or time.monotonic() > deadline:
                raise RuntimeError(f"Job did not succeed: {job['status']}")
            time.sleep(1)
        response = client.get(f"/api/jobs/{identifier}/download")
        response.raise_for_status()
        # Never log the signed URL: it grants temporary access to a private object.
        downloaded = httpx.get(response.json()["url"], timeout=30)
        downloaded.raise_for_status()
        assert downloaded.content
        assert len(downloaded.content) == min(job["original_size"], job["output_size"])
        print(
            json.dumps(
                {
                    "job_id": identifier,
                    "status": job["status"],
                    "original_bytes": len(content),
                    "downloaded_bytes": len(downloaded.content),
                    "seconds": round(time.monotonic() - started, 3),
                }
            )
        )


if __name__ == "__main__":
    main()
