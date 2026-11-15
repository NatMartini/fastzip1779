import argparse
import json
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("--tag", required=True)
parser.add_argument("--timeout", type=int, default=240)
args = parser.parse_args()
deadline = time.monotonic() + args.timeout
while time.monotonic() < deadline:
    lines = subprocess.check_output(
        [
            "docker",
            "service",
            "ls",
            "--filter",
            "label=com.docker.stack.namespace=fastzip",
            "--format",
            "{{.Name}} {{.Replicas}}",
        ],
        text=True,
    ).splitlines()
    complete = len(lines) == 6
    for line in lines:
        name, replicas = line.split()
        running, desired = replicas.split("/")
        complete = complete and running == desired and int(desired) > 0
        service = json.loads(subprocess.check_output(["docker", "service", "inspect", name]))[0]
        update = service.get("UpdateStatus", {}).get("State", "completed")
        if update in {"paused", "rollback_paused", "rollback_completed"}:
            raise SystemExit(f"{name} update failed or rolled back")
        if name != "fastzip_postgres":
            image = service["Spec"]["TaskTemplate"]["ContainerSpec"]["Image"]
            complete = complete and (":" + args.tag) in image
        complete = complete and update not in {"updating", "rollback_started"}
    if complete:
        print("All six services converged on the requested image tag")
        raise SystemExit(0)
    time.sleep(3)
raise SystemExit("Swarm did not converge before the deadline")
