"""Email when the database Volume stays above its threshold for five minutes."""

import argparse
import json
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from email.utils import make_msgid
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--verify-delivery",
    action="store_true",
    help="Use a 0%% threshold, a separate state file, and a test subject for a delivery drill.",
)
verify = parser.parse_args().verify_delivery
threshold = 0 if verify else 75
mount = Path(os.environ["VOLUME_MOUNT"])
state_file = Path(os.environ.get("VOLUME_WATCH_STATE", "/var/lib/fastzip-volume-watch/state.json"))
if verify:
    state_file = state_file.with_name("delivery-check-" + state_file.name)
if not mount.is_mount() or mount.stat().st_dev == Path("/").stat().st_dev:
    raise RuntimeError("Expected a mounted Volume on a separate filesystem from /")
stats = os.statvfs(mount)
used = 100 * (1 - stats.f_bavail / stats.f_blocks)
state = json.loads(state_file.read_text()) if state_file.exists() else {}
stamp = time.time()
report = {"volume": str(mount), "used_percent": round(used, 2), "threshold": threshold}
if used > threshold:
    state.setdefault("above_since", stamp)
    if stamp - state["above_since"] >= 300 and stamp - state.get("last_email", 0) >= 3600:
        message = EmailMessage()
        message["From"] = os.environ["SMTP_FROM"]
        message["To"] = os.environ["ALERT_EMAILS"]
        subject = f"FastZip: database Volume above {threshold}%"
        message["Subject"] = f"[TEST] {subject}" if verify else subject
        message["Message-ID"] = make_msgid()
        message.set_content(
            f"Volume {mount} is {used:.1f}% full and has exceeded {threshold}% for at least five minutes.\n"
            f"Alert ID: {message['Message-ID']}"
        )
        with smtplib.SMTP(
            os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=20
        ) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(
                os.environ["SMTP_USER"], Path(os.environ["SMTP_PASSWORD_FILE"]).read_text().strip()
            )
            refused = smtp.send_message(message)
        state["last_email"] = stamp
        report.update(
            smtp_status="partial" if refused else "accepted",
            recipients_refused=len(refused),
            message_id=message["Message-ID"],
        )
else:
    state.pop("above_since", None)
state_file.parent.mkdir(parents=True, exist_ok=True)
state_file.write_text(json.dumps(state))
print(json.dumps(report))
if report.get("smtp_status") == "partial":
    raise SystemExit(1)
