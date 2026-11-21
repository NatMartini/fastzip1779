# Two-Droplet deployment

This runbook describes the two-Droplet FastZip architecture, operational procedures, and observed cloud validation. Sensitive credentials and SSH access are supplied separately by the team and must not be committed. See [validation evidence](validation.md), [experiments](experiments.md), and [backup recovery](recovery.md) for measured results.

**Observed production deployment (November 20–21, 2026):** A manager (`fastzip-manager`) and a worker node (`fastzip-worker`) formed a two-node DigitalOcean Docker Swarm. A four-image GHCR release was deployed successfully, all six services converged, and the live HTTPS upload/compress/download smoke test passed. The recorded release image tag was `4b2afbec2c878634cc8aa76774018a42828d2a34`. This is a snapshot of the tested deployment, not a claim that every later repository commit has been deployed.

## Infrastructure

Use two Linux Droplets with Docker Engine; the initial infrastructure plan specified 2 vCPU / 4 GB RAM per node. Place them on a private VPC and restrict Swarm TCP 2377, TCP/UDP 7946, and UDP 4789 to communication between the two nodes. Expose TCP 80/443 on the manager and restrict SSH to authorized operators. Do not publicly expose PostgreSQL, API port 8000, or the Caddy health listener. The DigitalOcean `do-agent` service was confirmed `active` on both nodes.

**Actual networking/security tradeoff:** The running Swarm overlay uses an **unencrypted** overlay network after the encrypted configuration caused inter-node connectivity problems. The corresponding encrypted-overlay settings were removed from `deploy/bootstrap.yaml` and `deploy/stack.yaml`. This must not be represented as encrypted east–west traffic. Keep node-to-node ports restricted to the private network, and treat restoration of encrypted overlay networking as a separate hardening task. If re-enabling Swarm overlay encryption, first validate ESP (IP protocol 50) support and firewall behavior in a non-production test. No verified encrypted-overlay result is claimed here.

Attach and mount a dedicated DigitalOcean Block Storage Volume on the manager using DigitalOcean's generated mount instructions. **The actual mount point is `/mnt/fastzip_postgres`, and the PostgreSQL data directory is `/mnt/fastzip_postgres/postgres`.** The verified filesystem was `/dev/sda` (ext4); do not infer the provisioned capacity solely from the original 10 GB design example. The deployed `POSTGRES_VOLUME_PATH` must point to the real data directory. Verify the mount with `findmnt -T /mnt/fastzip_postgres/postgres` before deploying; ensure boot order prevents Docker from starting the database against an unmounted root-disk directory. PostgreSQL's official entrypoint sets data-directory permissions. A container-replacement persistence test has not been separately recorded.

```sh
findmnt -T /mnt/fastzip_postgres/postgres
stat -c '%a %n' /opt/fastzip/production.env  # expected: 600
```

```sh
# Manager, using its private VPC address:
docker swarm init --advertise-addr MANAGER_PRIVATE_IP
docker swarm join-token worker
# Run the generated join command on Droplet B.
# Back on the manager:
docker node update --label-add fastzip.db=true MANAGER_NODE
docker node update --label-add fastzip.app=true MANAGER_NODE
docker node update --label-add fastzip.app=true WORKER_NODE
```

There is one manager. A manager/Volume outage stops the database and control plane; the second node cannot provide database HA. The architecture deliberately accepts this risk and uses off-node encrypted backups. A two-manager Swarm would still fail quorum after losing either manager.

Point `fastzip1779.site` A/AAAA records to the manager. Only publish an AAAA record if IPv6 is configured and reachable. Caddy obtains certificates automatically once DNS and ports are ready.

## Private storage and secrets

Create two private Spaces buckets in the same region, one for media and one for encrypted dumps. Leave public access/CDN disabled. Create separate scoped credentials: media list/read/write/delete for the media bucket; backup list/read/write/delete for the backup bucket. No browser credentials are needed. Presigned GET links carry temporary access and must not be logged or copied into public reports.

Generate an age key pair **on an offline operator machine**, retain the private identity securely, and put only the public `age1…` recipient in server configuration. See recovery.md.

Create these Swarm secrets from protected files or stdin on the manager:

```sh
docker secret create fastzip_db_password /secure/db-password
docker secret create fastzip_session_secret /secure/random-session-secret
docker secret create fastzip_spaces_access /secure/media-access-key
docker secret create fastzip_spaces_secret /secure/media-secret-key
docker secret create fastzip_backup_access /secure/backup-access-key
docker secret create fastzip_backup_secret /secure/backup-secret-key
docker secret create fastzip_do_token /secure/read-only-monitoring-token
```

The session secret must be randomly generated and at least 32 characters. Do not pass secret values directly on command lines, commit these files, or use sample credentials in production. The DO token needs read access for monitoring; alert setup can use a separate operator token with alert-policy write permissions. Swarm secrets are immutable: rotating a Swarm secret requires a new versioned name and a service update.

**Observed age recipient correction:** The initial `BACKUP_AGE_RECIPIENT` mistakenly matched an example key printed by `age-keygen --help`, not an operator-controlled identity. The team changed the public recipient in `/opt/fastzip/production.env`, updated the `fastzip_backup` service environment, generated a new hourly backup, and confirmed it could be decrypted and restored with the locally held identity. Keep only the **public** age recipient in server configuration; never put the private identity into the repo, on the manager, or in a screenshot. Historical objects encrypted to the earlier recipient remain potentially unrecoverable unless its matching private key is found. See [recovery.md](recovery.md).

Copy `deploy/production.env.example` to `/opt/fastzip/production.env`, replace all placeholders, and set mode 600. It holds non-secret resource configuration. Do not change `/opt/fastzip/current-tag` manually. Log both nodes into GHCR if images are private; the deploy script forwards manager registry authentication to Swarm.

## First deployment and later releases

Build and publish images via the Release workflow after its tests and Compose smoke test pass. For the first release, merge the reviewed implementation and workflow into the default `main` branch. Leave `DEPLOY_ENABLED` disabled while publishing images and preparing the manual deployment. Once the workflow is on the default branch, `workflow_dispatch` can build images for `develop`. Automatic deployment requires a `main` release and `DEPLOY_ENABLED=true`. [GitHub manual workflow requirements](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

On the manager, place this checked-out release under `/opt/fastzip/releases/COMMIT_SHA`, then run:

```sh
cd /opt/fastzip/releases/COMMIT_SHA
bash scripts/deploy.sh COMMIT_SHA
```

The script serializes deployments, checks the mounted database path, and bootstraps the database and network if needed. It waits for PostgreSQL before running Alembic as a one-shot Swarm service, then deploys the application and checks convergence, image tags, and an HTTPS upload-to-download smoke test. It records the new tag after success. If rollout fails and a previous successful tag exists, it attempts to redeploy that tag and checks convergence. Fix and rerun a failed first release.

**Actual rollout and fixes (November 20, 2026):** The team staged the checked source under `/opt/fastzip/releases/4b2afbec2c878634cc8aa76774018a42828d2a34` and ran `bash scripts/deploy.sh` on the manager. The first attempts did not converge because application services needed writable `/tmp` despite read-only root filesystems, and `scripts/wait_stack.py` did not handle Docker's replica display `2/2 (max 1 per node)`. The repaired deployment script explicitly adds a tmpfs mount after `docker stack deploy` when needed (initially **256 MiB for `fastzip_api`; 64 MiB each for `fastzip_worker`, `fastzip_backup`, and `fastzip_cleanup`**; subsequently raised to **256 MiB for `fastzip_worker`** and **128 MiB for `fastzip_backup`** to comfortably accommodate 100 MiB MP4 encodes and database dumps). It avoids duplicate mounts on later runs. The corrected convergence check strips the optional replica-count annotation. After these fixes, all six services converged and the end-to-end smoke test returned `SUCCEEDED`; `/opt/fastzip/current-tag` contained the expected full SHA. These script fixes were merged into the `kevin` branch in PR #2.

API placement uses two replicas and `max_replicas_per_node: 1`. Caddy, PostgreSQL, cleanup, and backup stay on the manager. Workers spread across labeled application nodes; each worker handles one job at a time. The database has resource reservations. Non-database services use read-only root filesystems with their explicitly mounted writable `/tmp` filesystems. The frontend update uses stop-first because it binds host ports; a brief frontend interruption is expected during replacement. APIs also cannot start a third temporary replica under the per-node limit, so use stop-first for API updates on this two-node cluster.

**Observed steady-state services:** `fastzip_api` 2/2, `fastzip_worker` 2/2, and `fastzip_postgres`, `fastzip_frontend`, `fastzip_backup`, and `fastzip_cleanup` each 1/1 during successful validation. The worker count was deliberately scaled to 1, 2, and 4 during benchmarking; it should be returned to the configured steady-state **2** afterwards. See [experiments.md](experiments.md).

To create an admin, run the CLI inside an API container on either node:

```sh
docker ps --filter label=com.docker.swarm.service.name=fastzip_api
docker exec -it API_CONTAINER_ID fastzip create-admin admin@example.com
```

## Windows release command

Complete the infrastructure and secret setup above once. Keep the mounted database directory, manager labels, readable `/opt/fastzip/production.env`, Docker access for the SSH user, and a writable `/opt/fastzip` on the manager. Use Git and Windows OpenSSH on the operator PC. Verify the manager's SSH host key through a trusted channel and place it in the `KnownHostsFile` you specify. If the SSH key uses a passphrase, load it into your SSH agent first.

```powershell
Copy-Item .\deploy\cloud.psd1.example .\deploy\.env.cloud.psd1
```

Fill in `ManagerHost`, `ManagerUser`, `KnownHostsFile`, and `ImageTag`. Set `ManagerPort` or `IdentityFile` when needed. Relative paths in this file resolve from the file's directory. Use the full SHA of a commit available locally whose four images passed the GitHub Release workflow and are readable from the manager. The script packages `deploy/` and `scripts/` from that commit, so publish a new commit to include local changes. The filled settings file is ignored by Git.

```powershell
# Optional prerequisite check through SSH.
powershell -NoProfile -File .\scripts\deploy-cloud.ps1 -CheckOnly
# Upload, migrate, deploy, and run the HTTPS smoke test.
powershell -NoProfile -File .\scripts\deploy-cloud.ps1
```

Both commands verify the SSH host key, manager and worker labels, mounted database path, Swarm secrets, and accessible image manifests. The release command stages and verifies the archive before calling `scripts/deploy.sh`. Droplet, VPC, Volume, Spaces, DNS, and alert setup use the one-time steps in this runbook. The HTTPS smoke test requires the public DNS record and Caddy certificate issuance to succeed.

## GitHub Actions configuration

Configure repository variable `DEPLOY_ENABLED=true` only when infrastructure is ready. Configure environment `production` secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY`, and `DEPLOY_KNOWN_HOSTS`. Obtain and verify the server host key through a trusted channel. The workflow does not disable SSH host-key checking. The deploy user needs access to `/opt/fastzip` and Docker; membership in the Docker group is equivalent to host administrative privileges. Configure environment reviewers if desired.

Require the Checks workflow on PRs through repository branch protection. A release runs the same reusable Checks workflow before publishing or deploying. Do not merge destructive schema changes alongside releases relying on image rollback; the initial migration is additive, and later changes must use expand/contract migrations.

## Monitoring and Volume alerts

DigitalOcean Monitoring agents (`do-agent`) were confirmed `active` on **both** `fastzip-manager` and `fastzip-worker`. The team configured alerts through **DigitalOcean Insights / Alert Rules**, covering both Droplets:

| Resource alert | Threshold | Evaluation window | Configuration status |
| --- | --- | --- | --- |
| CPU utilization | >= 80% | 5 minutes | Rule created |
| Memory utilization | >= 85% | 5 minutes | Rule created |
| Root-disk utilization | >= 80% | 5 minutes | Rule created |

A notification channel was configured in the DigitalOcean UI, but **end-to-end notification delivery was not exercised**. The above establishes configuration, not observed email delivery. The existing `scripts/configure_alerts.py` provides an alternative API-based setup path (using DigitalOcean's `v1/insights/droplet/cpu`, `memory_utilization_percent`, and `disk_utilization_percent` types); note that its threshold defaults (`80% / 80% / 75%`) differ from the UI rules above.

DigitalOcean's root-disk utilization alert is **not** a substitute for measuring usage on the separately attached database Volume at `/mnt/fastzip_postgres`. The repository includes `scripts/volume_watch.py`, `deploy/volume-watch.service`, and `deploy/volume-watch.timer` as the intended custom solution. The following are *setup instructions*, not a claim that the Volume watcher is already running:

1. Install `scripts/volume_watch.py` at `/opt/fastzip/scripts/volume_watch.py`.
2. Copy `deploy/volume-watch.service` and `.timer` to `/etc/systemd/system/`.
3. Populate the restricted `/etc/fastzip/volume-watch.env` with `VOLUME_MOUNT=/mnt/fastzip_postgres`, notification settings, and a root-readable password file (mode 600).
4. Verify the alert delivery route before enabling the timer; DigitalOcean Droplets may block outbound SMTP ports 25, 465, and 587 by default. Use an approved reachable relay or provider-supported notification mechanism.
5. Run `systemctl daemon-reload` and `systemctl enable --now volume-watch.timer`, then test mount detection, threshold behavior, and delivery separately.

The Volume watcher is not counted as verified in the cloud evidence. Relevant references: [Swarm stacks](https://docs.docker.com/engine/swarm/stack-deploy/), [Swarm secrets](https://docs.docker.com/engine/swarm/secrets/), and [DigitalOcean resource alerts](https://docs.digitalocean.com/products/monitoring/how-to/manage-resource-alerts/).
