"""Verify real MinIO honors signed expiry using a disposable two-second test URL."""

import time
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from fastzip.config import settings
from fastzip.storage import Storage

settings().s3_public_endpoint = settings().s3_endpoint
store = Storage()
key = "smoke/link-expiry-check"
with TemporaryDirectory() as directory:
    path = Path(directory) / "test.txt"
    path.write_text("temporary signature check")
    store.put(path, key, "text/plain")
    try:
        url = store.signed_url(key, "test.txt", expires_in=2)
        assert httpx.get(url, timeout=10).status_code == 200
        time.sleep(4)
        assert httpx.get(url, timeout=10).status_code == 403
        print("MinIO accepted the valid signature and rejected it after expiration")
    finally:
        store.delete(key)
