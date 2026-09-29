"""``factorlab-ingest-political``: Congressional references, House filing index and PTR trades into ClickHouse v2.

Each command forwards its remaining arguments to that command's own parser, e.g.
``factorlab-ingest-political bootstrap --help``. ``engine`` runs the provider-agnostic engine
(``factorlab.orchestration.cli``) with the providers this component ships.
Implementations load only when their command runs.
"""

from __future__ import annotations

from factorlab.runtime.cli import Command, dispatch


def _bootstrap(argv: list[str]) -> int | None:
    from factorlab.components.ingest_political.legacy.bootstrap import main

    return main(argv)


def _engine(argv: list[str]) -> int:
    from factorlab.components.ingest_political.providers import PROVIDERS
    from factorlab.orchestration import cli as engine_cli

    return engine_cli.main(argv, providers=PROVIDERS, prog="factorlab-ingest-political engine")


COMMANDS = {
    "bootstrap": Command("one political collection run (host cron schedules it)", _bootstrap),
    "engine": Command("provider-agnostic engine: validate, run, daemon, replay", _engine),
}


def main(argv: list[str] | None = None) -> int:
    return dispatch("factorlab-ingest-political", COMMANDS, argv)


if __name__ == "__main__":
    raise SystemExit(main())
