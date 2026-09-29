"""Verify a built component image against its manifest and lock file.

    uv run python tools/verify_image.py <component> <image>

Checks, for Python components:
  * the installed third-party distributions equal ``uv export --package <pkg>`` exactly
    (nothing missing, nothing extra such as pytest or another component's dependencies);
  * heavy dependencies appear only where they belong (``ONLY_IN``);
  * the image runs as the manifest's user and carries ``io.factorlab.component``;
  * ``<entrypoint> --help`` exits 0.
Static components (web) get the label and user checks.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from packaging.markers import Marker  # noqa: E402

from components import load_all  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ONLY_IN = {  # distribution -> the only components allowed to ship it
    "ib-async": {"ingest-broker"},
    "pdfplumber": {"ingest-political"},
    "fastapi": {"api"},
    "uvicorn": {"api"},
    "pandas": {"api", "ingest-india", "ingest-us", "ingest-political", "ingest-broker"},
    "pytest": set(),
}
# The images are linux/amd64 on CPython 3.12.
LINUX = {"sys_platform": "linux", "platform_system": "Linux", "os_name": "posix",
         "platform_machine": "x86_64", "python_version": "3.12", "python_full_version": "3.12.0",
         "implementation_name": "cpython", "platform_python_implementation": "CPython"}
LIST_DISTS = ("import importlib.metadata as m, json; "
              "print(json.dumps(sorted({d.metadata['Name'] for d in m.distributions()})))")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, cwd=REPO)


def expected_dists(package: str) -> set[str]:
    out = run("uv", "export", "--frozen", "--no-dev", "--package", package, "--no-hashes",
              "--no-emit-workspace", "--no-header", "--no-annotate", "--format",
              "requirements-txt")
    if out.returncode:
        raise SystemExit(out.stderr)
    wanted = set()
    for line in out.stdout.splitlines():
        if not line or line.startswith(("#", "-e", ".")):
            continue
        requirement, _, marker = line.partition(";")
        if marker.strip() and not Marker(marker.strip()).evaluate(LINUX):
            continue  # e.g. tzdata ; sys_platform == 'win32'
        wanted.add(norm(requirement.split("==")[0].strip()))
    return wanted


def main(component: str, image: str) -> int:
    [manifest] = [c for c in load_all() if c.name == component]
    failures: list[str] = []
    inspect = json.loads(run("docker", "image", "inspect", image).stdout or "[]")
    if not inspect:
        return print(f"no such image: {image}", file=sys.stderr) or 2
    config = inspect[0]["Config"]
    if (config.get("Labels") or {}).get("io.factorlab.component") != component:
        failures.append("label io.factorlab.component is missing or wrong")
    user = (config.get("User") or "0").split(":")[0]
    if user != manifest.platform.user:
        failures.append(f"image user {user!r} != manifest {manifest.platform.user!r}")
    if manifest.kind == "python":
        listed = run("docker", "run", "--rm", "--entrypoint", "python", image, "-c", LIST_DISTS)
        installed = {norm(n) for n in json.loads(listed.stdout)}
        third_party = {n for n in installed if not n.startswith("factorlab-")}
        wanted = expected_dists(manifest.package)
        if missing := sorted(wanted - third_party):
            failures.append(f"missing distributions: {missing}")
        if extra := sorted(third_party - wanted):
            failures.append(f"unexpected distributions: {extra}")
        for dist, allowed in ONLY_IN.items():
            if dist in installed and component not in allowed:
                failures.append(f"{dist} must not ship in {component}")
        help_run = run("docker", "run", "--rm", image, "--help")
        if help_run.returncode:
            failures.append(f"{manifest.entrypoint} --help exited {help_run.returncode}")
        summary = f"{len(third_party)} third-party + {len(installed - third_party)} factorlab"
    else:
        summary = "static"
    for failure in failures:
        print(f"FAIL {component}: {failure}", file=sys.stderr)
    if not failures:
        print(f"ok   {component}: {summary}; user {user}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
