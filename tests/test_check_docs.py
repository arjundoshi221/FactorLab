"""tools/check_docs.py and tools/env_example.py: the docs and .env.example stay true."""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO = Path(__file__).resolve().parents[1]


def _load(name: str):
    key = f"{name}_tool"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, REPO / "tools" / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


docs = _load("check_docs")
env_example = _load("env_example")


def feature(**changes):
    data = {
        "id": "F-900",
        "title": "T",
        "status": "planned",
        "priority": "P1",
        "components": ["api"],
        "owner": "unassigned",
        "sprint": "2026-S20",
        "decisions": ["ADR-0016"],
        "links": [],
        "created": dt.date(2026, 9, 30),
        "updated": dt.date(2026, 9, 30),
    } | changes
    return docs.Feature.model_validate(data)


def test_the_repository_docs_are_consistent():
    problems, _ = docs.run()
    assert problems == []


def test_env_example_is_current():
    assert env_example.main(["--check"]) == 0


def test_every_member_has_a_context_file_with_the_template_headings():
    assert docs.check_context() == []


@pytest.mark.parametrize(
    "change", [{"status": "done"}, {"priority": "P9"}, {"id": "F-1"}, {"surprise": True}]
)
def test_feature_frontmatter_is_validated(change):
    with pytest.raises(ValidationError):
        feature(**change)


def test_references_must_exist():
    item = feature(
        components=["no-such-unit"], sprint="2099-S01", decisions=["ADR-9999"], links=["F-999"]
    )
    problems = docs.check_references({"F-900": (docs.FEATURES / "F-900-x.md", item, "")}, {}, {})
    joined = "\n".join(problems)
    for expected in (
        "unknown component",
        "has no docs/sprints file",
        "unknown decision",
        "unknown feature",
    ):
        assert expected in joined


def test_roadmap_cards_follow_feature_status():
    path = Path("docs/features/F-900-x.md")
    items = {
        "F-900": (
            path,
            feature(status="in-progress", roadmap={"version": "V2", "summary": "b"}),
            "",
        ),
        "F-901": (
            path,
            feature(id="F-901", status="backlog", roadmap={"version": "V10", "summary": "c"}),
            "",
        ),
        "F-902": (
            path,
            feature(id="F-902", status="dropped", roadmap={"version": "V1", "summary": "a"}),
            "",
        ),
    }
    cards = json.loads(docs.roadmap(items))
    assert [(c["version"], c["state"]) for c in cards] == [("V2", "Now"), ("V10", "Planned")]


def test_generated_blocks_need_their_markers():
    text = "a\n<!-- BEGIN GENERATED: x -->\nold\n<!-- END GENERATED: x -->\nb"
    assert docs.replace_block(text, "x", "new") == (
        "a\n<!-- BEGIN GENERATED: x -->\nnew\n<!-- END GENERATED: x -->\nb"
    )
    with pytest.raises(ValueError):
        docs.replace_block("no markers", "x", "new")


def test_links_and_anchors_are_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(docs, "REPO", tmp_path)
    (tmp_path / "a.md").write_text(
        "# Title\n\n## Next steps\n\n[ok](#next-steps) [bad](#nope)\n"
        "[file](b.md#section-two) [gone](missing.md)\n"
        "`[code](ignored.md)`\n```\n[fenced](ignored.md)\n```\n",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text("# B\n\n## Section two\n", encoding="utf-8")
    broken = docs.broken_links([tmp_path / "a.md", tmp_path / "b.md"])
    assert broken == {"a.md -> #nope", "a.md -> missing.md"}
