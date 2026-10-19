"""``factorlab-ingest-broker``: Read-only IBKR account mirror into broker.*.

Each command forwards its remaining arguments to that command's own parser, e.g.
``factorlab-ingest-broker snapshot --help``. ``engine`` runs the provider-agnostic engine
(``factorlab.orchestration.cli``) with the providers this component ships.
Implementations load only when their command runs.
"""

from __future__ import annotations

from factorlab.runtime.cli import Command, dispatch


def _snapshot(argv: list[str]) -> int | None:
    from factorlab.components.ingest_broker.snapshot import cli

    return cli(argv)


def _engine(argv: list[str]) -> int:
    from factorlab.components.ingest_broker.providers import PROVIDERS
    from factorlab.orchestration import cli as engine_cli

    return engine_cli.main(argv, providers=PROVIDERS, prog="factorlab-ingest-broker engine")


def _profiles(argv: list[str]) -> int:
    from factorlab.components.ingest_broker.profiles import profiles_cli

    return profiles_cli(argv)


def _simulate(argv: list[str]) -> int:
    from factorlab.components.ingest_broker.profiles import simulate_cli

    return simulate_cli(argv)


def _simulate_stream(argv: list[str]) -> int:
    from factorlab.components.ingest_broker.stream_simulation import cli

    return cli(argv)


COMMANDS = {
    "simulate-stream": Command("replay timed synthetic orders, fills and quotes", _simulate_stream),
    "profiles": Command("register shared test profiles and account ownership", _profiles),
    "simulate": Command("ingest synthetic paper broker responses (no Gateway)", _simulate),
    "snapshot": Command("account snapshot (one run or the scheduled daemon)", _snapshot),
    "engine": Command("provider-agnostic engine: validate, run, daemon, replay", _engine),
}


def main(argv: list[str] | None = None) -> int:
    return dispatch("factorlab-ingest-broker", COMMANDS, argv)


if __name__ == "__main__":
    raise SystemExit(main())
