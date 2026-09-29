"""factorlab.runtime.cli.dispatch: one entry point, many commands."""

from __future__ import annotations

from factorlab.runtime.cli import Command, dispatch

COMMANDS = {
    "daemon": Command("run forever", lambda argv: 7 if argv == ["--once"] else 0),
    "noop": Command("returns None", lambda argv: None),
}


def test_forwards_remaining_arguments_and_the_exit_code():
    assert dispatch("prog", COMMANDS, ["daemon", "--once"]) == 7
    assert dispatch("prog", COMMANDS, ["noop"]) == 0


def test_help_lists_commands_and_no_arguments_is_a_usage_error(capsys):
    assert dispatch("prog", COMMANDS, ["--help"]) == 0
    assert "daemon" in capsys.readouterr().out
    assert dispatch("prog", COMMANDS, []) == 2


def test_unknown_command_is_rejected(capsys):
    assert dispatch("prog", COMMANDS, ["bogus"]) == 2
    assert "unknown command 'bogus'" in capsys.readouterr().err
