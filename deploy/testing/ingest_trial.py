"""Install a branch-trial ingestion image for one scope, then plan, apply and verify it.

Installed by the ingestion trial workflow (branch-ingest-deploy.yml). Under the release
lock it:

1. creates the private trial directory, backs up the live deployment directory and
   installs the candidate Compose model the workflow transferred;
2. pulls the image by digest, checks its labels and runtime user, and tags it as the
   local test image ``factorlab-test-<unit>:<test-id>`` that ``ingestion_test`` expects;
3. runs ``ingestion_test`` ``plan`` (offline smoke tests, no container changes),
   ``apply`` (only the scope's services are replaced or added; a startup failure
   restores the preceding model) and ``verify``.

Schema migrations are never run here: apply any wave the image needs first.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
from pathlib import Path

import ingestion_test as test

UNITS = {
    "ingest-us": "ingest-us",
    "futures-us": "ingest-us",
    "indexes-us": "ingest-us",
    "ingest-india": "ingest-india",
    "ingest-broker": "ingest-broker",
}
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
TRIAL_RE = re.compile(r"[0-9]{8}-[0-9a-f]{12}\Z")


def image_re(unit: str) -> re.Pattern[str]:
    return re.compile(rf"ghcr\.io/arjundoshi221/factorlab-{unit}@sha256:[0-9a-f]{{64}}\Z")


def install_image(image: str, unit: str, commit: str, test_id: str, user: str = "10001") -> str:
    if image_re(unit).fullmatch(image) is None:
        raise RuntimeError(f"Invalid {unit} image reference")
    test.run("docker", "pull", image)
    [metadata] = json.loads(test.run("docker", "image", "inspect", image))
    labels = metadata["Config"].get("Labels") or {}
    if (
        labels.get("io.factorlab.component") != unit
        or labels.get("org.opencontainers.image.revision") != commit
        or labels.get("org.opencontainers.image.version") != f"0.0.0-trial.{commit[:12]}"
    ):
        raise RuntimeError(f"{unit} image labels do not match the trial commit")
    if metadata["Config"].get("User", "").split(":")[0] != user:
        raise RuntimeError(f"Wrong runtime user in the {unit} image")
    test.run("docker", "tag", metadata["Id"], f"factorlab-test-{unit}:{test_id}")
    return metadata["Id"]


def prepare(
    root: Path, model: Path, runners: tuple[str, ...] = ("ingest_trial.py", "ingestion_test.py")
) -> None:
    if root.exists():
        raise RuntimeError("Trial ID already exists")
    if (test.DEPLOY.parent / "state/images.env").exists():
        raise RuntimeError("Component pins exist; use the normal release workflow")
    if shutil.disk_usage(test.DEPLOY.parent).free < 2 * 1024**3:
        raise RuntimeError("Less than 2 GiB free on the VPS root filesystem")
    os.umask(0o077)
    root.mkdir(mode=0o700, parents=True)
    # The backup can hold resolved settings: it stays inside the root-only directory.
    shutil.copytree(test.DEPLOY, root / "backup/deploy", symlinks=True)
    (root / "source/deploy").mkdir(parents=True)
    shutil.copy2(model, root / "source/deploy/compose.production.yml")
    for filename in runners:
        shutil.copy2(Path(__file__).with_name(filename), root / filename)
        (root / filename).chmod(0o600)


def apply(root: Path, test_id: str, scope: str, commit: str, image: str, model: Path) -> None:
    prepare(root, model)
    install_image(image, UNITS[scope], commit, test_id)
    (root / "commit").write_text(commit + "\n")
    test.configure_scope(root, scope, planning=True)
    test.plan(root, test_id)
    print("FACTORLAB_RESULT_PLAN=passed", flush=True)
    # apply() restores the preceding model itself when startup verification fails.
    try:
        test.apply(root)
    except Exception:
        print("FACTORLAB_RESULT_ROLLBACK=restored", flush=True)
        raise
    print("FACTORLAB_RESULT_VERIFICATION=passed", flush=True)
    print("FACTORLAB_RESULT_ROLLBACK=not-needed", flush=True)
    services = json.loads((root / "after-containers.json").read_text())
    print(f"FACTORLAB_RESULT_SERVICES={','.join(c['name'].strip('/') for c in services)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "verify", "restore"))
    parser.add_argument("test_id")
    parser.add_argument("--scope", choices=tuple(UNITS))
    parser.add_argument("--commit")
    parser.add_argument("--image")
    parser.add_argument("--model", type=Path, help="candidate compose.production.yml")
    args = parser.parse_args()
    if os.geteuid() != 0 or not TRIAL_RE.fullmatch(args.test_id):
        raise RuntimeError("Run as root with a YYYYMMDD-<12 hex> trial ID")
    root = test.BASE / args.test_id
    with Path("/var/lock/factorlab-release.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "apply":
            if not args.commit or not COMMIT_RE.fullmatch(args.commit):
                raise RuntimeError("A full commit SHA is required")
            if not args.test_id.endswith(f"-{args.commit[:12]}"):
                raise RuntimeError("Trial ID must name the commit")
            if not (args.scope and args.image and args.model and args.model.is_file()):
                raise RuntimeError("apply needs --scope, --image and --model")
            apply(root, args.test_id, args.scope, args.commit, args.image, args.model)
            return
        if not root.is_dir():
            raise RuntimeError("Unknown trial ID")
        test.configure_scope(root, None, planning=False)
        if args.command == "verify":
            test.verify(root)
            print("FACTORLAB_RESULT_VERIFICATION=passed", flush=True)
        else:
            test.restore(root)
            print("FACTORLAB_RESULT_ROLLBACK=restored", flush=True)


if __name__ == "__main__":
    main()
