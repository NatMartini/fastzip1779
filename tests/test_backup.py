import json
import sys
from datetime import timedelta

import pytest
from botocore.exceptions import ClientError
from botocore.stub import Stubber
from fastzip.backup import audit_restored_media, backup_once, postgres_arguments, prune
from fastzip.cli import main
from fastzip.config import settings
from fastzip.db import session_factory
from fastzip.models import BackupRun, ErrorEvent, Job, now
from fastzip.worker import claim, complete
from sqlalchemy import select

from tests.conftest import upload


def test_backup_credentials_do_not_appear_in_arguments():
    args, env = postgres_arguments(
        "postgresql+psycopg://test:very-secret@db:5432/fastzip?sslmode=require"
    )
    socket_args, _ = postgres_arguments("postgresql+psycopg://postgres@/postgres?host=/tmp/socket")
    assert socket_args[1] == "/tmp/socket"
    assert "very-secret" not in " ".join(args)
    assert env["PGPASSWORD"] == "very-secret" and env["PGSSLMODE"] == "require"


def test_backup_failure_is_visible(monkeypatch):
    monkeypatch.setattr(settings(), "backup_age_recipient", "")
    with pytest.raises(ValueError):
        backup_once()
    with session_factory()() as db:
        assert db.scalar(select(BackupRun)).status == "FAILED"
        assert db.scalar(select(ErrorEvent)).service == "backup"


def test_retention_keeps_latest_24_hourly_and_7_daily():
    class Store:
        removed = []

        def objects(self, prefix):
            return [{"Key": prefix + (now() - timedelta(hours=n)).isoformat()} for n in range(40)]

        def delete(self, key):
            self.removed.append(key)

    store = Store()
    prune(store, "hourly/", 24)
    assert len(store.removed) == 16
    store.removed.clear()
    prune(store, "daily/", 7)
    assert len(store.removed) == 33


def test_restored_media_audit_includes_expired_and_queued_jobs(
    client, account, image, s3, monkeypatch, capsys
):
    for expired in (True, False):
        assert upload(client, image).status_code == 202
        job = claim()
        key = f"outputs/{job.id}/result.webp"
        s3.client.put_object(Bucket=s3.bucket, Key=key, Body=b"abc")
        assert complete(job, key, 3 if expired else 4, "image/webp", 1)
        if expired:
            expired_id = job.id
            s3.delete(job.original_key)
            with session_factory()() as db, db.begin():
                db.get(Job, expired_id).expires_at = now() - timedelta(seconds=1)
    assert upload(client, image).status_code == 202

    monkeypatch.setattr(
        sys, "argv", ["fastzip", "audit-restored-media", "--database-url", settings().database_url]
    )
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 1
    assert json.loads(capsys.readouterr().out) == {
        "jobs_checked": 3,
        "expired_jobs": 1,
        "unexpired_inconsistent_jobs": 1,
        "originals": {"matched": 2, "missing": 1, "size_mismatch": 0, "unreferenced": 0},
        "outputs": {"matched": 1, "missing": 0, "size_mismatch": 1, "unreferenced": 1},
    }
    with session_factory()() as db:
        assert db.get(Job, expired_id).status == "SUCCEEDED"  # The audit only reads.


def test_media_size_distinguishes_missing_empty_and_forbidden(s3):
    key = "originals/test/zero"
    assert s3.size_bytes(key) is None
    s3.client.put_object(Bucket=s3.bucket, Key=key, Body=b"")
    assert s3.exists(key) and s3.size_bytes(key) == 0
    with Stubber(s3.client) as stub:
        stub.add_client_error(
            "head_object", service_error_code="AccessDenied", http_status_code=403
        )
        with pytest.raises(ClientError, match="AccessDenied"):
            s3.size_bytes(key)


def test_restored_media_audit_rejects_a_missing_bucket(s3, monkeypatch):
    monkeypatch.setattr(settings(), "s3_bucket", "missing-restore-media")
    with session_factory()() as db, pytest.raises(ClientError) as error:
        audit_restored_media(db)
    assert error.value.operation_name == "HeadBucket"
    assert error.value.response["ResponseMetadata"]["HTTPStatusCode"] == 404
