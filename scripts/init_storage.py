import time

from fastzip.storage import Storage

for attempt in range(30):
    try:
        for backup in (False, True):
            store = Storage(backup=backup)
            try:
                store.client.head_bucket(Bucket=store.bucket)
            except store.client.exceptions.ClientError as exc:
                if str(exc.response["Error"]["Code"]) not in {"404", "NoSuchBucket"}:
                    raise
                store.client.create_bucket(Bucket=store.bucket)
        break
    except Exception:
        if attempt == 29:
            raise
        time.sleep(2)
