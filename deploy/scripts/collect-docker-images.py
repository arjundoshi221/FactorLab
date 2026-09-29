#!/usr/bin/env python3
"""Write a narrow, atomic Docker inventory for the private hub.

Two files, both atomic and world-readable (0644), published to the API read-only:

    snapshot.json     images, containers and the monolith release (the original shape;
                      API images released before per-component releases reject any
                      extra field, so it never changes)
    snapshot.v2.json  the same plus ``schema: 2``, each image's ``component`` label, the
                      platform version and one row per deployed component (version from
                      releases/<c>/current, pinned image, running services)
"""

import json
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/opt/factorlab")
OUTPUT = Path("/var/lib/factorlab/docker-images/snapshot.json")
OUTPUT_V2 = OUTPUT.with_name("snapshot.v2.json")
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
COMPONENT = re.compile(r"^[a-z][a-z0-9-]{1,40}$")
VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-rc\.[0-9]+)?$")
PINNED_IMAGE = re.compile(r"^ghcr\.io/arjundoshi221/factorlab(-[a-z0-9-]+)?@sha256:[0-9a-f]{64}$")
IMAGE_VAR = re.compile(r"^FACTORLAB_[A-Z0-9_]+_IMAGE$")


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
        records = [
            path
            for path in (root / "releases").iterdir()
            if path.is_dir() and RELEASE_ID.match(path.name)
        ]
    except FileNotFoundError:
        return []
    return [
        read_release(path)
        for path in sorted(records, key=lambda path: path.name, reverse=True)[:limit]
    ]


def image_labels(image: dict) -> dict[str, str]:
    labels = (image.get("Config") or {}).get("Labels") or {}
    return {
        name: str(labels[f"org.opencontainers.image.{name}"])[:256]
        for name in IMAGE_LABELS
        if labels.get(f"org.opencontainers.image.{name}")
    }


def _inventory(root: Path) -> tuple[dict, dict[str, str], list[dict]]:
    """The v1 snapshot, each image's component label, and a flat list of containers."""
    image_ids = list(dict.fromkeys(docker("image", "ls", "-aq", "--no-trunc").splitlines()))
    container_ids = docker("ps", "-aq", "--no-trunc").splitlines()
    containers_by_image: dict[str, list[dict]] = {}
    for container in inspect("container", container_ids):
        started = container["State"].get("StartedAt")
        if not started or started.startswith("0001-"):
            started = None
        labels = (container.get("Config") or {}).get("Labels") or {}
        containers_by_image.setdefault(container["Image"], []).append(
            {
                "name": container["Name"].lstrip("/"),
                "status": container["State"]["Status"],
                "started_at": started,
                "service": labels.get("com.docker.compose.service"),
                "image_ref": (container.get("Config") or {}).get("Image"),
            }
        )

    release_id, activated_at = read_current_release(root)
    try:
        current_image = (root / "current-image").read_text().strip()
        current_image_id = json.loads(docker("image", "inspect", current_image))[0]["Id"]
    except (FileNotFoundError, subprocess.CalledProcessError, IndexError, json.JSONDecodeError):
        current_image_id = None

    images = []
    components: dict[str, str] = {}
    flat: list[dict] = []
    for image in inspect("image", image_ids):
        raw_labels = (image.get("Config") or {}).get("Labels") or {}
        component = str(raw_labels.get("io.factorlab.component") or "")
        if COMPONENT.match(component):
            components[image["Id"]] = component
        version = str(raw_labels.get("org.opencontainers.image.version") or "")
        for container in containers_by_image.get(image["Id"], []):
            flat.append(
                {
                    "service": container["service"],
                    "container": container["name"],
                    "status": container["status"],
                    "started_at": container["started_at"],
                    "image_id": image["Id"],
                    "version": version if VERSION.match(version) else None,
                }
            )
        associated = sorted(containers_by_image.get(image["Id"], []), key=lambda item: item["name"])
        images.append(
            {
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
                "platform": "/".join(
                    part for part in (image.get("Os"), image.get("Architecture")) if part
                )
                or None,
                "labels": image_labels(image),
            }
        )
    snapshot = {
        "snapshot_at": datetime.now(UTC).isoformat(),
        "release_id": release_id,
        "images": images,
        "release": read_release(root / "releases" / release_id) if release_id else None,
        "releases": read_release_history(root),
    }
    return snapshot, components, flat


def collect(root: Path = ROOT) -> dict:
    return _inventory(root)[0]


def read_version(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return value if VERSION.match(value) else None


def read_pins(root: Path) -> dict[str, str]:
    """FACTORLAB_<C>_IMAGE digest pins written by the host deployer (validated)."""
    pins = {}
    try:
        lines = (root / "state" / "images.env").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return pins
    for line in lines:
        key, _, value = line.partition("=")
        if IMAGE_VAR.match(key.strip()) and PINNED_IMAGE.match(value.strip()):
            pins[key.strip()] = value.strip()
    return pins


def deployed_components(root: Path, containers: list[dict]) -> list[dict]:
    """One row per component the deployer manages, with its running services."""
    pins = read_pins(root)
    rows = []
    for manifest_path in sorted((root / "components").glob("*/component.json")):
        name = manifest_path.parent.name
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8")[:65536])
            services = [
                str(s["name"])
                for s in manifest.get("services", [])
                if isinstance(s, dict) and "name" in s
            ]
            image_var = str((manifest.get("platform") or {}).get("image_var", ""))
        except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError):
            continue
        if not COMPONENT.match(name) or manifest.get("name") != name:
            continue
        rows.append(
            {
                "component": name,
                "version": read_version(root / "releases" / name / "current"),
                "image": pins.get(image_var) if IMAGE_VAR.match(image_var) else None,
                "services": [
                    {
                        key: container[key]
                        for key in (
                            "service",
                            "container",
                            "status",
                            "started_at",
                            "version",
                            "image_id",
                        )
                    }
                    for container in sorted(containers, key=lambda c: c["container"])
                    if container["service"] in services
                ],
            }
        )
    return rows


def collect_v2(root: Path = ROOT) -> dict:
    snapshot, components, containers = _inventory(root)
    images = [
        {
            **image,
            "labels": {
                **image["labels"],
                **({"component": components[image["id"]]} if image["id"] in components else {}),
            },
        }
        for image in snapshot["images"]
    ]
    return {
        **snapshot,
        "schema": 2,
        "images": images,
        "platform_version": read_version(root / "releases" / "platform" / "current"),
        "components": deployed_components(root, containers),
    }


def write_atomic(path: Path, snapshot: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=".snapshot-", delete=False
    ) as temporary:
        json.dump(snapshot, temporary, separators=(",", ":"))
        temporary.write("\n")
        temporary_path = Path(temporary.name)
    temporary_path.chmod(0o644)
    temporary_path.replace(path)


def main() -> None:
    snapshot = collect_v2(ROOT)
    legacy = {key: snapshot[key] for key in ("snapshot_at", "release_id", "release", "releases")}
    legacy["images"] = [
        {**image, "labels": {k: v for k, v in image["labels"].items() if k in IMAGE_LABELS}}
        for image in snapshot["images"]
    ]
    write_atomic(OUTPUT, legacy)
    write_atomic(OUTPUT_V2, snapshot)


if __name__ == "__main__":
    main()
