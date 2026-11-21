from datetime import timedelta

import pytest
from fastzip.backup import backup_once, postgres_arguments, prune
from fastzip.config import settings
from fastzip.db import session_factory
from fastzip.models import BackupRun, ErrorEvent, now
from sqlalchemy import select


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
