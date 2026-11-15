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
