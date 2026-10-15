"""One-time removal of five audited, unreferenced dangling VPS images."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

IMAGES = (
    "sha256:2ac78402f848888d17bd429006b7677abddcc921e4c6ffca6c18093760a0b0ba",
    "sha256:3f5698d7db9f0caafb7a3cbb8d3cb36ffb6b5b228adc3465d4de6a6436fc90c5",
    "sha256:7ed249197fd4d43aa80cd03b83b85728c2e7924146ae47fcf673d8a3a1d8a62b",
    "sha256:04962ef2cf024d45f78a340b14d37d188b78f94e29ab0bab63e1acc662e28ff3",
    "sha256:35d552f41b186a4b9f400ea5195b2b7945c8f701cff86dedbe57ad1bcd21c7eb",
)
ROOT = Path("/opt/factorlab")


def run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed while auditing images")
    return result.stdout


def referenced(image_id: str) -> bool:
    files = [
        ROOT / "current-image",
        ROOT / "state/images.env",
        ROOT / "deploy/compose.production.yml",
    ]
    files.extend((ROOT / "deployment-tests").rglob("*.json"))
    files.extend((ROOT / "releases").rglob("*.json"))
    needle = image_id.encode()
    return any(path.is_file() and needle in path.read_bytes() for path in files)


def audit(image_id: str) -> None:
    [image] = json.loads(run("docker", "image", "inspect", image_id))
    if image["Id"] != image_id or image.get("RepoTags") or image.get("RepoDigests"):
        raise RuntimeError(f"Image is no longer dangling: {image_id}")
    if run("docker", "ps", "--all", "--filter", f"ancestor={image_id}", "--quiet").strip():
        raise RuntimeError(f"A container now references {image_id}")
    if referenced(image_id):
        raise RuntimeError(f"A saved deployment references {image_id}")


def main() -> None:
    if os.geteuid() != 0:
        raise RuntimeError("Run as root")
    # Finish every check before making the first change.
    for image_id in IMAGES:
        audit(image_id)
    for image_id in IMAGES:
        run("docker", "image", "rm", image_id)
        print(f"removed {image_id}", flush=True)
    print(run("df", "-h", "/"), end="", flush=True)


if __name__ == "__main__":
    main()
