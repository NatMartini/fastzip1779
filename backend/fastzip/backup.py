import logging
import os
import signal
import subprocess
import threading
import time
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from fastzip.config import settings
from fastzip.db import record_error, session_factory
from fastzip.models import BackupRun, now
from fastzip.storage import Storage


def postgres_arguments(url: str):
    parsed = make_url(url)
    env = os.environ.copy()
    env["PGPASSWORD"] = (
        parsed.password if parsed.password is not None else env.get("PGPASSWORD", "")
    )
    args = [
        "--host",
        parsed.host or parsed.query.get("host") or "localhost",
        "--port",
        str(parsed.port or parsed.query.get("port") or 5432),
        "--username",
        parsed.username or "postgres",
        "--dbname",
        parsed.database or "postgres",
    ]
    if sslmode := parsed.query.get("sslmode"):
        env["PGSSLMODE"] = sslmode
    return args, env


def prune(store: Storage, prefix: str, keep: int):
    entries = sorted(store.objects(prefix), key=lambda obj: obj["Key"], reverse=True)
    for obj in entries[keep:]:
        store.delete(obj["Key"])


def backup_once():
    s = settings()
    started = time.monotonic()
    # Session-level lock on a dedicated connection survives commits of backup status.
    with session_factory()() as lock_db:
        if not lock_db.scalar(text("SELECT pg_try_advisory_lock(779002)")):
            return
        try:
            if not s.backup_age_recipient:
                raise ValueError("Backup age recipient has not been configured")
            store = Storage(backup=True)
            stamp = now()
            key = f"hourly/{stamp:%Y-%m-%dT%H-%M-%S}.dump.age"
            with TemporaryDirectory(prefix="fastzip-backup-") as directory:
                dump = Path(directory) / "database.dump"
                encrypted = Path(directory) / "database.dump.age"
                args, env = postgres_arguments(s.database_url)
                subprocess.run(
                    [s.pg_dump_bin, *args, "--format=custom", "--file", str(dump)],
                    env=env,
                    capture_output=True,
                    check=True,
                    timeout=600,
                )
                subprocess.run(
                    [
                        s.age_bin,
                        "--recipient",
                        s.backup_age_recipient,
                        "--output",
                        str(encrypted),
                        str(dump),
                    ],
                    capture_output=True,
                    check=True,
                    timeout=120,
                )
                dump.unlink()
                store.put(encrypted, key, "application/octet-stream")
                daily_key = f"daily/{stamp:%Y-%m-%d}.dump.age"
                if not store.exists(daily_key):
                    store.put(encrypted, daily_key, "application/octet-stream")
                size = encrypted.stat().st_size
            prune(store, "hourly/", 24)
            prune(store, "daily/", 7)
            with session_factory()() as db, db.begin():
                db.add(
                    BackupRun(
                        status="SUCCEEDED",
                        object_key=key,
                        size_bytes=size,
                        duration_seconds=time.monotonic() - started,
                    )
                )
            return key
        except Exception:
            with session_factory()() as db, db.begin():
                db.add(
                    BackupRun(
                        status="FAILED",
                        duration_seconds=time.monotonic() - started,
                        message="Backup failed; check credentials, age recipient, and PostgreSQL tools",
                    )
                )
            record_error("backup", "Encrypted database backup failed")
            raise
        finally:
            lock_db.execute(text("SELECT pg_advisory_unlock(779002)"))
            lock_db.commit()


HEARTBEAT_PATH = Path("/tmp/fastzip-backup-heartbeat")


def run():
    logging.basicConfig(level=logging.INFO)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    next_run = 0.0
    while not stop.is_set():
        HEARTBEAT_PATH.touch()
        if time.monotonic() >= next_run:
            started = time.monotonic()
            done = threading.Event()

            def keep_alive(done=done):
                while not done.wait(15):
                    HEARTBEAT_PATH.touch()

            thread = threading.Thread(target=keep_alive, daemon=True)
            thread.start()
            try:
                backup_once()
            except Exception:
                pass
            finally:
                done.set()
                thread.join(timeout=2)
                HEARTBEAT_PATH.touch()
            next_run = started + 3600
        stop.wait(10)


def last_success():
    with session_factory()() as db:
        return db.scalar(
            select(BackupRun)
            .where(
                BackupRun.status == "SUCCEEDED", BackupRun.created_at >= now() - timedelta(hours=2)
            )
            .limit(1)
        )


if __name__ == "__main__":
    run()
