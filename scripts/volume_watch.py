"""Host timer: email when the mounted database Volume exceeds 75% for five minutes."""

import json
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path

mount = Path(os.environ["VOLUME_MOUNT"])
state_file = Path(os.environ.get("VOLUME_WATCH_STATE", "/var/lib/fastzip-volume-watch/state.json"))
if not os.path.ismount(mount):
    raise RuntimeError("Expected Volume is not mounted; refusing to measure the root filesystem")
stats = os.statvfs(mount)
used = 100 * (1 - stats.f_bavail / stats.f_blocks)
state = json.loads(state_file.read_text()) if state_file.exists() else {}
stamp = time.time()
if used > 75:
    state.setdefault("above_since", stamp)
    if stamp - state["above_since"] >= 300 and stamp - state.get("last_email", 0) >= 3600:
        message = EmailMessage()
        message["From"] = os.environ["SMTP_FROM"]
        message["To"] = os.environ["ALERT_EMAILS"]
        message["Subject"] = "FastZip: database Volume above 75%"
        message.set_content(
            f"Volume {mount} is {used:.1f}% full and has exceeded 75% for at least five minutes."
        )
        with smtplib.SMTP(
            os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=20
        ) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(
                os.environ["SMTP_USER"], Path(os.environ["SMTP_PASSWORD_FILE"]).read_text().strip()
            )
            smtp.send_message(message)
        state["last_email"] = stamp
else:
    state.pop("above_since", None)
state_file.parent.mkdir(parents=True, exist_ok=True)
state_file.write_text(json.dumps(state))
print(json.dumps({"volume": str(mount), "used_percent": round(used, 2), "threshold": 75}))
