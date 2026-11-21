r"""Regression checks for job ownership and administrator API permissions.

Run from the repository root with dev dependencies installed. Reuse the isolated
PostgreSQL 17 container described in test_auth_security.py:

    docker start fastzip-security-qa
    docker exec fastzip-security-qa pg_isready -U fastzip -d fastzip_security_test

Wait for "accepting connections", then run this suite (expected: 29 passed):

    TEST_DATABASE_URL='postgresql+psycopg://fastzip:test-only@127.0.0.1:55432/fastzip_security_test' \
      .venv/bin/python -m pytest tests/test_authorization.py -v

Run both new security suites together (expected: 59 passed):

    TEST_DATABASE_URL='postgresql+psycopg://fastzip:test-only@127.0.0.1:55432/fastzip_security_test' \
      .venv/bin/python -m pytest tests/test_auth_security.py tests/test_authorization.py -v

    .venv/bin/ruff check tests/test_authorization.py
    .venv/bin/ruff format --check tests/test_authorization.py
    git diff --check
    docker stop fastzip-security-qa

Use only a disposable database: fixtures truncate application tables. TestClient,
Moto, and the in-process image worker avoid requiring a running app stack or FFmpeg.
These checks do not establish browser, real object storage, or deployment results.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from fastzip.api import app
from fastzip.db import session_factory
from fastzip.models import User
from fastzip.worker import claim, process

from tests.conftest import register, upload


@pytest.fixture
def visitor(request):
    """Use real sessions; provision the admin role directly in the disposable DB."""
    role = request.param
    with TestClient(app) as browser:
        response = browser.get("/api/auth/csrf")
        assert response.status_code == 200
        browser.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        if role != "anonymous":
            account = register(browser, "visitor@example.com")
            if role == "admin":
                # Test setup only: the application exposes no HTTP role-granting endpoint.
                with session_factory()() as db, db.begin():
                    db.get(User, uuid.UUID(account["id"])).role = "admin"
            assert browser.get("/api/auth/me").json()["role"] == role
        yield browser


@pytest.fixture
def completed_job(client, account, image):
    response = upload(client, image)
    assert response.status_code == 202
    identifier = response.json()["id"]
    job = claim("authorization-test")
    assert job is not None and str(job.id) == identifier
    process(job, "authorization-test")
    response = client.get(f"/api/jobs/{identifier}")
    assert response.status_code == 200
    assert response.json()["status"] == "SUCCEEDED"
    return response.json()


@pytest.mark.parametrize("endpoint", ["overview", "jobs", "errors", "metrics"])
@pytest.mark.parametrize("visitor", ["anonymous", "user", "admin"], indirect=True)
def test_admin_endpoint_permission_matrix(visitor, endpoint):
    identity = visitor.get("/api/auth/me")
    response = visitor.get(f"/api/admin/{endpoint}")
    if identity.status_code == 401:
        assert response.status_code == 401
        assert response.json() == {"detail": "Please sign in"}
    elif identity.json()["role"] == "user":
        assert response.status_code == 403
        assert response.json() == {"detail": "Administrator access required"}
    else:
        assert response.status_code == 200


@pytest.mark.parametrize("endpoint", ["list", "detail", "original", "result", "upload"])
@pytest.mark.parametrize("visitor", ["anonymous"], indirect=True)
def test_anonymous_job_access_requires_login(visitor, completed_job, image, endpoint):
    path = f"/api/jobs/{completed_job['id']}"
    if endpoint == "upload":
        # visitor has a valid CSRF token, so this exercises authentication rejection.
        response = upload(visitor, image)
    elif endpoint == "list":
        response = visitor.get("/api/jobs")
    elif endpoint == "detail":
        response = visitor.get(path)
    else:
        response = visitor.get(f"{path}/download?kind={endpoint}")
    assert response.status_code == 401
    assert response.json() == {"detail": "Please sign in"}


@pytest.mark.parametrize("suffix", ["", "/download?kind=original", "/download?kind=result"])
@pytest.mark.parametrize("visitor", ["user", "admin"], indirect=True)
def test_other_accounts_cannot_access_job_or_discover_its_existence(visitor, completed_job, suffix):
    response = visitor.get(f"/api/jobs/{completed_job['id']}{suffix}")
    missing = visitor.get(f"/api/jobs/{uuid.uuid4()}{suffix}")
    assert response.status_code == missing.status_code == 404
    assert response.json() == missing.json() == {"detail": "Job not found"}


@pytest.mark.parametrize("kind", ["detail", "original", "result"])
def test_owner_can_access_completed_job_and_both_downloads(client, completed_job, kind):
    path = f"/api/jobs/{completed_job['id']}"
    if kind == "detail":
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == completed_job
    else:
        response = client.get(f"{path}/download?kind={kind}")
        assert response.status_code == 200
        download = response.json()
        assert download["is_original"] is (kind == "original")
        assert 0 < download["expires_in"] <= 300
        assert download["url"]


@pytest.mark.parametrize("visitor", ["user", "admin"], indirect=True)
def test_job_lists_and_pagination_only_include_own_jobs(visitor, client, completed_job, image):
    response = upload(visitor, image)
    assert response.status_code == 202
    visitor_job = response.json()["id"]

    for browser, identifier, active_count in (
        (client, completed_job["id"], 0),
        (visitor, visitor_job, 1),
    ):
        response = browser.get("/api/jobs?limit=1")
        assert response.status_code == 200
        page = response.json()
        assert [item["id"] for item in page["items"]] == [identifier]
        assert page["total"] == 1
        assert page["active_count"] == active_count
        response = browser.get("/api/jobs?offset=1&limit=1")
        assert response.status_code == 200
        assert response.json() == {**page, "items": []}


@pytest.mark.parametrize("visitor", ["admin"], indirect=True)
def test_admin_job_listing_exposes_only_approved_metadata(visitor, completed_job, account):
    response = visitor.get("/api/admin/jobs")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    metadata = items[0]
    # An explicit allowlist catches accidental exposure of filenames, storage keys, or URLs.
    assert set(metadata) == {
        "id",
        "user_id",
        "status",
        "media_type",
        "preset",
        "created_at",
        "attempts",
        "original_size",
        "output_size",
    }
    assert metadata["id"] == completed_job["id"]
    assert metadata["user_id"] == account["id"]
    assert metadata["status"] == "SUCCEEDED"
