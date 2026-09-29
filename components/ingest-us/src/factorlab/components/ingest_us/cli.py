"""``factorlab-ingest-us``: US universe resolution and daily/1-min bars into ClickHouse v2.

Each command forwards its remaining arguments to that command's own parser, e.g.
``factorlab-ingest-us daemon --help``. ``engine`` runs the provider-agnostic engine
(``factorlab.orchestration.cli``) with the providers this component ships.
Implementations load only when their command runs.
"""

from __future__ import annotations

from factorlab.runtime.cli import Command, dispatch


def _daemon(argv: list[str]) -> int | None:
    from factorlab.components.ingest_us.legacy.daemon import main

    return main(argv)


def _universe(argv: list[str]) -> int | None:
    from factorlab.components.ingest_us.legacy.universe_daemon import main

    return main(argv)


def _engine(argv: list[str]) -> int:
    from factorlab.components.ingest_us.providers import PROVIDERS
    from factorlab.orchestration import cli as engine_cli

    return engine_cli.main(argv, providers=PROVIDERS, prog="factorlab-ingest-us engine")


COMMANDS = {
    "daemon": Command("daily and 1-min bar collection daemon (production path)", _daemon),
    "universe": Command("resolve and publish the configured US universe", _universe),
    "engine": Command("provider-agnostic engine: validate, run, daemon, replay", _engine),
}


def main(argv: list[str] | None = None) -> int:
    return dispatch("factorlab-ingest-us", COMMANDS, argv)


if __name__ == "__main__":
    raise SystemExit(main())
