# FastZip

Private, asynchronous image and video compression built as a stateful cloud-native application with a React frontend, Caddy reverse proxy, FastAPI API, PostgreSQL-backed job queue, private S3-compatible object storage, and independently scalable compression workers.

**Course:** ECE1779 — Introduction to Cloud Computing (Fall 2026)  
**Live Deployment:** [https://fastzip1779.site](https://fastzip1779.site)  
**Repository:** [https://github.com/NatMartini/fastzip1779](https://github.com/NatMartini/fastzip1779)

---

## Team Information

| Team Member | Student Number | Email |
| --- | --- | --- |
| Kevin Li | 1008809498 | dongye.li@mail.utoronto.ca |
| Xi Chen | 1012863634 | xialex.chen@mail.utoronto.ca |
| Yuxin Duan | 1008873131 | yuxin.duan@mail.utoronto.ca |
| Ouyi Yang | 1012421407 | ouyi.yang@mail.utoronto.ca |

---

## Motivation

Photos and short screen recordings are essential for coursework, demos, and everyday sharing, yet raw media frequently exceeds platform attachment limits (for example, Gmail's 25 MB limit or course submission caps). Target users—especially students submitting recorded demos or assignments from lightweight laptops or mobile devices—face clear drawbacks with existing options:

- **Desktop encoders** (e.g., HandBrake) require local installation, tuning knowledge, and heavy local CPU/battery usage.
- **Public online compressors** often impose opaque retention policies, lack visibility into long-running jobs, or restrict file sizes unpredictably.
- **In-browser encoders** avoid network upload but are slow and thermally constrained on low-power devices.

**FastZip** offloads image and video compression to replicated cloud workers with strict privacy guarantees: every upload is tied to an authenticated account, served via short-lived private presigned links, and automatically purged from object storage after 24 hours while retaining lightweight job history in PostgreSQL.

---

## Objectives

Our primary objectives for FastZip are to design, deploy, and validate a stateful cloud-native system that satisfies all ECE1779 core requirements and three advanced features:

1. **Responsive asynchronous web application:** Provide a React/TypeScript frontend and Python FastAPI backend supporting user registration/login, upload validation, preset selection, job polling, private downloads, and an administrator operations portal.
2. **Durable state management:** Persist accounts, hashed sessions, jobs, rate limits, worker heartbeats, structured error events, and backup metadata in **PostgreSQL 17** backed by a **DigitalOcean Volume** so state survives container restarts and redeployments.
3. **Fault-tolerant distributed workers:** Process JPEG/PNG and MP4 jobs asynchronously using replicated containerized workers coordinated via PostgreSQL `FOR UPDATE SKIP LOCKED`, renewable leases, bounded retries, and attempt-ID fencing.
4. **Cloud orchestration and observability:** Deploy across two **DigitalOcean Droplets** using **Docker Swarm** with replication, VIP load balancing, health checks, rolling updates/rollbacks, DigitalOcean Monitoring metrics, and resource/Volume alerts.
5. **Three integrated advanced features:** Implement **Security Enhancements**, an end-to-end **CI/CD Pipeline**, and **Encrypted Off-Node Backup & Recovery**.

**Scope boundaries:** The MVP supports JPEG/static PNG images (≤ 20 MiB, ≤ 25 MP) and MP4 videos (≤ 100 MiB, ≤ 120 s, ≤ 1920×1080 or 1080×1920), one file per job, up to three concurrent active jobs per account, and three quality presets (`Smaller file`, `Balanced`, `Higher quality`). Animated PNG, batch uploads, editing, streaming, GPU encoding, email verification, and password reset are intentionally out of scope.

---

## Technical Stack

| Layer | Technology | Role and Design Rationale |
| --- | --- | --- |
| **Frontend** | React 19, TypeScript, Vite | Single-page upload workspace, job history, and admin dashboard served statically by Caddy. |
| **Ingress & TLS** | Caddy 2 | Terminates automatic Let's Encrypt HTTPS (`fastzip1779.site`), serves built assets, sets strict security headers, and reverse-proxies `/api/*` on one origin so session cookies remain first-party. |
| **Backend & Workers** | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pillow, FFmpeg/FFprobe | Synchronous HTTP API (`uvicorn`), Pillow WebP encoding, and isolated shell-less FFmpeg H.264/AAC encoding. |
| **Database & Persistence** | PostgreSQL 17, DigitalOcean Block Storage Volume | Single-replica Swarm service pinned via node label (`fastzip.db=true`) to the manager Droplet with an attached DigitalOcean Volume mounted at `/mnt/fastzip_postgres` (`/mnt/fastzip_postgres/postgres`, ext4 `/dev/sda`). We intentionally avoid managed PostgreSQL so persistent storage is directly implemented and verified. |
| **Object Storage** | DigitalOcean Spaces (prod), MinIO built from pinned source (local) | Private S3-compatible buckets decouple media bytes from compute nodes; workers on any node read/write without a shared filesystem. |
| **Orchestration** | Docker, Docker Compose (local), Docker Swarm (cloud) | Two DigitalOcean Droplets (`fastzip-manager` and `fastzip-worker`) in one private VPC. Chosen over Kubernetes for lower operational overhead while delivering service replication, VIP load balancing, health checks, secrets, and rolling updates/rollbacks. |
| **Monitoring & Alerts** | DigitalOcean `do-agent` & Insights Alerts, Monitoring API, `configure_alerts.py`, `volume-watch.timer` | `do-agent` runs on both Droplets with Insights alert rules for CPU (≥80%), memory (≥85%), and root disk (≥80%) over 5-minute windows; `scripts/configure_alerts.py` provides an API-based alternative, and `deploy/volume-watch.timer` covers the attached Volume. Live metrics, worker heartbeats, and structured error events render in the Admin Portal. |
| **CI/CD** | GitHub Actions, GitHub Container Registry (GHCR) | Runs lint/format, PostgreSQL 17 tests, frontend build, full Compose/MinIO smoke + recovery drills, builds multi-platform (`amd64`/`arm64`) SHA-tagged images, and gates SSH Swarm deployment. |
| **Backup & Encryption** | `pg_dump`, `age`, private backup bucket | Hourly encrypted database snapshots using public-key-only `age` encryption; private decryption keys never touch production servers. |

### System Architecture

![FastZip System Architecture](proposal/architecture.png)

```text
Browser → Caddy (HTTPS + React) → FastAPI replicas (x2) → PostgreSQL 17 (DO Volume)
                                       ↓                        ↑
                                 private Spaces / MinIO ← Workers (replicated)
                                       ↑                        ↓
                                    Cleanup              leases / heartbeats

PostgreSQL → pg_dump → age public-key encryption → separate private backup bucket
```

**Key architectural tradeoffs:**

- **PostgreSQL queue vs. Redis/RabbitMQ:** Using PostgreSQL `FOR UPDATE SKIP LOCKED` with 60-second leases (renewed every 10 seconds), up to three retry attempts, and attempt-specific output keys avoids operating an extra broker while guaranteeing **at-least-once** processing with a **single accepted publication** (stale workers cannot overwrite newer attempts).
- **Single-manager Swarm vs. two managers:** A two-manager Raft cluster cannot tolerate losing either manager anyway. Pinning PostgreSQL and the Swarm manager to Droplet A accepts manager-loss downtime and mitigates data loss via off-node encrypted backups and fresh-database restore.
- **VPC overlay networking:** Swarm overlay traffic uses an **unencrypted** overlay restricted to the private DigitalOcean VPC between the two nodes, after IPsec ESP overlay encryption (`encrypted: "true"`) caused cross-node connectivity issues under DigitalOcean Cloud Firewall rules (which only cover ICMP, TCP, and UDP). External client traffic remains protected by Caddy HTTPS, and Spaces/backup transfers use TLS plus `age` encryption.

---

## Features

### Core Cloud-Native Capabilities

- **Containerization & Local Compose (`compose.yaml`):** Separate Docker build targets for `api`, `worker`, `backup`, and `frontend`, plus PostgreSQL 17 and pinned-source MinIO (`deploy/minio.Dockerfile`), allowing full local execution without cloud credentials.
- **Stateful Persistence:** Accounts, sessions, jobs, rate limits, heartbeats, error logs, and backup runs live in PostgreSQL 17 on a named Docker volume locally and a mounted DigitalOcean Volume in production (`scripts/deploy.sh` verifies via `findmnt` that the path is not on `/`).
- **Docker Swarm Orchestration (`deploy/stack.yaml`):** Runs two `api` replicas spread across both Droplets (`max_replicas_per_node: 1`) behind Swarm VIP load balancing (database-backed sessions require no sticky sessions), replicated `worker` containers (`WORKER_REPLICAS`, scalable via `docker service scale fastzip_worker=N`), health checks, resource reservations/limits, and `failure_action: rollback`.
- **Monitoring & Observability:** The Admin Portal (`/api/admin/overview`, `/api/admin/metrics`, `/api/admin/errors`) displays queue depth, 24-hour throughput and failure rate, live worker heartbeats, latest backup status, Droplet CPU/memory/filesystem metrics from the DigitalOcean API, and filterable structured error events. `scripts/configure_alerts.py` configures DigitalOcean 5-minute alert policies, and `deploy/volume-watch.timer` alerts on the attached database Volume.

### Supported Media Workflow

Register / sign in → upload a file → select a preset → poll job status (`QUEUED`, `PROCESSING`, `SUCCEEDED`, `FAILED`, `EXPIRED`) → download the result. Uploads return `202 Accepted`. Completed jobs display size savings and processing time, and automatically serve the original file if compression produces a larger output. Media expires 24 hours after upload; the `cleanup` service deletes expired objects every 30 seconds and sweeps orphaned objects after two hours while preserving job metadata.

| Input | Limits (Binary MiB) | Output |
| --- | --- | --- |
| JPEG / static PNG | 20 MiB, 25 million pixels | WebP (alpha preserved, EXIF stripped) |
| MP4 | 100 MiB, 120 seconds, ≤ 1920×1080 or 1080×1920 | H.264 + AAC MP4 |

| Preset | WebP Quality | H.264 CRF |
| --- | ---: | ---: |
| Smaller file | 55 | 32 |
| Balanced | 75 | 26 |
| Higher quality | 88 | 21 |

Presets govern encoding quality rather than guaranteeing a target file size. Video encoding runs in an isolated subprocess with a single FFmpeg thread, a 300-second timeout, and container CPU/memory bounds.

### Advanced Feature 1: Security Enhancements

- **Transport & headers:** Automatic Caddy HTTPS with Let's Encrypt, HSTS, strict CSP, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, and `Referrer-Policy: no-referrer`.
- **Authentication, sessions & CSRF:** Argon2id password hashing (12–128 characters); random session tokens stored as keyed HMAC-SHA256 hashes in PostgreSQL (7-day expiry, revoked on logout) in `HttpOnly`, `SameSite=Lax`, `Secure` (production) cookies; double-submit signed CSRF tokens plus `Origin` / `Sec-Fetch-Site` validation on all state-changing routes; distributed PostgreSQL rate limiting across API replicas.
- **Authorization & media privacy:** Every job endpoint enforces user ownership; downloads issue short-lived presigned S3 URLs (≤ 5 minutes, shortened near 24-hour expiry). Admin accounts can only be provisioned via the server-side CLI (`fastzip create-admin`), and admins can inspect job metadata and telemetry without ever accessing user media.
- **Hardened execution & secrets:** Uploads are validated by file signatures, Pillow decoding, and FFprobe metadata (never trusting extensions or MIME headers); FFmpeg runs without a shell and with a restricted protocol whitelist; application containers run as non-root UID `10001` with read-only root filesystems and bounded `/tmp` tmpfs mounts; production credentials are mounted exclusively via Docker Swarm secrets (`*_FILE`) with separate bucket-scoped Spaces keys.

### Advanced Feature 2: CI/CD Pipeline

- **Continuous Integration (`.github/workflows/ci.yml`):** On every pull request and push to `main`/`develop`, GitHub Actions runs Ruff lint and format checks, the PostgreSQL 17 pytest suite, `requirements.lock` sync verification, frontend Prettier/TypeScript/Vite checks, full `docker compose` build and real MinIO upload-to-download smoke test (`scripts/smoke.py`), provider-side signed-link expiry check (`scripts/check_link_expiry.py`), encrypted backup/restore drill (`scripts/recovery_smoke.py`), and `docker stack config` validation.
- **Continuous Delivery & Rollback (`.github/workflows/release.yml`, `scripts/deploy.sh`):** Pushes to `main` (and manual `workflow_dispatch`) run the full `Checks` workflow before building and pushing multi-platform (`linux/amd64`, `linux/arm64`) SHA-tagged images (`api`, `worker`, `backup`, `frontend`) to GHCR. When `DEPLOY_ENABLED=true`, the deploy job transfers `deploy/` and `scripts/` over SSH with strict host-key verification, runs one-shot Alembic migrations, deploys the Swarm stack, waits for service convergence (`scripts/wait_stack.py`), and runs an HTTPS smoke test. Swarm can roll back an unhealthy service update. If the smoke test fails, `scripts/deploy.sh` restores the tag in `/opt/fastzip/current-tag` with the writable `/tmp` mounts and reruns the upload, compression, and signed download before confirming recovery. The candidate release still reports failure. [Measured rollback results](#fault-recovery-in-an-isolated-swarm)

### Advanced Feature 3: Backup and Recovery

- **Encrypted automated backups (`backend/fastzip/backup.py`):** A single-replica backup service runs hourly using a PostgreSQL advisory lock, executes `pg_dump --format=custom`, encrypts the dump with an `age` public recipient key, deletes the temporary plaintext dump, uploads to a separate private Spaces bucket, and prunes to the latest 24 hourly and 7 daily snapshots. Each run records status, size, and duration in `backup_runs` (surfaced on the Admin Portal) and logs failures to `error_events`.
- **Fresh-database restore & media reconciliation (`fastzip restore`):** On a trusted recovery host holding the offline `age` private key, the CLI refuses non-empty target databases, decrypts and restores into a fresh PostgreSQL database (`--no-owner --no-acl --exit-on-error`), applies newer additive Alembic migrations, and reconciles restored job rows against live media objects—marking any job whose objects have expired or been deleted as `EXPIRED`.
- **Measured disaster recovery.** After powering off a local VM, the original HTTPS address accepted a new upload in **107.439 s** from a private MinIO backup and **40.929 s** from a downloaded copy on a second fresh VM. The first trial recovered four of seven jobs. The latest recovered job committed **33.606 s** before the fault. A signed download confirmed the new worker could finish a job in both trials. [Timings and media checks](docs/recovery.md#timed-recovery-on-fresh-vms)

---

## User Guide

1. **Create an account or sign in:** Open [https://fastzip1779.site](https://fastzip1779.site) (or `http://localhost:8080` locally), switch to **Create account**, enter an email and a 12–128 character password, and submit.
2. **Upload and choose a preset:** Drag and drop (or browse for) a JPEG, static PNG (≤ 20 MiB), or MP4 video (≤ 100 MiB). Select **Smaller file**, **Balanced**, or **Higher quality**, then click **Compress**.
3. **Monitor status and download:** The job appears immediately in **Job history** (`QUEUED` → `PROCESSING` → `SUCCEEDED`) and refreshes automatically. Once complete, view original vs. output size, savings percentage, and worker duration, then download either the compressed result (`.webp` / `.mp4`) or the original file before the 24-hour expiration window elapses.
4. **Administrator Portal (operators only):** Create an admin via the server-side CLI (`docker compose exec api fastzip create-admin admin@example.com` locally, or `docker exec -it <api-container> fastzip create-admin admin@example.com` on Swarm). Sign in with the admin account and open the **Admin** tab to inspect queue depth, 24h throughput/failure rate, online worker heartbeats, DigitalOcean Droplet CPU/memory/filesystem metrics, latest backup status, and filterable error events.

---

## Development Guide

### Repository Layout

- `backend/fastzip/` — FastAPI API (`api.py`), worker (`worker.py`), media validation/encoding (`media.py`), security (`security.py`), storage (`storage.py`), cleanup/reconcile (`cleanup.py`), encrypted backup (`backup.py`), DO monitoring (`monitoring.py`), CLI (`cli.py`).
- `frontend/` — React/TypeScript Vite application and production Caddy image (`frontend/Dockerfile`).
- `migrations/` — Frozen Alembic database schema migrations (`alembic.ini`, `migrations/versions/0001_initial.py`).
- `deploy/` — Production Swarm configs (`bootstrap.yaml`, `stack.yaml`, `Caddyfile`, `production.env.example`, `cloud.psd1.example`), pinned-source `minio.Dockerfile`, and `volume-watch` systemd units.
- `scripts/` — Deployment (`deploy.sh`, `deploy-cloud.ps1`, `wait_stack.py`), smoke/expiry/recovery verification (`smoke.py`, `check_link_expiry.py`, `recovery_smoke.py`), alerting (`configure_alerts.py`, `volume_watch.py`), and load benchmark (`benchmark.py`).
- `tests/` — PostgreSQL-backed integration and unit tests (`test_api.py`, `test_queue.py`, `test_media.py`, `test_backup.py`).
- `docs/` — Runbooks and evidence ([deployment](docs/deployment.md), [recovery](docs/recovery.md), [experiments](docs/experiments.md), [validation](docs/validation.md)).

### Quick Local Run (Docker Compose)

Install Docker Engine / Docker Desktop with Docker Compose v2. **No cloud account or cloud credentials are required for local development** (all sample local credentials live in `.env.example`; production secrets are stored exclusively as Docker Swarm secrets on the cloud manager and are never committed to Git).

> **Note:** The first build compiles MinIO from a pinned official upstream source revision (`deploy/minio.Dockerfile`) because its legacy Docker Hub image is no longer published; allow several minutes on the first build.

- **Windows one-click start:** Double-click `start.bat` (or run `.\start.bat --no-browser`). It initializes `.env`, builds and starts the stack, waits for readiness, and opens the app.
- **Manual start (macOS / Linux / Windows):**

```sh
cp .env.example .env       # PowerShell: Copy-Item .env.example .env
docker compose up --build -d
```

Open **http://localhost:8080** (if port `8080` is busy, set `FASTZIP_PORT=8088` in `.env` and open `http://localhost:8088`). The local MinIO console runs on `http://localhost:9001` with the credentials in `.env`; PostgreSQL is intentionally not exposed to the host.

```sh
# Provision a local admin account
docker compose exec api fastzip create-admin admin@example.com
# Scale compression workers independently
docker compose up -d --scale worker=2
# Inspect containers and logs
docker compose ps
docker compose logs --tail=100 api worker cleanup
# Stop without deleting database or media volumes (avoid -v unless resetting)
docker compose down
```

### Non-Container Development & Automated Tests

Requires Python 3.12+, `uv`, Node 22, FFmpeg/FFprobe, and a **disposable PostgreSQL** database (the test suite intentionally refuses SQLite so PostgreSQL `FOR UPDATE SKIP LOCKED` row-locking races are genuinely tested; tables are truncated between tests).

```sh
uv sync --frozen --extra dev
export TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/fastzip_test
uv run ruff check backend tests migrations scripts
uv run ruff format --check backend tests migrations scripts
uv run pytest -q
cd frontend && npm ci && npm run format:check && npm run build && cd ..
```

For focused session, CSRF, job ownership, and admin authorization checks, see the
[Security regression QA guide](docs/security-qa.md) for coverage, an isolated
PostgreSQL setup, run commands, and recorded results.

#### Boundary and Failure-Case Regression Tests

The boundary suites added **96 cases** to the 107-test baseline. Three restored-media
audit tests and 12 media commit recovery tests brought the full suite to **218 tests**.
Four administrator CLI cancellation cases and ten filename cases brought the current
total to **232 tests**. Parameterized cases are counted individually. [Interrupted commit tests and results](docs/security-qa.md#interrupted-media-commits)

| Suite | Cases | Coverage |
| --- | ---: | --- |
| [`test_media_boundaries.py`](tests/test_media_boundaries.py) | 47 | Image sizes just below/at/above 20 MiB; video sizes below/at/above 100 MiB; 25 MP image boundaries; 120-second duration and landscape/portrait 1080p boundaries; zero/multiple video streams; non-finite durations; FFprobe timeout, process failure, invalid JSON, null/missing/wrong-shaped fields; truncated JPEG; encoding timeout and retry without losing the original |
| [`test_api_boundaries.py`](tests/test_api_boundaries.py) | 28 | Password lengths 11/12/128/129; missing/invalid credentials; case-normalized duplicate registration; malformed UUID, pagination and download kind; file-signature detection despite forged MIME/extension; FFprobe failures returning 422 without objects/jobs; missing download objects; equal-size original fallback |
| [`test_rate_limit_boundaries.py`](tests/test_rate_limit_boundaries.py) | 6 | Last allowed attempt; rejection before the window ends; reset at the exact next window; `Retry-After`; eight concurrent database connections sharing one quota; registration reset; login limits across IPs and email case |
| [`test_cli_admin_boundaries.py`](tests/test_cli_admin_boundaries.py) | 13 | Creating an admin with a 12- or 128-character password permits HTTP login and admin access. Rejection cases cover lengths 11/129, mismatched confirmation, invalid email, duplicate accounts, and EOF or Ctrl+C at either password prompt. |
| [`test_password_boundaries.py`](tests/test_password_boundaries.py) | 6 | Invalid login password lengths preserving the current session; safe rejection of damaged stored hashes; successful login upgrading an outdated Argon2 hash |
| [`test_filename_handling.py`](tests/test_filename_handling.py) | 10 | Job history and signed download names after directory removal, control-character removal, truncation to 160 characters, or fallback to `upload`. Covers Unix and Windows paths, Unicode, punctuation, original files, and WebP/MP4 results. |

Run the new suites from the repository root, using the same **disposable**
`TEST_DATABASE_URL` as above (fixtures truncate its application tables):

```sh
uv run --extra dev pytest -q \
  tests/test_media_boundaries.py tests/test_api_boundaries.py \
  tests/test_rate_limit_boundaries.py tests/test_cli_admin_boundaries.py \
  tests/test_password_boundaries.py tests/test_filename_handling.py

# Run the complete regression suite, including the existing real FFmpeg test.
uv run --extra dev pytest -q
```

**Recorded local verification — November 22, 2026:** the new suites reported
`96 passed`, and the complete suite reported `203 passed` (22.10 seconds), using
Python 3.12.16, native PostgreSQL 16.2, Moto S3, Pillow, and real FFmpeg/FFprobe
for the existing encoding integration test. One existing Starlette TestClient
deprecation warning was emitted. The media boundary suite uses real image decoding
and sparse files for byte limits; its FFprobe metadata/failure cases are stubbed,
so they do not measure encoding performance of 100 MiB or two-minute videos.
GitHub Actions runs the suite against PostgreSQL 17. The local run including
the three restored-media audit tests reported `206 passed` in 21.59 seconds.

The tests exposed two input-error handling gaps, fixed with minimal changes:
invalid FFprobe document/field types now become `InvalidMedia` / HTTP 422 instead
of unhandled exceptions, and invalid stored Argon2 hash encodings now reject login
with HTTP 401 instead of returning HTTP 503. Other cloud fault-injection and
transactional object-cleanup scenarios remain separate validation work.

Run end-to-end smoke, link-expiry, and encrypted recovery drills against a running Compose stack:

```sh
uv run python scripts/smoke.py --base-url http://localhost:8080
docker compose exec -T api python scripts/check_link_expiry.py
docker compose --profile backup build backup
docker compose run --rm --no-deps backup python scripts/recovery_smoke.py
```

---

## Deployment Information

- **Live URL:** [https://fastzip1779.site](https://fastzip1779.site) (readiness endpoint: `https://fastzip1779.site/api/health/ready`)
- **Cloud Provider & Topology:** Two DigitalOcean Droplets (`fastzip-manager` and `fastzip-worker`) in a single private VPC running Docker Swarm:
  - **Manager Droplet (`fastzip.db=true`, `fastzip.app=true`):** Caddy (`frontend` 1/1), PostgreSQL 17 mounted to the DigitalOcean Block Storage Volume at `/mnt/fastzip_postgres/postgres` (ext4 `/dev/sda`), API replica 1 (`fastzip_api` 2/2 total), `cleanup` 1/1, `backup` 1/1, and `worker`.
  - **Worker Droplet (`fastzip.app=true`):** API replica 2 and `worker` (`fastzip_worker` 2/2 steady state).
  - **Storage & Monitoring:** Two private DigitalOcean Spaces buckets (media and encrypted backups), DigitalOcean `do-agent` confirmed active on both nodes, Insights alert rules configured for CPU (≥ 80%), memory (≥ 85%), and root disk (≥ 80%) over 5-minute windows, plus `deploy/volume-watch.timer` for the attached Volume.

### Observed Cloud Validation (November 20–21, 2026)

- **Production deployment & smoke test:** Deployed four-image GHCR release `4b2afbec2c878634cc8aa76774018a42828d2a34` via `scripts/deploy.sh`; all six Swarm services converged (`api` 2/2, `worker` 2/2, `postgres`/`frontend`/`cleanup`/`backup` 1/1) and live HTTPS upload → compress → signed-download smoke testing returned `SUCCEEDED`.
- **Horizontal worker scaling (`https://fastzip1779.site`):** Benchmarked 12 jobs per run using the same 17.01 MB MP4 file and `balanced` preset (single trial per worker count; includes sequential upload and polling):

| Workers | Jobs | Succeeded | Elapsed (s) | Throughput (jobs/min) | Speedup vs. 1 Worker | Retries |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 12 | 12 | 797.271 | 0.903 | 1.00x | 0 |
| 2 | 12 | 12 | 390.545 | 1.844 | 2.04x | 0 |
| 4 | 12 | 12 | 209.112 | 3.443 | 3.81x | 0 |

- **Off-node encrypted backup & restore:** After updating `BACKUP_AGE_RECIPIENT` to the operator-held public key, a live encrypted backup (`hourly/2026-11-20T22-43-10.dump.age`) from the private Spaces backup bucket was decrypted offline (`age -d`, exit `0`) and restored via `pg_restore` (exit `0`) into a network-isolated local PostgreSQL 17 container, recovering **4 users, 2 jobs, and 8 tables** without modifying production.
- The two restored production jobs still need verification against the private Spaces media bucket. The recovery guide includes the command to audit original and output keys and byte counts on the operator's machine. Production outage and write timestamps are still needed for a historical RTO and RPO. [Operator procedure](docs/recovery.md#check-a-downloaded-archive-against-its-media-bucket)
- **Volume alert delivery.** In an isolated Ubuntu VM, the systemd timer sent an email after a separate ext4 disk stayed above 75% for five minutes. The test inbox received it in 306 seconds, with the same message ID as the timer's journal. Production acceptance still needs the DigitalOcean alert email, the manager's Volume timer evidence and its email in the configured team mailbox. [Measured results](docs/validation.md#volume-alert-delivery) · [Production verification](docs/deployment.md#verify-alert-delivery)
- **Production fault-injection scope.** Intentional PostgreSQL/worker container-kill drills and forced bad-image rollback drills remain pending in production. [Validation record](docs/validation.md)

- **Windows release command:** After infrastructure preparation and GHCR image publication, copy `deploy/cloud.psd1.example` to `deploy/.env.cloud.psd1`, configure SSH and the published commit SHA, and run:

```powershell
powershell -NoProfile -File .\scripts\deploy-cloud.ps1
```

Full step-by-step cloud setup, secrets management, rollback procedures, and benchmark details are documented in:

- [Two-Droplet Swarm deployment & CI/CD runbook](docs/deployment.md)
- [Encrypted backup & fresh-database recovery guide](docs/recovery.md)
- [Scaling, crash-recovery & rollback experiment procedures](docs/experiments.md)
- [Verification & validation evidence log](docs/validation.md)

### Fault recovery in an isolated Swarm

On November 22, 2026, we ran the four faults below on two local Ubuntu 24.04.5 arm64 VMs using Docker Engine 29.1.3 and FastZip release `e9437ef`. We repeated the fourth fault on a fresh two-node Swarm after fixing the deployment script. PostgreSQL 17.12 used a dedicated ext4 filesystem, private MinIO buckets held the media and backups, and the API image trusted the lab's Caddy certificate authority.

Before fault injection, all six services were running, including two API and two worker replicas. An HTTPS upload completed and its signed download returned a 944-byte WebP. Times below are measured from the fault or deployment request.

| Fault injected | Result | What we observed |
| --- | --- | --- |
| Killed a worker with `SIGKILL` while FFmpeg was encoding a 30-second 720p video | **Pass** | The container exited with code 137. A different worker reclaimed job `ef51495e-0239-4530-a551-add6f3c42f81` at 60.88 s. At 66.40 s the signed download returned a 6,171,963-byte H.264 video. MinIO contained exactly one result for this job, from attempt 2. |
| Forced Swarm to replace the PostgreSQL container on the same volume | **Pass** | Container `8618f71de06b` was replaced by `bde5d36ae802` on `fastzip_database`. The database system ID and counts matched before and after (2 users, 2 sessions, 3 jobs). At 15.17 s the original session had retrieved its job history and downloaded the same video with a matching SHA-256. A fresh login also succeeded. |
| Updated the frontend service to lab image `ec76339`, whose health check always exited 1 | **Pass** | Swarm started rolling back at 21.36 s and reported `rollback_completed` on `e9437ef` at 49.58 s. The release verifier rejected the candidate. By 50.93 s a fresh PNG upload had completed and its signed download returned a 944-byte WebP. |
| Deployed lab release `1d7a9c7`, whose workers passed health checks but failed to encode | **Pass on retest** | Both workers were healthy and API readiness returned HTTP 200. The failed compression job triggered automatic rollback to `e9437ef`. By 174.10 s the script had verified a fresh upload, compression, and signed download on the restored release. |

**Why the first run failed.** Smoke job `6979b2be-9964-4100-950b-c1058b08b1e5` failed after three attempts. The deploy script exited 1 at 123.56 s, and the next upload returned HTTP 503. At the time, only the forward deployment added writable `/tmp` mounts after applying the stack. Both restored API replicas hit `EROFS`, and backup could not write its heartbeat. A manual redeployment restored service in 84.54 s. A second upload from the existing account also succeeded.

**Automatic rollback with the fix.** The archived `e9437ef` stack predated the new mount file. At 91.90 s we observed that candidate job `3d9388aa-356a-48f4-8768-3d4f4608ca17` had failed after three attempts and the worker service was switching back. The script applied the writable mounts with the archived stack. All six services converged. Its rollback smoke job `00903b7c-674e-4e58-a65f-2dde8d59f2ba` uploaded a 2,791-byte PNG and downloaded the 944-byte result in 2.279 s. The script exited 1 for the rejected candidate and `/opt/fastzip/current-tag` still pointed to `e9437ef`. Recovery required no manual deployment. [Deployment script](scripts/deploy.sh) · [Swarm mount configuration](deploy/tmpfs.yaml)

We then used a session opened before deployment to submit two fresh jobs. Both completed on their first attempt and both signed downloads returned HTTP 200.

| New upload after rollback | Downloaded output | Verification |
| --- | --- | --- |
| 2,790-byte PNG | 944-byte WebP | Pillow decoded the 800 × 600 image. The download finished 176.91 s after the deployment request. |
| 747,646-byte MP4 | 234,843-byte MP4 | The upload returned HTTP 202. FFprobe reported H.264 at 640 × 360 for 4.00 s. FFmpeg decoded all 96 frames. |

On the restored release, UID `10001` created, read, and deleted a temporary file in all six application containers. Each root filesystem was read-only. Each `/tmp` had mode `1777` and the expected capacity, with 256 MiB per API and worker replica, 128 MiB for backup, and 64 MiB for cleanup.

---

## Video Demo

> **TODO (Team Action Required before December 5, 2026):** Insert the URL to our 2–5 minute team-narrated video demo here (must use team members' own voices; AI-generated voiceover is not permitted).

The demo will showcase:
1. End-to-end user workflow on the live deployment ([https://fastzip1779.site](https://fastzip1779.site)): registration, image/video upload, preset compression, size savings, and private signed download.
2. Core cloud infrastructure: Docker containers, PostgreSQL state persistence on the DigitalOcean Volume across container restart, and Docker Swarm service replication/load balancing.
3. Monitoring & observability: Admin Portal telemetry, DigitalOcean Droplet metrics/alerts, worker heartbeats, and structured error logs.
4. Advanced features: Security controls, GitHub Actions CI/CD release pipeline, and encrypted backup/restore.

---

## AI Assistance & Verification

AI tools (OpenAI Codex / LLM assistants) were used selectively to accelerate exploration, scaffolding, and code review while our team retained ownership of architectural decisions, integration, and verification:

- **Where AI meaningfully contributed:** Exploring failure modes in PostgreSQL-based worker queues, scaffolding the initial FastAPI/React/Docker/Swarm codebase and test suite, reviewing frontend state/error handling, and auditing deployment scripts against course requirements.
- **Representative mistakes & limitations caught in AI output:**
  1. Early AI-assisted design suggestions risked privilege escalation if admin status were keyed to an unverified email address, and initially focused on `SKIP LOCKED` without lease-expiry fencing—we switched to CLI-only admin creation and added renewable leases + attempt-ID output fencing.
  2. Initial container configurations referenced the discontinued Docker Hub MinIO image, assumed `docker stack deploy` honored Compose `tmpfs:` shorthand on read-only containers, enabled IPsec ESP overlay encryption incompatible with DigitalOcean Cloud Firewalls, and generated an invalid DigitalOcean CPU alert metric string (`cpu_utilization_percent` instead of `v1/insights/droplet/cpu`).
  3. An initial frontend upload handler awaited job-history refresh inside the upload try/catch block (surfacing refresh errors as upload failures) and calculated active quota from only the current paginated list.
- **How correctness was verified:** Verification combines Ruff lint/format checks, PostgreSQL-backed integration and regression tests (row-lock races, stale-worker fencing, CSRF/RBAC, boundary cases, real FFmpeg encoding), frontend Prettier/TypeScript/Vite builds, real MinIO Compose smoke + signed-link expiry + encrypted recovery drills in GitHub Actions CI, desktop/mobile browser testing, and live deployment verification on DigitalOcean Swarm (`https://fastzip1779.site`). See the boundary-test section above for the latest local counts and its distinction from the existing CI results.

See [`ai-session.md`](ai-session.md) for four concrete, structured AI interactions documenting prompts, responses, critical evaluation, and verification.

---

## Individual Contributions

Team ownership was established in the [project proposal](proposal/FastZip_Proposal.md) and reflected in repository development and integration history:

| Member | Responsibilities & Verified Contributions |
| --- | --- |
| **Kevin Li** | Project proposal & architecture diagrams; compression worker workflow (Pillow/FFmpeg), leasing/retry/fencing logic, storage & cleanup modules, frontend UI polish & responsive layout, Windows local (`start.bat`) and cloud (`scripts/deploy-cloud.ps1`) release tooling, final branch integration, CI/alert/backup reliability fixes, and final report alignment. |
| **Ouyi Yang** | GitHub Actions CI (`ci.yml`) and multi-platform GHCR Release (`release.yml`) workflows; React frontend & end-to-end smoke validation; live two-Droplet DigitalOcean Swarm integration & debugging (PR #1, PR #2: Swarm convergence, writable tmpfs mounts, overlay networking fix). |
| **Xi Chen** | Docker Compose & two-Droplet Swarm stack design (`compose.yaml`, `deploy/stack.yaml`, `deploy/bootstrap.yaml`), DigitalOcean Volume persistence, Caddy HTTPS & Swarm secrets architecture, DigitalOcean monitoring/alerting (`configure_alerts.py`, `volume-watch`), and encrypted `age` backup & recovery design. |
| **Yuxin Duan** | FastAPI REST API design (`api.py`), email accounts & PostgreSQL session management, Argon2id/CSRF/rate-limiting security controls (`security.py`), role-based authorization & CLI-only admin provisioning, deep file signature/Pillow/FFprobe upload validation, and admin telemetry APIs. |

*(Note: Early implementation commits on `develop` were authored collaboratively using AI-assisted scaffolding and subsequently reviewed, modified, deployed, and merged via pull requests by team members.)*

---

## Lessons Learned and Concluding Remarks

Building and deploying FastZip surfaced several practical cloud-engineering lessons that cannot be observed in purely local development:

1. **Local Compose does not guarantee Swarm or cloud parity:** Moving from Docker Compose to two-Droplet Swarm revealed real infrastructure subtleties—Docker Hub removing official MinIO images (solved via a pinned source build), `docker stack deploy` ignoring Compose `tmpfs:` shorthand on read-only containers (fixed by declaring `type: tmpfs` volumes for both rollout and rollback), DigitalOcean Cloud Firewalls not supporting IPsec ESP rules for encrypted overlays, and DigitalOcean's built-in disk alert covering only the root disk rather than the attached Volume (requiring a dedicated host systemd timer).
2. **State coordination requires defensive concurrency design:** Relying solely on `FOR UPDATE SKIP LOCKED` is insufficient when workers crash mid-encode. Combining renewable leases, bounded retries, attempt-specific output keys, and atomic publication guards prevents stale workers from corrupting newer results without introducing an external message broker.
3. **Defense-in-depth and real recovery drills catch silent failures:** Hardening the application required validating media bytes rather than MIME headers, keeping `age` decryption keys completely offline, restricting admin accounts to server-side CLI creation, and redacting presigned URLs from CI logs. Equally important, actually performing an off-node restore drill caught an early `BACKUP_AGE_RECIPIENT` misconfiguration (pointing to an `age-keygen --help` example key) and a Windows PowerShell 5.1 binary-pipe quirk (`cmd.exe /c` required for streaming `age -d | pg_restore`)—proving that an untested backup is not a verified recovery plan.
4. **Automated end-to-end CI catches real integration regressions:** Running real PostgreSQL 17 tests, real MinIO link-expiry checks, encrypted backup/restore drills, and strict formatting gates in GitHub Actions prevented regressions from reaching production and gave the team confidence during iterative cloud releases.
