import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert

from fastzip.config import settings
from fastzip.db import node_name, record_error, session_factory
from fastzip.media import validate
from fastzip.models import ErrorEvent, Heartbeat, Job, now
from fastzip.storage import storage

log = logging.getLogger("fastzip.worker")
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def claim(worker_id=WORKER_ID) -> Job | None:
    s = settings()
    with session_factory()() as db, db.begin():
        while True:
            stamp = now()
            job = db.scalar(
                select(Job)
                .where(
                    Job.expires_at > stamp,
                    or_(
                        and_(Job.status == "QUEUED", Job.available_at <= stamp),
                        and_(Job.status == "PROCESSING", Job.lease_until < stamp),
                    ),
                )
                .order_by(Job.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if job is None:
                return None
            if job.attempts >= s.max_attempts:
                job.status, job.completed_at, job.lease_until = "FAILED", stamp, None
                job.error = "Compression could not finish after three attempts"
                db.add(
                    ErrorEvent(
                        service="worker",
                        node=node_name(),
                        job_id=job.id,
                        message="Retry limit exhausted after an expired lease",
                    )
                )
                db.flush()
                continue
            job.status = "PROCESSING"
            job.attempts += 1
            job.attempt_id = uuid.uuid4()
            job.worker_id = worker_id
            job.lease_until = stamp + timedelta(seconds=s.lease_seconds)
            job.started_at = stamp
            job.error = None
            db.flush()
            return job


def ownership(job: Job):
    return (
        Job.id == job.id,
        Job.status == "PROCESSING",
        Job.attempt_id == job.attempt_id,
        Job.lease_until > now(),
        Job.expires_at > now(),
    )


def heartbeat(worker_id=WORKER_ID, job: Job | None = None) -> bool:
    with session_factory()() as db, db.begin():
        if job is not None:
            changed = db.execute(
                update(Job)
                .where(*ownership(job))
                .values(lease_until=now() + timedelta(seconds=settings().lease_seconds)),
            ).rowcount
            if not changed:
                return False
        statement = insert(Heartbeat).values(
            worker_id=worker_id,
            node=node_name(),
            job_id=job.id if job else None,
            seen_at=now(),
        )
        db.execute(
            statement.on_conflict_do_update(
                index_elements=[Heartbeat.worker_id],
                set_={
                    "seen_at": now(),
                    "job_id": job.id if job else None,
                    "node": node_name(),
                },
            )
        )
    Path("/tmp/fastzip-worker-heartbeat").touch()
    return True


def complete(job: Job, output_key: str, size: int, mime: str, duration: float) -> bool:
    with session_factory()() as db, db.begin():
        return bool(
            db.execute(
                update(Job)
                .where(*ownership(job))
                .values(
                    status="SUCCEEDED",
                    output_key=output_key,
                    output_size=size,
                    output_mime=mime,
                    completed_at=now(),
                    processing_seconds=duration,
                    lease_until=None,
                    error=None,
                )
            ).rowcount
        )


def fail(job: Job):
    exhausted = job.attempts >= settings().max_attempts
    with session_factory()() as db, db.begin():
        changed = db.execute(
            update(Job)
            .where(*ownership(job))
            .values(
                status="FAILED" if exhausted else "QUEUED",
                lease_until=None,
                available_at=now() + timedelta(seconds=min(60, 2**job.attempts)),
                completed_at=now() if exhausted else None,
                error="Compression failed; try a different file" if exhausted else None,
            )
        ).rowcount
        if changed:
            db.add(
                ErrorEvent(
                    service="worker",
                    node=node_name(),
                    job_id=job.id,
                    message="Compression attempt failed"
                    + ("; retries exhausted" if exhausted else "; queued for retry"),
                )
            )


def process(job: Job, worker_id=WORKER_ID):
    stopped = threading.Event()
    lost_lease = threading.Event()

    def renew():
        while not stopped.wait(settings().heartbeat_seconds):
            try:
                if not heartbeat(worker_id, job):
                    lost_lease.set()
                    return
            except Exception:
                # A disconnected worker cannot publish after its lease expires.
                log.warning("Lease renewal unavailable")

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    output_key = None
    published = False
    started = time.monotonic()
    try:
        if not heartbeat(worker_id, job):
            return
        with TemporaryDirectory(prefix="fastzip-worker-") as directory:
            root = Path(directory)
            source = root / "source"
            storage().get(job.original_key, source)
            actual, _, _ = validate(source)
            if actual != job.media_type:
                raise ValueError("Media type changed")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "fastzip.media",
                    str(source),
                    str(root),
                    job.media_type,
                    job.preset,
                ],
                capture_output=True,
                check=True,
                timeout=settings().compression_timeout + 20,
                start_new_session=True,
            )
            output = root / ("result.webp" if job.media_type == "image" else "result.mp4")
            mime = "image/webp" if job.media_type == "image" else "video/mp4"
            if lost_lease.is_set():
                return
            output_key = f"outputs/{job.id}/{job.attempt_id}/{output.name}"
            storage().put(output, output_key, mime)
            published = complete(
                job, output_key, output.stat().st_size, mime, time.monotonic() - started
            )
    except Exception:
        fail(job)
    finally:
        stopped.set()
        thread.join(timeout=settings().heartbeat_seconds + 2)
        if output_key and not published:
            try:
                storage().delete(output_key)
            except Exception:
                record_error("worker", "Could not delete an unclaimed output object", job.id)


def run():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        try:
            heartbeat()
            if job := claim():
                process(job)
            else:
                stop.wait(2)
        except Exception:
            record_error("worker", "Worker loop could not access a required service")
            stop.wait(5)


if __name__ == "__main__":
    run()
