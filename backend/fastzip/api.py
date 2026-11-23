import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from fastzip.cleanup import discard_unreferenced_media, lock_media_key
from fastzip.config import settings
from fastzip.db import database, record_error
from fastzip.media import MAX_UPLOAD, InvalidMedia, validate
from fastzip.models import BackupRun, ErrorEvent, Heartbeat, Job, User, now
from fastzip.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    active_session,
    admin,
    create_session,
    csrf,
    dummy_hash,
    hasher,
    new_csrf,
    rate_limit,
    user,
    verify_password,
)
from fastzip.storage import storage

app = FastAPI(
    title="FastZip API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


def account_json(account: User):
    return {"id": str(account.id), "email": account.email, "role": account.role}


def active_jobs():
    return Job.status.in_(["QUEUED", "PROCESSING"]) & (Job.expires_at > now())


def job_json(job: Job):
    status = "EXPIRED" if job.expires_at <= now() else job.status
    savings = None
    if job.output_size is not None:
        savings = round(100 * (1 - min(job.output_size, job.original_size) / job.original_size), 1)
    return {
        "id": str(job.id),
        "filename": job.filename,
        "media_type": job.media_type,
        "preset": job.preset,
        "status": status,
        "original_size": job.original_size,
        "output_size": job.output_size,
        "savings_percent": savings,
        "original_is_smaller": job.output_size is not None and job.output_size >= job.original_size,
        "attempts": job.attempts,
        "processing_seconds": job.processing_seconds,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
        "expires_at": job.expires_at,
        "error": job.error,
    }


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    record_error("api", "Request failed due to an internal service error")
    return JSONResponse(
        status_code=503, content={"detail": "Service temporarily unavailable; please try again"}
    )


@app.get("/api/health/live")
def live():
    return {"status": "ok"}


@app.get("/api/health/ready")
def ready(db: DBSession = Depends(database)):
    db.execute(text("SELECT 1 FROM alembic_version"))
    storage().client.head_bucket(Bucket=settings().s3_bucket)
    return {"status": "ready"}


@app.get("/api/auth/csrf")
def get_csrf(response: Response):
    return {"csrf_token": new_csrf(response)}


@app.post("/api/auth/register", status_code=201, dependencies=[Depends(csrf)])
def register(
    body: Credentials, request: Request, response: Response, db: DBSession = Depends(database)
):
    ip = request.client.host if request.client else "unknown"
    rate_limit(db, "register:" + ip, settings().registration_rate_limit, 3600)
    account = User(
        email=str(body.email).lower(), password_hash=hasher.hash(body.password), role="user"
    )
    db.add(account)
    try:
        db.flush()
        create_session(db, account, response)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "An account with that email already exists") from exc
    return account_json(account)


@app.post("/api/auth/login", dependencies=[Depends(csrf)])
def login(
    body: Credentials, request: Request, response: Response, db: DBSession = Depends(database)
):
    ip = request.client.host if request.client else "unknown"
    email = str(body.email).lower()
    rate_limit(db, "login-ip:" + ip, settings().auth_rate_limit * 3, 900)
    rate_limit(db, "login-account:" + email, settings().auth_rate_limit, 900)
    account = db.scalar(select(User).where(User.email == email))
    if (
        not verify_password(body.password, account.password_hash if account else dummy_hash)
        or account is None
    ):
        raise HTTPException(401, "Email or password is incorrect")
    if hasher.check_needs_rehash(account.password_hash):
        account.password_hash = hasher.hash(body.password)
    # Rotate the current browser's session to prevent session fixation and abandoned tokens.
    if old := active_session(db, request):
        db.delete(old)
    create_session(db, account, response)
    db.commit()
    return account_json(account)


@app.post("/api/auth/logout", status_code=204, dependencies=[Depends(csrf)])
def logout(
    request: Request,
    response: Response,
    db: DBSession = Depends(database),
    account: User = Depends(user),
):
    if session := active_session(db, request):
        db.delete(session)
        db.commit()
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings().secure_cookies, httponly=True, samesite="lax"
    )
    response.delete_cookie(
        CSRF_COOKIE, path="/", secure=settings().secure_cookies, httponly=True, samesite="lax"
    )


@app.get("/api/auth/me")
def me(account: User = Depends(user)):
    return account_json(account)


@app.post("/api/jobs", status_code=202, dependencies=[Depends(csrf)])
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


@app.get("/api/jobs")
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


@app.get("/api/jobs/{identifier}")
def job_detail(
    identifier: uuid.UUID, account: User = Depends(user), db: DBSession = Depends(database)
):
    return job_json(owned_job(identifier, account, db))


@app.get("/api/jobs/{identifier}/download")
def download(
    identifier: uuid.UUID,
    kind: Literal["result", "original"] = "result",
    account: User = Depends(user),
    db: DBSession = Depends(database),
):
    job = owned_job(identifier, account, db)
    if job.expires_at <= now() or job.status == "EXPIRED":
        raise HTTPException(410, "This file has expired")
    if kind == "result" and job.status != "SUCCEEDED":
        raise HTTPException(409, "The result is not ready")
    use_original = (
        kind == "original" or job.output_size is None or job.output_size >= job.original_size
    )
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


@app.get("/api/admin/overview", dependencies=[Depends(admin)])
def overview(db: DBSession = Depends(database)):
    status = case((Job.expires_at <= now(), "EXPIRED"), else_=Job.status).label("effective_status")
    counts = dict(db.execute(select(status, func.count()).group_by(status)).all())
    counts.setdefault("EXPIRED", 0)
    since = now() - timedelta(hours=24)
    succeeded = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.status == "SUCCEEDED", Job.completed_at >= since)
    )
    failed = db.scalar(
        select(func.count())
        .select_from(Job)
        .where(Job.status == "FAILED", Job.completed_at >= since)
    )
    average = db.scalar(
        select(func.avg(Job.processing_seconds)).where(
            Job.status == "SUCCEEDED", Job.completed_at >= since
        )
    )
    workers = [
        {
            "worker_id": w.worker_id,
            "node": w.node,
            "job_id": str(w.job_id) if w.job_id else None,
            "seen_at": w.seen_at,
            "online": (now() - w.seen_at).total_seconds() < settings().lease_seconds * 2,
        }
        for w in db.scalars(select(Heartbeat).order_by(Heartbeat.seen_at.desc()).limit(100))
    ]
    backup = db.scalar(select(BackupRun).order_by(BackupRun.created_at.desc()).limit(1))
    return {
        "jobs_by_status": counts,
        "queue_depth": counts.get("QUEUED", 0),
        "completed_24h": succeeded,
        "throughput_per_hour": round(succeeded / 24, 2),
        "failure_rate_24h": round(failed / (succeeded + failed), 4) if succeeded + failed else 0,
        "average_processing_seconds": average,
        "workers": workers,
        "last_backup": {
            "status": backup.status,
            "created_at": backup.created_at,
            "size_bytes": backup.size_bytes,
            "duration_seconds": backup.duration_seconds,
            "message": backup.message,
        }
        if backup
        else None,
    }


@app.get("/api/admin/jobs", dependencies=[Depends(admin)])
def admin_jobs(
    db: DBSession = Depends(database),
    offset: int = Query(0, ge=0),
    limit: int = Query(30, ge=1, le=100),
):
    # Metadata only: no storage keys, presigned links, or media access through the admin role.
    return {
        "items": [
            {
                "id": str(j.id),
                "user_id": str(j.user_id),
                "status": job_json(j)["status"],
                "media_type": j.media_type,
                "preset": j.preset,
                "created_at": j.created_at,
                "attempts": j.attempts,
                "original_size": j.original_size,
                "output_size": j.output_size,
            }
            for j in db.scalars(
                select(Job).order_by(Job.created_at.desc()).offset(offset).limit(limit)
            )
        ]
    }


@app.get("/api/admin/errors", dependencies=[Depends(admin)])
def errors(
    db: DBSession = Depends(database),
    service: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    query = select(ErrorEvent)
    if service:
        query = query.where(ErrorEvent.service == service)
    for stamp in (since, until):
        if stamp and stamp.tzinfo is None:
            raise HTTPException(422, "Time filters must include a timezone")
    if since:
        query = query.where(ErrorEvent.created_at >= since)
    if until:
        query = query.where(ErrorEvent.created_at <= until)
    items = db.scalars(
        query.order_by(ErrorEvent.created_at.desc(), ErrorEvent.id.desc())
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return {
        "items": [
            {
                "id": event.id,
                "service": event.service,
                "node": event.node,
                "job_id": str(event.job_id) if event.job_id else None,
                "message": event.message,
                "created_at": event.created_at,
            }
            for event in items[:limit]
        ],
        "has_more": len(items) > limit,
    }


@app.get("/api/admin/metrics", dependencies=[Depends(admin)])
def metrics():
    from fastzip.monitoring import droplet_metrics

    return droplet_metrics()
