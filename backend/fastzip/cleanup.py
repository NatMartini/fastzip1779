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
                storage().delete(obj["Key"])
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
