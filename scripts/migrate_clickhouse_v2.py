"""Forward-only, resumable ClickHouse v2 migration runner.

This script is intentionally independent of production bootstrap.  Run ``plan``
without a ClickHouse connection; all mutating commands require ``--yes``.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import socket
import sys
import time
import uuid
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_DIR = PROJECT_ROOT / "sql" / "clickhouse" / "v2"
MIN_CLICKHOUSE_VERSION = (25, 3)
LOCK_TABLE = "default._factorlab_v2_migration_lock"
PHASES = ("schema", "backfill", "rbac")
FILE_RE = re.compile(r"^wave_(?P<wave>\d{2})_(?P<phase>schema|views|backfill|rbac|validate)\.sql$")
SAFE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class MigrationError(RuntimeError):
    """Expected migration refusal or validation failure."""


@dataclass(frozen=True)
class Migration:
    migration_id: str
    wave: int
    phase: str
    path: Path
    sql: str
    checksum: str


@dataclass(frozen=True)
class JournalEntry:
    migration_id: str
    checksum: str
    status: str
    error: str = ""


def split_sql(script: str) -> list[str]:
    """Split SQL safely across quoted semicolons and SQL comments."""
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
                statements.append(statement)
            current = []
        else:
            current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def checksum_sql(sql: str) -> str:
    normalized = sql.replace("\r\n", "\n").encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def discover_migrations(directory: Path = MIGRATION_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in directory.glob("wave_*.sql"):
        match = FILE_RE.match(path.name)
        if not match or match.group("phase") == "validate":
            continue
        file_phase = match.group("phase")
        phase = "schema" if file_phase == "views" else file_phase
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(path.stem, int(match.group("wave")), phase, path, sql, checksum_sql(sql))
        )
    phase_order = {"schema": 0, "backfill": 1, "rbac": 2}
    return sorted(migrations, key=lambda item: (phase_order[item.phase], item.wave, item.path.name))


def selected_migrations(
    migrations: Iterable[Migration], phase: str, through_wave: int
) -> list[Migration]:
    return [item for item in migrations if item.phase == phase and item.wave <= through_wave]


def render_sql(sql: str, source_database: str, run_id: uuid.UUID) -> str:
    if not SAFE_IDENTIFIER_RE.fullmatch(source_database):
        raise MigrationError(f"unsafe source database identifier: {source_database!r}")
    return sql.replace("{{source_database}}", source_database).replace(
        "{{migration_run_id}}", str(run_id)
    )


def _first_value(result: Any) -> Any:
    rows = getattr(result, "result_rows", result)
    if not rows:
        return None
    row = rows[0]
    return row[0] if isinstance(row, (tuple, list)) else row


def _sql_string(value: object) -> str:
    return "'" + str(value).replace("\\", "\\\\").replace("'", "''") + "'"


def _clickhouse_datetime(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def create_client() -> Any:
    try:
        import clickhouse_connect
    except ImportError as exc:  # pragma: no cover - installation failure path
        raise MigrationError(
            "clickhouse-connect is required; install the project dependencies"
        ) from exc
    secure = os.getenv("CLICKHOUSE_SECURE", "false").lower() in {"1", "true", "yes"}
    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST", "localhost"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8443" if secure else "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default"),
        password=os.getenv("CLICKHOUSE_PASSWORD", ""),
        secure=secure,
        connect_timeout=int(os.getenv("CLICKHOUSE_CONNECT_TIMEOUT", "10")),
    )


def check_server_preflight(client: Any, require_raw_archive: bool) -> None:
    version_text = str(_first_value(client.query("SELECT version()")))
    version_match = re.match(r"(\d+)\.(\d+)", version_text)
    if not version_match or tuple(map(int, version_match.groups())) < MIN_CLICKHOUSE_VERSION:
        raise MigrationError(
            f"ClickHouse 25.3+ is required; connected server reports {version_text!r}"
        )
    if require_raw_archive:
        count = int(
            _first_value(
                client.query(
                    "SELECT count() FROM system.storage_policies WHERE policy_name = 'raw_archive'"
                )
            )
            or 0
        )
        if count == 0:
            raise MigrationError("required ClickHouse storage policy 'raw_archive' is unavailable")


def journal_exists(client: Any) -> bool:
    return bool(
        int(
            _first_value(
                client.query(
                    "SELECT count() FROM system.tables "
                    "WHERE database = 'meta' AND name = 'schema_migrations'"
                )
            )
            or 0
        )
    )


def load_journal(client: Any) -> dict[str, JournalEntry]:
    if not journal_exists(client):
        return {}
    result = client.query(
        "SELECT migration_id, argMax(checksum, version), argMax(status, version), "
        "argMax(error, version) FROM meta.schema_migrations "
        "GROUP BY migration_id"
    )

    def text(value: Any) -> str:
        return value.decode("utf-8").rstrip("\x00") if isinstance(value, bytes) else str(value)

    return {
        text(row[0]): JournalEntry(text(row[0]), text(row[1]), text(row[2]), text(row[3]))
        for row in result.result_rows
    }


def ensure_no_checksum_drift(
    migrations: Sequence[Migration], journal: dict[str, JournalEntry]
) -> None:
    drifted = [
        item.migration_id
        for item in migrations
        if item.migration_id in journal and journal[item.migration_id].checksum != item.checksum
    ]
    if drifted:
        raise MigrationError("checksum drift detected for: " + ", ".join(drifted))


def write_journal(
    client: Any,
    migration: Migration,
    run_id: uuid.UUID,
    status: str,
    error: str = "",
    started_at: datetime | None = None,
) -> None:
    started = started_at or datetime.now(UTC)
    finished = "NULL" if status == "running" else "now64(3, 'UTC')"
    client.command(
        "INSERT INTO meta.schema_migrations VALUES ("
        f"{_sql_string(migration.migration_id)}, {_sql_string(migration.phase)}, "
        f"{migration.wave}, {_sql_string(migration.checksum)}, {_sql_string(status)}, "
        f"toUUID({_sql_string(run_id)}), "
        f"toDateTime64({_sql_string(_clickhouse_datetime(started))}, 3, 'UTC'), {finished}, "
        f"{_sql_string(error[:16000])}, {time.time_ns()})"
    )


def write_run(
    client: Any,
    run_id: uuid.UUID,
    phase: str,
    through_wave: int,
    source_database: str,
    status: str,
    error: str = "",
) -> None:
    finished = "NULL" if status == "running" else "now64(3, 'UTC')"
    client.command(
        "INSERT INTO meta.migration_runs VALUES ("
        f"toUUID({_sql_string(run_id)}), 'apply', {_sql_string(phase)}, {through_wave}, "
        f"{_sql_string(source_database)}, {_sql_string(status)}, now64(3, 'UTC'), "
        f"{finished}, {_sql_string(error[:16000])}, {_sql_string(socket.gethostname())}, "
        f"{time.time_ns()})"
    )


def _is_table_exists_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "already exists" in message or "table_already_exists" in message or "code: 57" in message


@contextlib.contextmanager
def migration_lock(client: Any, timeout_seconds: int = 3600) -> Iterator[uuid.UUID]:
    owner = uuid.uuid4()
    create = (
        f"CREATE TABLE {LOCK_TABLE} (owner UUID, acquired_at DateTime64(3, 'UTC')) ENGINE = Memory"
    )
    try:
        client.command(create)
    except Exception as exc:
        if not _is_table_exists_error(exc):
            raise
        row = client.query(f"SELECT owner, acquired_at FROM {LOCK_TABLE} LIMIT 1").result_rows
        if not row:
            raise MigrationError(
                "migration lock exists without an owner; inspect it manually"
            ) from exc
        acquired = row[0][1]
        if acquired.tzinfo is None:
            acquired = acquired.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - acquired).total_seconds()
        if age <= timeout_seconds:
            raise MigrationError(
                f"another migration run holds the lock (owner={row[0][0]}, age={age:.0f}s)"
            ) from exc
        client.command(f"DROP TABLE {LOCK_TABLE}")
        client.command(create)
    client.command(
        f"INSERT INTO {LOCK_TABLE} VALUES (toUUID({_sql_string(owner)}), now64(3, 'UTC'))"
    )
    try:
        yield owner
    finally:
        client.command(f"DROP TABLE IF EXISTS {LOCK_TABLE}")


def execute_migration(
    client: Any, migration: Migration, source_database: str, run_id: uuid.UUID
) -> None:
    started = datetime.now(UTC)
    # Wave 0 creates the journal itself, so its running row can only be written afterward.
    can_journal_first = journal_exists(client)
    if can_journal_first:
        write_journal(client, migration, run_id, "running", started_at=started)
    try:
        for statement in split_sql(render_sql(migration.sql, source_database, run_id)):
            client.command(statement)
    except Exception as exc:
        if journal_exists(client):
            write_journal(client, migration, run_id, "failed", str(exc), started)
        raise
    write_journal(client, migration, run_id, "succeeded", started_at=started)


def validation_files(directory: Path, through_wave: int) -> list[Path]:
    files: list[tuple[int, Path]] = []
    for path in directory.glob("wave_*_validate.sql"):
        match = FILE_RE.match(path.name)
        if match and int(match.group("wave")) <= through_wave:
            files.append((int(match.group("wave")), path))
    return [path for _, path in sorted(files)]


def run_validation_file(client: Any, path: Path, source_database: str) -> list[str]:
    failures: list[str] = []
    run_id = uuid.UUID(int=0)
    for position, statement in enumerate(
        split_sql(render_sql(path.read_text(encoding="utf-8"), source_database, run_id)), 1
    ):
        label_match = re.search(r"--\s*check:\s*([^\n]+)", statement, re.IGNORECASE)
        label = label_match.group(1).strip() if label_match else f"statement {position}"
        value = _first_value(client.query(statement))
        if int(value or 0) != 0:
            failures.append(f"{path.name}: {label} ({value} violations)")
    return failures


def run_validations(client: Any, through_wave: int, source_database: str) -> None:
    failures: list[str] = []
    for path in validation_files(MIGRATION_DIR, through_wave):
        failures.extend(run_validation_file(client, path, source_database))
    if failures:
        raise MigrationError("validation failed:\n  - " + "\n  - ".join(failures))


def print_plan(migrations: Sequence[Migration], phase: str | None, through_wave: int) -> None:
    selected = [
        item
        for item in migrations
        if item.wave <= through_wave and (phase is None or item.phase == phase)
    ]
    print("FactorLab ClickHouse v2 migration plan")
    for item in selected:
        print(
            f"  wave {item.wave}  {item.phase:<8} {item.migration_id:<28} "
            f"{len(split_sql(item.sql)):>2} statements  sha256:{item.checksum[:12]}"
        )
    print(f"{len(selected)} migration files selected; legacy tables are never modified.")


def command_status(client: Any, migrations: Sequence[Migration]) -> None:
    journal = load_journal(client)
    ensure_no_checksum_drift(migrations, journal)
    for item in migrations:
        entry = journal.get(item.migration_id)
        state = entry.status if entry else "pending"
        suffix = f" - {entry.error}" if entry and entry.error else ""
        print(f"wave {item.wave} {item.phase:<8} {item.migration_id:<28} {state}{suffix}")


def command_apply(client: Any, args: argparse.Namespace, migrations: Sequence[Migration]) -> None:
    chosen = selected_migrations(migrations, args.phase, args.through_wave)
    if not chosen:
        raise MigrationError("no migration files match the requested phase and wave")
    if args.dry_run:
        print_plan(chosen, args.phase, args.through_wave)
        return
    if not args.yes:
        raise MigrationError("apply mutates ClickHouse; rerun with --yes")
    check_server_preflight(
        client,
        require_raw_archive=args.phase == "schema" and args.through_wave >= 1,
    )
    if args.phase == "backfill":
        run_validations(client, args.through_wave, args.source_database)
    with migration_lock(client, args.lock_timeout):
        journal = load_journal(client)
        ensure_no_checksum_drift(chosen, journal)
        run_id = uuid.uuid4()
        run_written = journal_exists(client)
        if run_written:
            write_run(
                client, run_id, args.phase, args.through_wave, args.source_database, "running"
            )
        try:
            for migration in chosen:
                entry = journal.get(migration.migration_id)
                if entry and entry.status == "succeeded" and args.phase != "backfill":
                    print(f"skip {migration.migration_id} (already succeeded)")
                    continue
                print(f"apply {migration.migration_id}")
                execute_migration(client, migration, args.source_database, run_id)
                if migration.wave == 0 and not run_written:
                    write_run(
                        client,
                        run_id,
                        args.phase,
                        args.through_wave,
                        args.source_database,
                        "running",
                    )
                    run_written = True
            if run_written:
                write_run(
                    client,
                    run_id,
                    args.phase,
                    args.through_wave,
                    args.source_database,
                    "succeeded",
                )
        except Exception as exc:
            if run_written:
                write_run(
                    client,
                    run_id,
                    args.phase,
                    args.through_wave,
                    args.source_database,
                    "failed",
                    str(exc),
                )
            raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-database",
        default=os.getenv("FACTORLAB_LEGACY_DATABASE", "factorlab"),
        help="legacy ClickHouse database (default: factorlab)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="show ordered migrations without connecting")
    plan.add_argument("--phase", choices=PHASES)
    plan.add_argument("--through-wave", type=int, choices=range(9), default=8)

    subparsers.add_parser("status", help="show journal status and checksum drift")

    apply = subparsers.add_parser("apply", help="apply one phase")
    apply.add_argument("--phase", required=True, choices=PHASES)
    apply.add_argument("--through-wave", required=True, type=int, choices=range(9))
    apply.add_argument("--yes", action="store_true", help="confirm ClickHouse mutations")
    apply.add_argument("--dry-run", action="store_true", help="print selection without connecting")
    apply.add_argument("--lock-timeout", type=int, default=3600)

    validate = subparsers.add_parser("validate", help="run preflight and data-quality gates")
    validate.add_argument("--through-wave", required=True, type=int, choices=range(9))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    migrations = discover_migrations()
    try:
        if args.command == "plan":
            print_plan(migrations, args.phase, args.through_wave)
            return 0
        if args.command == "apply" and args.dry_run:
            print_plan(
                selected_migrations(migrations, args.phase, args.through_wave),
                args.phase,
                args.through_wave,
            )
            return 0
        client = create_client()
        if args.command == "status":
            command_status(client, migrations)
        elif args.command == "apply":
            command_apply(client, args, migrations)
        elif args.command == "validate":
            check_server_preflight(client, require_raw_archive=args.through_wave >= 1)
            ensure_no_checksum_drift(
                [item for item in migrations if item.wave <= args.through_wave],
                load_journal(client),
            )
            run_validations(client, args.through_wave, args.source_database)
            print(f"validation passed through wave {args.through_wave}")
        return 0
    except MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        if "client" in locals():
            close = getattr(client, "close", None)
            if close:
                close()


if __name__ == "__main__":
    raise SystemExit(main())
