import time

import httpx

from fastzip.config import settings
from fastzip.db import record_error


def droplet_metrics():
    s = settings()
    if not s.do_token or not s.do_droplet_ids:
        return {"configured": False, "droplets": []}
    end = int(time.time())
    droplets = []
    with httpx.Client(
        base_url="https://api.digitalocean.com/v2/monitoring/metrics/droplet/",
        headers={"Authorization": "Bearer " + s.do_token},
        timeout=5,
    ) as client:
        for identifier in s.do_droplet_ids.split(","):
            identifier = identifier.strip()
            try:
                data = {}
                for metric in (
                    "cpu",
                    "memory_available",
                    "memory_total",
                    "filesystem_free",
                    "filesystem_size",
                ):
                    response = client.get(
                        metric, params={"host_id": identifier, "start": end - 300, "end": end}
                    )
                    response.raise_for_status()
                    data[metric] = response.json()["data"]["result"]
                total_delta = idle_delta = 0.0
                for series in data["cpu"]:
                    values = series["values"]
                    if len(values) >= 2:
                        delta = max(0, float(values[-1][1]) - float(values[0][1]))
                        total_delta += delta
                        if series["metric"].get("mode") == "idle":
                            idle_delta += delta

                def latest(metric, data=data):
                    rows = data[metric]
                    return sum(float(row["values"][-1][1]) for row in rows if row["values"])

                memory_total = latest("memory_total")
                free = {
                    row["metric"].get("mountpoint", row["metric"].get("device", "unknown")): float(
                        row["values"][-1][1]
                    )
                    for row in data["filesystem_free"]
                    if row["values"]
                }
                filesystems = []
                for row in data["filesystem_size"]:
                    mount = row["metric"].get("mountpoint", row["metric"].get("device", "unknown"))
                    size = float(row["values"][-1][1]) if row["values"] else 0
                    if size and mount in free:
                        filesystems.append(
                            {
                                "mount": mount,
                                "used_percent": round(100 * (1 - free[mount] / size), 1),
                            }
                        )
                droplets.append(
                    {
                        "id": identifier,
                        "available": True,
                        "cpu_percent": round(100 * (1 - idle_delta / total_delta), 1)
                        if total_delta
                        else None,
                        "memory_percent": round(
                            100 * (1 - latest("memory_available") / memory_total), 1
                        )
                        if memory_total
                        else None,
                        "filesystems": filesystems,
                    }
                )
            except (httpx.HTTPError, KeyError, ValueError, TypeError):
                record_error("monitoring", "DigitalOcean metrics request failed")
                droplets.append({"id": identifier, "available": False})
    return {"configured": True, "droplets": droplets}
