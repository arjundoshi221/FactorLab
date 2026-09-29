"""Frozen checksums of the ClickHouse v2 migrations (forward-only schema changes).

    uv run python tools/schema_checksums.py check
    uv run python tools/schema_checksums.py add              record new migrations as pending
    uv run python tools/schema_checksums.py update <id>      refreeze a pending migration you edited
    uv run python tools/schema_checksums.py applied <id>     mark a migration applied in production

The lock (``checksums.lock`` next to the SQL) holds one line per migration:
``<sha256>  <applied|pending>  <migration id>``. The checksum is the one the migration
journal stores (factorlab.schema.migrate.checksum_sql). An applied migration can never
change or disappear: fix forward with a new wave. A pending one (built, not yet run in
production) may still change, but only through ``update``, so every edit is deliberate
and reviewed. ``tests/test_schema_checksums.py`` runs ``check`` in CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

from factorlab.schema.migrate import MIGRATION_DIR, discover_migrations

LOCK = MIGRATION_DIR / "checksums.lock"
HEADER = """\
# Frozen ClickHouse v2 migration checksums; managed by tools/schema_checksums.py.
# <sha256>  <applied|pending>  <migration id>
# Applied migrations are forward-only: never edit or delete one; add a new wave instead.
"""
STATUSES = ("applied", "pending")


class LockError(Exception):
    pass


def read_lock(path: Path = LOCK) -> dict[str, tuple[str, str]]:
    """migration id -> (checksum, status)."""
    entries: dict[str, tuple[str, str]] = {}
    if not path.exists():
        return entries
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3 or parts[1] not in STATUSES or len(parts[0]) != 64:
            raise LockError(f"{path.name}:{number}: expected '<sha256>  <status>  <id>'")
        if parts[2] in entries:
            raise LockError(f"{path.name}:{number}: {parts[2]} is listed twice")
        entries[parts[2]] = (parts[0], parts[1])
    return entries


def write_lock(entries: dict[str, tuple[str, str]], path: Path = LOCK) -> None:
    order = {m.migration_id: i for i, m in enumerate(discover_migrations(path.parent))}
    lines = [f"{checksum}  {status:<7}  {migration_id}"
             for migration_id, (checksum, status) in
             sorted(entries.items(), key=lambda item: (order.get(item[0], 10**6), item[0]))]
    path.write_text(HEADER + "\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def problems(directory: Path = MIGRATION_DIR, path: Path | None = None) -> list[str]:
    lock = read_lock(path or directory / "checksums.lock")
    current = {m.migration_id: m.checksum for m in discover_migrations(directory)}
    found = []
    for migration_id, (checksum, status) in lock.items():
        if migration_id not in current:
            found.append(f"{migration_id} ({status}) was deleted; migrations are forward-only")
        elif current[migration_id] != checksum:
            hint = ("applied migrations never change; add a new wave" if status == "applied"
                    else f"run: tools/schema_checksums.py update {migration_id}")
            found.append(f"{migration_id} changed ({hint})")
    for migration_id in sorted(set(current) - set(lock)):
        found.append(f"{migration_id} is not in the lock (run: tools/schema_checksums.py add)")
    return found


def main(argv: list[str] | None = None, directory: Path = MIGRATION_DIR) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "check"
    path = directory / LOCK.name
    lock = read_lock(path)
    current = {m.migration_id: m.checksum for m in discover_migrations(directory)}
    if command == "check" and len(args) <= 1:
        issues = problems(directory, path)
        for issue in issues:
            print(issue, file=sys.stderr)
        if not issues:
            print(f"{len(lock)} migration checksums match the lock")
        return 1 if issues else 0
    if command == "add" and len(args) == 1:
        new = sorted(set(current) - set(lock))
        lock.update({migration_id: (current[migration_id], "pending") for migration_id in new})
        write_lock(lock, path)
        print(f"added {len(new)} pending migration(s): {', '.join(new) or '-'}")
        return 0
    if command in {"update", "applied"} and len(args) == 2:
        migration_id = args[1]
        if migration_id not in lock:
            print(f"{migration_id} is not in the lock", file=sys.stderr)
            return 1
        checksum, status = lock[migration_id]
        if command == "update":
            if status == "applied":
                print(f"{migration_id} is applied; add a new wave instead", file=sys.stderr)
                return 1
            lock[migration_id] = (current[migration_id], "pending")
        else:
            if current.get(migration_id) != checksum:
                print(f"{migration_id} differs from its frozen checksum; refusing", file=sys.stderr)
                return 1
            lock[migration_id] = (checksum, "applied")
        write_lock(lock, path)
        print(f"{command}: {migration_id}")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
