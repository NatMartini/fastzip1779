# Backup and recovery

Hourly backups use `pg_dump --format=custom`, encrypt with an age public recipient, and upload to a separate private bucket. Only the public key exists on the server. Retention keeps the most recent 24 hourly copies and seven daily copies; a PostgreSQL advisory lock prevents overlapping backup services. Each run writes success/failure metadata; failures also create an error event. Temporary plaintext dumps live only in the service's temporary filesystem and are removed before upload.

## Enable locally

Generate a key pair outside the deployed system:

```sh
age-keygen -o /secure/offline/fastzip.agekey
age-keygen -y /secure/offline/fastzip.agekey
```

Set `BACKUP_AGE_RECIPIENT=age1…` in the local `.env`. Preserve the private file offline. Then:

```sh
docker compose --profile backup up --build -d backup
docker compose exec backup fastzip backup
```

The daemon runs immediately and then hourly; a manual backup is optional. The backup image includes PostgreSQL 17 client utilities and age. Use a pg_dump client at least as new as the source server, and a compatible PostgreSQL target. Backups do not include media; media is intentionally temporary.

## Restore into a new database

Run restore on a trusted recovery machine with the matching age identity, an empty PostgreSQL database and read access to the media bucket. Restoring by object key also requires read access to the backup bucket. During an actual cutover, stop application writers before switching databases. The CLI rejects a target with existing tables.

```sh
fastzip restore \
  --object-key hourly/UTC_TIMESTAMP.dump.age \
  --identity /secure/offline/fastzip.agekey \
  --target-database-url postgresql+psycopg://USER@FRESH_HOST:5432/fastzip_restored
```

Provide source backup credentials, media credentials, endpoints and bucket names via environment / secret files. `DATABASE_URL` refers to the ordinary application configuration, while `--target-database-url` is the new destination. Run the command in a trusted session so command history/process visibility is controlled; prefer a target URL without a password and use `PGPASSWORD` when available. The CLI passes database passwords to subprocesses through environment, not process arguments. It decrypts, restores with owner/ACL rewriting disabled, applies newer additive migrations, and reconciles jobs against current media objects. Missing originals or outputs and elapsed expiry deadlines cause jobs to become `EXPIRED`. Files still present for expired records are removed on the next cleanup sweep.

Record backup creation time, bytes, source/restored user and job counts, restore duration, and the newest recovered application timestamp. Write a known record after backup to measure the actual lost-data window. Successful hourly scheduling targets RPO ≤1 hour; this is a target, not an established production guarantee. Rehearse recovery regularly, including restoring onto another machine after manager loss. Keep offline identity copies secure and test that they can decrypt a backup.

Local validation during implementation restored 3 job records and 4 user records from a 16,755-byte encrypted backup into an empty database. The CLI restore step took approximately 0.35 seconds on that tiny fixture. It does not predict production restore duration.

### Check a downloaded archive against its media bucket

On the operator's trusted machine, mount the encrypted file and the offline identity read-only. Set `RESTORE_DB_URL` to an empty, isolated PostgreSQL 17 database, supply its password through `PGPASSWORD`, and configure `S3_ENDPOINT`, `S3_REGION`, `S3_BUCKET` and read-only media credentials. The media key needs permission to read objects and distinguish a missing key (HTTP 404) from denied access (HTTP 403). Keep the API, workers and cleanup service stopped while checking a production bucket.

```sh
fastzip restore --archive-file "$ENCRYPTED_ARCHIVE" --identity "$OFFLINE_IDENTITY" \
  --target-database-url "$RESTORE_DB_URL"
fastzip audit-restored-media --database-url "$RESTORE_DB_URL"
```

Record both JSON reports. The restore reports how many rows reconciliation marked expired. The audit verifies bucket access, then checks the original and output of every row with an S3 `HEAD`, including rows already expired. For each kind it counts keys with matching byte length, missing objects, size mismatches and rows without a reference. Pending jobs can legitimately lack an output. An inconsistent unexpired row causes exit 1. A missing bucket, denied access or a transport error aborts the audit. Expired rows appear in the totals even when the exit code is zero. Byte identity requires a trusted digest or a before-and-after download. Use a separate media bucket for any new test uploads following a production-archive replay.

## Production encrypted-backup validation (November 20, 2026)

The team performed a real **off-node** recovery of a production PostgreSQL backup from the private DigitalOcean Spaces backup bucket. This is distinct from the earlier Compose-based `recovery_smoke.py` run and from the application-level `fastzip restore` command described above.

### Key correction and backup selection

The initial production `BACKUP_AGE_RECIPIENT` inadvertently used a public key copied from the `age-keygen` help output. Neither locally available identity matched that recipient. Because a matching private identity was not available, the earlier encrypted objects are **not proven recoverable**. They were not deleted. The team then selected an existing age identity held on the Windows operator machine, confirmed its public recipient, updated `/opt/fastzip/production.env` (mode 600) and the live `fastzip_backup` service's `BACKUP_AGE_RECIPIENT`, and observed a newly generated `.age` object. The private identity remained on the operator machine; it was not transferred to the manager or committed to Git.

The **new** backup used for the restore test was `hourly/2026-11-20T22-43-10.dump.age` (object name recorded from the downloaded file; verify the prefix against the bucket when using the command in a different environment).

### Isolation, decryption, and restore procedure

The age identity and encrypted backup were available on a Windows operator workstation. The full database was **not** restored onto the production manager. A disposable PostgreSQL 17 container was created locally with `--network none`, no host port publishing, and no production volume mounts. The recovered plaintext stream was passed directly to `pg_restore`, rather than saved as an unencrypted dump on disk.

Windows PowerShell 5.1 may transform native-process binary pipelines. A direct PowerShell pipe to `pg_restore` produced an EOF error; using `cmd.exe /c` for the **binary** pipe worked. The exact secret-bearing identity path is intentionally not included here. The following commands use safe placeholders and are not intended for blind execution:

```powershell
$identity = 'PATH_TO_OFFLINE_AGE_IDENTITY'
$backup   = 'PATH_TO_2026-11-20T22-43-10.dump.age'

# Verify complete age decryption without leaving a plaintext file.
age -d -i $identity -o NUL $backup
$LASTEXITCODE   # observed: 0

# Read the PostgreSQL archive's table of contents.
cmd.exe /c "age -d -i `"$identity`" `"$backup`" | docker run --rm -i postgres:17-bookworm pg_restore --list"

# The disposable DB container had already been created and checked with pg_isready.
cmd.exe /c "age -d -i `"$identity`" `"$backup`" | docker exec -i fastzip-restore-test pg_restore -U fastzip -d fastzip_restored --no-owner --no-acl --exit-on-error"
$LASTEXITCODE   # observed: 0

# Read-only integrity checks inside the disposable container.
docker exec fastzip-restore-test psql -U fastzip -d fastzip_restored -c "SELECT count(*) AS users FROM users;"
docker exec fastzip-restore-test psql -U fastzip -d fastzip_restored -c "SELECT count(*) AS jobs FROM jobs;"
docker exec fastzip-restore-test psql -U fastzip -d fastzip_restored -c "\dt"

# After retaining evidence, remove the isolated container and any anonymous volumes.
docker rm -f -v fastzip-restore-test
```

### Observed result

| Check | Observed result |
| --- | --- |
| Source | New `.dump.age` object from private DigitalOcean Spaces backup bucket |
| age decryption | Successful, exit code `0` |
| PostgreSQL archive | Custom format, gzip compressed; 44 TOC entries |
| Database version in archive | PostgreSQL 17.12 |
| Restore target | New network-isolated local PostgreSQL 17 container |
| `pg_restore` | Successful, exit code `0` (`--no-owner --no-acl --exit-on-error`) |
| Restored records | **4 users**, **2 jobs** |
| Tables recovered | **8** (including `users`, `jobs`, `sessions`, `backup_runs`) |
| Production database | Not overwritten, restarted, or modified by the restore drill |

This establishes successful **age decryption and PostgreSQL archive recovery** for the newly encrypted production backup. It does **not** measure a production RPO/RTO, test application cutover, or demonstrate the media-object reconciliation and migration behavior of the full `fastzip restore` CLI on that real production object. Preserve the correctly paired offline identity, and perform future recovery rehearsals with an empty destination database. Historical backups encrypted to the old recipient must not be described as recoverable without the matching private identity.

The two production rows still need the [downloaded-archive check](#check-a-downloaded-archive-against-its-media-bucket) on a machine with the matching identity and read-only Spaces access. Record the audit time because the media retention window is 24 hours. A replay today can establish which objects still exist. Measuring a historical outage also requires its start time, a record of acknowledged writes around the backup, and the time the application accepted its first new job.

## Timed recovery on fresh VMs

On November 22, 2026 (Toronto), we forcibly stopped the VM running the API, worker and PostgreSQL. For each of two trials we created a new Ubuntu 24.04.5 arm64 VM after the fault, installed Docker 29.1.3, and initialized PostgreSQL 17.12 on a fresh ext4 filesystem. Each target started with zero public tables and a different PostgreSQL system identifier. A separate VM kept the private MinIO buckets and the Caddy HTTPS address available. The OS image, packages and container layers were cached locally. These timings include local VM creation and gateway cutover. Cloud provisioning, DNS changes and Swarm manager reconstruction are outside the drill.

| Elapsed from forced power-off request | Private backup bucket | Downloaded copy of the same archive |
| --- | ---: | ---: |
| Outage confirmed and recovery started | 69.176 s | 1.590 s |
| New VM booted | 86.137 s | 18.386 s |
| Empty PostgreSQL ready | 104.288 s | 37.180 s |
| Original HTTPS address ready | 107.405 s | 40.898 s |
| **Existing session received HTTP 202 for a new upload (RTO)** | **107.439 s** | **40.929 s** |
| Worker succeeded and client downloaded and decoded the signed WebP (HTTP 200) | 109.969 s | 43.310 s |

Both trials used one monotonic clock from the power-off request to the client response. In the first trial the readiness probe timed out while the gateway still served its static page. The clock continued through diagnosis. The second trial used a bounded probe and replayed the saved archive with the same SHA-256 digest. Its restore and audit could only send `HEAD` to the media bucket. The endpoint returned 200 for an existing key, 404 for a missing key and 403 for both a backup-bucket lookup and a media `POST`.

**Data loss in the first trial.** Four completed jobs were in the snapshot. Three more completed before power-off. We recorded their IDs and source-side PostgreSQL commit timestamps before the fault with `track_commit_timestamp=on`. The latest recovered job committed at 04:29:37.968 UTC and the fault was estimated at 04:30:11.573 UTC on the source database clock. The observed job recovery point was **33.606 seconds** before failure, with ±0.042 s clock uncertainty. The dump's snapshot was 32.228–33.304 s old. The target restored one user, the four expected job IDs and eight tables. All three later job lookups returned 404. In the second trial the same snapshot restored four of six jobs on the previous replacement. The two acknowledged jobs written there after the snapshot were absent. The hourly backup schedule remains a separate recovery target.

| Restored fixture | Object storage and download result in both trials |
| --- | --- |
| Retained job | Original PNG (2,790 bytes) and output WebP (944 bytes) both downloaded. Each SHA-256 matched its own pre-fault download. |
| Deleted original | Original missing, output still present. Reconciliation marked the job `EXPIRED`. |
| Deleted output | Original still present, output missing. Reconciliation marked the job `EXPIRED`. |
| Elapsed retention | Both objects still present at audit time. The shortened fixture deadline had passed and the job was `EXPIRED`. |

Both download routes returned 410 for all three expired jobs. The audit found three matching originals and three matching outputs out of four jobs, one missing object of each kind, and zero inconsistent unexpired jobs. A new upload from the pre-fault session produced a 942-byte WebP in the first trial and a 944-byte WebP in the second. The following [measurement record](evidence/disaster-recovery-2026-11-22.json) contains UTC timestamps, object digest, row counts and HTTP outcomes for both trials.

To repeat the measurement, use a private test bucket and an independent object-storage node. Create and finish identifiable jobs on both sides of a backup, and record the IDs and source commit timestamps outside the failure domain. Capture a healthy original and result digest. In the test bucket, delete an original and an output belonging to different backed-up jobs and let a third fixture expire. Start the outage clock, power off the source VM, provision the empty target and run the restore and audit above. Point the same HTTPS address at the target, upload using the existing session and fetch the new signed result. Compare the restored IDs, hashes and HTTP responses against the pre-fault record.

