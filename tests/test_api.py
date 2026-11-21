import base64
import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from fastzip.api import app
from fastzip.config import settings
from fastzip.db import session_factory
from fastzip.models import Job, Session, User, now
from fastzip.worker import claim, process
from PIL import Image
from sqlalchemy import select

from tests.conftest import register, upload


def test_csrf_and_auth(client):
    body = {"email": "alice@example.com", "password": "correct-horse-battery"}
    assert (
        client.post("/api/auth/register", json=body, headers={"X-CSRF-Token": ""}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/auth/register", json=body, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert client.post("/api/auth/register", json={**body, "role": "admin"}).status_code == 422
    register(client)
    with session_factory()() as db:
        account = db.scalar(select(User))
        session = db.scalar(select(Session))
        assert account.password_hash.startswith("$argon2id$")
        assert session.token_hash != client.cookies["fastzip_session"]
    assert client.get("/api/auth/me").json()["role"] == "user"
    stolen = client.cookies.get("fastzip_session")
    assert client.post("/api/auth/logout").status_code == 204
    with TestClient(app) as other:
        other.cookies.set("fastzip_session", stolen)
        assert other.get("/api/auth/me").status_code == 401


def test_sessions_survive_api_instances(client, account):
    with TestClient(app) as replica:
        replica.cookies.update(client.cookies)
        assert replica.get("/api/auth/me").json()["id"] == account["id"]


def test_image_flow_and_ownership(client, account, image, s3):
    response = upload(client, image)
    assert response.status_code == 202
    identifier = response.json()["id"]
    assert "original_key" not in response.json()
    assert client.get(f"/api/jobs/{identifier}/download").status_code == 409
    job = claim("worker-test")
    process(job, "worker-test")
    result = client.get(f"/api/jobs/{identifier}").json()
    assert result["status"] == "SUCCEEDED"
    assert result["processing_seconds"] > 0
    assert result["savings_percent"] >= 0
    download = client.get(f"/api/jobs/{identifier}/download").json()
    query = parse_qs(urlparse(download["url"]).query)
    assert query["X-Amz-Expires"] == ["300"]
    assert "filename*=" in query["response-content-disposition"][0]
    with TestClient(app) as other:
        other.headers["X-CSRF-Token"] = other.get("/api/auth/csrf").json()["csrf_token"]
        register(other, "bob@example.com")
        assert other.get(f"/api/jobs/{identifier}").status_code == 404
        assert other.get(f"/api/jobs/{identifier}/download").status_code == 404
        assert other.get("/api/admin/overview").status_code == 403
    assert client.get("/api/jobs").json()["total"] == 1


def test_original_fallback_and_expiry(client, account, image):
    identifier = upload(client, image).json()["id"]
    with session_factory()() as db, db.begin():
        job = db.get(Job, uuid.UUID(identifier))
        job.status = "SUCCEEDED"
        job.output_size = job.original_size + 10
    result = client.get(f"/api/jobs/{identifier}/download").json()
    assert result["is_original"] is True
    with session_factory()() as db, db.begin():
        db.get(Job, uuid.UUID(identifier)).expires_at = now() + timedelta(seconds=60)
    near_expiry = client.get(f"/api/jobs/{identifier}/download").json()
    assert 0 < near_expiry["expires_in"] <= 60
    assert int(parse_qs(urlparse(near_expiry["url"]).query)["X-Amz-Expires"][0]) <= 60
    with session_factory()() as db, db.begin():
        db.get(Job, uuid.UUID(identifier)).expires_at = now() - timedelta(seconds=1)
    assert client.get(f"/api/jobs/{identifier}").json()["status"] == "EXPIRED"
    assert client.get(f"/api/jobs/{identifier}/download?kind=original").status_code == 410


def test_bad_uploads(client, account, image):
    assert upload(client, b"not an image").status_code == 422
    bad_crc = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aPioAAAAASUVORK5CYII="
    )
    assert upload(client, bad_crc).status_code == 422
    assert upload(client, image[:50]).status_code == 422
    assert upload(client, b"").status_code == 422
    assert upload(client, image, preset="arbitrary").status_code == 422
    assert upload(client, image + b"x" * (20 * 1024 * 1024)).status_code == 422
    oversized = io.BytesIO()
    Image.new("RGB", (5100, 5000)).save(oversized, "PNG")
    assert upload(client, oversized.getvalue()).status_code == 422


def test_concurrent_account_limit(client, account, image):
    cookies, headers = dict(client.cookies), dict(client.headers)

    def submit(_):
        with TestClient(app) as replica:
            replica.cookies.update(cookies)
            replica.headers.update(headers)
            return upload(replica, image).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        statuses = list(pool.map(submit, range(6)))
    assert statuses.count(202) == 3
    assert statuses.count(409) == 3
    page = client.get("/api/jobs?offset=3").json()
    assert page == {"items": [], "total": 3, "active_count": 3, "active_limit": 3}


def test_rate_limit_is_database_backed(client, monkeypatch):
    monkeypatch.setattr(settings(), "auth_rate_limit", 2)
    body = {"email": "missing@example.com", "password": "wrong-password-here"}
    assert client.post("/api/auth/login", json=body).status_code == 401
    assert client.post("/api/auth/login", json=body).status_code == 401
    assert client.post("/api/auth/login", json=body).status_code == 429


def test_admin_only_sees_metadata(client, account, image):
    identifier = upload(client, image).json()["id"]
    with session_factory()() as db, db.begin():
        db.get(Job, uuid.UUID(identifier)).status = "EXPIRED"  # Reconciled by restore.
        db.add(User(email="admin@example.com", password_hash="unused", role="admin"))
        admin = db.scalar(select(User).where(User.email == "admin@example.com"))
        from fastzip.security import digest

        db.add(
            Session(
                token_hash=digest("session:admin-test"),
                user_id=admin.id,
                expires_at=now() + timedelta(days=1),
            )
        )
    with TestClient(app) as operator:
        operator.cookies.set("fastzip_session", "admin-test")
        overview = operator.get("/api/admin/overview")
        assert overview.status_code == 200
        assert overview.json()["jobs_by_status"]["EXPIRED"] == 1
        metadata = operator.get("/api/admin/jobs").json()["items"][0]
        assert "original_key" not in metadata and "filename" not in metadata
        assert operator.get(f"/api/jobs/{identifier}/download?kind=original").status_code == 404
        assert operator.get("/api/admin/errors?since=2026-01-01T00:00:00").status_code == 422
        assert operator.get("/api/admin/metrics").json()["configured"] is False
