# AI Interaction Record (`ai-session.md`)

This file documents four representative AI interactions that meaningfully influenced FastZip's architecture, implementation, frontend behavior, and cloud deployment, following the ECE1779 course project guidelines.

---

## Session 1: Designing Crash-Safe Worker Coordination and Admin Access Control

### Prompt

> We are designing FastZip, an asynchronous image and video compression web app on two DigitalOcean Droplets using FastAPI, PostgreSQL, and Docker Swarm. To avoid operating Redis or RabbitMQ, we want workers to claim jobs from PostgreSQL using `FOR UPDATE SKIP LOCKED`. What failure modes should we handle if a worker crashes mid-encode? Also, we plan to support an admin dashboard by checking an admin email allowlist—are there security risks with that approach?

### AI Response

> - `FOR UPDATE SKIP LOCKED` prevents two healthy workers from claiming the same queued row simultaneously, but it does not recover a job if a worker is killed after committing a `PROCESSING` status, nor does it stop a slow/partitioned worker from later overwriting a retried job's result. Pair `SKIP LOCKED` with a time-bounded lease (`lease_until`), periodic lease renewal heartbeats, a maximum retry count, and a unique `attempt_id` checked conditionally on completion.
> - Granting administrator privileges by matching an email address in an allowlist is unsafe when email verification is out of scope: an attacker could register the configured admin email first and immediately gain admin access. Provision admin accounts only through a trusted server-side CLI command running on the host/manager, and keep user media inaccessible to the admin role.

### What Your Team Did With It

- **Useful:** Highlighted two subtle correctness and security gaps—stuck/overwritten jobs after worker crashes and unverified-email admin account squatting—before we wrote database migrations or auth endpoints.
- **Adapted / refined:** We clarified explicitly in our design and README that this yields *at-least-once* encoding with a *single accepted publication* (not strictly *exactly-once* encoding), and added per-attempt S3 output keys (`outputs/<job_id>/<attempt_id>/...`) plus orphan cleanup so failed or superseded attempts never clobber valid outputs.
- **Verified:** Implemented `claim()`, `heartbeat()`, `complete()`, and `fail()` in `backend/fastzip/worker.py`, CLI-only `fastzip create-admin` in `backend/fastzip/cli.py`, and PostgreSQL integration tests in `tests/test_queue.py` and `tests/test_api.py` (racing 4 claimers for 3 jobs, expiring a lease, verifying obsolete attempts cannot publish, and verifying admins receive `404` on other users' downloads).

---

## Session 2: Containerized Stack Implementation, Real MinIO CI, and Recovery Verification (2026-11-14)

### Prompt

> Implement the FastZip application from our project proposal (`README.md` / `proposal/FastZip_Proposal.md`) on the `develop` branch: FastAPI API, PostgreSQL + Alembic schema, Pillow/FFmpeg worker, cleanup and hourly `age`-encrypted backup services, React frontend, Docker Compose for local development, two-Droplet Swarm stack, and GitHub Actions CI/CD.

### AI Response

> Proposed and scaffolded the full repository structure (`backend/fastzip/`, `frontend/`, `migrations/`, `deploy/`, `scripts/`, `tests/`, `.github/workflows/`) with synchronous FastAPI/SQLAlchemy handlers, PostgreSQL-backed sessions/CSRF/rate-limiting, Docker Compose with PostgreSQL 17 and MinIO, Swarm stack with file-mounted secrets, and GitHub Actions workflows. Initially referenced the legacy Docker Hub `minio/minio` image in `compose.yaml` and used an S3 mock (`moto`) for unit tests.

### What Your Team Did With It

- **Useful:** Produced a cohesive multi-service architecture matching the proposal, including automated PostgreSQL tests, encrypted backup/restore tooling, and a multi-stage Dockerfile separating `api`, `worker`, and `backup` targets.
- **Incorrect / incomplete:** The first GitHub Actions CI run failed in `compose-smoke` because MinIO discontinued its public Docker Hub container image, and a subsequent YAML syntax quirk broke the `backup` service's `tmpfs` mount option in Compose. In addition, `moto` alone could not prove provider-side presigned URL expiry or real `pg_dump`/`age` container recovery.
- **Verified & fixed:** Replaced the removed image with a pinned upstream MinIO source build (`deploy/minio.Dockerfile`), fixed the Compose `tmpfs` syntax (`0636d94`), and expanded `.github/workflows/ci.yml` to run real MinIO upload-to-download smoke (`scripts/smoke.py`), provider-side link-expiry enforcement (`scripts/check_link_expiry.py`), and a fresh-database encrypted restore drill (`scripts/recovery_smoke.py`). Confirmed all 20 PostgreSQL 17 tests, Ruff checks, frontend build, and container CI checks passed (GitHub Actions run `43671446276`).

---

## Session 3: Frontend Upload/Quota Bug Review and Windows Cloud Release Entry Point (2026-11-20)

### Prompt

> 阅读proposal，优化当前的前端并检查这个项目还有什么没做的。我现在需要：一键部署到云端，需要填信息我自己填，先跑本地。

### AI Response

> Compared `proposal/FastZip_Proposal.md`, the ECE1779 course rubric, and the codebase. Redesigned the React frontend (`frontend/src/main.tsx`, `frontend/src/style.css`, `frontend/src/api.ts`), added a local Windows launcher (`start.bat`), and built a Windows cloud release script (`scripts/deploy-cloud.ps1` + `deploy/cloud.psd1.example`) that validates manager prerequisites over SSH, packages `deploy/` and `scripts/` from a pinned Git commit with SHA-256 verification, and invokes `scripts/deploy.sh`. Also surfaced an audit checklist in `docs/validation.md` noting remaining gaps (DO CPU alert metric name, backup heartbeat/cadence, smoke-test signed URL logging, and remaining cloud acceptance tests).

### What Your Team Did With It

- **Useful:** Delivered a cleaner single-page UI, a zero-friction local launcher (`start.bat`), and a repeatable operator deployment entry point while explicitly flagging gaps rather than claiming cloud deployment was already verified.
- **Bugs identified during review:** Found two frontend state defects in the earlier implementation: (1) awaiting the job-history refresh inside the upload handler caused a transient history-fetch error to look like a failed upload, and (2) active job quota was computed from only the currently rendered page of history instead of the account-wide count.
- **Verified & fixed:** Updated `/api/jobs` to return server-computed `active_count` and `active_limit`, separated upload and history-refresh error states in `frontend/src/main.tsx`, added a PostgreSQL pagination regression check in `tests/test_api.py`, verified desktop and 390px mobile layouts plus image/video workflows against live local Docker Compose (`http://localhost:8088`), and tested `deploy-cloud.ps1` preflight and packaging logic (`4b2afbe`).

---

## Session 4: Production Swarm Rollout, DigitalOcean Alert Audit, and CI Gate Verification (2026-11-20 – 2026-11-21)

### Prompt

> Merge the `kevin` and `main` branches (preserving `kevin`'s work on any conflicts) and audit the codebase, CI workflows, and documentation against the ECE1779 course project requirements so the repository is ready for cloud release and grading.

### AI Response

> Audited the live Swarm fixes in PR #2 (`27fcfaf`), GitHub Actions runs, and the ECE1779 course handout (`project/overview/` and `project/handout/`). Identified a live CI failure (`ruff format --check` failing on `scripts/wait_stack.py` in run `44827283659`), undersized `/tmp` tmpfs mounts (64 MiB) for 100 MiB video workers in `scripts/deploy.sh`, three previously logged audit defects (`scripts/configure_alerts.py` sending invalid `v1/insights/droplet/cpu_utilization_percent` instead of `v1/insights/droplet/cpu`; `backend/fastzip/backup.py` pausing its heartbeat during `pg_dump` and drifting hourly schedules; `scripts/smoke.py` risking presigned URL leakage on HTTP error tracebacks), and missing mandatory final-report sections in `README.md` (`Team Information` IDs/emails, `Motivation`, `Objectives`, `Technical Stack`, `User Guide`, `Deployment Information` live URL, `## Video Demo`, `AI Assistance & Verification`, `Individual Contributions`, and `Lessons Learned`).

### What Your Team Did With It

- **Useful:** Prevented a broken CI merge onto `main` by catching the `ruff format` failure before pushing, closed four real operational bugs (Swarm worker `/tmp` sizing, DigitalOcean CPU alert API type, backup healthcheck/cadence, and presigned URL redaction), and brought `README.md` and `ai-session.md` into alignment with the course rubric.
- **Careful boundary kept:** Verified against the live endpoint (`https://fastzip1779.site/api/health/ready` returning `{"status":"ready"}`) without fabricating unexecuted cloud benchmark numbers or a fake video demo—leaving `## Video Demo` explicitly marked for our team-narrated recording and preserving `docs/validation.md`'s distinction between locally verified checks and pending cloud experiments.
- **Verified:** Confirmed DigitalOcean's exact `v1/insights/droplet/cpu` policy type against official API documentation, verified `ruff check`, `ruff format --check`, PostgreSQL 17 `pytest`, frontend `format:check` & `build`, `docker compose config`, and `docker stack config` locally, then merged `kevin` into `main` and monitored GitHub Actions `Checks` and `Release` workflows on `main`.
