"""Subcommand dispatch for a component's single console entry point.

Each deployable component exposes one command (``factorlab-ingest-us``) whose first
argument picks what to run (``daemon``, ``universe``, ``engine``); the remaining
arguments go to that command's own parser unchanged::

    COMMANDS = {
        "daemon": Command("price collection daemon", daemon.main),
        "engine": Command("provider-agnostic engine", run_engine),
    }

    def main(argv=None):
        return dispatch("factorlab-ingest-us", COMMANDS, argv)
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Command:
    summary: str
    run: Callable[[list[str]], int | None]


def dispatch(prog: str, commands: Mapping[str, Command], argv: Sequence[str] | None = None) -> int:
    """Run ``commands[argv[0]]`` with the rest of *argv*; print usage for help or no arguments."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in {"-h", "--help", "help"}:
        width = max(map(len, commands), default=0)
        lines = [f"usage: {prog} <command> [options]", "", "commands:"]
        lines += [f"  {name.ljust(width)}  {command.summary}" for name, command in commands.items()]
        lines += ["", f"Run `{prog} <command> --help` for a command's options."]
        print("\n".join(lines))
        return 0 if args else 2
    name, rest = args[0], args[1:]
    command = commands.get(name)
    if command is None:
        print(f"{prog}: unknown command {name!r}; choose from {', '.join(commands)}", file=sys.stderr)
        return 2
    result = command.run(rest)
    return int(result or 0)


__all__ = ["Command", "dispatch"]
