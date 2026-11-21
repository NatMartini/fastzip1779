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

Run restore on a trusted recovery machine with the age identity and access to both private buckets. Stop application writers during a production cutover, restore into a fresh database, verify counts/records, then point the application at that database. Never overwrite the only existing database. The CLI rejects targets with existing tables.

```sh
fastzip restore \
  --object-key hourly/UTC_TIMESTAMP.dump.age \
  --identity /secure/offline/fastzip.agekey \
  --target-database-url postgresql+psycopg://USER:PASSWORD@FRESH_HOST:5432/fastzip_restored
```

Provide source backup credentials, media credentials, endpoints and bucket names via environment / secret files. `DATABASE_URL` refers to the ordinary application configuration, while `--target-database-url` is the new destination. Run the command in a trusted session so command history/process visibility is controlled; prefer a target URL without a password and use `PGPASSWORD` when available. The CLI passes database passwords to subprocesses through environment, not process arguments. It decrypts, restores with owner/ACL rewriting disabled, applies newer additive migrations, and reconciles jobs against current media objects. Missing originals or outputs and elapsed expiry deadlines cause jobs to become `EXPIRED`. Files still present for expired records are removed on the next cleanup sweep.

Record backup creation time, bytes, source/restored user and job counts, restore duration, and the newest recovered application timestamp. Write a known record after backup to measure the actual lost-data window. Successful hourly scheduling targets RPO ≤1 hour; this is a target, not an established production guarantee. Rehearse recovery regularly, including restoring onto another machine after manager loss. Keep offline identity copies secure and test that they can decrypt a backup.

Local validation during implementation restored 3 job records and 4 user records from a 16,755-byte encrypted backup into an empty database. The CLI restore step took approximately 0.35 seconds on that tiny fixture. It does not predict production restore duration.

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

