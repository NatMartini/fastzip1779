# Validation evidence

Validated locally on 2026-11-14, America/Toronto. Docker was unavailable on the implementation host. Native isolated Python 3.12 / Node 22 processes and an actual disposable PostgreSQL server were used; S3 HTTP smoke tests used Moto's S3-compatible development server. GitHub CI subsequently built and ran the actual Docker Compose stack with MinIO, including its HTTP upload-to-download smoke test. Two-Droplet Swarm, Spaces, HTTPS certificates, external alert delivery, and cloud rollout behavior still require target-environment validation.

| Check | Observed result |
| --- | --- |
| PostgreSQL-backed automated tests | 20 passed, including row-lock races, per-user concurrent quota, session persistence/revocation, CSRF, ownership/admin isolation, retry exhaustion, stale-worker fencing, expiry/reconciliation, malformed uploads, and real FFmpeg H.264/AAC encoding |
| Python lint/format | Ruff checks and formatting passed |
| Linux container integration | [Checks run for `2556139`](https://github.com/NatMartini/fastzip1779/actions/runs/43670756040) passed PostgreSQL 17 tests, frontend checks/build, all local application image builds, real MinIO initialization, and HTTP upload-to-download smoke |
| Frontend | TypeScript and Vite production build passed; npm audit reported zero vulnerabilities at installation |
| Real HTTP image smoke | 2,790 input bytes → 944 downloaded bytes, successful queued worker completion and signed download |
| Real HTTP video smoke | 310,426 input bytes → 215,709 downloaded bytes; 3-second 640×360 generated H.264/AAC fixture |
| Browser user flow | Playwright MCP: registration → upload → wait for completion → download `.webp`; original download available |
| Browser responsive check | 390px viewport: no horizontal document overflow; history and download usable |
| Browser admin flow | Administrator login, worker heartbeat, latest backup, structured error table, and unconfigured cloud-metrics state displayed |
| Encrypted backup and fresh restore | 16,755 encrypted bytes; 3 jobs / 4 users before and after restore; CLI restore ~0.35s, whole tiny-fixture drill ~1.22s |

Measurements above describe tiny generated fixtures, not representative compression quality or cloud performance. The local backup drill used PostgreSQL 16 tools matching its temporary server; deployment uses PostgreSQL 17 and matching backup-client tools. Provider-side signed URL expiry was not inferred from Moto's behavior.

The frontend and Python dependencies are locked. The first GitHub run passed PostgreSQL 17 tests and frontend checks; its Compose step identified the removed Docker Hub MinIO image. Compose now builds a pinned upstream source revision instead, per [MinIO’s source distribution guidance](https://github.com/minio/minio#source-only-distribution). CI repeats PostgreSQL 17 and frontend checks and builds/starts the full Compose stack with real MinIO before a release can proceed. It additionally builds the backup image, runs a fresh-database encrypted recovery drill, checks provider-side link expiry using a short disposable signature, and parses production Swarm configuration. See the GitHub Checks run for the pushed commit for its actual result. Outstanding cloud evidence includes one/two/four-worker throughput, real worker/container loss, database Volume survival across redeployments, issued-certificate verification, actual email alerts, backup RPO measurement, and broken-image/smoke-failure rollback drills.

## Local verification on 2026-11-20

Docker Desktop ran FastZip at `http://localhost:8088`, using real PostgreSQL 17, MinIO, Caddy, and a compression worker. This port was selected because another service was listening on 8080.

| Check | Observed result |
| --- | --- |
| Backend | 20 tests passed against a disposable PostgreSQL 17 database. Ruff lint and format passed. |
| Frontend | Prettier, TypeScript and Vite passed. Edge verified registration, invalid-file rejection, remove and reselect, preset selection, compression, WebP download, logout and return to the saved job history. The authenticated admin displayed the online worker and unconfigured DigitalOcean metrics. Desktop and 390px layouts were checked. |
| Compression animation | Edge played the 920 ms illustration once on sign-in and new compression. At 1440px and 390px the layouts fit, and reduced-motion mode showed a static illustration. A real PNG compressed from 2,786 to 926 bytes, and history displayed the worker's processing time. |
| Real MinIO downloads | A PNG went from 2,790 to 944 bytes and a three-second MP4 went from 663,777 to 217,341 bytes. MinIO accepted a disposable signed URL before expiry and returned HTTP 403 after expiry. |
| Local encrypted backup and recovery | Restored 5 test accounts and 3 jobs from a MinIO backup into a fresh database. The restore tool reported 0.235 seconds and the drill took 1.466 seconds. |
| Windows and Swarm release entry points | PowerShell argument parsing, pinned-commit packaging, integrity verification, and manager preflight passed with simulated SSH and Docker. Bash syntax, bootstrap/stack rendering, readiness and rollback branches passed locally with no cloud deployment. |

## Proposal audit findings and resolution status

- **Backup scheduling and health (Fixed 2026-11-21).** `backend/fastzip/backup.py` now updates `/tmp/fastzip-backup-heartbeat` from a background keep-alive thread while `backup_once()` runs (bounded by `pg_dump`/`age` timeouts) and schedules the next hourly run from the previous run's start time rather than completion time. Still verify long-running backups and measure actual RPO in the cloud environment. [Backup loop](../backend/fastzip/backup.py) [Health check](../backend/Dockerfile)
- **Alert configuration (Fixed CPU type 2026-11-21; SMTP relay environment-dependent).** `scripts/configure_alerts.py` now sends DigitalOcean's expected `v1/insights/droplet/cpu` policy type for CPU alerts alongside `memory_utilization_percent` and `disk_utilization_percent`. Note that DigitalOcean Droplets block outbound SMTP ports by default; configure an allowed mail relay/API before demonstrating Volume-watcher email delivery. [Alert builder](../scripts/configure_alerts.py) [DigitalOcean alert API](https://docs.digitalocean.com/products/monitoring/reference/api/) [Droplet SMTP policy](https://docs.digitalocean.com/support/why-is-smtp-blocked/)
- **Cross-node networking and writable `/tmp` mounts (Fixed in PR #2 & 2026-11-21).** Removed `driver_opts: {encrypted: "true"}` from `deploy/stack.yaml` and `deploy/bootstrap.yaml` because DigitalOcean Cloud Firewalls do not support ESP rules; updated `scripts/deploy.sh` to explicitly attach writable `/tmp` tmpfs mounts via `docker service update --mount-add` (256 MiB for `api`/`worker`, 128 MiB for `backup`, 64 MiB for `cleanup`) while keeping read-only root filesystems, and fixed `scripts/wait_stack.py` replica parsing and Ruff formatting. Live endpoint `https://fastzip1779.site/api/health/ready` returned `{"status":"ready"}` via Caddy HTTPS on 2026-11-21. [Stack configuration](../deploy/stack.yaml) [Deploy script](../scripts/deploy.sh)
- **Signed download logging (Fixed 2026-11-21).** `scripts/smoke.py` now catches download request errors and non-2xx statuses without including the presigned URL in exception messages or CI tracebacks. [Smoke test](../scripts/smoke.py)
- **Admin and image packaging.** The dashboard presents aggregate job counts and error events. Per-job metadata is exposed by the admin-only API (`/api/admin/jobs`) and has no dedicated table view yet. The build produces four images using `frontend/Dockerfile` and a multi-target `backend/Dockerfile` (`api`, `worker`, `backup`). [Admin API](../backend/fastzip/api.py) [Frontend](../frontend/src/main.tsx) [Docker build targets](../backend/Dockerfile)

## Remaining acceptance and course deliverables

- **Capture cloud experiment evidence.** Record cloud evidence for one/two/four-worker throughput (`scripts/benchmark.py`), lease recovery after real worker container kill, PostgreSQL Volume persistence across container replacement, verified DigitalOcean and mounted-Volume alert delivery, a failing CI check blocking release, rollback after a broken image/smoke failure, and off-node encrypted restore with measured RPO and restore duration. [Experiment procedures](experiments.md)
- **Record Video Demo & final review.** Record the 2–5 minute team-narrated demo video (no AI voiceover), add its URL under `## Video Demo` in `README.md`, and have all team members review `Individual Contributions` and `ai-session.md` before the December 5, 2026 deadline.
