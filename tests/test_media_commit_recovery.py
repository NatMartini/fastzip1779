import io
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import urlsplit

import psycopg
import pytest
from fastzip import cleanup, worker
from fastzip.db import engine, session_factory
from fastzip.models import Job, now
from PIL import Image
from sqlalchemy import select

from tests.conftest import upload


def interrupt_media_commit(patch, s3, *, committed, verification_available):
    """Disconnect the publishing connection on either side of a real PostgreSQL COMMIT."""
    dialect = engine().dialect
    commit = dialect.do_commit
    put = s3.put
    armed = threading.local()
    keys = []

    def store(path, key, mime):
        put(path, key, mime)
        keys.append(key)
        armed.publishing = True

    def unavailable():
        raise psycopg.OperationalError("verification database unavailable")

    def disconnect(connection):
        if not getattr(armed, "publishing", False):
            return commit(connection)
        armed.publishing = False
        if committed:
            commit(connection)
        connection.close()
        if not verification_available:
            patch.setattr(cleanup, "session_factory", unavailable)
        raise psycopg.OperationalError("connection lost during commit")

    patch.setattr(s3, "put", store)
    patch.setattr(dialect, "do_commit", disconnect)
    return keys


def sweep_after_grace(monkeypatch):
    later = now() + timedelta(hours=3)
    monkeypatch.setattr(cleanup, "now", lambda: later)
    cleanup.cleanup_once()


def download_bytes(client, s3, job_id, kind, key):
    response = client.get(f"/api/jobs/{job_id}/download", params={"kind": kind})
    assert response.status_code == 200
    assert urlsplit(response.json()["url"]).path.endswith("/" + key)
    return s3.client.get_object(Bucket=s3.bucket, Key=key)["Body"].read()


@pytest.mark.parametrize("committed", [True, False], ids=["committed", "rolled-back"])
@pytest.mark.parametrize("verification_available", [True, False], ids=["verified", "unavailable"])
def test_upload_commit_disconnect(
    failure_client, image, s3, monkeypatch, committed, verification_available
):
    with monkeypatch.context() as patch:
        keys = interrupt_media_commit(
            patch, s3, committed=committed, verification_available=verification_available
        )
        response = upload(failure_client, image)

    (key,) = keys
    assert s3.exists(key) == (committed or not verification_available)
    assert response.status_code == (202 if committed and verification_available else 503)
    sweep_after_grace(monkeypatch)
    assert s3.exists(key) == committed
    with session_factory()() as db:
        rows = list(db.scalars(select(Job)))
    if committed:
        (job,) = rows
        assert job.original_key == key
        if verification_available:
            assert response.json()["id"] == str(job.id)
        assert download_bytes(failure_client, s3, job.id, "original", key) == image
    else:
        assert rows == []
        assert upload(failure_client, image).status_code == 202


@pytest.mark.parametrize("committed", [True, False], ids=["committed", "rolled-back"])
@pytest.mark.parametrize("verification_available", [True, False], ids=["verified", "unavailable"])
def test_result_commit_disconnect(
    client, account, image, s3, monkeypatch, committed, verification_available
):
    assert upload(client, image).status_code == 202
    job = worker.claim("worker-1")
    with monkeypatch.context() as patch:
        keys = interrupt_media_commit(
            patch, s3, committed=committed, verification_available=verification_available
        )
        worker.process(job, "worker-1")

    (key,) = keys
    assert s3.exists(key) == (committed or not verification_available)
    sweep_after_grace(monkeypatch)
    assert s3.exists(key) == committed
    assert download_bytes(client, s3, job.id, "original", job.original_key) == image
    with session_factory().begin() as db:
        current = db.get(Job, job.id)
        assert current.attempts == 1
        assert current.status == ("SUCCEEDED" if committed else "QUEUED")
        assert current.output_key == (key if committed else None)
        if not committed:
            current.available_at = now() - timedelta(seconds=1)
    if not committed:
        retry = worker.claim("worker-2")
        assert retry.id == job.id and retry.attempt_id != job.attempt_id
        worker.process(retry, "worker-2")
        with session_factory()() as db:
            current = db.get(Job, job.id)
            assert current.status == "SUCCEEDED" and current.output_key != key
            key = current.output_key
    with Image.open(io.BytesIO(download_bytes(client, s3, job.id, "result", key))) as result:
        result.load()
        assert result.format == "WEBP" and result.size == (400, 300)


@pytest.mark.parametrize("kind", ["original", "result"])
def test_orphan_sweep_during_publication(client, account, image, s3, monkeypatch, kind):
    if kind == "result":
        assert upload(client, image).status_code == 202
        job = worker.claim()
    put = s3.put
    stored, resume = threading.Event(), threading.Event()
    keys = []

    def pause_after_put(path, key, mime):
        put(path, key, mime)
        keys.append(key)
        stored.set()
        assert resume.wait(10)

    monkeypatch.setattr(s3, "put", pause_after_put)
    with ThreadPoolExecutor(max_workers=1) as pool:
        publication = (
            pool.submit(upload, client, image)
            if kind == "original"
            else pool.submit(worker.process, job)
        )
        try:
            assert stored.wait(10)
            sweep_after_grace(monkeypatch)
            (key,) = keys
            assert s3.exists(key)
        finally:
            resume.set()
        response = publication.result(timeout=10)
    job_id = uuid.UUID(response.json()["id"]) if kind == "original" else job.id
    assert download_bytes(client, s3, job_id, kind, key)


def test_stale_worker_removes_only_its_own_result(client, account, image, s3, monkeypatch):
    assert upload(client, image).status_code == 202
    old = worker.claim("old-worker")
    put = s3.put
    written = []

    def finish_replacement_first(path, key, mime):
        put(path, key, mime)
        written.append(key)
        if len(written) == 1:
            with session_factory().begin() as db:
                db.get(Job, old.id).lease_until = now() - timedelta(seconds=1)
            replacement = worker.claim("replacement")
            assert replacement.id == old.id and replacement.attempt_id != old.attempt_id
            worker.process(replacement, "replacement")

    monkeypatch.setattr(s3, "put", finish_replacement_first)
    worker.process(old, "old-worker")
    stale, winning = written
    assert not s3.exists(stale)
    assert download_bytes(client, s3, old.id, "original", old.original_key) == image
    assert download_bytes(client, s3, old.id, "result", winning)


def test_duplicate_finalization_preserves_published_bytes(client, account, image, s3, tmp_path):
    assert upload(client, image).status_code == 202
    job = worker.claim()
    worker.process(job)
    with session_factory()() as db:
        key = db.get(Job, job.id).output_key
    original_result = download_bytes(client, s3, job.id, "result", key)
    replay = tmp_path / "replay.webp"
    replay.write_bytes(b"different bytes")
    assert not worker.complete(job, key, replay.stat().st_size, "image/webp", 1, output_file=replay)
    assert download_bytes(client, s3, job.id, "result", key) == original_result
