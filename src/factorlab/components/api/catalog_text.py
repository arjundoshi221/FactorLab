"""Plain-language titles and descriptions for v2 tables, columns, and namespaces.

Generated text (catalog_descriptions.json) comes from the schema design doc and DDL
comments; hand-written text (catalog_curated.json) overrides it. Shared by the data
catalog and the schema explorer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def _descriptions() -> tuple[dict[str, Any], dict[str, Any]]:
    directory = Path(__file__).resolve().parent
    generated = json.loads((directory / "catalog_descriptions.json").read_text(encoding="utf-8"))
    curated = json.loads((directory / "catalog_curated.json").read_text(encoding="utf-8"))
    return generated.get("tables", {}), curated


def _humanize(name: str) -> str:
    text = name.split(".", 1)[-1].replace("_", " ").strip()
    return text[:1].upper() + text[1:]


@dataclass(frozen=True)
class TableText:
    title: str
    summary: str
    design_notes: str | None
    notes: list[str]
    columns: dict[str, str]


def table_text(name: str) -> TableText:
    generated, curated = _descriptions()
    design = generated.get(name, {})
    manual = curated.get("tables", {}).get(name, {})
    columns = {**design.get("columns", {}), **manual.get("columns", {})}
    design_notes = design.get("description")
    summary = manual.get("summary") or design_notes or "No description has been written for this table yet."
    return TableText(
        title=manual.get("title") or _humanize(name),
        summary=summary,
        design_notes=design_notes if design_notes and design_notes != summary else None,
        notes=list(manual.get("notes", [])),
        columns=columns,
    )


def namespace_text(namespace: str) -> tuple[str, str]:
    """Return ``(title, summary)`` for a v2 database."""

    _, curated = _descriptions()
    entry = curated.get("namespaces", {}).get(namespace, {})
    return entry.get("title") or namespace, entry.get("summary", "")
