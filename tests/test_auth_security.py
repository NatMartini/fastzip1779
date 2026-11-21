"""Regression checks for session lifecycle and browser request protections.

Run from the repository root with Docker running and dev dependencies installed.
Use only a disposable database: the test fixtures truncate application tables.

First run: create an isolated PostgreSQL 17 container (no app stack required):

    docker run -d --name fastzip-security-qa \
      -e POSTGRES_USER=fastzip \
      -e POSTGRES_PASSWORD=test-only \
      -e POSTGRES_DB=fastzip_security_test \
      -p 127.0.0.1:55432:5432 \
      postgres:17-bookworm

For later runs, start the existing container instead:

    docker start fastzip-security-qa

Wait until this command reports "accepting connections":

    docker exec fastzip-security-qa pg_isready -U fastzip -d fastzip_security_test

Run this focused suite (expected: 30 passed) and static checks:

    TEST_DATABASE_URL='postgresql+psycopg://fastzip:test-only@127.0.0.1:55432/fastzip_security_test' \
      .venv/bin/python -m pytest tests/test_auth_security.py -v
    .venv/bin/ruff check tests/test_auth_security.py
    .venv/bin/ruff format --check tests/test_auth_security.py
    git diff --check

Stop the test database when finished:

    docker stop fastzip-security-qa

The API uses TestClient and object storage uses Moto; these checks do not require
running API, worker, or MinIO services.
"""

from datetime import timedelta
from http.cookies import SimpleCookie

import pytest
from fastapi.testclient import TestClient
from fastzip.api import app
from fastzip.config import settings
from fastzip.db import session_factory
from fastzip.models import Session, now
from fastzip.security import CSRF_COOKIE, SESSION_COOKIE, digest


def test_expired_session_is_rejected(client, account):
    token = client.cookies[SESSION_COOKIE]
    with session_factory()() as db, db.begin():
        db.get(Session, digest("session:" + token)).expires_at = now() - timedelta(seconds=1)

    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json() == {"detail": "Please sign in"}


def test_login_rotates_session_and_rejects_old_token(client, account):
    old_token = client.cookies[SESSION_COOKIE]
    response = client.post(
        "/api/auth/login",
        json={"email": account["email"], "password": "correct-horse-battery"},
    )
    assert response.status_code == 200
    assert response.json() == account
    assert client.cookies[SESSION_COOKIE] != old_token
    assert client.get("/api/auth/me").json() == account

    with TestClient(app) as stale_browser:
        stale_browser.cookies.set(SESSION_COOKIE, old_token)
        assert stale_browser.get("/api/auth/me").status_code == 401


def test_failed_login_does_not_disclose_account_or_replace_session(client, account):
    token = client.cookies[SESSION_COOKIE]
    responses = [
        client.post("/api/auth/login", json={"email": email, "password": "wrong-password-here"})
        for email in (account["email"], "missing@example.com")
    ]
    for response in responses:
        assert response.status_code == 401
        assert response.json() == {"detail": "Email or password is incorrect"}
        assert "set-cookie" not in response.headers
    assert client.cookies[SESSION_COOKIE] == token
    assert client.get("/api/auth/me").json() == account


@pytest.mark.parametrize("route", ["register", "login", "logout", "upload"])
@pytest.mark.parametrize(
    "violation",
    ["missing_header", "missing_cookie", "mismatch", "forged_signature", "cross_site", "origin"],
)
def test_state_changing_routes_reject_invalid_csrf(client, account, image, route, violation):
    headers = {}
    if violation == "missing_header":
        headers["X-CSRF-Token"] = ""
    elif violation == "missing_cookie":
        client.cookies.delete(CSRF_COOKIE)
    elif violation == "mismatch":
        headers["X-CSRF-Token"] = "different-token"
    elif violation == "forged_signature":
        # Matching cookie/header alone must not bypass the server-side signature check.
        client.cookies.delete(CSRF_COOKIE)
        client.cookies.set(CSRF_COOKIE, "forged-nonce.forged-signature")
        headers["X-CSRF-Token"] = "forged-nonce.forged-signature"
    elif violation == "cross_site":
        headers["Sec-Fetch-Site"] = "cross-site"
    else:
        headers["Origin"] = "https://untrusted.example"

    if route == "upload":
        response = client.post(
            "/api/jobs",
            files={"file": ("sample.png", image, "image/png")},
            data={"preset": "balanced"},
            headers=headers,
        )
    else:
        response = client.post(
            f"/api/auth/{route}",
            json={"email": "new@example.com", "password": "correct-horse-battery"},
            headers=headers,
        )
    assert response.status_code == 403
    assert client.get("/api/auth/me").json() == account
    assert client.get("/api/jobs").json()["total"] == 0


def test_same_origin_request_with_valid_csrf_is_accepted(client, account):
    response = client.post(
        "/api/auth/login",
        json={"email": account["email"], "password": "correct-horse-battery"},
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code == 200
    assert response.json() == account


@pytest.mark.parametrize("secure", [False, True], ids=["local", "https"])
def test_authentication_cookie_attributes(monkeypatch, secure):
    monkeypatch.setattr(settings(), "secure_cookies", secure)
    origin = "https://testserver" if secure else "http://testserver"
    with TestClient(app, base_url=origin) as browser:
        csrf_response = browser.get("/api/auth/csrf")
        assert csrf_response.status_code == 200
        browser.headers["X-CSRF-Token"] = csrf_response.json()["csrf_token"]
        registration = browser.post(
            "/api/auth/register",
            json={"email": "cookie@example.com", "password": "correct-horse-battery"},
        )
        assert registration.status_code == 201
        for response, name, max_age in (
            (csrf_response, CSRF_COOKIE, 86400),
            (registration, SESSION_COOKIE, settings().session_days * 86400),
        ):
            cookie = SimpleCookie()
            cookie.load(response.headers["set-cookie"])
            assert cookie[name]["httponly"]
            assert cookie[name]["samesite"].lower() == "lax"
            assert cookie[name]["path"] == "/"
            assert int(cookie[name]["max-age"]) == max_age
            assert bool(cookie[name]["secure"]) is secure

        logout = browser.post("/api/auth/logout")
        assert logout.status_code == 204
        deleted = SimpleCookie()
        for header in logout.headers.get_list("set-cookie"):
            deleted.load(header)
        for name in (SESSION_COOKIE, CSRF_COOKIE):
            assert deleted[name].value == ""
            assert deleted[name]["max-age"] == "0"
        assert browser.get("/api/auth/me").status_code == 401
