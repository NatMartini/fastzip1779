from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session as DBSession

from fastzip.config import settings
from fastzip.db import database
from fastzip.models import BackupRun, ErrorEvent, Heartbeat, Job, now
from fastzip.security import admin

router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin)])


@router.get("/overview")
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


@router.get("/jobs")
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
                "status": j.effective_status,
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


@router.get("/errors")
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


@router.get("/metrics")
def metrics():
    from fastzip.monitoring import droplet_metrics

    return droplet_metrics()
