#!/usr/bin/env python3
"""Write a narrow, atomic Docker inventory for the private hub."""

import json
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/opt/factorlab")
OUTPUT = Path("/var/lib/factorlab/docker-images/snapshot.json")
RELEASE_HISTORY = 12
RELEASE_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{7,12}$")
# Only these non-secret release.env keys and image labels are ever published.
RELEASE_FIELDS = {
    "commit": re.compile(r"^[0-9a-f]{40}$"),
    "image": re.compile(r"^[A-Za-z0-9./:_@-]{1,256}$"),
    "previous_release": re.compile(r"^[A-Za-z0-9TZ-]{1,64}$"),
    "previous_image": re.compile(r"^[A-Za-z0-9./:_@-]{1,256}$"),
}
IMAGE_LABELS = ("revision", "version", "source", "created", "title")


def docker(*args: str) -> str:
    return subprocess.check_output(["docker", *args], text=True).strip()


def inspect(kind: str, ids: list[str]) -> list[dict]:
    if not ids:
        return []
    return json.loads(docker(kind, "inspect", *ids))


def read_current_release(root: Path) -> tuple[str | None, str | None]:
    try:
        release_id = (root / "current-release").read_text().strip()
        if not release_id or "/" in release_id or ".." in release_id:
            return None, None
    except FileNotFoundError:
        return None, None
    try:
        activated_at = (root / "releases" / release_id / "activated-at").read_text().strip()
    except FileNotFoundError:
        activated_at = None
    return release_id, activated_at or None


def read_release(record: Path) -> dict:
    """Summarize one release record with only whitelisted, validated values."""
    fields: dict[str, str | None] = dict.fromkeys(RELEASE_FIELDS)
    try:
        for line in (record / "release.env").read_text().splitlines():
            key, separator, value = line.partition("=")
            pattern = RELEASE_FIELDS.get(key)
            if separator and pattern and pattern.match(value.strip()):
                fields[key] = value.strip()
    except (FileNotFoundError, UnicodeDecodeError):
        pass
    try:
        activated_at = (record / "activated-at").read_text().strip() or None
    except FileNotFoundError:
        activated_at = None
    return {"id": record.name, **fields, "activated_at": activated_at}


def read_release_history(root: Path, limit: int = RELEASE_HISTORY) -> list[dict]:
    try:
        records = [path for path in (root / "releases").iterdir() if path.is_dir() and RELEASE_ID.match(path.name)]
    except FileNotFoundError:
        return []
    return [read_release(path) for path in sorted(records, key=lambda path: path.name, reverse=True)[:limit]]


def image_labels(image: dict) -> dict[str, str]:
    labels = (image.get("Config") or {}).get("Labels") or {}
    return {
        name: str(labels[f"org.opencontainers.image.{name}"])[:256]
        for name in IMAGE_LABELS
        if labels.get(f"org.opencontainers.image.{name}")
    }


def collect(root: Path = ROOT) -> dict:
    image_ids = list(dict.fromkeys(docker("image", "ls", "-aq", "--no-trunc").splitlines()))
    container_ids = docker("ps", "-aq", "--no-trunc").splitlines()
    containers_by_image: dict[str, list[dict]] = {}
    for container in inspect("container", container_ids):
        started = container["State"].get("StartedAt")
        if not started or started.startswith("0001-"):
            started = None
        labels = (container.get("Config") or {}).get("Labels") or {}
        containers_by_image.setdefault(container["Image"], []).append({
            "name": container["Name"].lstrip("/"),
            "status": container["State"]["Status"],
            "started_at": started,
            "service": labels.get("com.docker.compose.service"),
            "image_ref": (container.get("Config") or {}).get("Image"),
        })

    release_id, activated_at = read_current_release(root)
    try:
        current_image = (root / "current-image").read_text().strip()
        current_image_id = json.loads(docker("image", "inspect", current_image))[0]["Id"]
    except (FileNotFoundError, subprocess.CalledProcessError, IndexError, json.JSONDecodeError):
        current_image_id = None

    images = []
    for image in inspect("image", image_ids):
        associated = sorted(containers_by_image.get(image["Id"], []), key=lambda item: item["name"])
        images.append({
            "id": image["Id"],
            "tags": image.get("RepoTags") or [],
            "digests": image.get("RepoDigests") or [],
            "size_bytes": image["Size"],
            "created_at": image["Created"],
            "containers": associated,
            "last_container_start_at": max(
                (item["started_at"] for item in associated if item["started_at"]),
                default=None,
            ),
            "release_activated_at": activated_at if image["Id"] == current_image_id else None,
            "current_release": image["Id"] == current_image_id,
            "platform": "/".join(part for part in (image.get("Os"), image.get("Architecture")) if part) or None,
            "labels": image_labels(image),
        })
    return {
        "snapshot_at": datetime.now(UTC).isoformat(),
        "release_id": release_id,
        "images": images,
        "release": read_release(root / "releases" / release_id) if release_id else None,
        "releases": read_release_history(root),
    }


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    snapshot = collect()
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=OUTPUT.parent,
                                     prefix=".snapshot-", delete=False) as temporary:
        json.dump(snapshot, temporary, separators=(",", ":"))
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.chmod(0o644)
    temporary_path.replace(OUTPUT)


if __name__ == "__main__":
    main()
