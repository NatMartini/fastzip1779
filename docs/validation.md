# Validation evidence

Validated locally on 2026-11-14, America/Toronto. Docker was unavailable on the implementation host. Native isolated Python 3.12 / Node 22 processes and an actual disposable PostgreSQL server were used; S3 HTTP smoke tests used Moto's S3-compatible development server. MinIO/Compose, two-Droplet Swarm, Spaces, HTTPS certificates, external alert delivery, and cloud rollout behavior must be verified by CI / deployment separately.

| Check | Observed result |
| --- | --- |
| PostgreSQL-backed automated tests | 20 passed, including row-lock races, per-user concurrent quota, session persistence/revocation, CSRF, ownership/admin isolation, retry exhaustion, stale-worker fencing, expiry/reconciliation, malformed uploads, and real FFmpeg H.264/AAC encoding |
| Python lint/format | Ruff checks and formatting passed |
| Frontend | TypeScript and Vite production build passed; npm audit reported zero vulnerabilities at installation |
| Real HTTP image smoke | 2,790 input bytes → 944 downloaded bytes, successful queued worker completion and signed download |
| Real HTTP video smoke | 310,426 input bytes → 215,709 downloaded bytes; 3-second 640×360 generated H.264/AAC fixture |
| Browser user flow | Playwright MCP: registration → upload → wait for completion → download `.webp`; original download available |
| Browser responsive check | 390px viewport: no horizontal document overflow; history and download usable |
| Browser admin flow | Administrator login, worker heartbeat, latest backup, structured error table, and unconfigured cloud-metrics state displayed |
| Encrypted backup and fresh restore | 16,755 encrypted bytes; 3 jobs / 4 users before and after restore; CLI restore ~0.35s, whole tiny-fixture drill ~1.22s |

Measurements above describe tiny generated fixtures, not representative compression quality or cloud performance. The local backup drill used PostgreSQL 16 tools matching its temporary server; deployment uses PostgreSQL 17 and matching backup-client tools. Provider-side signed URL expiry was not inferred from Moto's behavior.

The frontend and Python dependencies are locked. CI is configured to repeat checks with PostgreSQL 17 and to build/start the full Compose stack with real MinIO before a release can proceed. See the GitHub Checks run for the pushed commit for its actual result. Outstanding cloud evidence includes one/two/four-worker throughput, real worker/container loss, database Volume survival across redeployments, issued-certificate verification, actual email alerts, backup RPO measurement, and broken-image/smoke-failure rollback drills.
