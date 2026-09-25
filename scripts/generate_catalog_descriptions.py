"""Generate the data catalog's table and column descriptions.

Table descriptions come from the ``### `db.table``` sections of
docs/architecture/06-schema-rehau.md; column descriptions come from the inline
``--`` comments in sql/clickhouse/v2/wave_*_schema*.sql. The result is written to
src/factorlab/api/catalog_descriptions.json, which ships in the image (docs/
does not). Hand-written text belongs in catalog_curated.json, which overrides this.

    python scripts/generate_catalog_descriptions.py          # rewrite the JSON
    python scripts/generate_catalog_descriptions.py --check  # fail if it is stale
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN_DOC = ROOT / "docs" / "architecture" / "06-schema-rehau.md"
SQL_DIRECTORY = ROOT / "sql" / "clickhouse" / "v2"
OUTPUT = ROOT / "src" / "factorlab" / "api" / "catalog_descriptions.json"
MAX_DESCRIPTION = 600

TABLE_REFERENCE = re.compile(r"`([a-z_]+\.[a-z0-9_]+)`")
CREATE_TABLE = re.compile(r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+\.[a-z0-9_]+)", re.IGNORECASE)
COLUMN_LINE = re.compile(r"^\s+([a-z_][a-z0-9_]*)\s+([A-Z][^-]*?)(?:,)?\s*(?:--\s?(.*))?$")
CONTINUATION = re.compile(r"^\s+--\s{2,}(.*)$")
REVISION_NOISE = re.compile(r"\s*\((?=[^()]*(?:\brev\s*\d|\bFix\b|\bF\d+\b|§))[^()]*\)", re.IGNORECASE)
INLINE_REVISION = re.compile(
    r"(?i)(?:\b(?:added|renamed|removed|changed|moved)\s+)?(?:in\s+)?\brev\s*\d+(?:\s+per\s+F\d+)?\s*(?:—|;|:)?\s*"
    r"|\bper\s+F\d+\b\s*(?:—)?\s*|\(Fix\s+F\d+[^)]*\)"
)
NOT_COLUMNS = {"index", "projection", "constraint", "primary", "order", "partition", "engine", "settings", "ttl"}


def clean(text: str) -> str:
    text = REVISION_NOISE.sub("", text)
    text = INLINE_REVISION.sub("", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^(?:Why this exists|Purpose)\s*:\s*", "", text, flags=re.IGNORECASE)
    text = text.replace("★", "")
    return re.sub(r"\s+", " ", text).strip(" ;,")


def shorten(text: str, limit: int = MAX_DESCRIPTION) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    sentence = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[: sentence + 1] if sentence > limit // 3 else cut.rstrip() + "…").strip()


def table_descriptions(markdown: str) -> dict[str, str]:
    """Return the first prose paragraph under each table heading."""

    descriptions: dict[str, str] = {}
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.startswith("### "):
            continue
        names = TABLE_REFERENCE.findall(line)
        if not names:
            continue
        # The introduction before the DDL block; later paragraphs are design notes, not user copy.
        paragraph: list[str] = []
        while index < len(lines):
            current = lines[index]
            if current.startswith("#") or current.lstrip().startswith("```"):
                break
            index += 1
            stripped = current.strip()
            if stripped.startswith(("|", "- ", "* ", "> ", "---")) or re.match(r"\d+\.\s", stripped):
                if paragraph:
                    break
                continue
            if stripped:
                paragraph.append(stripped)
            elif paragraph:
                break
        text = shorten(clean(" ".join(paragraph)))
        if text:
            for name in names:
                descriptions.setdefault(name, text)
    return descriptions


def column_descriptions(sql: str) -> dict[str, dict[str, str]]:
    """Return ``{table: {column: comment}}`` from inline DDL comments."""

    tables: dict[str, dict[str, str]] = {}
    table: str | None = None
    column: str | None = None
    for line in sql.splitlines():
        created = CREATE_TABLE.match(line)
        if created:
            table, column = created.group(1).lower(), None
            tables.setdefault(table, {})
            continue
        if table is None:
            continue
        if line.startswith(")") or line.strip().startswith(")"):
            table, column = None, None
            continue
        continuation = CONTINUATION.match(line)
        if continuation and column is not None:
            tables[table][column] = f"{tables[table].get(column, '')} {continuation.group(1)}".strip()
            continue
        match = COLUMN_LINE.match(line)
        if match and match.group(1) not in NOT_COLUMNS:
            column = match.group(1)
            if match.group(3):
                tables[table][column] = match.group(3).strip()
            continue
        column = None
    return {
        name: {key: text for key, value in columns.items() if (text := clean(value))}
        for name, columns in tables.items()
    }


def build() -> dict:
    tables: dict[str, dict] = {}
    for name, text in table_descriptions(DESIGN_DOC.read_text(encoding="utf-8")).items():
        tables.setdefault(name, {})["description"] = text
    for path in sorted(SQL_DIRECTORY.glob("wave_*_schema*.sql")):
        for name, columns in column_descriptions(path.read_text(encoding="utf-8")).items():
            if columns:
                tables.setdefault(name, {}).setdefault("columns", {}).update(columns)
    return {
        "generated_by": "scripts/generate_catalog_descriptions.py (do not edit; override in catalog_curated.json)",
        "tables": {
            name: {key: tables[name][key] for key in sorted(tables[name])}
            for name in sorted(tables)
        },
    }


def render() -> str:
    return json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when the checked-in JSON is stale")
    arguments = parser.parse_args()
    content = render()
    if arguments.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != content:
            print(f"{OUTPUT.relative_to(ROOT)} is stale; run {Path(__file__).name}", file=sys.stderr)
            return 1
        return 0
    OUTPUT.write_text(content, encoding="utf-8", newline="\n")
    print(f"Wrote {OUTPUT.relative_to(ROOT)} ({len(content):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
