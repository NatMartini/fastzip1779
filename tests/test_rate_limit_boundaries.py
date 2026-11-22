"""Rate-window boundaries and atomic counts against real PostgreSQL connections."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from fastzip import security
from fastzip.api import app
from fastzip.config import settings
from fastzip.db import session_factory
from fastzip.models import RateLimit
from sqlalchemy import select


@pytest.fixture
def clock(monkeypatch):
    stamp = [1_800_000_000]  # Exact common boundary for 60/900/3600-second windows.
    # Replace only the security module binding; avoid changing HTTP cookie or SDK clocks.
    monkeypatch.setattr(security, "time", SimpleNamespace(time=lambda: stamp[0]))
    return stamp


def test_limit_last_allowed_attempt_and_next_window(clock):
    key = "window-boundary"
    for _ in range(2):
        with session_factory()() as db:
            security.rate_limit(db, key, limit=2, seconds=60)
    clock[0] += 59
    with session_factory()() as db, pytest.raises(HTTPException) as rejected:
        security.rate_limit(db, key, limit=2, seconds=60)
    assert rejected.value.status_code == 429
    assert rejected.value.headers["Retry-After"] == "1"
    clock[0] += 1
    with session_factory()() as db:
        security.rate_limit(db, key, limit=2, seconds=60)
        rows = list(db.scalars(select(RateLimit).order_by(RateLimit.window)))
        assert [(row.window, row.count) for row in rows] == [(1_800_000_000, 3), (1_800_000_060, 1)]
        assert all(row.key == security.digest(key) for row in rows)


def test_concurrent_connections_share_one_atomic_allowance(clock):
    def attempt(_):
        with session_factory()() as db:
            try:
                security.rate_limit(db, "shared-key", limit=3, seconds=60)
                return 200
            except HTTPException as exc:
                return exc.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(attempt, range(8)))
    assert statuses.count(200) == 3
    assert statuses.count(429) == 5
    with session_factory()() as db:
        row = db.scalar(select(RateLimit))
        assert row.count == 8  # Blocked attempts are persisted as well.


def test_registration_limit_resets_at_exact_hour(client, monkeypatch, clock):
    monkeypatch.setattr(settings(), "registration_rate_limit", 2)
    for index in range(2):
        response = client.post(
            "/api/auth/register",
            json={"email": f"boundary-{index}@example.com", "password": "valid-password-here"},
        )
        assert response.status_code == 201
    clock[0] += 3599
    body = {"email": "after-limit@example.com", "password": "valid-password-here"}
    response = client.post("/api/auth/register", json=body)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    clock[0] += 1
    assert client.post("/api/auth/register", json=body).status_code == 201


def test_login_ip_limit_cannot_be_bypassed_by_changing_email(client, monkeypatch, clock):
    monkeypatch.setattr(settings(), "auth_rate_limit", 1)
    for index in range(3):
        response = client.post(
            "/api/auth/login",
            json={"email": f"missing-{index}@example.com", "password": "wrong-password-here"},
        )
        assert response.status_code == 401
    response = client.post(
        "/api/auth/login",
        json={"email": "missing-3@example.com", "password": "wrong-password-here"},
    )
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "900"
    # A different IP has its own allowance when authenticating a different account.
    with TestClient(app, client=("198.51.100.20", 5000)) as other:
        other.headers["X-CSRF-Token"] = other.get("/api/auth/csrf").json()["csrf_token"]
        assert (
            other.post(
                "/api/auth/login",
                json={"email": "fresh@example.com", "password": "wrong-password-here"},
            ).status_code
            == 401
        )


def test_account_limit_is_shared_across_ips_and_email_case(client, monkeypatch, clock):
    monkeypatch.setattr(settings(), "auth_rate_limit", 1)
    assert (
        client.post(
            "/api/auth/login",
            json={"email": "alice@example.com", "password": "wrong-password-here"},
        ).status_code
        == 401
    )
    with TestClient(app, client=("198.51.100.21", 5001)) as other:
        other.headers["X-CSRF-Token"] = other.get("/api/auth/csrf").json()["csrf_token"]
        response = other.post(
            "/api/auth/login",
            json={"email": "ALICE@EXAMPLE.COM", "password": "wrong-password-here"},
        )
        assert response.status_code == 429


def test_rejected_attempts_keep_correct_retry_after_near_window_end(client, monkeypatch, clock):
    monkeypatch.setattr(settings(), "auth_rate_limit", 1)
    body = {"email": "missing@example.com", "password": "wrong-password-here"}
    assert client.post("/api/auth/login", json=body).status_code == 401
    clock[0] += 899
    response = client.post("/api/auth/login", json=body)
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "1"
    clock[0] += 1
    assert client.post("/api/auth/login", json=body).status_code == 401
