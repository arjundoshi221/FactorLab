"""Registry of deployable components (``components/*/component.yaml``).

    uv run python tools/components.py list            one line per component
    uv run python tools/components.py check           validate every manifest against the code
    uv run python tools/components.py matrix [names]  GitHub Actions matrix JSON (CI image builds)
    uv run python tools/components.py show <name>     one manifest as JSON

A manifest describes what the platform needs to build, run and release a component:
its package and console entry point, image, compose services and their commands,
the providers it ships, the secret *names* it reads, and its release class
(``rollback``: auto | writer | forward-only).
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

REPO = Path(__file__).resolve().parents[1]
REGISTRY = "ghcr.io/arjundoshi221"
NAME = re.compile(r"^[a-z][a-z0-9-]{1,40}$")


class Service(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    command: list[str] = Field(default_factory=list)
    mode: Literal["daemon", "run-on-deploy", "scheduled"] = "daemon"
    profile: str | None = None
    schedule: str | None = None


class Health(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["http", "heartbeat", "running", "exit-zero", "compose"]
    url: str | None = None
    service: str | None = None
    max_age: int | None = None


class Platform(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image_var: str
    rollback: Literal["auto", "writer", "forward-only"]
    data_contract: int | None = None
    user: str
    user_exception: str | None = None
    logs: str

    @model_validator(mode="after")
    def _consistent(self) -> Platform:
        if self.rollback == "writer" and self.data_contract is None:
            raise ValueError("writers declare a data_contract (bump it when written data changes)")
        if self.user == "0" and not self.user_exception:
            raise ValueError("a root container needs a user_exception explaining why")
        return self


class Component(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_: Literal[1] = Field(alias="schema")
    name: str
    kind: Literal["python", "static"]
    package: str | None = None
    summary: str
    image: str
    dockerfile: str
    entrypoint: str | None = None
    providers: list[str] = Field(default_factory=list)
    secrets: list[str] = Field(default_factory=list)
    services: list[Service]
    health: Health
    platform: Platform

    @property
    def root(self) -> Path:
        return REPO / "components" / self.name


def load_all() -> list[Component]:
    components = []
    for path in sorted((REPO / "components").glob("*/component.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        components.append(Component.model_validate(data))
    return components


def _provider_tuple(component: Component) -> list[str] | None:
    """The ``PROVIDERS`` tuple in the component's providers.py, if it has one."""
    if not component.package:
        return None
    pkg = component.name.replace("-", "_")
    path = component.root / "src" / "factorlab" / "components" / pkg / "providers.py"
    if not path.exists():
        return None
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.AnnAssign | ast.Assign):
            targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
            if any(isinstance(t, ast.Name) and t.id == "PROVIDERS" for t in targets):
                return list(ast.literal_eval(node.value))
    return None


def problems(components: list[Component]) -> list[str]:
    found: list[str] = []
    seen_services: dict[str, str] = {}
    for c in components:
        where = f"components/{c.name}/component.yaml"
        if not NAME.match(c.name) or not c.root.is_dir():
            found.append(f"{where}: name must match its directory")
        if c.image != f"{REGISTRY}/factorlab-{c.name}":
            found.append(f"{where}: image must be {REGISTRY}/factorlab-{c.name}")
        expected_var = f"FACTORLAB_{c.name.upper().replace('-', '_')}_IMAGE"
        if c.platform.image_var != expected_var:
            found.append(f"{where}: platform.image_var must be {expected_var}")
        dockerfile = REPO / c.dockerfile
        if not dockerfile.is_file():
            found.append(f"{where}: {c.dockerfile} is missing")
        if not dockerfile.with_name(dockerfile.name + ".dockerignore").is_file():
            found.append(f"{where}: {c.dockerfile}.dockerignore is missing")
        for service in c.services:
            if service.name in seen_services:
                found.append(f"{where}: service {service.name} also belongs to "
                             f"{seen_services[service.name]}")
            seen_services[service.name] = c.name
        if c.kind != "python":
            continue
        pyproject = c.root / "pyproject.toml"
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        if c.package != project["name"]:
            found.append(f"{where}: package must be {project['name']}")
        if c.entrypoint not in project.get("scripts", {}):
            found.append(f"{where}: entrypoint {c.entrypoint} is not a [project.scripts] entry")
        if dockerfile.is_file() and f"--package {c.package}" not in dockerfile.read_text("utf-8"):
            found.append(f"{where}: {c.dockerfile} must install --package {c.package}")
        declared = sorted(d.split(">")[0].split("[")[0].removeprefix("factorlab-provider-")
                          .replace("-", "_")
                          for d in project.get("dependencies", [])
                          if d.startswith("factorlab-provider-"))
        shipped = _provider_tuple(c)
        if sorted(c.providers) != declared:
            found.append(f"{where}: providers {sorted(c.providers)} != provider dependencies "
                         f"{declared}")
        if shipped is not None and sorted(shipped) != sorted(c.providers):
            found.append(f"{where}: providers.py PROVIDERS {sorted(shipped)} != manifest "
                         f"{sorted(c.providers)}")
    return found


def matrix(components: list[Component], names: list[str] | None = None) -> dict:
    chosen = [c for c in components if not names or c.name in names]
    return {"include": [{"component": c.name, "dockerfile": c.dockerfile, "kind": c.kind,
                         "entrypoint": c.entrypoint or ""} for c in chosen]}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "list"
    components = load_all()
    if command == "list":
        for c in components:
            services = ", ".join(s.name for s in c.services)
            print(f"{c.name:18} {c.kind:7} {c.platform.rollback:13} {services}")
        return 0
    if command == "check":
        issues = problems(components)
        for issue in issues:
            print(issue, file=sys.stderr)
        if not issues:
            print(f"{len(components)} component manifests are consistent")
        return 1 if issues else 0
    if command == "matrix":
        print(json.dumps(matrix(components, args[1:] or None)))
        return 0
    if command == "show" and len(args) == 2:
        match = [c for c in components if c.name == args[1]]
        if not match:
            print(f"unknown component {args[1]}", file=sys.stderr)
            return 2
        print(match[0].model_dump_json(indent=2, by_alias=True))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
