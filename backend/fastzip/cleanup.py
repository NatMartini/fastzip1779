import logging
import signal
import threading
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import delete, select, text, update

from fastzip.db import record_error, session_factory
from fastzip.models import Heartbeat, Job, RateLimit, Session, now
from fastzip.storage import storage


def lock_media_key(db, key: str):
    # Hold the same per-key lock as orphan deletion until the object reference commits.
    db.execute(text("SELECT pg_advisory_xact_lock(779003, hashtext(:key))"), {"key": key})


def discard_unreferenced_media(key: str) -> Job | None:
    """Keep referenced or in-flight media and return the referencing job when visible."""
    with session_factory().begin() as db:
        # The reference query needs a snapshot taken after the publication lock was acquired.
        db.connection(execution_options={"isolation_level": "READ COMMITTED"})
        if not db.scalar(
            text("SELECT pg_try_advisory_xact_lock(779003, hashtext(:key))"), {"key": key}
        ):
            return None  # Publication is still in flight. The orphan sweep can retry later.
        owner = db.scalar(
            select(Job)
            .where(
                Job.media_deleted_at.is_(None), (Job.original_key == key) | (Job.output_key == key)
            )
            .limit(1)
        )
        if owner is None:
            storage().delete(key)
        return owner


def cleanup_once():
    with session_factory()() as db, db.begin():
        if not db.scalar(text("SELECT pg_try_advisory_xact_lock(779001)")):
            return
        stamp = now()
        db.execute(
            update(Job).where(Job.expires_at <= stamp).values(status="EXPIRED", lease_until=None)
        )
        expired = list(
            db.scalars(select(Job).where(Job.status == "EXPIRED", Job.media_deleted_at.is_(None)))
        )
        for job in expired:
            # Idempotent object deletion. Keep keys until both deletes succeed so a later run retries.
            try:
                storage().delete(job.original_key)
                if job.output_key:
                    storage().delete(job.output_key)
                job.media_deleted_at = stamp
            except Exception:
                record_error("cleanup", "Could not delete expired media; will retry", job.id)
        referenced = set(db.scalars(select(Job.original_key).where(Job.media_deleted_at.is_(None))))
        referenced.update(
            db.scalars(
                select(Job.output_key).where(
                    Job.output_key.is_not(None), Job.media_deleted_at.is_(None)
                )
            )
        )
        for obj in storage().objects():
            if obj["Key"] not in referenced and obj["LastModified"] < stamp - timedelta(hours=2):
                discard_unreferenced_media(obj["Key"])
        db.execute(delete(Session).where(Session.expires_at <= stamp))
        # Every auth window is at most one hour; retain a full day for boundary safety.
        db.execute(delete(RateLimit).where(RateLimit.window < int(time.time()) - 86400))
        db.execute(delete(Heartbeat).where(Heartbeat.seen_at < stamp - timedelta(days=7)))
    Path("/tmp/fastzip-cleanup-heartbeat").touch()


def reconcile():
    """After restore, metadata survives but unavailable media must never be downloadable."""
    count = 0
    with session_factory()() as db, db.begin():
        for job in db.scalars(select(Job).where(Job.status != "EXPIRED")):
            if (
                job.expires_at <= now()
                or not storage().exists(job.original_key)
                or (job.output_key and not storage().exists(job.output_key))
            ):
                job.status = "EXPIRED"
                job.expires_at = min(job.expires_at, now())
                job.lease_until = None
                count += 1
    return count


def run():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        try:
            cleanup_once()
        except Exception:
            record_error("cleanup", "Cleanup run failed; will retry")
        stop.wait(30)


if __name__ == "__main__":
    run()
