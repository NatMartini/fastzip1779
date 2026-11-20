import argparse
import json
import subprocess
import time

parser = argparse.ArgumentParser()
parser.add_argument("--tag", required=True)
parser.add_argument("--timeout", type=int, default=240)
parser.add_argument(
    "--allow-rolled-back",
    action="store_true",
    help="Accept a completed rollback to the expected tag",
)
args = parser.parse_args()
expected = {
    f"fastzip_{service}"
    for service in ("postgres", "api", "worker", "cleanup", "backup", "frontend")
}
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
    services = dict(line.split(maxsplit=1) for line in lines)
    complete = services.keys() == expected
    for name, replicas in services.items():
        running, desired = replicas.split("/")
        complete = complete and running == desired and int(desired) > 0
        service = json.loads(subprocess.check_output(["docker", "service", "inspect", name]))[0]
        update = service.get("UpdateStatus", {}).get("State", "completed")
        tag_matches = True
        if name != "fastzip_postgres":
            image = service["Spec"]["TaskTemplate"]["ContainerSpec"]["Image"]
            tag_matches = image.partition("@")[0].rpartition(":")[2] == args.tag
        if update in {"paused", "rollback_paused"} or (
            update == "rollback_completed" and not (args.allow_rolled_back and tag_matches)
        ):
            raise SystemExit(f"{name} update failed or rolled back")
        complete = complete and tag_matches and update not in {"updating", "rollback_started"}
    if complete:
        print("All six services converged on the requested image tag")
        raise SystemExit(0)
    time.sleep(3)
raise SystemExit("Swarm did not converge before the deadline")
