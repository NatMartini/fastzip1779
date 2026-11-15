# FastZip

Private image and video compression with a React frontend, FastAPI API, PostgreSQL-backed job queue, and independently scalable workers.

**Course:** ECE1779 · **Team:** Yuxin Duan, Kevin Li, Xi Chen, Ouyi Yang
The original design and team responsibilities remain in [the project proposal](proposal/FastZip_Proposal.md). This branch implements the complete application. The local destination happens to be named ECE779; the course name follows the proposal.

## Run locally

Install Docker Engine / Docker Desktop with Docker Compose v2. No cloud account is required.

```sh
cp .env.example .env
docker compose up --build -d
```

Open **http://localhost:8080**, create an account, and upload a JPEG, PNG, or MP4. Bucket initialization and Alembic migrations run before the API and workers start. The private MinIO console is available at http://localhost:9001 using the local credentials in `.env`. PostgreSQL is not published to the host.

```sh
# Create an administrator through the server-side CLI; prompts for a password.
docker compose exec api fastzip create-admin admin@example.com
# Scale compression independently of HTTP traffic.
docker compose up -d --scale worker=2
# Inspect services and logs.
docker compose ps
docker compose logs --tail=100 api worker cleanup
# Stop while keeping accounts, job history, and media volumes.
docker compose down
```

Do not use `docker compose down -v` unless you intend to erase local data. For access from another device, configure Compose port bindings, `PUBLIC_ORIGIN`, and a publicly reachable `S3_PUBLIC_ENDPOINT`; localhost-only bindings are the default.

## Supported workflow

Register / sign in → upload one file → select a preset → poll job status → download a private result. The API returns `202` after it validates and stores an upload. Jobs are `QUEUED`, `PROCESSING`, `SUCCEEDED`, `FAILED`, or `EXPIRED`. Completed jobs show size savings and processing time. The original is selected automatically when the encoded result is larger.

| Input | Limits | Output |
| --- | --- | --- |
| JPEG / static PNG | 20 MiB, 25 million pixels | WebP, with alpha preserved and EXIF removed |
| MP4 | 100 MiB, 120 seconds, at most 1920×1080 or 1080×1920 | H.264 + AAC MP4 |

The limits use binary megabytes (MiB). Animated PNG, batch uploads, editing, streaming, GPU encoding, email verification, and password reset are outside this MVP. Up to three queued/processing jobs are allowed per account. Media becomes unavailable 24 hours after upload; cleanup deletes objects on its next 30-second sweep. Account job metadata remains.

| Preset | WebP quality | H.264 CRF |
| --- | ---: | ---: |
| Smaller file | 55 | 32 |
| Balanced | 75 | 26 |
| Higher quality | 88 | 21 |

Presets control quality, rather than guaranteeing a target output size. Video encoding uses one FFmpeg thread and a 300-second timeout; the isolated encoding subprocess has an additional outer deadline. Container limits bound CPU and memory.

## Architecture

```text
Browser → Caddy (HTTPS + React) → FastAPI replicas → PostgreSQL
                                      ↓                 ↑
                                private S3 / Spaces ← workers
                                      ↑                 ↓
                                   cleanup      leases / heartbeats

PostgreSQL → pg_dump → age public-key encryption → separate backup bucket
```

PostgreSQL owns accounts, hashed session identifiers, jobs, rate limits, worker heartbeats, error events, and backup results. `FOR UPDATE SKIP LOCKED` assigns jobs without blocking other workers. A 60-second lease is renewed every 10 seconds. Expired leases can be reclaimed up to three attempts. Publication checks the attempt ID and unexpired lease; an old worker cannot replace a newer result. Processing is **at least once** with a single accepted result, rather than an exactly-once encoding guarantee. Each attempt has its own output key; cleanup sweeps orphaned objects after two hours.

The API checks ownership before issuing a signed URL valid for up to five minutes, shortened near media expiry. API replicas share database sessions and rate limits and require no sticky sessions. Admin endpoints expose metadata and operational health; the admin role does not grant access to another user's files.

## Security

Passwords use Argon2id; passwords must contain 12–128 characters. Sessions are random, persisted as keyed hashes, expire after seven days, and are revoked on logout. Cookies are HttpOnly and SameSite=Lax; production requires HTTPS and Secure cookies. All state-changing routes, including login and registration, require a signed double-submit CSRF token; origins are checked against `PUBLIC_ORIGIN`. Login and registration limits are enforced in PostgreSQL across API replicas.

File signatures and decoded image / FFprobe metadata are checked instead of trusting extensions or MIME headers. FFmpeg is invoked without a shell and with a restricted protocol whitelist. Application containers run as UID 10001. Production secrets are read from Swarm-mounted files, and private age recovery keys are never deployed. Use separate bucket-scoped Spaces credentials for media and backups.

## Development and verification

Python 3.12+, Node 22, FFmpeg/FFprobe, and a **disposable PostgreSQL** database are required for non-container development. The tests deliberately refuse SQLite because it cannot verify PostgreSQL row locks.

```sh
uv sync --frozen --extra dev
export TEST_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/fastzip_test
uv run ruff check backend tests migrations scripts
uv run ruff format --check backend tests migrations scripts
uv run pytest -q
cd frontend
npm ci
npm run format:check
npm run build
```

The test database is truncated between tests. Do not point it at a development or production database that contains valuable data. Tests use an S3 mock; the Compose smoke job verifies real MinIO separately. Frontend development runs with `npm run dev`; Vite proxies `/api` to port 8000. For a native API process, set `DATABASE_URL`, `PUBLIC_ORIGIN`, and both S3 endpoints, then run `fastzip migrate` and `uvicorn fastzip.api:app`. The schema is available at `/api/openapi.json`.

```sh
uv run python scripts/smoke.py --base-url http://localhost:8080
uv run python scripts/smoke.py --base-url http://localhost:8080 --file /path/to/video.mp4
```

See [validation evidence](docs/validation.md) for measured local results and explicit outstanding cloud checks. CI runs lint, format, PostgreSQL-backed tests, frontend checks/build, container builds, and an actual Compose upload-to-download smoke test. Main-branch releases depend on all checks, publish SHA-tagged multi-platform GHCR images, and deploy only when `DEPLOY_ENABLED=true` is configured. CI is not a substitute for enabling required checks in repository branch protection.

## Deployment, operations, and recovery

- [Two-Droplet Swarm deployment and CI/CD](docs/deployment.md)
- [Encrypted backups and fresh-database recovery](docs/recovery.md)
- [Failure, rollback, and scaling experiments](docs/experiments.md)
- [AI assistance and implementation decisions](ai-session.md)

`backend/fastzip/` contains the API and background services, `frontend/` the React application, `migrations/` the frozen database migrations, `deploy/` the Caddy / Swarm / host-monitoring configuration, and `scripts/` the smoke test, deployment, monitoring setup, and workload runner. `uv.lock`, `requirements.lock`, and `frontend/package-lock.json` lock application dependencies. Backend Dockerfile targets `api`, `worker`, and `backup` produce separate images; the frontend has its own Dockerfile.
