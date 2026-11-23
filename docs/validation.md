# Validation evidence

For focused session, CSRF, and authorization regression coverage, reproducible
commands, and recorded results, see [Security regression QA](security-qa.md).

The original implementation was validated locally on November 14, 2026 (America/Toronto) using isolated Python/Node processes, a disposable PostgreSQL database, and Moto for S3-compatible tests; GitHub subsequently ran Docker Compose with real MinIO. **Additional local and production cloud evidence was collected on November 20–21, 2026**, as detailed below. Test outcomes are tied to the versions and environments actually exercised; passing a smoke test does not establish every reliability or security requirement.

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

The frontend and Python dependencies are locked. The first GitHub run passed PostgreSQL 17 tests and frontend checks; its Compose step identified the removed Docker Hub MinIO image. Compose now builds a pinned upstream source revision instead, per [MinIO’s source distribution guidance](https://github.com/minio/minio#source-only-distribution). CI repeats PostgreSQL 17 and frontend checks and builds/starts the full Compose stack with real MinIO before a release can proceed. It additionally builds the backup image, runs a fresh-database encrypted recovery drill, checks provider-side link expiry using a short disposable signature, and parses production Swarm configuration. See the GitHub Checks run for the pushed commit for its actual result. Outstanding evidence includes real worker/container loss, database Volume survival across container replacement, actual email alert delivery, attached-Volume alert delivery, backup RPO/RTO measurement, encrypted overlay networking, and broken-image/smoke-failure rollback drills.

## Additional local verification (November 20, 2026)

On Windows with Docker Desktop, FastZip ran with real PostgreSQL 17, MinIO, Caddy, and compression workers. Local backend verification reported 20 PostgreSQL-backed tests plus Ruff lint/format success; the frontend passed formatting, TypeScript, and Vite checks. A real image upload/compression/download produced a 944-byte result from a 2,790-byte PNG; an MP4 fixture also completed a successful conversion. In a local encrypted recovery drill, 5 test accounts and 3 jobs were restored from a MinIO backup to a fresh database. Those measurements describe **local** development, not the DigitalOcean production performance results below.

## DigitalOcean cloud validation (November 20–21, 2026)

| Area | Evidence observed | Scope / limitation |
| --- | --- | --- |
| Two-node orchestration | `fastzip-manager` (Swarm leader) and `fastzip-worker` both `Ready` | One manager; no database HA is claimed |
| Service convergence | `fastzip_api` 2/2 and `fastzip_worker` 2/2; PostgreSQL, frontend, cleanup, backup each 1/1 | Recorded after correcting writable tmpfs and replica-count parsing |
| Production release | `Deployed and smoke-tested 4b2afbec2c878634cc8aa76774018a42828d2a34`; `current-tag` matched | Deployment executed from manager with `scripts/deploy.sh` |
| Live application | Public `https://fastzip1779.site` accessed; image and video compression worked | End-to-end smoke reported `SUCCEEDED` |
| Private database persistence | PostgreSQL data directory on mounted `/mnt/fastzip_postgres/postgres` (ext4 `/dev/sda`) | Survival of a deliberately replaced PostgreSQL container not separately tested |
| Encrypted Spaces backup | New `.dump.age` file created after correcting `BACKUP_AGE_RECIPIENT` | Earlier objects encrypted with mismatched recipient not proven recoverable |
| Actual off-node recovery | New backup decrypted successfully; PostgreSQL 17 archive restored to an isolated local container with **4 users, 2 jobs, 8 tables**; `pg_restore` exit `0` | No app cutover, RPO/RTO measurement, or media reconciliation test performed |
| Horizontal scaling | 12/12 successful at 1, 2 and 4 workers; **0.903 / 1.844 / 3.443 jobs/min**, respectively | One measured trial at each setting; HTTP upload and polling included |
| Provider monitoring | `do-agent` active on both Droplets; CPU >=80%, memory >=85%, root disk >=80% rules created (5-minute windows) | Production team-mailbox receipt and a timer sample from the manager's attached Volume are pending. [Isolated delivery test](#volume-alert-delivery) |
| Swarm network security | Cross-node functionality succeeded with **unencrypted** overlay | Encrypted overlay compatibility still requires separate validation |

The backup is a real production-object recovery, not merely a successful backup upload. See [recovery.md](recovery.md). The 1/2/4-worker benchmark used the same 17.01 MiB-class video, 12 jobs, and balanced preset; see [experiments.md](experiments.md) for raw measurements and interpretation.

The observed single-run scaling result does not establish performance at arbitrary concurrency or across different file types. Production fault-injection drills and production notification delivery are outside these observations.

## Volume alert delivery

On November 22, 2026 (Toronto), an isolated Ubuntu 24.04.5 VM ran the repository's `volume-watch.service` and `volume-watch.timer` under systemd 255. The timer was enabled and active. Two separately mounted ext4 loop devices supplied the disk samples. Mailpit 1.34.0 received mail on loopback over authenticated STARTTLS with a verified lab certificate. The timestamps below are UTC on November 23.

| Timer check | Usage and threshold | First reading above threshold | Message present in the lab inbox | Time to receipt |
| --- | --- | --- | --- | ---: |
| Normal watcher on `/mnt/fastzip_postgres` | 82.00% used, above 75% | 04:12:59.770 | 04:18:06.086 | 306.32 seconds |
| Delivery check on a lightly used test Volume | 8.04% used, above the 0% test threshold | 04:13:00.946 | 04:18:06.086 | 305.14 seconds |

For both emails, the journal ID matched the original email header and body. The normal alert carried `<179540748595.1888.2638540449595522509@lima-fzalert>`. At 04:17:58 UTC the inbox was still empty. Sampling both high readings again after delivery left the count at two. Returning the first disk to 8.04% cleared its high-usage window. The watcher rejected both `/` and an ordinary directory. In a separate recipient-rejection check, the relay accepted one address and refused another. The watcher recorded `partial`, reported one refusal and exited with status 1.

**Cloud acceptance remains open.** The two outstanding receipts are a DigitalOcean resource alert in the configured team mailbox and an alert from the production manager's mounted PostgreSQL Volume in that mailbox. For the latter, also capture the manager's mount, active timer and a recent journal sample. [Production verification procedure](deployment.md#verify-alert-delivery)
