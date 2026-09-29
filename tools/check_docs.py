"""Documentation checks and the generated parts of the docs.

    uv run python tools/check_docs.py                         check (CI and pre-commit)
    uv run python tools/check_docs.py --write                 regenerate, then check
    uv run python tools/check_docs.py --update-link-baseline  record fixed links (shrink only)

Checks (failures):
  D1  features (docs/features/F-*.md): frontmatter schema; ids unique and matching the file
      name; components, sprint, decisions and linked features exist
  D2  decisions (docs/decisions/NNNN-*.md): frontmatter schema; ids unique; superseded_by exists
  D3  sprints (docs/sprints/YYYY-Sxx.md): frontmatter schema; at most one active sprint
  D4  generated blocks are current: the features, sprints and decisions indexes, each
      sprint's planned list, and components/web/src/roadmap.generated.json
  D5  every workspace member and release unit has a CONTEXT.md with the template's
      headings (at most 150 lines) plus CLAUDE.md / AGENTS.md pointers to it; every
      release unit has a CHANGELOG.md
  D6  relative Markdown links resolve (files and #anchors). The broken links that existed
      when this check was introduced are listed in tools/docs_link_baseline.txt, which
      may only shrink.
Warnings (printed, never fail):
  W1  retired technology named in current-state docs (Postgres, Railway, Task Scheduler, NAS)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPO = Path(__file__).resolve().parents[1]
FEATURES = REPO / "docs" / "features"
DECISIONS = REPO / "docs" / "decisions"
SPRINTS = REPO / "docs" / "sprints"
ROADMAP_JSON = REPO / "components" / "web" / "src" / "roadmap.generated.json"
LINK_BASELINE = REPO / "tools" / "docs_link_baseline.txt"
CONTEXT_TEMPLATE = REPO / "tools" / "templates" / "CONTEXT.md"
MAX_CONTEXT_LINES = 150
RETIRED_TERMS = re.compile(r"\b(Postgres(?:QL)?|Railway|Task Scheduler|NAS)\b")
CURRENT_STATE_DOCS = ("AGENTS.md", "CLAUDE.md", "docs/architecture/08-repository-layout.md")
STATUS_ORDER = ("in-progress", "review", "planned", "backlog", "idea", "shipped", "dropped")
ROADMAP_STATE = {
    "in-progress": "Now",
    "review": "Now",
    "planned": "Next",
    "backlog": "Planned",
    "idea": "Planned",
    "shipped": "Shipped",
}

FEATURE_FILE = re.compile(r"^(F-\d{3})-[a-z0-9-]+\.md$")
DECISION_FILE = re.compile(r"^(\d{4})-[a-z0-9-]+\.md$")
SPRINT_FILE = re.compile(r"^(\d{4}-S\d{2})\.md$")
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")


class Roadmap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(pattern=r"^V\d+$")
    summary: str


class Feature(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^F-\d{3}$")
    title: str
    status: Literal["idea", "backlog", "planned", "in-progress", "review", "shipped", "dropped"]
    priority: Literal["P0", "P1", "P2", "P3"]
    components: list[str]
    owner: str
    sprint: str | None = None
    decisions: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    roadmap: Roadmap | None = None
    created: dt.date
    updated: dt.date


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^ADR-\d{4}$")
    title: str
    status: Literal["proposed", "accepted", "implemented", "superseded", "rejected"]
    date: dt.date
    superseded_by: str | None = None
    status_note: str | None = None
    status_confirmed: bool = False


class Sprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^\d{4}-S\d{2}$")
    start: dt.date
    end: dt.date
    status: Literal["planned", "active", "closed"]
    goal: str


# ── helpers ──────────────────────────────────────────────────────────────────


def rel(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing YAML frontmatter")
    head, _, body = text[4:].partition("\n---\n")
    return yaml.safe_load(head) or {}, body


def markdown_files() -> list[Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.md"],
        cwd=REPO,
        capture_output=True,
        check=True,
    ).stdout.decode("utf-8")
    return sorted(
        REPO / p
        for p in listed.split("\0")
        if p and (REPO / p).is_file() and "node_modules/" not in p
    )


def members() -> list[Path]:
    """Every workspace member and release unit directory."""
    found = [
        p.parent
        for pattern in (
            "libs/*/pyproject.toml",
            "providers/*/pyproject.toml",
            "components/*/component.yaml",
            "cloudflare/*/package.json",
        )
        for p in REPO.glob(pattern)
    ]
    return sorted({*found, REPO / "deploy"})


def release_units() -> list[Path]:
    return sorted(
        {p.parent for p in REPO.glob("components/*/component.yaml")}
        | {p.parent for p in REPO.glob("cloudflare/*/package.json")}
        | {REPO / "deploy"}
    )


def unit_names() -> set[str]:
    names = {p.name for p in members()} | {"platform"}
    return names


# ── loading ──────────────────────────────────────────────────────────────────


def load(
    directory: Path, pattern: re.Pattern[str], model: type[BaseModel], problems: list[str]
) -> dict[str, tuple[Path, BaseModel, str]]:
    found: dict[str, tuple[Path, BaseModel, str]] = {}
    if not directory.is_dir():
        return found
    for path in sorted(directory.glob("*.md")):
        match = pattern.match(path.name)
        if not match:
            continue
        try:
            data, body = frontmatter(path)
            item = model.model_validate(data)
        except (ValueError, yaml.YAMLError, ValidationError) as exc:
            problems.append(f"{rel(path)}: {str(exc).splitlines()[0]}")
            continue
        expected = match.group(1) if model is not Decision else f"ADR-{match.group(1)}"
        if item.id != expected:  # type: ignore[attr-defined]
            problems.append(f"{rel(path)}: id {item.id} does not match the file name")  # type: ignore[attr-defined]
        if item.id in found:  # type: ignore[attr-defined]
            problems.append(f"{rel(path)}: duplicate id {item.id}")  # type: ignore[attr-defined]
        found[item.id] = (path, item, body)  # type: ignore[attr-defined]
    return found


# ── generated content ────────────────────────────────────────────────────────


def features_index(features: dict) -> str:
    rows = ["| ID | Title | Status | Priority | Sprint | Components |", "|---|---|---|---|---|---|"]
    ordered = sorted(
        features.values(), key=lambda f: (STATUS_ORDER.index(f[1].status), f[1].priority, f[1].id)
    )
    for path, feature, _ in ordered:
        rows.append(
            f"| [{feature.id}]({path.name}) | {feature.title} | {feature.status} | "
            f"{feature.priority} | {feature.sprint or '—'} | {', '.join(feature.components)} |"
        )
    return "\n".join(rows)


def sprint_features(sprint_id: str, features: dict) -> str:
    chosen = sorted(
        (f for f in features.values() if f[1].sprint == sprint_id),
        key=lambda f: (f[1].priority, f[1].id),
    )
    if not chosen:
        return "_No features are planned into this sprint yet._"
    return "\n".join(
        f"- [{'x' if feature.status == 'shipped' else ' '}] [{feature.id}](../features/{path.name}) "
        f"{feature.title} ({feature.priority}, {feature.status})"
        for path, feature, _ in chosen
    )


def sprints_index(sprints: dict) -> str:
    rows = ["| Sprint | Dates | Status | Goal |", "|---|---|---|---|"]
    for path, sprint, _ in sorted(sprints.values(), key=lambda s: s[1].id, reverse=True):
        rows.append(
            f"| [{sprint.id}]({path.name}) | {sprint.start} – {sprint.end} | "
            f"{sprint.status} | {sprint.goal} |"
        )
    return "\n".join(rows)


def decisions_index(decisions: dict) -> str:
    rows = ["| ID | Title | Status | Date |", "|---|---|---|---|"]
    for path, decision, _ in sorted(decisions.values(), key=lambda d: d[1].id):
        status = decision.status + (
            f" by {decision.superseded_by}" if decision.superseded_by else ""
        )
        if not decision.status_confirmed:
            status += " (unconfirmed)"
        rows.append(
            f"| [{decision.id}]({path.name}) | {decision.title} | {status} | {decision.date} |"
        )
    return "\n".join(rows)


def roadmap(features: dict) -> str:
    cards = [
        {
            "id": feature.id,
            "version": feature.roadmap.version,
            "title": feature.title,
            "state": ROADMAP_STATE[feature.status],
            "copy": feature.roadmap.summary,
        }
        for _, feature, _ in features.values()
        if feature.roadmap and feature.status != "dropped"
    ]
    cards.sort(key=lambda card: int(card["version"][1:]))
    return json.dumps(cards, indent=2, ensure_ascii=False) + "\n"


def replace_block(text: str, name: str, content: str) -> str:
    begin, end = f"<!-- BEGIN GENERATED: {name} -->", f"<!-- END GENERATED: {name} -->"
    if begin not in text or end not in text:
        raise ValueError(f"missing generated block '{name}'")
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    return f"{head}{begin}\n{content}\n{end}{tail}"


def generated(features: dict, sprints: dict, decisions: dict) -> dict[Path, str]:
    """Every generated file's expected content."""
    out: dict[Path, str] = {}
    readme = FEATURES / "README.md"
    if readme.exists():
        out[readme] = replace_block(
            readme.read_text(encoding="utf-8"), "features index", features_index(features)
        )
    readme = SPRINTS / "README.md"
    if readme.exists():
        out[readme] = replace_block(
            readme.read_text(encoding="utf-8"), "sprints index", sprints_index(sprints)
        )
    for sprint_id, (path, _, _) in sprints.items():
        out[path] = replace_block(
            path.read_text(encoding="utf-8"),
            "sprint features",
            sprint_features(sprint_id, features),
        )
    readme = DECISIONS / "README.md"
    if readme.exists():
        out[readme] = replace_block(
            readme.read_text(encoding="utf-8"), "decisions index", decisions_index(decisions)
        )
    out[ROADMAP_JSON] = roadmap(features)
    return out


# ── checks ───────────────────────────────────────────────────────────────────


def check_references(features: dict, sprints: dict, decisions: dict) -> list[str]:
    problems = []
    names = unit_names()
    for path, feature, _ in features.values():
        where = rel(path)
        for component in feature.components:
            if component not in names:
                problems.append(f"{where}: unknown component {component!r}")
        if feature.sprint and feature.sprint not in sprints:
            problems.append(f"{where}: sprint {feature.sprint} has no docs/sprints file")
        for decision in feature.decisions:
            if decision not in decisions:
                problems.append(f"{where}: unknown decision {decision}")
        for link in feature.links:
            if link not in features:
                problems.append(f"{where}: unknown feature {link}")
    for path, decision, _ in decisions.values():
        if decision.superseded_by and decision.superseded_by not in decisions:
            problems.append(f"{rel(path)}: superseded_by {decision.superseded_by} does not exist")
        if decision.status == "superseded" and not decision.superseded_by:
            problems.append(f"{rel(path)}: a superseded decision names superseded_by")
    active = [s.id for _, s, _ in sprints.values() if s.status == "active"]
    if len(active) > 1:
        problems.append(f"docs/sprints: more than one active sprint: {active}")
    return problems


def context_headings() -> list[str]:
    return [
        line
        for line in CONTEXT_TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line.startswith("## ")
    ]


def check_context() -> list[str]:
    problems = []
    required = context_headings()
    for member in members():
        where = rel(member) if member != REPO else "."
        context = member / "CONTEXT.md"
        if not context.exists():
            problems.append(f"{where}: missing CONTEXT.md (template: tools/templates/CONTEXT.md)")
            continue
        lines = context.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_CONTEXT_LINES:
            problems.append(f"{where}/CONTEXT.md: {len(lines)} lines (at most {MAX_CONTEXT_LINES})")
        headings = [line for line in lines if line.startswith("## ")]
        if headings != required:
            missing = [h for h in required if h not in headings]
            problems.append(
                f"{where}/CONTEXT.md: headings must be the template's, in order"
                + (f" (missing {missing})" if missing else "")
            )
        for pointer in ("CLAUDE.md", "AGENTS.md"):
            path = member / pointer
            if not path.exists() or "CONTEXT.md" not in path.read_text(encoding="utf-8"):
                problems.append(f"{where}: {pointer} must exist and point to CONTEXT.md")
    for unit in release_units():
        if not (unit / "CHANGELOG.md").exists():
            problems.append(f"{rel(unit)}: missing CHANGELOG.md")
    return problems


def slug(heading: str) -> str:
    text = re.sub(r"[`*_~]|<[^>]+>", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    found: set[str] = set()
    counts: dict[str, int] = {}
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        match = re.match(r"^#{1,6}\s+(.*?)\s*#*\s*$", line)
        if match:
            base = slug(match.group(1))
            n = counts.get(base, 0)
            counts[base] = n + 1
            found.add(base if n == 0 else f"{base}-{n}")
        found |= set(re.findall(r'<a\s+(?:name|id)="([^"]+)"', line))
    return found


def broken_links(files: list[Path]) -> set[str]:
    broken: set[str] = set()
    cache: dict[Path, set[str]] = {}
    for path in files:
        if path.name.startswith("_template") or rel(path).startswith("tools/templates/"):
            continue
        in_code = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                continue
            for target in LINK.findall(re.sub(r"`[^`]*`", "", line)):
                if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("//"):
                    continue  # http:, mailto:, ...
                file_part, _, anchor = target.partition("#")
                destination = (path.parent / file_part).resolve() if file_part else path
                ok = destination.exists()
                if ok and anchor and destination.suffix == ".md":
                    if destination not in cache:
                        cache[destination] = anchors(destination)
                    ok = anchor.lower() in cache[destination]
                if not ok:
                    broken.add(f"{rel(path)} -> {target}")
    return broken


def read_baseline() -> set[str]:
    if not LINK_BASELINE.exists():
        return set()
    return {
        line
        for line in LINK_BASELINE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }


def write_baseline(links: set[str]) -> None:
    LINK_BASELINE.write_text(
        "# Broken relative links known when tools/check_docs.py was introduced (D6).\n"
        "# Shrink only: fix links, then run `tools/check_docs.py --update-link-baseline`.\n"
        + "".join(f"{link}\n" for link in sorted(links)),
        encoding="utf-8",
        newline="\n",
    )


def retired_term_warnings(files: list[Path]) -> list[str]:
    warnings = []
    for path in files:
        name = rel(path)
        if not (name in CURRENT_STATE_DOCS or path.name == "CONTEXT.md"):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = RETIRED_TERMS.search(line)
            if match and "retired" not in line.lower() and "removed" not in line.lower():
                warnings.append(f"{name}:{number}: mentions {match.group(1)}")
    return warnings


def run(write: bool = False, update_baseline: bool = False) -> tuple[list[str], list[str]]:
    problems: list[str] = []
    features = load(FEATURES, FEATURE_FILE, Feature, problems)
    decisions = load(DECISIONS, DECISION_FILE, Decision, problems)
    sprints = load(SPRINTS, SPRINT_FILE, Sprint, problems)
    problems += check_references(features, sprints, decisions)
    try:
        expected = generated(features, sprints, decisions)
    except ValueError as exc:
        problems.append(str(exc))
        expected = {}
    for path, content in expected.items():
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current != content:
            if write:
                path.write_text(content, encoding="utf-8", newline="\n")
            else:
                problems.append(f"{rel(path)} is stale; run `tools/check_docs.py --write`")
    problems += check_context()
    files = markdown_files()
    broken = broken_links(files)
    baseline = read_baseline()
    if update_baseline:
        if broken - baseline and LINK_BASELINE.exists():
            problems.append("--update-link-baseline only shrinks the list; fix the new links first")
        else:
            write_baseline(broken)
            baseline = broken
    problems += [f"broken link: {link}" for link in sorted(broken - baseline)]
    fixed = baseline - broken
    if fixed:
        problems.append(
            f"{len(fixed)} baselined links are fixed or gone; run "
            "`tools/check_docs.py --update-link-baseline`"
        )
    return problems, retired_term_warnings(files)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--update-link-baseline", action="store_true")
    args = parser.parse_args(argv)
    problems, warnings = run(write=args.write, update_baseline=args.update_link_baseline)
    for warning in warnings:
        print(f"warning: {warning}")
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print("docs are consistent")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
