"""``factorlab-ingest-india``: NSE equities and stock futures from Upstox into ClickHouse v2.

Each command forwards its remaining arguments to that command's own parser, e.g.
``factorlab-ingest-india daemon --help``. ``engine`` runs the provider-agnostic engine
(``factorlab.orchestration.cli``) with the providers this component ships.
Implementations load only when their command runs.
"""

from __future__ import annotations

from factorlab.runtime.cli import Command, dispatch


def _daemon(argv: list[str]) -> int | None:
    from factorlab.components.ingest_india.legacy.daemon import main

    return main(argv)


def _premarket(argv: list[str]) -> int | None:
    from factorlab.components.ingest_india.legacy.premarket import main

    return main(argv)


def _engine(argv: list[str]) -> int:
    from factorlab.components.ingest_india.providers import PROVIDERS
    from factorlab.orchestration import cli as engine_cli

    return engine_cli.main(argv, providers=PROVIDERS, prog="factorlab-ingest-india engine")


COMMANDS = {
    "daemon": Command("1-min bar collection daemon (production path)", _daemon),
    "premarket": Command("refresh the NSE instrument master and universes", _premarket),
    "engine": Command("provider-agnostic engine: validate, run, daemon, replay", _engine),
}


def main(argv: list[str] | None = None) -> int:
    return dispatch("factorlab-ingest-india", COMMANDS, argv)


if __name__ == "__main__":
    raise SystemExit(main())
