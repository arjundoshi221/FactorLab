"""Generate the checked-in v2 schema waves from the authoritative design document.

The design deliberately uses a few compact column bundles.  This generator expands
those bundles to executable ClickHouse DDL and excludes the explicitly deferred
materialized-view sketches.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "docs" / "architecture" / "06-schema-rehau.md"
OUTPUT = ROOT / "sql" / "clickhouse" / "v2"

WAVES = {
    1: {"ref", "raw"},
    2: {"market"},
    3: {"meta"},
    4: {"alt"},
    5: {"fundamentals"},
    6: {"derived"},
    7: {"broker"},
    8: {"book", "risk"},
}

DEFERRED_TABLES = {"fundamentals.snapshots_pit_eom"}

NULLABLE_SORT_KEY_TABLES = {
    "ref.contracts",
    "raw.archive",
    "alt.political_trades",
    "broker.positions_snapshot",
    "broker.cash_flows",
}

BUNDLE_TYPES = {
    "resolution": "LowCardinality(String)",
    "session": "LowCardinality(String)",
    "bar_time": "DateTime64(3, 'UTC')",
    "trade_date": "Date",
    "source": "LowCardinality(String)",
    "source_channel": "LowCardinality(String)",
    "raw_id": "Nullable(UUID)",
    "ingest_run_id": "UUID",
    "as_of_time": "DateTime64(3, 'UTC')",
    "ingested_at": "DateTime64(3, 'UTC')",
    "latency_ms": "Nullable(Int32)",
    "version": "UInt64",
}


def sql_blocks(text: str) -> list[str]:
    return re.findall(r"```sql\s*\n(.*?)```", text, flags=re.DOTALL)


def split_sql(script: str) -> list[str]:
    """Split SQL on semicolons while respecting strings and comments."""
    statements: list[str] = []
    current: list[str] = []
    quote: str | None = None
    line_comment = False
    block_comment = False
    index = 0
    while index < len(script):
        char = script[index]
        nxt = script[index + 1] if index + 1 < len(script) else ""
        if line_comment:
            current.append(char)
            if char == "\n":
                line_comment = False
        elif block_comment:
            current.append(char)
            if char == "*" and nxt == "/":
                current.append(nxt)
                index += 1
                block_comment = False
        elif quote:
            current.append(char)
            if char == quote:
                if nxt == quote:
                    current.append(nxt)
                    index += 1
                else:
                    quote = None
            elif char == "\\" and nxt:
                current.append(nxt)
                index += 1
        elif char == "-" and nxt == "-":
            current.extend((char, nxt))
            index += 1
            line_comment = True
        elif char == "/" and nxt == "*":
            current.extend((char, nxt))
            index += 1
            block_comment = True
        elif char in {"'", '"', "`"}:
            current.append(char)
            quote = char
        elif char == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement + ";")
            current = []
        else:
            current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail + ";")
    return statements


def expand_grouped_columns(sql: str) -> str:
    grouped = re.compile(
        r"^(?P<indent>\s+)(?P<names>[a-z_]+(?:\s*,\s*[a-z_]+)+)"
        r"\s+(?P<type>(?:Nullable\([^\n]+\)|Decimal\([^\n]+\)|Float\w*|UInt\w*|Int\w*))"
        r"(?P<comma>,?)(?P<comment>\s*(?:--.*)?)$",
        flags=re.MULTILINE,
    )

    def replace_group(match: re.Match[str]) -> str:
        names = [name.strip() for name in match.group("names").split(",")]
        lines = []
        for position, name in enumerate(names):
            comma = "," if position < len(names) - 1 or match.group("comma") else ""
            comment = match.group("comment") if position == len(names) - 1 else ""
            lines.append(f"{match.group('indent')}{name:<24} {match.group('type')}{comma}{comment}")
        return "\n".join(lines)

    sql = grouped.sub(replace_group, sql)

    bundle = re.compile(
        r"^(?P<indent>\s+)(?P<names>[a-z_]+(?:\s*,\s*[a-z_]+)+)(?P<comma>,?)"
        r"(?P<comment>\s*(?:--.*)?)$",
        flags=re.MULTILINE,
    )

    def replace_bundle(match: re.Match[str]) -> str:
        names = [name.strip() for name in match.group("names").split(",")]
        if not all(name in BUNDLE_TYPES for name in names):
            return match.group(0)
        lines = []
        for position, name in enumerate(names):
            comma = "," if position < len(names) - 1 or match.group("comma") else ""
            comment = match.group("comment") if position == len(names) - 1 else ""
            lines.append(f"{match.group('indent')}{name:<24} {BUNDLE_TYPES[name]}{comma}{comment}")
        return "\n".join(lines)

    return bundle.sub(replace_bundle, sql)


def normalize(statement: str) -> str:
    statement = expand_grouped_columns(statement)
    # ClickHouse rejects Nullable(LowCardinality(T)); wrapper order is significant.
    statement = re.sub(
        r"Nullable\(LowCardinality\(([^)]+)\)\)",
        r"LowCardinality(Nullable(\1))",
        statement,
    )
    statement = re.sub(
        r"\bCREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS)",
        "CREATE TABLE IF NOT EXISTS ",
        statement,
        count=1,
        flags=re.IGNORECASE,
    )
    table_match = re.search(
        r"\bCREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([\w.]+)", statement, re.IGNORECASE
    )
    if table_match and table_match.group(1).lower() in NULLABLE_SORT_KEY_TABLES:
        if re.search(r"\bSETTINGS\b", statement, re.IGNORECASE):
            statement = re.sub(r";\s*$", ", allow_nullable_key = 1;", statement, flags=re.DOTALL)
        else:
            statement = re.sub(
                r";\s*$", "\nSETTINGS allow_nullable_key = 1;", statement, flags=re.DOTALL
            )
    return statement.strip()


def collect_tables() -> dict[int, list[tuple[str, str]]]:
    waves: dict[int, list[tuple[str, str]]] = {wave: [] for wave in WAVES}
    text = DESIGN.read_text(encoding="utf-8")
    for block in sql_blocks(text):
        for statement in split_sql(block):
            match = re.search(r"\bCREATE\s+TABLE\s+([a-z_]+\.[a-z_]+)", statement, re.IGNORECASE)
            if not match:
                continue
            name = match.group(1).lower()
            if name in DEFERRED_TABLES:
                continue
            namespace = name.split(".", 1)[0]
            for wave, namespaces in WAVES.items():
                if namespace in namespaces:
                    waves[wave].append((name, normalize(statement)))
                    break
    return waves


def render_wave(wave: int, statements: list[tuple[str, str]]) -> str:
    heading = (
        "-- Generated from docs/architecture/06-schema-rehau.md.\n"
        "-- Do not hand-edit: update the design or generator, then regenerate.\n"
        f"-- Wave {wave} schema.\n\n"
    )
    return heading + "\n\n".join(sql for _, sql in statements) + "\n"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    waves = collect_tables()
    for wave, statements in waves.items():
        target = OUTPUT / f"wave_{wave:02d}_schema.sql"
        target.write_text(render_wave(wave, statements), encoding="utf-8", newline="\n")
        print(f"{target.relative_to(ROOT)}: {len(statements)} tables")


if __name__ == "__main__":
    main()
