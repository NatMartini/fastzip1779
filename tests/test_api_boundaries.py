"""Request validation, content detection, and private-download edge cases."""

import subprocess
import uuid
from urllib.parse import unquote, urlparse

import pytest
from fastzip import media
from fastzip.db import session_factory
from fastzip.models import Job, Session, User
from fastzip.security import SESSION_COOKIE, hasher
from sqlalchemy import func, select

from tests.conftest import upload


@pytest.mark.parametrize("length", [11, 12, 128, 129])
def test_registration_password_length_boundaries(client, length):
    password = "p" * length
    response = client.post(
        "/api/auth/register", json={"email": "boundary@example.com", "password": password}
    )
    accepted = 12 <= length <= 128
    assert response.status_code == (201 if accepted else 422)
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(User)) == int(accepted)
        assert db.scalar(select(func.count()).select_from(Session)) == int(accepted)
        if accepted:
            assert hasher.verify(db.scalar(select(User)).password_hash, password)
    assert (SESSION_COOKIE in client.cookies) is accepted
    if accepted:
        login = client.post(
            "/api/auth/login", json={"email": "boundary@example.com", "password": password}
        )
        assert login.status_code == 200


@pytest.mark.parametrize("route", ["register", "login"])
@pytest.mark.parametrize(
    "body",
    [
        {"password": "valid-password-here"},
        {"email": "boundary@example.com"},
        {"email": "not-an-email", "password": "valid-password-here"},
        {"email": "boundary@example.com", "password": None},
    ],
    ids=["missing-email", "missing-password", "invalid-email", "null-password"],
)
def test_invalid_credentials_do_not_create_accounts_or_sessions(client, route, body):
    response = client.post("/api/auth/" + route, json=body)
    assert response.status_code == 422
    assert "set-cookie" not in response.headers
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0
        assert db.scalar(select(func.count()).select_from(Session)) == 0


def test_duplicate_registration_normalizes_email_and_preserves_session(client, account):
    token = client.cookies[SESSION_COOKIE]
    response = client.post(
        "/api/auth/register",
        json={"email": account["email"].upper(), "password": "another-valid-password"},
    )
    assert response.status_code == 409
    assert "set-cookie" not in response.headers
    assert client.cookies[SESSION_COOKIE] == token
    assert client.get("/api/auth/me").json() == account
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert hasher.verify(db.scalar(select(User)).password_hash, "correct-horse-battery")


@pytest.mark.parametrize("identifier", ["not-a-uuid", "123"])
def test_malformed_job_identifiers_are_rejected(client, account, identifier):
    assert client.get(f"/api/jobs/{identifier}").status_code == 422
    assert client.get(f"/api/jobs/{identifier}/download").status_code == 422


@pytest.mark.parametrize("query", ["offset=-1", "limit=0", "limit=101", "limit=not-a-number"])
def test_invalid_job_pagination_is_rejected(client, account, query):
    assert client.get("/api/jobs?" + query).status_code == 422


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [("pretend.mp4", "video/mp4"), ("pretend.jpg", "image/jpeg"), ("photo.png", "text/plain")],
)
def test_upload_type_comes_from_bytes_not_filename_or_mime(
    client, account, image, s3, filename, content_type
):
    response = client.post(
        "/api/jobs", files={"file": (filename, image, content_type)}, data={"preset": "balanced"}
    )
    assert response.status_code == 202
    assert response.json()["media_type"] == "image"
    with session_factory()() as db:
        job = db.get(Job, uuid.UUID(response.json()["id"]))
        assert job.original_key.endswith(".png")
        assert (
            s3.client.head_object(Bucket=s3.bucket, Key=job.original_key)["ContentType"]
            == "image/png"
        )


@pytest.mark.parametrize("failure", ["null-metadata", "probe-timeout"])
def test_probe_edge_failures_return_422_without_creating_objects(
    client, account, s3, monkeypatch, failure
):
    def failed_probe(*args, **kwargs):
        if failure == "probe-timeout":
            raise subprocess.TimeoutExpired(args[0], 15)
        return subprocess.CompletedProcess(args[0], 0, stdout=b"null")

    monkeypatch.setattr(media.subprocess, "run", failed_probe)
    response = client.post(
        "/api/jobs",
        files={"file": ("broken.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00" * 52, "video/mp4")},
        data={"preset": "balanced"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Cannot read MP4 metadata"}
    with session_factory()() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    assert not list(s3.objects())


@pytest.fixture
def downloadable_job(client, account, image, s3, tmp_path):
    response = upload(client, image)
    assert response.status_code == 202
    identifier = uuid.UUID(response.json()["id"])
    output_key = f"outputs/{identifier}/boundary/result.webp"
    result = tmp_path / "result.webp"
    result.write_bytes(b"a smaller encoded result")
    s3.put(result, output_key, "image/webp")
    with session_factory()() as db, db.begin():
        job = db.get(Job, identifier)
        job.status = "SUCCEEDED"
        job.output_key = output_key
        job.output_size = result.stat().st_size
        job.output_mime = "image/webp"
        return job


@pytest.mark.parametrize(
    ("size_change", "use_original", "original_is_smaller"),
    [(-1, False, False), (0, True, True), (1, True, True), (None, True, False)],
    ids=["smaller", "equal", "larger", "unrecorded-size"],
)
def test_result_selection_in_history_detail_and_download(
    client, downloadable_job, size_change, use_original, original_is_smaller
):
    job = downloadable_job
    with session_factory()() as db, db.begin():
        db.get(Job, job.id).output_size = (
            None if size_change is None else job.original_size + size_change
        )
    path = f"/api/jobs/{job.id}"
    history = client.get("/api/jobs").json()["items"][0]
    detail = client.get(path).json()
    assert history == detail
    assert detail["original_is_smaller"] is original_is_smaller
    response = client.get(path + "/download")
    assert response.status_code == 200
    result = response.json()
    assert result["is_original"] is use_original
    assert result["filename"] == (job.filename if use_original else "sample.webp")
    key = job.original_key if use_original else job.output_key
    assert unquote(urlparse(result["url"]).path).endswith("/" + key)
    original = client.get(path + "/download?kind=original").json()
    assert original["is_original"] is True
    assert original["filename"] == job.filename


@pytest.mark.parametrize("kind", ["original", "result"])
def test_missing_download_object_returns_gone(client, downloadable_job, s3, kind):
    job = downloadable_job
    s3.delete(job.original_key if kind == "original" else job.output_key)
    response = client.get(f"/api/jobs/{job.id}/download?kind={kind}")
    assert response.status_code == 410
    assert "url" not in response.json()
    # Losing one object must not prevent access to the other still-present object.
    other = "result" if kind == "original" else "original"
    assert client.get(f"/api/jobs/{job.id}/download?kind={other}").status_code == 200


def test_invalid_download_kind_is_rejected(client, downloadable_job):
    assert client.get(f"/api/jobs/{downloadable_job.id}/download?kind=anything").status_code == 422
