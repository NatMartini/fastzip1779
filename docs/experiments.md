# Reproducible course experiments

Run destructive failure experiments on a disposable environment and retain timestamped logs and result JSON. A real DigitalOcean worker-scaling benchmark was measured on November 20–21, 2026; other fault-injection scenarios below remain procedures rather than completed cloud results.

## Worker counts 1, 2, and 4

Use the same representative video, file preset, number of jobs, hardware, and empty starting queue each time. Benchmark accounts are reused from a mode-600 file under ignored `work/`, avoiding repeated registration limits. Do not commit that file.

```sh
docker compose up -d --scale worker=1
uv run python scripts/benchmark.py --file sample.mp4 --workers 1 --jobs 12 --output work/scale-1.json
docker compose up -d --scale worker=2
uv run python scripts/benchmark.py --file sample.mp4 --workers 2 --jobs 12 --output work/scale-2.json
docker compose up -d --scale worker=4
uv run python scripts/benchmark.py --file sample.mp4 --workers 4 --jobs 12 --output work/scale-4.json
```

For Swarm, use `docker service scale fastzip_worker=N` instead. Record CPU, memory, elapsed time, jobs/minute, failure count, per-job processing times, and attempts. Registration/login preparation is excluded from the workload timer; sequential HTTP upload and job polling time **are** included. Four replicas share the two hosts' compute capacity with the API/database; do not assume linear scaling. Restore the steady-state replica setting afterwards and verify it is 2/2.

### Observed DigitalOcean benchmark (November 20–21, 2026)

An operator PC executed `py scripts/benchmark.py --base-url https://fastzip1779.site` against the live two-Droplet Swarm. Each run used the **same 17.01 MB MP4**, the **balanced** preset, **12 jobs**, and the same benchmark account file. The worker service was configured for 1, 2, or 4 replicas before the corresponding run. These were **one trial per worker count**; no repeated-run confidence interval or CPU/memory traces were collected.

| Workers | Jobs | Succeeded | Elapsed (s) | Throughput (jobs/min) | Speedup vs. 1 worker | Attempts |
| ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 12 | 12 | **797.271** | **0.903** | **1.00x** | All 1 |
| 2 | 12 | 12 | **390.545** | **1.844** | **2.04x** | All 1 |
| 4 | 12 | 12 | **209.112** | **3.443** | **3.81x** | All 1 |

Across the three runs, **36/36** jobs succeeded with no retries. With four workers, observed throughput improved by approximately **281%** over one worker. Results show strong scaling for this workload, not a general performance guarantee; tasks are uploaded sequentially, the run count is small, and external traffic/resource conditions may vary.

Raw JSON outputs were written locally to ignored paths `work/scale-1.json`, `work/scale-2.json`, and `work/scale-4.json` (not embedded in Git). For provenance, retain the original result JSON and relevant status screenshots when preparing the course report. The recorded JSON fields are `workers`, `jobs`, `succeeded`, `elapsed_seconds`, `jobs_per_minute`, `processing_seconds`, and `attempts`.

Example production benchmark command (for the two-worker run; use a tested period without competing user traffic):

```powershell
py scripts/benchmark.py `
  --base-url https://fastzip1779.site `
  --file .\work\benchmark.mp4 `
  --workers 2 `
  --jobs 12 `
  --output .\work\scale-2.json
```

After the 4-worker test, return the production service to its normal replica count and verify it before concluding the experiment:

```powershell
ssh root@MANAGER_PUBLIC_IP "docker service scale fastzip_worker=2"
ssh root@MANAGER_PUBLIC_IP "docker service ls --filter name=fastzip_worker"
```


## Worker crash and fencing

Start a sufficiently long video job. Once `PROCESSING`, inspect its ID and attempt count in the account. Kill the specific processing worker with `docker kill --signal KILL CONTAINER_ID`. Ensure a replacement worker exists. After the old 60-second lease expires, the job should be reclaimed, increment its attempts, and finish. The old attempt must not publish a result. Record the interruption and recovery times; finalization is atomic, but encoding itself can occur more than once.

Automated PostgreSQL tests additionally race four claimers for three jobs, expire a lease, and explicitly try to publish/renew from the obsolete attempt. They verify only the replacement attempt can publish.

## Swarm rollout and smoke-test rollback

After establishing a known-good tag, create a deliberately broken test image that fails its health check. Deploy it in a disposable Swarm and observe `UpdateStatus`, task history, and the automatic service rollback. The deployment script should fail if it sees a rollback instead of treating old healthy tasks as a successful release.

Separately use a test release whose health check passes but whose compression path fails; the full HTTP smoke test should fail and `scripts/deploy.sh` should redeploy `/opt/fastzip/current-tag`. Verify the old application still works against the forward-migrated database. Retain logs and tags. These cloud rollout drills require an actual Swarm; a unit test cannot establish that they work in the target environment.

## Security and persistence

Use two ordinary accounts and one CLI-created admin. Confirm cross-account job/detail/download requests return 404, non-admin dashboard requests return 403, and an admin cannot download another account's file. Try invalid signatures, a truncated image, excessive pixels, >20 MiB images, >100 MiB videos, >1080p video, and >120-second video. Inspect built images/configuration for accidental secrets without printing actual secret values. Check signed URL rejection after its lifetime against MinIO/Spaces; mocked S3 does not establish signature expiry. Replace PostgreSQL's container while retaining the Volume, then confirm accounts, sessions, and job history survive. Perform the separate fresh-database restore drill in recovery.md.

