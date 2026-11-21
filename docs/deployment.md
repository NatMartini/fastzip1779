# Two-Droplet deployment

Cloud credentials, DNS, resource IDs, alert recipients, and SSH access are supplied by the team. See [validation evidence](validation.md) for the acceptance checks to run on the target environment.

## Infrastructure

Use two Linux Droplets with Docker Engine, each with 2 vCPU / 4 GB RAM. Choose one private VPC; allow Swarm TCP 2377, TCP/UDP 7946, and UDP 4789 **only between the nodes**. Because DigitalOcean Cloud Firewalls only support ICMP, TCP, and UDP rules (not IP protocol 50 / ESP), the production overlay network relies on private VPC isolation without IPsec ESP overlay encryption; client traffic is protected by Caddy HTTPS and object storage transfers use TLS (with `age` encryption for backups). Expose TCP 80/443 on the manager and restrict SSH to operator/CI addresses. Do not expose PostgreSQL, API port 8000, or the Caddy health listener. Install the DigitalOcean metrics agent on both nodes using the provider's documented installer.

Attach and mount a 10 GB DigitalOcean Volume on the manager, e.g. `/mnt/fastzip`, using DigitalOcean's generated mount instructions. Verify it is mounted before creating `/mnt/fastzip/postgres`. Ensure boot waits for the Volume mount before Docker services start; otherwise a bind directory on the root disk could be used accidentally. PostgreSQL's official entrypoint sets permissions inside its data directory.

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

The session secret must be randomly generated and at least 32 characters. Do not pass values directly on command lines, commit these files, or use local sample credentials. The DO token needs read access for monitoring; alert setup can use a separate operator token with alert-policy write permissions. Secret rotation requires new versioned Swarm secret names and a stack update; Swarm secrets are immutable.

Copy `deploy/production.env.example` to `/opt/fastzip/production.env`, replace all placeholders, and set mode 600. It holds non-secret resource configuration. Do not change `/opt/fastzip/current-tag` manually. Log both nodes into GHCR if images are private; the deploy script forwards manager registry authentication to Swarm.

## First deployment and later releases

Build and publish images via the Release workflow after its tests and Compose smoke test pass. For the first release, merge the reviewed implementation and workflow into the default `main` branch. Leave `DEPLOY_ENABLED` disabled while publishing images and preparing the manual deployment. Once the workflow is on the default branch, `workflow_dispatch` can build images for `develop`. Automatic deployment requires a `main` release and `DEPLOY_ENABLED=true`. [GitHub manual workflow requirements](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)

On the manager, place this checked-out release under `/opt/fastzip/releases/COMMIT_SHA`, then run:

```sh
cd /opt/fastzip/releases/COMMIT_SHA
bash scripts/deploy.sh COMMIT_SHA
```

The script serializes deployments, checks the mounted database path, and bootstraps the database and network if needed. It waits for PostgreSQL before running Alembic as a one-shot Swarm service, then deploys the application and checks convergence, image tags, and an HTTPS upload-to-download smoke test. It records the new tag after success. If rollout fails and a previous successful tag exists, it attempts to redeploy that tag and checks convergence. Fix and rerun a failed first release.

API placement uses two replicas and `max_replicas_per_node: 1`. Caddy, PostgreSQL, cleanup, and backup stay on the manager. Workers spread across labeled application nodes; each worker handles one job at a time. The database has resource reservations. Non-database services use read-only root filesystems and temporary writable storage. The frontend update uses stop-first because it binds host ports; a brief frontend interruption is expected during replacement. APIs also cannot start a third temporary replica under the per-node limit, so use stop-first for API updates on this two-node cluster.

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

Set the real droplet IDs and read-only monitoring token to populate the admin dashboard. The dashboard derives CPU utilization from counter deltas and displays memory and filesystem usage when DO returns metrics. Missing credentials show an unconfigured state; provider errors show unavailable metrics and a structured error.

```sh
export DIGITALOCEAN_TOKEN=OPERATOR_TOKEN
uv run python scripts/configure_alerts.py --droplets ID_A,ID_B --emails team@example.com
```

The script upserts five-minute policies for CPU >80%, memory >80%, and root disk >75%. **DigitalOcean's built-in disk alert applies to root storage, not the attached Volume.** Install the supplied Volume timer for that separate requirement:

1. Put the checked-out `scripts/volume_watch.py` at `/opt/fastzip/scripts/volume_watch.py`.
2. Copy `deploy/volume-watch.service` and `.timer` into `/etc/systemd/system/`.
3. Create root-readable `/etc/fastzip/volume-watch.env` with `VOLUME_MOUNT=/mnt/fastzip`, `SMTP_HOST`, `SMTP_PORT=587`, `SMTP_USER`, `SMTP_FROM`, `ALERT_EMAILS`, and `SMTP_PASSWORD_FILE=/etc/fastzip/smtp-password`. Keep that password file mode 600.
4. Run `systemctl daemon-reload` and `systemctl enable --now volume-watch.timer`.

The timer checks the actual mount, records usage, sends email after >75% for five minutes, and limits reminders to hourly. SMTP is only contacted if the threshold is met. Validate both root and Volume alerts in the target environment. Authoritative references: [Swarm stacks](https://docs.docker.com/engine/swarm/stack-deploy/), [Swarm secrets](https://docs.docker.com/engine/swarm/secrets/), [DO resource alerts](https://docs.digitalocean.com/products/monitoring/how-to/manage-resource-alerts/).
