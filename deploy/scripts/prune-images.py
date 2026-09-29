#!/usr/bin/env python3
"""Remove old FactorLab images from the VPS (stdlib only; run daily by a systemd timer).

For each FactorLab repository (ghcr.io/arjundoshi221/factorlab and factorlab-<component>)
it keeps the three newest images plus every image that is:

  * used by a container (running or stopped),
  * pinned in /opt/factorlab/state/images.env or /opt/factorlab/current-image,
  * the previous image of a component's current release, so a rollback needs no pull.

Everything else in those repositories is removed with `docker image rm`, never forced,
so Docker still refuses anything in use. Other images (ClickHouse, gateways, tools) are
never touched.

    prune-images.py [--dry-run] [--keep 3]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path("/opt/factorlab")
REPOSITORY = re.compile(r"^ghcr\.io/arjundoshi221/factorlab(-[a-z0-9-]+)?$")
DIGEST = re.compile(r"^ghcr\.io/arjundoshi221/factorlab(-[a-z0-9-]+)?@sha256:[0-9a-f]{64}$")

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _repository(reference: str) -> str:
    name = reference.split("@", 1)[0]
    head, _, tail = name.rpartition(":")
    return head if head and "/" not in tail else name


def protected_references(root: Path) -> set[str]:
    """image@sha256 references that must stay: pins, the monolith, rollback targets."""
    references = set()
    sources = [root / "state" / "images.env", root / "current-image"]
    for source in sources:
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            value = line.partition("=")[2] if "=" in line else line
            if DIGEST.match(value.strip()):
                references.add(value.strip())
    for current in root.glob("releases/*/current"):
        try:
            version = current.read_text(encoding="utf-8").strip()
            previous = json.loads((current.parent / version / "previous.json").read_text("utf-8"))
        except (OSError, ValueError):
            continue
        image = str(previous.get("image", ""))
        if DIGEST.match(image):
            references.add(image)
    return references


def plan(images: list[dict], used: set[str], protected: set[str], keep: int) -> list[dict]:
    """The images to remove, newest-first order preserved per repository."""
    by_repository: dict[str, list[dict]] = {}
    for image in images:
        names = [*(image.get("RepoTags") or []), *(image.get("RepoDigests") or [])]
        for repository in {r for r in map(_repository, names) if REPOSITORY.match(r)}:
            by_repository.setdefault(repository, []).append(image)
    removable = {}
    for members in by_repository.values():
        newest = sorted(members, key=lambda item: item.get("Created", ""), reverse=True)
        for image in newest[keep:]:
            digests = set(image.get("RepoDigests") or [])
            if image["Id"] in used or digests & protected:
                continue
            removable[image["Id"]] = image
    # An image in several repositories is removed only if no repository keeps it.
    kept = {
        image["Id"]
        for members in by_repository.values()
        for image in sorted(members, key=lambda i: i.get("Created", ""), reverse=True)[:keep]
    }
    return [image for image_id, image in removable.items() if image_id not in kept]


def prune(
    root: Path = ROOT,
    *,
    keep: int = 3,
    dry_run: bool = False,
    run: Runner = _run,
    log: Callable[[str], None] = print,
) -> int:
    listed = run(["docker", "image", "ls", "--no-trunc", "--quiet"])
    containers = run(["docker", "ps", "--all", "--no-trunc", "--quiet"])
    if listed.returncode or containers.returncode:
        log("docker is unavailable; nothing pruned")
        return 1
    image_ids = sorted(set(listed.stdout.split()))
    images = (
        json.loads(run(["docker", "image", "inspect", *image_ids]).stdout or "[]")
        if image_ids
        else []
    )
    used = set()
    container_ids = containers.stdout.split()
    if container_ids:
        for container in json.loads(run(["docker", "inspect", *container_ids]).stdout or "[]"):
            used.add(container.get("Image", ""))
    doomed = plan(images, used, protected_references(root), keep)
    failures = 0
    for image in doomed:
        label = ", ".join(image.get("RepoTags") or image.get("RepoDigests") or [image["Id"]])
        if dry_run:
            log(f"would remove {label}")
            continue
        result = run(["docker", "image", "rm", image["Id"]])
        if result.returncode:
            failures += 1
            log(f"kept {label}: {result.stderr.strip()[:200]}")
        else:
            log(f"removed {label}")
    log(
        f"{len(doomed) - failures} of {len(doomed)} old FactorLab images removed"
        + (" (dry run)" if dry_run else "")
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep", type=int, default=3)
    args = parser.parse_args(argv)
    return prune(keep=max(1, args.keep), dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
