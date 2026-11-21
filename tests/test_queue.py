import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from fastzip.cleanup import cleanup_once, reconcile
from fastzip.db import session_factory
from fastzip.models import ErrorEvent, Job, now
from fastzip.worker import claim, complete, fail, heartbeat
from sqlalchemy import select

from tests.conftest import upload


def test_claims_are_exclusive(client, account, image):
    for _ in range(3):
        assert upload(client, image).status_code == 202
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda index: claim(f"w{index}"), range(4)))
    ids = [j.id for j in jobs if j]
    assert len(ids) == 3 and len(set(ids)) == 3


def test_expired_lease_reclaimed_and_stale_worker_fenced(client, account, image):
    upload(client, image)
    old = claim("crashed-worker")
    with session_factory()() as db, db.begin():
        db.get(Job, old.id).lease_until = now() - timedelta(seconds=1)
    replacement = claim("replacement-worker")
    assert replacement.id == old.id and replacement.attempt_id != old.attempt_id
    assert replacement.attempts == 2
    assert not complete(old, "stale-output", 10, "image/webp", 1)
    assert not heartbeat("crashed-worker", old)
    assert complete(replacement, "current-output", 20, "image/webp", 2)
    with session_factory()() as db:
        assert db.get(Job, old.id).output_key == "current-output"


def test_retry_limit_and_error_log(client, account, image):
    upload(client, image)
    for attempt in range(3):
        job = claim("faulty-worker")
        assert job.attempts == attempt + 1
        fail(job)
        with session_factory()() as db, db.begin():
            db.get(Job, job.id).available_at = now() - timedelta(seconds=1)
    assert claim() is None
    with session_factory()() as db:
        assert db.get(Job, job.id).status == "FAILED"
        assert len(list(db.scalars(select(ErrorEvent)))) == 3


def test_crash_retry_exhaustion(client, account, image):
    upload(client, image)
    for _ in range(3):
        job = claim()
        with session_factory()() as db, db.begin():
            db.get(Job, job.id).lease_until = now() - timedelta(seconds=1)
    assert claim() is None
    with session_factory()() as db:
        assert db.get(Job, job.id).status == "FAILED"


def test_cleanup_and_restore_reconciliation(client, account, image, s3):
    identifier = uuid.UUID(upload(client, image).json()["id"])
    with session_factory()() as db:
        key = db.get(Job, identifier).original_key
    assert s3.exists(key)
    s3.delete(key)
    assert reconcile() == 1
    cleanup_once()
    cleanup_once()
    with session_factory()() as db:
        job = db.get(Job, identifier)
        assert job.status == "EXPIRED" and job.media_deleted_at is not None


def test_expiry_prevents_late_completion(client, account, image):
    upload(client, image)
    job = claim()
    with session_factory()() as db, db.begin():
        db.get(Job, job.id).expires_at = now() - timedelta(seconds=1)
    assert not complete(job, "too-late", 20, "image/webp", 1)
    cleanup_once()
    with session_factory()() as db:
        assert db.get(Job, job.id).status == "EXPIRED"
