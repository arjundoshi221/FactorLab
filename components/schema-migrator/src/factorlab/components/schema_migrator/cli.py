"""``factorlab-db``: ClickHouse v2 schema operations.

    factorlab-db bootstrap        fail unless the v2 schema (through Wave 9) is ready and
                                  raw.archive uses its storage policy (compose ``bootstrap``)
    factorlab-db verify-writes    wait for a fresh raw-to-curated write after a release
    factorlab-db migrate ...      the forward-only wave runner; production waves are a
                                  separate, reviewed, user-requested operation

``bootstrap`` applies no DDL.
"""

from __future__ import annotations

from factorlab.runtime.cli import Command, dispatch


def _bootstrap(argv: list[str]) -> int:
    from factorlab.schema import readiness, storage_policy

    if argv:
        raise SystemExit("factorlab-db bootstrap takes no arguments")
    readiness.main()
    storage_policy.main()
    return 0


def _verify_writes(argv: list[str]) -> int:
    from factorlab.schema import verify_writes

    verify_writes.main(argv)
    return 0


def _migrate(argv: list[str]) -> int:
    from factorlab.schema import migrate

    return migrate.main(argv)


COMMANDS = {
    "bootstrap": Command(
        "check v2 schema readiness and the raw archive storage policy", _bootstrap
    ),
    "verify-writes": Command("wait for a fresh v2 write after a release", _verify_writes),
    "migrate": Command("run the forward-only v2 wave migrations", _migrate),
}


def main(argv: list[str] | None = None) -> int:
    return dispatch("factorlab-db", COMMANDS, argv)


if __name__ == "__main__":
    raise SystemExit(main())
