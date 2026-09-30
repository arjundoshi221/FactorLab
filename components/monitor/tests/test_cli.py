import pytest

from factorlab.components.monitor.cli import main


def test_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "--registry" in capsys.readouterr().out
