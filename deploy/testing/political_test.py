"""Install a branch-trial ingest-political image, then run the identity repair.

Installed by the political trial workflow. Only the ``ingest-political`` image in the
live Compose model changes (the daily cron runs it); no long-running container is
recreated. Under the release and political locks it:

1. pulls the image by digest, checks its labels and runtime user, and smoke-tests the
   production subcommands with no network;
2. installs the candidate model;
3. runs ``dedupe-political-trades``, ``retire-duplicate-listings`` and
   ``reresolve-political-trades`` as dry runs, refuses to apply above ``--max-delete``
   superseded rows, any ambiguous trade or ``--max-retire`` retirements, then applies
   all three in that order;
4. verifies that dry runs find nothing left to delete, retire or relink.

A failure before any data write restores the preceding model. Data writes are not
rolled back (docs/operations/identity-repair.md describes the manual undo).
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

SERVICE = "ingest-political"
IMAGE_RE = re.compile(r"ghcr\.io/arjundoshi221/factorlab-ingest-political@sha256:[0-9a-f]{64}\Z")
COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")
TRIAL_RE = re.compile(r"political-[0-9a-f]{12}\Z")
COMMANDS = (
    "bootstrap",
    "engine",
    "dedupe-political-trades",
    "retire-duplicate-listings",
    "reresolve-political-trades",
)


def image_id(image: str, commit: str, version: str) -> str:
    if IMAGE_RE.fullmatch(image) is None:
        raise RuntimeError("Invalid ingest-political image reference")
    test.run("docker", "pull", image)
    [metadata] = json.loads(test.run("docker", "image", "inspect", image))
    labels = metadata["Config"].get("Labels") or {}
    if (
        labels.get("io.factorlab.component") != SERVICE
        or labels.get("org.opencontainers.image.revision") != commit
        or labels.get("org.opencontainers.image.version") != version
    ):
        raise RuntimeError("ingest-political image labels do not match the trial commit")
    if metadata["Config"].get("User", "").split(":")[0] != "10001":
        raise RuntimeError("Wrong runtime user in the ingest-political image")
    for command in COMMANDS:
        test.run("docker", "run", "--rm", "--network", "none", metadata["Id"], command, "--help")
    test.run("docker", "run", "--rm", "--network", "none", metadata["Id"], "engine", "validate")
    return metadata["Id"]


def job(command: str, *, apply: bool) -> dict:
    output = test.compose(
        test.DEPLOY / "compose.production.yml",
        "run",
        "--rm",
        "--no-deps",
        "-T",
        SERVICE,
        command,
        *(["--apply"] if apply else []),
    )
    report = json.loads(output)
    print(
        f"{command}{' --apply' if apply else ''}: {json.dumps(report, sort_keys=True)}", flush=True
    )
    return report


def summary(report: dict) -> dict:
    return {
        k: v
        for k, v in report.items()
        if k not in {"retirements", "unresolved_tickers", "ambiguous_trades"}
    }


def restore(root: Path) -> None:
    test.install_model(root / "before.json")
    print("FACTORLAB_RESULT_ROLLBACK=restored", flush=True)


def apply(root: Path, commit: str, image: str, max_retire: int, max_delete: int) -> None:
    if root.exists():
        raise RuntimeError("Trial ID already exists")
    if (test.DEPLOY.parent / "state/images.env").exists():
        raise RuntimeError("Component pins exist; use the normal release workflow")
    if shutil.disk_usage(test.DEPLOY.parent).free < 2 * 1024**3:
        raise RuntimeError("Less than 2 GiB free on the VPS root filesystem")
    os.umask(0o077)
    root.mkdir(mode=0o700, parents=True)
    for filename in ("political_test.py", "ingestion_test.py"):
        shutil.copy2(Path(__file__).with_name(filename), root / filename)
        (root / filename).chmod(0o600)
    candidate_id = image_id(image, commit, f"0.0.0-trial.{commit[:12]}")
    live = json.loads(
        test.compose(test.DEPLOY / "compose.production.yml", "config", "--format", "json")
    )
    if SERVICE not in live["services"]:
        raise RuntimeError("The live model has no ingest-political service")
    candidate = json.loads(json.dumps(live))
    candidate["services"][SERVICE]["image"] = candidate_id
    test.store(root / "before.json", live)
    test.store(root / "candidate.json", candidate)
    (root / "commit").write_text(commit + "\n")
    test.compose(root / "candidate.json", "config", "--quiet")
    written = False
    try:
        test.install_model(root / "candidate.json")
        dedupe = job("dedupe-political-trades", apply=False)
        if dedupe["ambiguous"] or dedupe["stale_rows"] > max_delete:
            raise RuntimeError(
                f"Dry run deletes {dedupe['stale_rows']} rows with {dedupe['ambiguous']} "
                f"ambiguous trades; limit is {max_delete} and none ambiguous"
            )
        retire = job("retire-duplicate-listings", apply=False)
        test.store(root / "retire-plan.json", retire)
        if retire["retired_listings"] > max_retire:
            raise RuntimeError(
                f"Dry run retires {retire['retired_listings']} listings; limit is {max_retire}"
            )
        reresolve = job("reresolve-political-trades", apply=False)
        written = True
        deduped = job("dedupe-political-trades", apply=True)
        retired = job("retire-duplicate-listings", apply=True)
        relinked = job("reresolve-political-trades", apply=True)
        test.store(
            root / "repair.json", {"dedupe": deduped, "retire": retired, "reresolve": relinked}
        )
        print(f"FACTORLAB_RESULT_DEDUPE={json.dumps(summary(deduped), sort_keys=True)}")
        print(f"FACTORLAB_RESULT_RETIRE={json.dumps(summary(retired), sort_keys=True)}")
        print(f"FACTORLAB_RESULT_RERESOLVE={json.dumps(summary(relinked), sort_keys=True)}")
        print(f"dry-run re-resolve before retirement: {json.dumps(summary(reresolve))}")
        verify()
    except Exception:
        if written:
            print("FACTORLAB_RESULT_ROLLBACK=not-possible-after-data-writes", flush=True)
        else:
            restore(root)
        raise
    (root / "applied").touch(mode=0o600)
    print("FACTORLAB_RESULT_ROLLBACK=not-needed", flush=True)


def verify() -> None:
    if job("dedupe-political-trades", apply=False)["stale_rows"]:
        raise RuntimeError("Superseded political trade rows remain after the repair")
    if job("retire-duplicate-listings", apply=False)["retired_listings"]:
        raise RuntimeError("Duplicate listings remain after the repair")
    if job("reresolve-political-trades", apply=False)["relinked"]:
        raise RuntimeError("Political trades still need relinking after the repair")
    print("FACTORLAB_RESULT_VERIFICATION=passed", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "verify", "restore"))
    parser.add_argument("test_id")
    parser.add_argument("--commit")
    parser.add_argument("--image")
    parser.add_argument("--max-retire", type=int, default=200)
    parser.add_argument("--max-delete", type=int, default=500)
    args = parser.parse_args()
    if os.geteuid() != 0 or not TRIAL_RE.fullmatch(args.test_id):
        raise RuntimeError("Run as root with a political-<12 hex> trial ID")
    root = test.BASE / args.test_id
    with (
        Path("/var/lock/factorlab-release.lock").open("a") as release,
        Path("/run/lock/factorlab-political.lock").open("a") as political,
    ):
        fcntl.flock(release, fcntl.LOCK_EX)
        # The daily political cron holds this lock; never repair while it runs.
        fcntl.flock(political, fcntl.LOCK_EX)
        if args.command == "apply":
            if not args.commit or not COMMIT_RE.fullmatch(args.commit):
                raise RuntimeError("A full commit SHA is required")
            if args.test_id != f"political-{args.commit[:12]}" or not args.image:
                raise RuntimeError("Trial ID and image must match the commit")
            apply(root, args.commit, args.image, args.max_retire, args.max_delete)
        elif not root.is_dir():
            raise RuntimeError("Unknown trial ID")
        elif args.command == "verify":
            verify()
        else:
            restore(root)


if __name__ == "__main__":
    main()
