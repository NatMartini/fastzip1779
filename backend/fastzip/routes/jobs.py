import re
import uuid
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DBSession

from fastzip.cleanup import discard_unreferenced_media, lock_media_key
from fastzip.config import settings
from fastzip.db import database
from fastzip.media import MAX_UPLOAD, InvalidMedia, validate
from fastzip.models import Job, User, now
from fastzip.security import csrf, user
from fastzip.storage import storage

router = APIRouter(prefix="/api/jobs")


def active_jobs():
    return Job.status.in_(["QUEUED", "PROCESSING"]) & (Job.expires_at > now())


def job_json(job: Job):
    savings = None
    if job.output_size is not None:
        savings = round(100 * (1 - min(job.output_size, job.original_size) / job.original_size), 1)
    return {
        "id": str(job.id),
        "filename": job.filename,
        "media_type": job.media_type,
        "preset": job.preset,
        "status": job.effective_status,
        "original_size": job.original_size,
        "output_size": job.output_size,
        "savings_percent": savings,
        "original_is_smaller": job.original_is_no_larger,
        "attempts": job.attempts,
        "processing_seconds": job.processing_seconds,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "expires_at": job.expires_at,
        "error": job.error,
    }


@router.post("", status_code=202, dependencies=[Depends(csrf)])
def upload(
    file: UploadFile = File(...),
    preset: Literal["smaller", "balanced", "higher_quality"] = Form("balanced"),
    account: User = Depends(user),
    db: DBSession = Depends(database),
):
    with TemporaryDirectory(prefix="fastzip-upload-") as directory:
        path = Path(directory) / "source"
        total = 0
        with path.open("wb") as target:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD:
                    raise HTTPException(413, "Files must be no larger than 100 MB")
                target.write(chunk)
        try:
            media_type, suffix, mime = validate(path)
        except InvalidMedia as exc:
            raise HTTPException(422, str(exc)) from exc
        # Serialize submissions per account, including across API replicas.
        account_id = account.id
        db.scalar(select(User).where(User.id == account_id).with_for_update())
        active = db.scalar(
            select(func.count()).select_from(Job).where(Job.user_id == account_id, active_jobs())
        )
        if active >= settings().max_active_jobs:
            raise HTTPException(409, "You already have three active jobs; wait for one to finish")
        identifier = uuid.uuid4()
        key = f"originals/{identifier}/source{suffix}"
        filename = (file.filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
        filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)[:160] or "upload"
        job = Job(
            id=identifier,
            user_id=account_id,
            filename=filename,
            media_type=media_type,
            preset=preset,
            original_key=key,
            original_size=total,
            expires_at=now() + timedelta(hours=settings().media_hours),
        )
        try:
            lock_media_key(db, key)
            storage().put(path, key, mime)
            db.add(job)
            db.commit()
        except Exception:
            db.rollback()
            try:
                saved = discard_unreferenced_media(key)
            except Exception:
                saved = None  # Verification or cleanup can resume in the orphan sweep.
            if (
                saved is not None
                and saved.id == identifier
                and saved.user_id == account_id
                and saved.original_key == key
            ):
                return job_json(saved)
            raise
    return job_json(job)


@router.get("")
def jobs(
    account: User = Depends(user),
    db: DBSession = Depends(database),
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    query = select(Job).where(Job.user_id == account.id).order_by(Job.created_at.desc(), Job.id)
    total, active = db.execute(
        select(func.count(), func.count().filter(active_jobs()))
        .select_from(Job)
        .where(Job.user_id == account.id)
    ).one()
    return {
        "items": [job_json(job) for job in db.scalars(query.offset(offset).limit(limit))],
        "total": total,
        "active_count": active,
        "active_limit": settings().max_active_jobs,
    }


def owned_job(identifier: uuid.UUID, account: User, db: DBSession):
    job = db.scalar(select(Job).where(Job.id == identifier, Job.user_id == account.id))
    if job is None:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/{identifier}")
def job_detail(
    identifier: uuid.UUID, account: User = Depends(user), db: DBSession = Depends(database)
):
    return job_json(owned_job(identifier, account, db))


@router.get("/{identifier}/download")
def download(
    identifier: uuid.UUID,
    kind: Literal["result", "original"] = "result",
    account: User = Depends(user),
    db: DBSession = Depends(database),
):
    job = owned_job(identifier, account, db)
    if job.effective_status == "EXPIRED":
        raise HTTPException(410, "This file has expired")
    if kind == "result" and job.status != "SUCCEEDED":
        raise HTTPException(409, "The result is not ready")
    use_original = kind == "original" or job.output_size is None or job.original_is_no_larger
    key = job.original_key if use_original else job.output_key
    if not key:
        raise HTTPException(409, "The result is not ready")
    if not storage().exists(key):
        raise HTTPException(410, "This file is no longer available")
    lifetime = max(1, min(300, int((job.expires_at - now()).total_seconds())))
    filename = (
        job.filename
        if use_original
        else Path(job.filename).stem + (".webp" if job.media_type == "image" else ".mp4")
    )
    return {
        "url": storage().signed_url(key, filename, lifetime),
        "expires_in": lifetime,
        "filename": filename,
        "is_original": use_original,
    }
