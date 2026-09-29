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
                      re.M)
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


def test_logrotate_covers_every_component_directory():
    lines = (REPO / "deploy" / "logrotate" / "factorlab").read_text(encoding="utf-8").splitlines()
    text = " ".join(line for line in lines if not line.lstrip().startswith("#"))
    assert "/var/log/factorlab/*/*.jsonl" in text
    assert "maxage 30" in text and "copytruncate" not in text
