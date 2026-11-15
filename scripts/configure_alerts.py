"""Idempotently configure DO's built-in alerts. Does not create cloud resources."""

import argparse
import os

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--droplets", required=True, help="Comma-separated droplet IDs")
    parser.add_argument("--emails", required=True, help="Comma-separated alert recipients")
    args = parser.parse_args()
    with httpx.Client(
        base_url="https://api.digitalocean.com/v2/monitoring/",
        timeout=20,
        headers={"Authorization": "Bearer " + os.environ["DIGITALOCEAN_TOKEN"]},
    ) as client:
        response = client.get("alerts", params={"per_page": 200})
        response.raise_for_status()
        existing = {policy["description"]: policy["uuid"] for policy in response.json()["policies"]}
        for metric, threshold in (("cpu", 80), ("memory", 80), ("disk", 75)):
            name = f"FastZip {metric} usage above {threshold}%"
            body = {
                "alerts": {"email": args.emails.split(","), "slack": []},
                "compare": "GreaterThan",
                "description": name,
                "enabled": True,
                "entities": args.droplets.split(","),
                "tags": [],
                "type": f"v1/insights/droplet/{metric}_utilization_percent",
                "value": threshold,
                "window": "5m",
            }
            response = (
                client.put("alerts/" + existing[name], json=body)
                if name in existing
                else client.post("alerts", json=body)
            )
            response.raise_for_status()
            print(f"Configured {name}")
    print(
        "DO's disk policy covers root storage. Install deploy/volume-watch.timer for the attached Volume."
    )


if __name__ == "__main__":
    main()
