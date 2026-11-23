r"""Regression checks for admin statistics, heartbeats, backups, and error filters.

Run from the repository root with dev dependencies installed. Reuse the isolated
PostgreSQL 17 container described in test_auth_security.py:

    docker start fastzip-security-qa
    docker exec fastzip-security-qa pg_isready -U fastzip -d fastzip_security_test

Wait for "accepting connections", then run this suite (expected: 24 passed):

    TEST_DATABASE_URL='postgresql+psycopg://fastzip:test-only@127.0.0.1:55432/fastzip_security_test' \
      .venv/bin/python -m pytest tests/test_admin_telemetry.py -v

    .venv/bin/ruff check tests/test_admin_telemetry.py
    .venv/bin/ruff format --check tests/test_admin_telemetry.py
    git diff --check
    docker stop fastzip-security-qa

Use only a disposable database: fixtures truncate application tables. Tests use
TestClient, real PostgreSQL, and Moto without a running app stack or FFmpeg.
The API clock is frozen per test to verify exact time boundaries without sleeps.
Fixtures grant the admin role directly in the test database and insert telemetry;
these checks do not exercise the admin CLI, real workers, backup creation, or DO APIs.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastzip import api
from fastzip.config import settings
from fastzip.db import session_factory
from fastzip.models import BackupRun, ErrorEvent, Heartbeat, Job, User, now


@pytest.fixture
def admin_client(client, account):
    with session_factory()() as db, db.begin():
        db.get(User, uuid.UUID(account["id"])).role = "admin"
    assert client.get("/api/auth/me").json()["role"] == "admin"
    return client


@pytest.fixture
def frozen_now(admin_client, monkeypatch):
    timestamp = now()
    monkeypatch.setattr(api, "now", lambda: timestamp)
    return timestamp


@pytest.fixture
def make_job(account, frozen_now):
    def build(status, **overrides):
        identifier = uuid.uuid4()
        values = {
            "id": identifier,
            "user_id": uuid.UUID(account["id"]),
            "filename": "telemetry.png",
            "media_type": "image",
            "preset": "balanced",
            "original_key": f"originals/{identifier}/source.png",
            "original_size": 1000,
            "status": status,
            "created_at": frozen_now - timedelta(days=2),
            "expires_at": frozen_now + timedelta(days=1),
        }
        return Job(**(values | overrides))

    return build


def overview(client):
    response = client.get("/api/admin/overview")
    assert response.status_code == 200
    return response.json()


def test_empty_overview_has_safe_defaults(admin_client):
    assert overview(admin_client) == {
        "jobs_by_status": {"EXPIRED": 0},
        "queue_depth": 0,
        "completed_24h": 0,
        "throughput_per_hour": 0,
        "failure_rate_24h": 0,
        "average_processing_seconds": None,
        "workers": [],
        "last_backup": None,
    }


def test_empty_error_log_returns_empty_items(admin_client):
    response = admin_client.get("/api/admin/errors")
    assert response.status_code == 200
    assert response.json() == {"items": [], "has_more": False}


@pytest.mark.parametrize(
    "recent, completed, throughput, failure_rate, average",
    [
        ([("SUCCEEDED", 10), ("SUCCEEDED", 30), ("FAILED", 90)], 2, 0.08, 0.3333, 20),
        ([("SUCCEEDED", 12.5)], 1, 0.04, 0, 12.5),
        ([("FAILED", 90)], 0, 0, 1, None),
        ([], 0, 0, 0, None),
    ],
    ids=["mixed", "success_only", "failure_only", "no_recent_completions"],
)
def test_overview_24h_statistics(
    admin_client, make_job, frozen_now, recent, completed, throughput, failure_rate, average
):
    with session_factory()() as db, db.begin():
        db.add_all(
            make_job(
                status,
                completed_at=frozen_now - timedelta(hours=1),
                processing_seconds=duration,
            )
            for status, duration in recent
        )
        # Old completions and unfinished jobs must not affect throughput or averages.
        db.add_all(
            [
                make_job(
                    "SUCCEEDED",
                    completed_at=frozen_now - timedelta(hours=25),
                    processing_seconds=1000,
                ),
                make_job("FAILED", completed_at=frozen_now - timedelta(hours=25)),
                make_job("QUEUED"),
                make_job("PROCESSING", processing_seconds=500),
            ]
        )
    result = overview(admin_client)
    assert result["completed_24h"] == completed
    assert result["throughput_per_hour"] == throughput
    assert result["failure_rate_24h"] == failure_rate
    assert result["average_processing_seconds"] == average


def test_overview_24h_cutoff_is_inclusive(admin_client, make_job, frozen_now):
    cutoff = frozen_now - timedelta(hours=24)
    with session_factory()() as db, db.begin():
        db.add_all(
            [
                make_job("SUCCEEDED", completed_at=cutoff, processing_seconds=10),
                make_job(
                    "SUCCEEDED",
                    completed_at=cutoff + timedelta(microseconds=1),
                    processing_seconds=30,
                ),
                make_job(
                    "SUCCEEDED",
                    completed_at=cutoff - timedelta(microseconds=1),
                    processing_seconds=1000,
                ),
                make_job("FAILED", completed_at=cutoff),
                make_job("FAILED", completed_at=cutoff - timedelta(microseconds=1)),
            ]
        )
    result = overview(admin_client)
    assert result["completed_24h"] == 2
    assert result["throughput_per_hour"] == 0.08
    assert result["failure_rate_24h"] == 0.3333
    assert result["average_processing_seconds"] == 20


def test_expired_jobs_are_not_counted_as_queued(admin_client, make_job, frozen_now):
    with session_factory()() as db, db.begin():
        db.add_all(
            [make_job("QUEUED"), make_job("FAILED"), make_job("EXPIRED")]
            + [
                make_job(status, expires_at=frozen_now)
                for status in ("QUEUED", "PROCESSING", "SUCCEEDED")
            ]
        )
    result = overview(admin_client)
    assert result["jobs_by_status"] == {"QUEUED": 1, "FAILED": 1, "EXPIRED": 4}
    assert result["queue_depth"] == 1


def test_worker_online_boundary_and_heartbeat_fields(admin_client, frozen_now, monkeypatch):
    monkeypatch.setattr(settings(), "lease_seconds", 60)
    job_id = uuid.uuid4()
    with session_factory()() as db, db.begin():
        db.add_all(
            Heartbeat(
                worker_id=name,
                node="test-node",
                job_id=job_id if name == "active" else None,
                seen_at=frozen_now - timedelta(seconds=age),
            )
            for name, age in [("idle", 0), ("active", 119), ("boundary", 120), ("stale", 121)]
        )
    workers = overview(admin_client)["workers"]
    assert [worker["worker_id"] for worker in workers] == ["idle", "active", "boundary", "stale"]
    assert [worker["online"] for worker in workers] == [True, True, False, False]
    assert workers[0]["job_id"] is None
    assert workers[1]["job_id"] == str(job_id)
    assert all(worker["node"] == "test-node" for worker in workers)
    assert datetime.fromisoformat(workers[1]["seen_at"]) == frozen_now - timedelta(seconds=119)


@pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED"])
def test_latest_backup_is_selected_by_timestamp(admin_client, frozen_now, status):
    with session_factory()() as db, db.begin():
        # Insert the newest record first so insertion order cannot stand in for recency.
        db.add_all(
            [
                BackupRun(
                    status=status,
                    created_at=frozen_now,
                    size_bytes=2048 if status == "SUCCEEDED" else None,
                    duration_seconds=2.5,
                    message="Latest test backup",
                ),
                BackupRun(
                    status="RUNNING",
                    created_at=frozen_now - timedelta(hours=1),
                    message="Older test backup",
                ),
            ]
        )
    backup = overview(admin_client)["last_backup"]
    assert backup["status"] == status
    assert datetime.fromisoformat(backup["created_at"]) == frozen_now
    assert backup["size_bytes"] == (2048 if status == "SUCCEEDED" else None)
    assert backup["duration_seconds"] == 2.5
    assert backup["message"] == "Latest test backup"


@pytest.fixture
def error_records(frozen_now):
    records = {}
    with session_factory()() as db, db.begin():
        for name, service, hour in (
            ("old", "worker", -3),
            ("lower", "api", -2),
            ("middle", "worker", -1),
            ("upper", "api", 0),
            ("new", "backup", 1),
        ):
            event = ErrorEvent(
                service=service,
                node="test-node",
                message=f"Test event {name}",
                created_at=frozen_now + timedelta(hours=hour),
            )
            db.add(event)
            db.flush()
            records[name] = event.id
    return records


@pytest.mark.parametrize(
    "filters, expected",
    [
        ({}, ["new", "upper", "middle", "lower", "old"]),
        ({"service": "api"}, ["upper", "lower"]),
        ({"service": "missing"}, []),
        ({"since": "lower"}, ["new", "upper", "middle", "lower"]),
        ({"until": "upper"}, ["upper", "middle", "lower", "old"]),
        ({"since": "lower", "until": "upper"}, ["upper", "middle", "lower"]),
        ({"service": "worker", "since": "lower", "until": "upper"}, ["middle"]),
        ({"since": "lower_offset", "until": "upper"}, ["upper", "middle", "lower"]),
    ],
    ids=["all", "service", "unknown_service", "since", "until", "range", "combined", "offset"],
)
def test_error_filters_are_inclusive_and_ordered(
    admin_client, error_records, frozen_now, filters, expected
):
    lower = frozen_now - timedelta(hours=2)
    timestamps = {
        "lower": lower.isoformat(),
        "upper": frozen_now.isoformat(),
        "lower_offset": lower.astimezone(timezone(timedelta(hours=2))).isoformat(),
    }
    params = {key: timestamps.get(value, value) for key, value in filters.items()}
    response = admin_client.get("/api/admin/errors", params=params)
    assert response.status_code == 200
    assert [event["id"] for event in response.json()["items"]] == [
        error_records[name] for name in expected
    ]


def test_error_pagination_has_no_duplicates_or_missing_rows(admin_client, frozen_now):
    with session_factory()() as db, db.begin():
        events = [
            ErrorEvent(service="worker", node="node", message=str(i), created_at=frozen_now)
            for i in range(50)
        ]
        extra = ErrorEvent(service="api", node="node", message="extra", created_at=frozen_now)
        db.add_all([*events, extra])
        db.flush()
        worker_ids = sorted((event.id for event in events), reverse=True)
        all_ids = sorted([*worker_ids, extra.id], reverse=True)

    for filters, expected, has_more in (
        ({"service": "worker"}, worker_ids, False),
        ({}, all_ids[:50], True),
        ({"offset": 50}, all_ids[50:], False),
        ({"service": "worker", "offset": 50}, [], False),
        ({"offset": 2, "limit": 2}, all_ids[2:4], True),
    ):
        response = admin_client.get("/api/admin/errors", params=filters)
        assert response.status_code == 200
        page = response.json()
        assert [event["id"] for event in page["items"]] == expected
        assert page["has_more"] is has_more


@pytest.mark.parametrize("field", ["since", "until"])
@pytest.mark.parametrize("value", ["2026-11-21T12:00:00", "not-a-timestamp"])
def test_error_filters_reject_naive_or_invalid_timestamps(admin_client, field, value):
    response = admin_client.get("/api/admin/errors", params={field: value})
    assert response.status_code == 422
    if value != "not-a-timestamp":
        assert response.json() == {"detail": "Time filters must include a timezone"}
