"""Password validation, damaged stored hashes, and opportunistic hash upgrades."""

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from fastzip.api import app
from fastzip.db import session_factory
from fastzip.models import User
from fastzip.security import SESSION_COOKIE, hasher
from sqlalchemy import select


@pytest.mark.parametrize("length", [11, 129])
def test_invalid_login_password_length_preserves_current_session(client, account, length):
    token = client.cookies[SESSION_COOKIE]
    response = client.post(
        "/api/auth/login", json={"email": account["email"], "password": "p" * length}
    )
    assert response.status_code == 422
    assert "set-cookie" not in response.headers
    assert client.cookies[SESSION_COOKIE] == token
    assert client.get("/api/auth/me").json() == account


@pytest.mark.parametrize("damaged_hash", ["", "not-an-argon2-hash", "$argon2id$invalid"])
def test_damaged_stored_password_hash_rejects_login_safely(client, account, damaged_hash):
    with session_factory()() as db, db.begin():
        db.scalar(select(User)).password_hash = damaged_hash
    with TestClient(app, raise_server_exceptions=False) as browser:
        browser.cookies.update(client.cookies)
        browser.headers.update(client.headers)
        response = browser.post(
            "/api/auth/login", json={"email": account["email"], "password": "correct-horse-battery"}
        )
    assert response.status_code == 401
    assert response.json() == {"detail": "Email or password is incorrect"}
    assert "set-cookie" not in response.headers


def test_successful_login_upgrades_an_outdated_hash(client, account):
    password = "correct-horse-battery"
    legacy = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1).hash(password)
    assert hasher.check_needs_rehash(legacy)
    with session_factory()() as db, db.begin():
        db.scalar(select(User)).password_hash = legacy
    response = client.post(
        "/api/auth/login", json={"email": account["email"], "password": password}
    )
    assert response.status_code == 200
    with session_factory()() as db:
        stored = db.scalar(select(User)).password_hash
        assert stored != legacy
        assert hasher.verify(stored, password)
        assert not hasher.check_needs_rehash(stored)
