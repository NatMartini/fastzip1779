import argparse
import getpass
import json
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from pydantic import TypeAdapter, ValidationError
from pydantic.networks import EmailStr
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from fastzip.backup import audit_restored_media, backup_once, postgres_arguments
from fastzip.cleanup import cleanup_once, reconcile
from fastzip.config import settings
from fastzip.db import session_factory
from fastzip.models import User
from fastzip.security import hasher
from fastzip.storage import Storage


def main():
    parser = argparse.ArgumentParser(description="FastZip server-side operations")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate")
    sub.add_parser("init-storage")
    account = sub.add_parser("create-admin")
    account.add_argument("email")
    sub.add_parser("backup")
    sub.add_parser("cleanup")
    sub.add_parser("reconcile")
    audit = sub.add_parser("audit-restored-media", help="Read restored rows and media metadata")
    audit.add_argument("--database-url", required=True, help="Restored database to inspect")
    restore = sub.add_parser("restore")
    source = restore.add_mutually_exclusive_group(required=True)
    source.add_argument("--object-key", help="Object in the private backup bucket")
    source.add_argument("--archive-file", type=Path, help="Previously downloaded encrypted backup")
    restore.add_argument("--identity", required=True, help="Offline age private-key file")
    restore.add_argument("--target-database-url", required=True, help="A fresh, empty database")
    args = parser.parse_args()
    if args.command == "migrate":
        command.upgrade(Config("alembic.ini"), "head")
    elif args.command == "init-storage":
        for backup in (False, True):
            store = Storage(backup=backup)
            try:
                store.client.head_bucket(Bucket=store.bucket)
            except store.client.exceptions.ClientError as exc:
                if str(exc.response["Error"]["Code"]) not in {"404", "NoSuchBucket"}:
                    raise
                kwargs = {"Bucket": store.bucket}
                if settings().s3_region != "us-east-1":
                    kwargs["CreateBucketConfiguration"] = {
                        "LocationConstraint": settings().s3_region
                    }
                store.client.create_bucket(**kwargs)
        print("Private storage buckets are ready")
    elif args.command == "create-admin":
        try:
            email = str(TypeAdapter(EmailStr).validate_python(args.email)).lower()
        except ValidationError:
            account.exit(2, "Invalid email address.\n")
        try:
            password = getpass.getpass("New admin password (12–128 characters): ")
            if not 12 <= len(password) <= 128 or password != getpass.getpass("Repeat password: "):
                parser.error("Passwords must match and contain 12–128 characters")
        except (EOFError, KeyboardInterrupt) as exc:
            code = 130 if isinstance(exc, KeyboardInterrupt) else 1
            account.exit(code, "\nAdministrator creation cancelled.\n")
        with session_factory()() as db, db.begin():
            if db.scalar(select(User).where(User.email == email)):
                parser.error("Account already exists; use a separate administrator email")
            db.add(User(email=email, password_hash=hasher.hash(password), role="admin"))
        print("Administrator created")
    elif args.command == "backup":
        print(backup_once())
    elif args.command == "cleanup":
        cleanup_once()
    elif args.command == "reconcile":
        print(f"Marked {reconcile()} jobs expired")
    elif args.command == "audit-restored-media":
        target = create_engine(args.database_url)
        try:
            with Session(target) as db:
                report = audit_restored_media(db)
        finally:
            target.dispose()
        print(json.dumps(report))
        if report["unexpired_inconsistent_jobs"]:
            raise SystemExit(1)
    elif args.command == "restore":
        target = create_engine(args.target_database_url)
        if inspect(target).get_table_names():
            parser.error("Restore target must be empty; existing databases are never overwritten")
        started = time.monotonic()
        with TemporaryDirectory(prefix="fastzip-restore-") as directory:
            dump = Path(directory) / "backup.dump"
            encrypted = args.archive_file
            if encrypted is None:
                encrypted = Path(directory) / "backup.age"
                Storage(backup=True).get(args.object_key, encrypted)
            subprocess.run(
                [
                    settings().age_bin,
                    "--decrypt",
                    "--identity",
                    args.identity,
                    "--output",
                    str(dump),
                    str(encrypted),
                ],
                check=True,
                capture_output=True,
            )
            pgargs, env = postgres_arguments(args.target_database_url)
            subprocess.run(
                [
                    settings().pg_restore_bin,
                    *pgargs,
                    "--no-owner",
                    "--no-acl",
                    "--exit-on-error",
                    str(dump),
                ],
                env=env,
                check=True,
                capture_output=True,
                timeout=600,
            )
        from fastzip.db import engine

        engine.cache_clear()
        settings().database_url = args.target_database_url
        settings().db_password = ""
        command.upgrade(Config("alembic.ini"), "head")
        missing = reconcile()
        with target.connect() as db:
            jobs = db.scalar(text("SELECT count(*) FROM jobs"))
        print(
            json.dumps(
                {
                    "restored_jobs": jobs,
                    "marked_expired": missing,
                    "restore_seconds": round(time.monotonic() - started, 3),
                }
            )
        )
        target.dispose()


if __name__ == "__main__":
    main()
