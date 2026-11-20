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

## Proposal audit findings

- **Backup scheduling and health.** The heartbeat currently pauses during `pg_dump`, encryption, and upload. A slow run can leave the heartbeat older than 60 seconds and lead Swarm to replace the container after failed probes. The next hourly interval starts after the preceding run finishes, which also makes snapshot intervals exceed one hour. Verify long-running backups and measure the achievable recovery point. [Backup loop](../backend/fastzip/backup.py) [Health check](../backend/Dockerfile) [Swarm health behavior](https://docs.docker.com/engine/swarm/how-swarm-mode-works/services/#services-tasks-and-containers)
- **Alert configuration.** The CPU alert builder appends `_utilization_percent`, while DigitalOcean expects `v1/insights/droplet/cpu`. The separate Volume watcher example uses SMTP 587, which Droplets block by default. Correct the CPU policy type and configure a reachable mail relay before demonstrating alert delivery. [Alert builder](../scripts/configure_alerts.py) [DigitalOcean alert API](https://docs.digitalocean.com/products/monitoring/reference/api/) [Droplet SMTP policy](https://docs.digitalocean.com/support/why-is-smtp-blocked/)
- **Cross-node networking and worker placement.** The encrypted Overlay requires ESP on the private node links. DigitalOcean Cloud Firewall allow rules cover ICMP, TCP, and UDP, so check the firewall design for the encrypted Overlay. The worker stack also declares `spread: node.id`, while the scheduler's explicit spread preferences are label-based. Verify placement and authenticated API traffic on both nodes. [Stack configuration](../deploy/stack.yaml) [Firewall limits](https://docs.digitalocean.com/products/networking/firewalls/details/limits/) [SwarmKit scheduler](https://github.com/moby/swarmkit/blob/master/manager/scheduler/nodeset.go#L61-L75)
- **Signed download logging.** The rollout smoke test can include the signed request URL in an HTTP error traceback. Sanitize failed download errors before collecting CI deployment logs. [Smoke test](../scripts/smoke.py)
- **Admin and image packaging.** The dashboard presents aggregate job counts and error events. Per-job metadata is exposed by the admin-only API and has no dashboard view. The build has four image outputs, using a frontend Dockerfile and a shared backend Dockerfile with separate API, worker, and backup targets. [Admin API](../backend/fastzip/api.py) [Frontend](../frontend/src/main.tsx) [Docker build targets](../backend/Dockerfile)

## Remaining acceptance and course deliverables

- **Publish the first release.** As of this check, GitHub's default branch contains the proposal and lacks the Release workflow. Merge the reviewed implementation and publish the four GHCR images for one commit. Prepare the two Droplets, private networking, Volume mount, two private Spaces buckets, DNS, and service secrets. [Windows release command](deployment.md#windows-release-command)
- **Capture cloud evidence.** Demonstrate HTTPS and Secure cookies, private Spaces downloads and five-minute expiry, absence of plaintext credentials in published images and service configuration, API placement and traffic on both nodes, persistence after a PostgreSQL service replacement, and delivery of DigitalOcean and mounted-Volume alerts. Record throughput with one, two, and four workers, lease recovery after worker loss, a failing CI check blocking release, rollback after a bad image and failed smoke test, and encrypted off-node recovery with measured RPO and restore time. [Experiment procedures](experiments.md)
- **Complete the course report and demonstration.** Add the live URL, a 2–5 minute team-narrated demo under `## Video Demo`, verified individual contributions, lessons learned, and the team's AI review and verification summary. The final report also requires the team's own contact details and explanations of motivation and objectives. Report actual cloud and team evidence as it becomes available.
