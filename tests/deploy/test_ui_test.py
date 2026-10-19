"""The branch UI trial changes only the candidate API and web image pins."""

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

pytest.importorskip("fcntl")

RUNNER = Path(__file__).resolve().parents[2] / "deploy" / "testing"
sys.path.insert(0, str(RUNNER))
spec = importlib.util.spec_from_file_location("ui_test", RUNNER / "ui_test.py")
assert spec and spec.loader
ui_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui_test)


def test_candidate_keeps_every_non_ui_service_unchanged():
    live = {
        "services": {
            "api": {"image": "old-api", "environment": {"SECRET_FILE": "/run/secrets/api"}},
            "api-candidate": {"image": "old-candidate", "ports": [18000]},
            "web": {"image": "old-web", "ports": [8080]},
            "ingest-us": {"image": "old-writer", "environment": {"SOURCE": "schwab"}},
        }
    }
    before = copy.deepcopy(live)
    ids = {"api-candidate": "sha256:" + "a" * 64, "web": "sha256:" + "b" * 64}
    candidate = ui_test.candidate_model(live, ids)

    assert live == before
    assert candidate["services"]["api-candidate"]["image"] == ids["api-candidate"]
    assert candidate["services"]["web"]["image"] == ids["web"]
    assert candidate["services"]["api"] == before["services"]["api"]
    assert candidate["services"]["ingest-us"] == before["services"]["ingest-us"]


@pytest.mark.parametrize(
    "ids",
    [
        {"web": "sha256:" + "b" * 64},
        {"api-candidate": "sha256:" + "a" * 64, "web": "mutable:latest"},
        {"api-candidate": "sha256:" + "a" * 64, "api": "sha256:" + "b" * 64},
    ],
)
def test_candidate_rejects_missing_mutable_and_non_ui_images(ids):
    live = {"services": {"api-candidate": {}, "web": {}, "api": {}}}
    with pytest.raises(RuntimeError):
        ui_test.candidate_model(live, ids)
