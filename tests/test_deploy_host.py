"""Host provisioning (deploy/scripts/prepare-host.sh) stays in step with the components."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
PREPARE_HOST = REPO / "deploy" / "scripts" / "prepare-host.sh"


def _manifest_names() -> set[str]:
    return {yaml.safe_load(p.read_text(encoding="utf-8"))["name"]
            for p in (REPO / "components").glob("*/component.yaml")}


def test_every_component_gets_a_log_directory():
    match = re.search(r'^FACTORLAB_COMPONENTS="([^"]+)"', PREPARE_HOST.read_text(encoding="utf-8"),
                      re.MULTILINE)
    assert match, "prepare-host.sh must list FACTORLAB_COMPONENTS"
    assert set(match.group(1).split()) == _manifest_names()


def test_log_mounts_match_the_component_log_directory():
    for manifest in (REPO / "components").glob("*/component.yaml"):
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        fragment = yaml.safe_load((manifest.parent / "deploy" / "compose.yaml").read_text("utf-8"))
        logs = data["platform"]["logs"]
        assert logs == f"/var/log/factorlab/{data['name']}"
        for name, service in fragment["services"].items():
            env = service.get("environment", {})
            if "FACTORLAB_LOG_DIR" in env:
                assert env["FACTORLAB_LOG_DIR"] == logs, name
                assert f"{logs}:{logs}" in service.get("volumes", []), name


def _logrotate_stanzas() -> list[tuple[list[str], str]]:
    """(paths, body) per stanza of deploy/logrotate/factorlab."""
    lines = (REPO / "deploy" / "logrotate" / "factorlab").read_text(encoding="utf-8").splitlines()
    text = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    return [(head.split(), body) for head, body in re.findall(r"([^{}]+)\{([^}]*)\}", text)]


def test_logrotate_covers_every_component_directory_exactly_once():
    seen: dict[str, int] = {}
    for paths, body in _logrotate_stanzas():
        assert "maxage 30" in body and "compress" in body
        for directory in {p.rsplit("/", 1)[0] for p in paths}:
            assert directory.startswith("/var/log/factorlab/") and "*" not in directory
            seen[directory] = seen.get(directory, 0) + 1
    expected = {f"/var/log/factorlab/{name}" for name in _manifest_names()}
    assert set(seen) == expected
    assert all(count == 1 for count in seen.values()), seen


def test_only_nginx_logs_are_rotated_in_place():
    for paths, body in _logrotate_stanzas():
        web = any(p.startswith("/var/log/factorlab/web/") for p in paths)
        assert ("copytruncate" in body) == web
        assert ("create 0640 factorlab factorlab-logs" in body) == (not web)


def test_the_log_reader_account_is_provisioned_safely():
    text = PREPARE_HOST.read_text(encoding="utf-8")
    assert "--gid factorlab-logs" in text and "--shell /bin/sh factorlab-logs" in text
    assert "/usr/local/bin/factorlab-log-reader" in text
    # sshd is validated before any reload, and a rejected drop-in is removed again.
    check, reload = text.index("sshd -t"), text.index("systemctl reload ssh")
    assert check < reload and 'rm -f "$sshd_dropin"' in text[check:reload]
    dropin = (REPO / "deploy" / "ssh" / "60-factorlab-logs.conf").read_text(encoding="utf-8")
    for rule in ("Match User factorlab-logs", "ForceCommand /usr/local/bin/factorlab-log-reader",
                 "PermitTTY no", "DisableForwarding yes", "AuthenticationMethods publickey",
                 "AuthorizedKeysFile /etc/factorlab/log-reader/authorized_keys"):
        assert rule in dropin, rule
    assert dropin.rstrip().endswith("Match all")
