import io
import os

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from moto import mock_aws
from PIL import Image
from sqlalchemy import text


@pytest.fixture(scope="session", autouse=True)
def configure():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.exit(
            "Set TEST_DATABASE_URL to a disposable PostgreSQL database; SQLite cannot verify job locking."
        )
    os.environ.update(
        DATABASE_URL=url,
        DB_PASSWORD="",
        ENVIRONMENT="test",
        SECURE_COOKIES="false",
        PUBLIC_ORIGIN="http://testserver",
        S3_ENDPOINT="https://s3.amazonaws.com",
        S3_PUBLIC_ENDPOINT="https://s3.amazonaws.com",
        S3_ACCESS_KEY="testing",
        S3_SECRET_KEY="testing",
        S3_REGION="us-east-1",
        DO_TOKEN="",
    )
    from fastzip.config import settings
    from fastzip.db import engine

    settings.cache_clear()
    engine.cache_clear()
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine().dispose()


@pytest.fixture(autouse=True)
def clean_database(configure):
    from fastzip.db import engine

    with engine().begin() as db:
        db.execute(
            text(
                "TRUNCATE users, sessions, jobs, error_events, worker_heartbeats, rate_limits, backup_runs RESTART IDENTITY CASCADE"
            )
        )


@pytest.fixture(autouse=True)
def s3():
    from fastzip.config import settings
    from fastzip.storage import storage

    storage.cache_clear()
    with mock_aws():
        store = storage()
        store.client.create_bucket(Bucket=settings().s3_bucket)
        store.client.create_bucket(Bucket=settings().backup_bucket)
        yield store
    storage.cache_clear()


@pytest.fixture
def client():
    from fastzip.api import app

    with TestClient(app) as client:
        token = client.get("/api/auth/csrf").json()["csrf_token"]
        client.headers["X-CSRF-Token"] = token
        yield client


def register(client, email="alice@example.com"):
    response = client.post(
        "/api/auth/register", json={"email": email, "password": "correct-horse-battery"}
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def account(client):
    return register(client)


@pytest.fixture
def image():
    file = io.BytesIO()
    Image.new("RGB", (400, 300), (87, 134, 58)).save(file, "PNG")
    return file.getvalue()


def upload(client, image, **kwargs):
    return client.post(
        "/api/jobs",
        files={"file": ("sample.png", image, "image/png")},
        data={"preset": "balanced", **kwargs},
    )
