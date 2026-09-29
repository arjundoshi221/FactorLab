"""Install each workspace member alone and import every module it ships.

Fails when a member imports something its pyproject does not declare (directly or
through its declared workspace dependencies), which is what keeps each component
image's dependency closure honest. Run from the repo root:

    uv run python tools/check_isolated_imports.py [member-name ...]
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path.cwd()
ENV = REPO / ".tmp" / "isolated-imports-venv"
PY = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

CHECK = r"""
import importlib, pkgutil, sys
from pathlib import Path
root = Path(sys.argv[1])
failed = []
for path in sorted(root.rglob("*.py")):
    if "__pycache__" in path.parts or path.name == "__main__.py":
        continue
    parts = ["factorlab", *path.relative_to(root).with_suffix("").parts]
    if parts[-1] == "__init__":
        parts.pop()
    name = ".".join(parts)
    try:
        importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001
        failed.append(f"{name}: {type(exc).__name__}: {exc}")
print("\n".join(failed) if failed else "ok")
sys.exit(1 if failed else 0)
"""


def members() -> list[tuple[str, Path]]:
    out = []
    for group in ("libs", "providers", "components"):
        for pyproject in sorted((REPO / group).glob("*/pyproject.toml")):
            name = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]
            out.append((name, pyproject.parent))
    return out


def main() -> int:
    wanted = set(sys.argv[1:])
    if not ENV.exists():
        subprocess.run(["uv", "venv", "-q", "--python", "3.12", str(ENV)], check=True)
    bad = 0
    for name, root in members():
        if wanted and name not in wanted:
            continue
        env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(ENV)}
        sync = subprocess.run(
            ["uv", "sync", "--frozen", "--no-dev", "--package", name, "-q"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if sync.returncode:
            print(f"FAIL {name}: uv sync: {sync.stderr.strip()[:300]}")
            bad += 1
            continue
        res = subprocess.run(
            [str(PY), "-c", CHECK, str(root / "src" / "factorlab")],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "FACTORLAB_HOME": str(REPO)},
        )
        status = "ok  " if res.returncode == 0 else "FAIL"
        bad += res.returncode != 0
        print(
            f"{status} {name}"
            + (
                ""
                if res.returncode == 0
                else "\n    " + res.stdout.strip().replace("\n", "\n    ")[:1500]
            )
        )
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
