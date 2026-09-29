"""Scaffold a new provider, component or library (``fl-scaffold``).

    uv run python tools/scaffold.py new-provider <name> --dataset <id> --market <ISO3>
        [--resolutions 1min,daily] [--component <c>] [--bind] [--summary "..."]
    uv run python tools/scaffold.py new-component <name> [--writer] [--summary "..."]
    uv run python tools/scaffold.py new-lib <name> [--depends-on core,ingest] [--summary "..."]
    common: [--dry-run] [--no-sync]

Renders tools/templates/<kind>/ (``{{placeholders}}``), writes the member's CONTEXT.md
from tools/templates/CONTEXT.md plus CLAUDE.md / AGENTS.md pointers and py.typed, and
wires the member in wherever the workspace needs it:

  provider   root [tool.uv.sources]; tests/architecture PROVIDER_TOKENS (R3);
             --component: the component's dependency, providers.py PROVIDERS and
             component.yaml providers; --bind: configs/sources/<p>.yaml and a *shadow*
             binding in configs/ingestion/bindings.yaml
  component  root [tool.uv.sources]; deploy/scripts/prepare-host.sh log directories;
             deploy/logrotate/factorlab; CHANGELOG.md; deploy/compose.production.yml
  lib        root [tool.uv.sources]; tests/architecture LIBRARY_LAYERS (R7)

Then (unless --no-sync) it runs ``uv lock``, ``uv sync --all-packages`` and the new
member's tests. A new provider's conformance tests fail until real captures are
recorded under its tests/fixtures/<dataset>/ (docs/architecture/07 §13.1): intended.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TEMPLATES = Path(__file__).resolve().parent / "templates"
NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
GROUP = {"provider": "providers", "component": "components", "lib": "libs"}
DIST = {
    "provider": "factorlab-provider-{}",
    "component": "factorlab-component-{}",
    "lib": "factorlab-{}",
}
PACKAGE_DIR = {
    "provider": "src/factorlab/sources/{}",
    "component": "src/factorlab/components/{}",
    "lib": "src/factorlab/{}",
}


class ScaffoldError(Exception):
    pass


def _load_tool(name: str):
    key = f"{name}_tool"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(f"{name}.py"))
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


@dataclass
class Plan:
    """Files to create and files to edit, applied only after everything validated."""

    root: Path
    create: dict[Path, str] = field(default_factory=dict)
    edit: dict[Path, str] = field(default_factory=dict)

    def read(self, relative: str) -> str:
        path = self.root / relative
        if path in self.edit:
            return self.edit[path]
        if not path.exists():
            raise ScaffoldError(f"{relative} is missing")
        return path.read_text(encoding="utf-8")

    def change(self, relative: str, old: str, new: str) -> None:
        text = self.read(relative)
        if text.count(old) != 1:
            raise ScaffoldError(f"{relative}: cannot find a unique anchor to edit")
        self.edit[self.root / relative] = text.replace(old, new)

    def apply(self) -> list[str]:
        for path, text in {**self.create, **self.edit}.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
        return sorted(p.relative_to(self.root).as_posix() for p in {**self.create, **self.edit})


def values_for(kind: str, name: str, summary: str) -> dict[str, str]:
    if not NAME.match(name) or len(name) > 40:
        raise ScaffoldError(
            f"{name!r}: use lowercase kebab-case, e.g. 'finnhub' or 'ingest-crypto'"
        )
    package = name.replace("-", "_")
    return {
        "name": name,
        "package": package,
        "NAME": package.upper(),
        "Class": "".join(part.capitalize() for part in name.split("-")),
        "title": " ".join(part.capitalize() for part in name.split("-")),
        "dist": DIST[kind].format(name),
        "summary": summary,
    }


def render(text: str, values: dict[str, str]) -> str:
    def value(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in values:
            raise ScaffoldError(f"template placeholder {{{{{key}}}}} has no value")
        return values[key]

    return PLACEHOLDER.sub(value, text)


def member_files(plan: Plan, kind: str, values: dict[str, str]) -> Path:
    member = plan.root / GROUP[kind] / values["name"]
    if member.exists():
        raise ScaffoldError(f"{member.relative_to(plan.root).as_posix()} already exists")
    for template in sorted((TEMPLATES / kind).rglob("*")):
        if template.is_dir() or "__pycache__" in template.parts:
            continue
        relative = render(template.relative_to(TEMPLATES / kind).as_posix(), values)
        plan.create[member / relative.removesuffix(".tmpl")] = render(
            template.read_text(encoding="utf-8"), values
        )
    package_dir = member / PACKAGE_DIR[kind].format(values["package"])
    plan.create[package_dir / "py.typed"] = ""
    context = (TEMPLATES / "CONTEXT.md").read_text(encoding="utf-8")
    context = context.replace("{name}", values["dist"]).replace("{summary}", values["summary"])
    plan.create[member / "CONTEXT.md"] = context
    plan.create[member / "CLAUDE.md"] = pointer_claude()
    plan.create[member / "AGENTS.md"] = pointer_agents(values["dist"], member, plan.root)
    return member


def pointer_claude() -> str:
    return "@CONTEXT.md\n"


def pointer_agents(title: str, member: Path, root: Path) -> str:
    up = Path(os.path.relpath(root, member)).as_posix()
    return (
        f"# {title}\n\nRead [CONTEXT.md](CONTEXT.md) before changing this member. The "
        f"repository-wide rules are in [AGENTS.md]({up}/AGENTS.md).\n"
    )


def add_workspace_source(plan: Plan, dist: str) -> None:
    text = plan.read("pyproject.toml")
    lines = text.split("\n")
    try:
        start = lines.index("[tool.uv.sources]")
    except ValueError:
        raise ScaffoldError("pyproject.toml has no [tool.uv.sources] table") from None
    end = start + 1
    while end < len(lines) and lines[end].strip() and not lines[end].startswith("["):
        end += 1
    if any(line.startswith(f"{dist} ") for line in lines[start:end]):
        raise ScaffoldError(f"{dist} is already a workspace source")
    lines.insert(end, f"{dist} = {{ workspace = true }}")
    plan.edit[plan.root / "pyproject.toml"] = "\n".join(lines)


# ── kinds ────────────────────────────────────────────────────────────────────


def new_provider(plan: Plan, args: argparse.Namespace) -> list[str]:
    values = values_for("provider", args.name, args.summary or f"{args.name} data source.")
    values |= {
        "dataset": args.dataset,
        "market": args.market,
        "resolutions": _resolutions(args.dataset, args.resolutions),
    }
    member_files(plan, "provider", values)
    add_workspace_source(plan, values["dist"])
    plan.change(
        "tests/architecture/test_boundaries.py",
        "PROVIDER_TOKENS = (\n",
        f'PROVIDER_TOKENS = (\n    "{values["package"]}",\n',
    )
    if args.component:
        wire_provider(plan, args.component, values)
    if args.bind:
        plan.create[plan.root / "configs" / "sources" / f"{values['package']}.yaml"] = (
            f"# {values['title']} settings (non-secret; docs/architecture/07 §9.3).\n"
            f"name: {values['package']}\ntimeout: 30\n"
        )
        bindings = plan.read("configs/ingestion/bindings.yaml").rstrip("\n")
        plan.edit[plan.root / "configs/ingestion/bindings.yaml"] = (
            f"{bindings}\n  # {values['package']}: scaffolded as shadow; promote after parity (07 §15.2)\n"
            f"  - {{dataset: {args.dataset}, market: {args.market}, provider: {values['package']}, "
            f"role: shadow, priority: 90}}\n"
        )
    return [
        "record captures: tests/fixtures/<dataset>/<case>.capture.json + .expected.json (07 §13.1)",
        "fill in plan/fetch/normalize in sources.py",
        "name any secret it needs in libs/core settings and the secrets agent",
    ]


def _resolutions(dataset: str, requested: str | None) -> str:
    """The Capabilities argument the dataset needs; validated against the catalogue."""
    from factorlab.ingest.datasets import DATASETS, RESOLUTIONS

    if dataset not in DATASETS:
        raise ScaffoldError(f"unknown dataset {dataset!r}; known: {', '.join(sorted(DATASETS))}")
    if not DATASETS[dataset].has_resolution:
        if requested:
            raise ScaffoldError(f"{dataset} has no resolutions; drop --resolutions")
        return ""
    chosen = [r.strip() for r in (requested or "daily").split(",") if r.strip()]
    unknown = sorted(set(chosen) - RESOLUTIONS)
    if unknown:
        raise ScaffoldError(
            f"unknown resolutions {unknown}; known: {', '.join(sorted(RESOLUTIONS))}"
        )
    return ", resolutions=frozenset({" + ", ".join(f'"{r}"' for r in chosen) + "})"


def wire_provider(plan: Plan, component: str, values: dict[str, str]) -> None:
    base = f"components/{component}"
    dep = f'    "{values["dist"]}",\n'
    pyproject = plan.read(f"{base}/pyproject.toml")
    head, marker, rest = pyproject.partition("dependencies = [\n")
    if not marker:
        raise ScaffoldError(f"{base}/pyproject.toml has no dependencies list")
    plan.edit[plan.root / base / "pyproject.toml"] = head + marker + dep + rest
    plan.change(
        f"{base}/pyproject.toml",
        "[tool.uv.sources]\n",
        f"[tool.uv.sources]\n{values['dist']} = {{ workspace = true }}\n",
    )
    package = component.replace("-", "_")
    providers = f"{base}/src/factorlab/components/{package}/providers.py"
    text = plan.read(providers)
    match = re.search(r"PROVIDERS: tuple\[str, \.\.\.\] = \(([^)]*)\)", text)
    if not match:
        raise ScaffoldError(f"{providers}: no PROVIDERS tuple")
    names = [n.strip().strip("\"'") for n in match.group(1).split(",") if n.strip()]
    names.append(values["package"])
    plan.edit[plan.root / providers] = text.replace(
        match.group(0),
        "PROVIDERS: tuple[str, ...] = ("
        + ", ".join(f'"{n}"' for n in names)
        + ("," if len(names) == 1 else "")
        + ")",
    )
    manifest = plan.read(f"{base}/component.yaml")
    listed = re.search(r"^providers: \[([^\]]*)\]$", manifest, re.MULTILINE)
    if not listed:
        raise ScaffoldError(f"{base}/component.yaml: no one-line providers list")
    items = [p.strip() for p in listed.group(1).split(",") if p.strip()] + [values["package"]]
    plan.edit[plan.root / base / "component.yaml"] = manifest.replace(
        listed.group(0), f"providers: [{', '.join(items)}]"
    )


def new_component(plan: Plan, args: argparse.Namespace) -> list[str]:
    values = values_for("component", args.name, args.summary or f"The {args.name} component.")
    values |= {
        "rollback": "writer" if args.writer else "auto",
        "data_contract": "\n  data_contract: 1" if args.writer else "",
    }
    member = member_files(plan, "component", values)
    plan.create[member / "CHANGELOG.md"] = (
        f"# Changelog: {values['name']}\n\nReleased as `{values['name']}/vX.Y.Z` tags. Sections "
        "are generated by `tools/release.py prepare` from conventional commits; edit them "
        "before the release commit if needed.\n"
    )
    add_workspace_source(plan, values["dist"])
    host = plan.read("deploy/scripts/prepare-host.sh")
    listed = re.search(r'^FACTORLAB_COMPONENTS="([^"]*)"$', host, re.MULTILINE)
    if not listed:
        raise ScaffoldError("deploy/scripts/prepare-host.sh: no FACTORLAB_COMPONENTS list")
    plan.edit[plan.root / "deploy/scripts/prepare-host.sh"] = host.replace(
        listed.group(0), f'FACTORLAB_COMPONENTS="{listed.group(1)} {values["name"]}"'
    )
    plan.change(
        "deploy/logrotate/factorlab",
        "\n{\n",
        f"\n/var/log/factorlab/{values['name']}/*.jsonl\n{{\n",
    )
    name = values["name"]
    return [
        (
            "give it a runtime secrets volume (compose.base.yml, secrets agent) if it needs "
            "more than ClickHouse"
        ),
        f"add providers: tools/scaffold.py new-provider <p> --component {name}",
        (
            "until the platform bootstrap (rollout R2), do not cut a monolith release "
            "(release.ps1 without -Component): it would try to start this service"
        ),
        f"release: deploy/release.ps1 -Component {name}",
    ]


def new_lib(plan: Plan, args: argparse.Namespace) -> list[str]:
    values = values_for("lib", args.name, args.summary or f"The {args.name} library.")
    depends = [d.strip() for d in (args.depends_on or "core").split(",") if d.strip()]
    layers = _layers(plan)
    unknown = [d for d in depends if d not in layers]
    if unknown:
        raise ScaffoldError(f"--depends-on names unknown libraries: {unknown}")
    values |= {
        "dependencies": "".join(f'    "factorlab-{d}",\n' for d in depends),
        "sources": "".join(f"factorlab-{d} = {{ workspace = true }}\n" for d in depends),
    }
    member_files(plan, "lib", values)
    add_workspace_source(plan, values["dist"])
    allowed = sorted(set(depends) | {d2 for d in depends for d2 in layers[d]})
    plan.change(
        "tests/architecture/test_boundaries.py",
        "\n}\n# R8:",
        f'\n    "{values["package"]}": {{{", ".join(repr(a) for a in allowed)}}},\n}}\n# R8:'.replace(
            "'", '"'
        ),
    )
    return [f"add factorlab-{values['name']} to the components that use it"]


def _layers(plan: Plan) -> dict[str, set[str]]:
    text = plan.read("tests/architecture/test_boundaries.py")
    block = re.search(r"LIBRARY_LAYERS = \{(.*?)\n\}", text, re.DOTALL)
    if not block:
        raise ScaffoldError("tests/architecture/test_boundaries.py: no LIBRARY_LAYERS")
    layers = {}
    for key, deps in re.findall(r'"(\w+)": (set\(\)|\{[^}]*\})', block.group(1)):
        layers[key] = set(re.findall(r'"(\w+)"', deps))
    return layers


def run_checks(kind: str, name: str, root: Path) -> None:
    member = f"{GROUP[kind]}/{name}"
    steps = [["uv", "lock"], ["uv", "sync", "--all-packages"]]
    if kind == "component":
        steps.append(["uv", "run", "python", "tools/components.py", "check"])
    steps.append(["uv", "run", "pytest", "-q", member])
    for step in steps:
        print("$", " ".join(step))
        result = subprocess.run(step, cwd=root, check=False)
        if result.returncode and step[-1] != member:
            raise ScaffoldError(f"`{' '.join(step)}` failed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fl-scaffold", description=(__doc__ or "").split("\n\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    provider = commands.add_parser("new-provider")
    provider.add_argument("name")
    provider.add_argument("--dataset", required=True, help="e.g. market.bars, ref.listings")
    provider.add_argument("--market", required=True, help="ISO 3166 alpha-3, e.g. USA, IND")
    provider.add_argument("--component", help="the component that ships it")
    provider.add_argument("--resolutions", help="for bar datasets, e.g. 1min,daily (default daily)")
    provider.add_argument("--bind", action="store_true", help="add a shadow binding")
    component = commands.add_parser("new-component")
    component.add_argument("name")
    component.add_argument(
        "--writer", action="store_true", help="writes production data (rollback class writer)"
    )
    lib = commands.add_parser("new-lib")
    lib.add_argument("name")
    lib.add_argument("--depends-on", help="comma-separated libraries, default: core")
    for sub in (provider, component, lib):
        sub.add_argument("--summary")
        sub.add_argument("--dry-run", action="store_true")
        sub.add_argument("--no-sync", action="store_true")
        sub.add_argument("--root", type=Path, default=REPO, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    kind = args.command.removeprefix("new-")
    plan = Plan(args.root.resolve())
    try:
        steps = {"provider": new_provider, "component": new_component, "lib": new_lib}[kind](
            plan, args
        )
        if args.dry_run:
            for path in sorted({*plan.create, *plan.edit}):
                verb = "create" if path in plan.create else "edit  "
                print(f"{verb} {path.relative_to(plan.root).as_posix()}")
            return 0
        written = plan.apply()
        if kind == "component" and (plan.root / "tools" / "components.py").exists():
            # The merged compose file is generated from every fragment; keep it current.
            subprocess.run(
                [sys.executable, "tools/components.py", "render-compose"], cwd=plan.root, check=True
            )
        print(f"scaffolded {GROUP[kind]}/{args.name}: {len(written)} files written or edited")
        if not args.no_sync:
            run_checks(kind, args.name, plan.root)
    except ScaffoldError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("next:")
    for step in steps:
        print(f"  - {step}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
