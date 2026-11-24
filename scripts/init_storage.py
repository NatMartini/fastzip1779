import time

from fastzip.storage import ensure_buckets

for attempt in range(30):
    try:
        ensure_buckets()
        break
    except Exception:
        if attempt == 29:
            raise
        time.sleep(2)
