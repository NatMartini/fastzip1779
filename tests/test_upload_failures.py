r"""Regression checks for upload rollback, object cleanup, and recovery.

Run from the repository root with dev dependencies installed. Reuse the isolated
PostgreSQL 17 container described in test_auth_security.py:

    docker start fastzip-security-qa
    docker exec fastzip-security-qa pg_isready -U fastzip -d fastzip_security_test

Wait for "accepting connections", then run this suite (expected: 4 passed):

    TEST_DATABASE_URL='postgresql+psycopg://fastzip:test-only@127.0.0.1:55432/fastzip_security_test' \
      .venv/bin/python -m pytest tests/test_upload_failures.py -v

Run all three added suites together (expected: 63 passed):

    TEST_DATABASE_URL='postgresql+psycopg://fastzip:test-only@127.0.0.1:55432/fastzip_security_test' \
      .venv/bin/python -m pytest tests/test_auth_security.py tests/test_authorization.py tests/test_upload_failures.py -v

    .venv/bin/ruff check tests/test_upload_failures.py
    .venv/bin/ruff format --check tests/test_upload_failures.py
    git diff --check
    docker stop fastzip-security-qa

Use only a disposable database: fixtures truncate application tables. Tests use
TestClient and Moto; no running app stack or FFmpeg installation is needed.
Failures are injected only inside each test. Cleanup failure intentionally leaves
an orphan in Moto; this suite does not verify the background orphan sweep.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from fastzip.api import app
from fastzip.db import session_factory
from fastzip.models import ErrorEvent, Job
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session as DBSession

from tests.conftest import upload


@pytest.fixture
def failure_client(client, account):
    # Exercise the real 503 handler instead of TestClient re-raising server errors.
    with TestClient(app, raise_server_exceptions=False) as browser:
        browser.cookies.update(client.cookies)
        browser.headers.update(client.headers)
        assert browser.get("/api/auth/me").json() == account
        yield browser


@pytest.mark.parametrize(
    "failure",
    [
        "storage_before_write",
        "storage_after_write",
        "database_commit",
        "database_commit_and_cleanup",
    ],
)
def test_failed_upload_preserves_existing_data_and_allows_retry(
    failure_client, image, s3, monkeypatch, caplog, failure
):
    # Preserve an existing successful upload so cleanup cannot silently delete other media.
    existing = upload(failure_client, image)
    assert existing.status_code == 202
    existing_id = existing.json()["id"]
    existing_keys = {obj["Key"] for obj in s3.objects()}
    assert len(existing_keys) == 1
    existing_key = existing_keys.pop()

    original_put = s3.put
    original_commit = DBSession.commit
    attempted_keys = []
    flushed_jobs = []
    failure_message = "simulated-internal-upload-failure"

    def failing_put(path, key, content_type):
        attempted_keys.append(key)
        if failure == "storage_before_write":
            raise RuntimeError(failure_message)
        original_put(path, key, content_type)
        if failure == "storage_after_write":
            # The object exists even though the caller receives a storage error.
            raise RuntimeError(failure_message)

    def failing_commit(db):
        if any(isinstance(row, Job) for row in db.new):
            # Exercise a real uncommitted INSERT before the simulated commit failure.
            db.flush()
            flushed_jobs.append(True)
            raise SQLAlchemyError(failure_message)
        return original_commit(db)

    def failing_delete(key):
        raise RuntimeError("simulated-internal-cleanup-failure")

    with monkeypatch.context() as patch:
        patch.setattr(s3, "put", failing_put)
        if failure.startswith("database_commit"):
            patch.setattr(DBSession, "commit", failing_commit)
        if failure == "database_commit_and_cleanup":
            patch.setattr(s3, "delete", failing_delete)
        response = upload(failure_client, image)

    assert response.status_code == 503
    assert response.json() == {"detail": "Service temporarily unavailable; please try again"}
    assert failure_message not in response.text
    assert failure_message not in caplog.text
    assert "simulated-internal-cleanup-failure" not in caplog.text
    assert len(attempted_keys) == 1
    failed_key = attempted_keys[0]
    assert failed_key != existing_key
    assert bool(flushed_jobs) is failure.startswith("database_commit")

    # Check committed rows through a fresh connection, independently of the failed request.
    with session_factory()() as db:
        assert [str(job.id) for job in db.scalars(select(Job))] == [existing_id]
        errors = list(db.scalars(select(ErrorEvent)))
        assert len(errors) == 1
        assert errors[0].service == "api"
        assert errors[0].message == "Request failed due to an internal service error"

    listing = failure_client.get("/api/jobs")
    assert listing.status_code == 200
    assert listing.json()["total"] == listing.json()["active_count"] == 1
    assert [item["id"] for item in listing.json()["items"]] == [existing_id]
    expected_keys = {existing_key}
    if failure == "database_commit_and_cleanup":
        expected_keys.add(failed_key)
    assert {obj["Key"] for obj in s3.objects()} == expected_keys
    assert s3.client.get_object(Bucket=s3.bucket, Key=existing_key)["Body"].read() == image

    # Patches are now removed: the same authenticated browser must be able to upload again.
    recovered = upload(failure_client, image)
    assert recovered.status_code == 202
    assert recovered.json()["status"] == "QUEUED"
    recovered_id = recovered.json()["id"]
    assert recovered_id != existing_id
    with session_factory()() as db:
        assert {str(job.id) for job in db.scalars(select(Job))} == {existing_id, recovered_id}
        recovered_key = db.get(Job, uuid.UUID(recovered_id)).original_key
    assert recovered_key != failed_key
    assert s3.client.get_object(Bucket=s3.bucket, Key=recovered_key)["Body"].read() == image
    assert {obj["Key"] for obj in s3.objects()} == expected_keys | {recovered_key}
    listing = failure_client.get("/api/jobs")
    assert listing.status_code == 200
    assert listing.json()["total"] == listing.json()["active_count"] == 2
