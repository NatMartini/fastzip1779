"""Disposable Compose-only encrypted backup/restore drill; never run against production."""

import json
import os
import secrets
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from fastzip.backup import audit_restored_media, backup_once
from fastzip.config import settings
from fastzip.db import engine
from fastzip.models import Job, now
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session


def main():
    s = settings()
    if s.environment == "production":
        raise SystemExit("This generates a disposable test identity and is forbidden in production")
    name = "restore_smoke_" + secrets.token_hex(6)
    started = time.monotonic()
    with TemporaryDirectory(prefix="fastzip-recovery-smoke-") as directory:
        identity = Path(directory) / "identity"
        subprocess.run(["age-keygen", "--output", str(identity)], check=True, capture_output=True)
        s.backup_age_recipient = subprocess.check_output(
            ["age-keygen", "-y", str(identity)], text=True
        ).strip()
        with engine().connect() as db:
            before = (
                db.scalar(text("SELECT count(*) FROM users")),
                db.scalar(text("SELECT count(*) FROM jobs")),
            )
            completed = db.execute(
                select(Job.id, Job.expires_at).where(Job.status == "SUCCEEDED")
            ).all()
        key = backup_once()
        with engine().connect().execution_options(isolation_level="AUTOCOMMIT") as db:
            db.execute(text(f'CREATE DATABASE "{name}"'))
        target_url = (
            make_url(s.database_url).set(database=name).render_as_string(hide_password=False)
        )
        target = create_engine(target_url)
        try:
            result = subprocess.run(
                [
                    "fastzip",
                    "restore",
                    "--object-key",
                    key,
                    "--identity",
                    str(identity),
                    "--target-database-url",
                    target_url,
                ],
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                check=True,
                timeout=120,
            )
            with Session(target) as db:
                after = (
                    db.scalar(text("SELECT count(*) FROM users")),
                    db.scalar(text("SELECT count(*) FROM jobs")),
                )
                media = audit_restored_media(db)
                restored_results = set(db.scalars(select(Job.id).where(Job.status == "SUCCEEDED")))
            checked_at = now()
            assert all(
                job_id in restored_results for job_id, expiry in completed if expiry > checked_at
            ), "A completed job became unavailable before its expiry during restore"
            assert before == after, "Restored account/job counts differ from the dump snapshot"
            assert media["jobs_checked"] == after[1] and media["unexpired_inconsistent_jobs"] == 0
            print(
                json.dumps(
                    {
                        "users": after[0],
                        "jobs": after[1],
                        "status": "SUCCEEDED",
                        "elapsed_seconds": round(time.monotonic() - started, 3),
                        "restore": json.loads(result.stdout),
                        "media_audit": media,
                    }
                )
            )
        finally:
            target.dispose()
            with engine().connect().execution_options(isolation_level="AUTOCOMMIT") as db:
                db.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


if __name__ == "__main__":
    main()
