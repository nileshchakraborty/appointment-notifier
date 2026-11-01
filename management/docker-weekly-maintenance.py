#!/usr/bin/env python3
"""Recreate explicitly selected, currently running Compose services once a week.

Persistent volumes and bind mounts are never pruned. A failed health check stops
the run and leaves the service available for diagnosis.
"""

import argparse
import fcntl
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path


HOST_SERVICES = {
    "fedora": [
        ("appointment-notifier-fedora", "kafka"),
        ("appointment-notifier-fedora", "appointment-notifier"),
        ("appointment-notifier-fedora", "telegram-delivery"),
        ("compute-stack", "redis"),
        ("compute-stack", "qdrant"),
        ("streambert", "redis"),
        ("streambert-production", "streambert-queue-redis"),
        ("streambert-production", "streambert-cache-redis"),
        ("streambert-production", "streambert-api"),
        ("streambert-production", "streambert-worker"),
        ("ollama-fedora", "ollama"),
        ("portainer-agent", "portainer-agent"),
        ("portainer-server", "portainer"),
        ("openwa-fedora", "openwa"),
    ],
    "pi": [("dns-management", "pihole")],
}


def run(args, *, cwd=None, check=True):
    logging.info("run: %s", " ".join(args))
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=check)


def inspect(container_id):
    return json.loads(run(["docker", "inspect", container_id]).stdout)[0]


def running_service(project, service):
    ids = run([
        "docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={project}",
        "--filter", f"label=com.docker.compose.service={service}",
    ]).stdout.splitlines()
    return [inspect(container_id) for container_id in ids]


def compose_command(container):
    labels = container["Config"]["Labels"]
    workdir = labels["com.docker.compose.project.working_dir"]
    files = labels["com.docker.compose.project.config_files"].split(",")
    if not files or any(not Path(path).is_file() for path in files):
        raise RuntimeError(f"Missing Compose config for {container['Name']}")
    command = ["docker", "compose"]
    for path in files:
        command.extend(["-f", path])
    return command, workdir


def await_recovery(project, service, count, timeout=180):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        containers = running_service(project, service)
        if len(containers) == count and all(
            item["State"]["Running"]
            and item["State"].get("Health", {}).get("Status", "healthy") == "healthy"
            for item in containers
        ):
            return
        time.sleep(5)
    states = [(item["Name"], item["State"].get("Health", {}).get("Status", item["State"]["Status"]))
              for item in running_service(project, service)]
    raise RuntimeError(f"Recovery failed for {project}/{service}: {states}")


def maintain(project, service, dry_run):
    containers = running_service(project, service)
    if not containers:
        logging.warning("skip stopped service %s/%s", project, service)
        return "stopped"
    if any(item["State"].get("Health", {}).get("Status") == "unhealthy" for item in containers):
        logging.warning("skip preexisting unhealthy service %s/%s", project, service)
        return "unhealthy"
    command, workdir = compose_command(containers[0])
    image = containers[0]["Config"]["Image"]
    logging.info("maintain %s/%s, replicas=%d, image=%s", project, service, len(containers), image)
    if dry_run:
        return "planned"
    if not image.startswith("local/"):
        run(["docker", "pull", image])
    elif service == "appointment-notifier":
        run(command + ["build", "--pull", service], cwd=workdir)
    # Local release images are updated by their release pipeline, then recreated here.
    run(command + ["up", "-d", "--no-deps", "--no-build", "--force-recreate", service], cwd=workdir)
    await_recovery(project, service, len(containers))
    logging.info("healthy %s/%s", project, service)
    return "healthy"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", choices=HOST_SERVICES, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", help="one allowlisted project/service for a manual drill")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with open(f"/tmp/docker-weekly-maintenance-{os.geteuid()}.lock", "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        targets = HOST_SERVICES[args.host]
        if args.only:
            targets = [(project, service) for project, service in targets
                       if f"{project}/{service}" == args.only]
            if not targets:
                parser.error("--only must name an allowlisted project/service")
        unhealthy = []
        for project, service in targets:
            if maintain(project, service, args.dry_run) == "unhealthy":
                unhealthy.append(f"{project}/{service}")
        if not args.dry_run and not args.only:
            # Only unused images and old build cache; never touch volumes or app data.
            run(["docker", "image", "prune", "-f"])
            run(["docker", "builder", "prune", "-f", "--filter", "until=168h"])
        if unhealthy:
            raise RuntimeError(f"Preexisting unhealthy services require attention: {', '.join(unhealthy)}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        logging.error("maintenance stopped: %s", error)
        sys.exit(1)
