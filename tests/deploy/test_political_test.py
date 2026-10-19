"""The political trial changes only the ingest-political image and gates the repair."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("fcntl")

RUNNER = Path(__file__).resolve().parents[2] / "deploy" / "testing"
sys.path.insert(0, str(RUNNER))
spec = importlib.util.spec_from_file_location("political_test", RUNNER / "political_test.py")
assert spec and spec.loader
political_test = importlib.util.module_from_spec(spec)
spec.loader.exec_module(political_test)

COMMIT = "a" * 40
IMAGE_ID = "sha256:" + "c" * 64
LIVE = {
    "services": {
        "ingest-political": {"image": "old-political", "environment": {"A": "1"}},
        "ingest-us": {"image": "old-us"},
    }
}


def harness(monkeypatch, tmp_path, reports, *, fail_on=None):
    deploy = tmp_path / "opt" / "deploy"
    deploy.mkdir(parents=True)
    calls, installed = [], []
    monkeypatch.setattr(political_test.test, "DEPLOY", deploy)
    monkeypatch.setattr(political_test.test, "BASE", tmp_path / "tests")
    monkeypatch.setattr(political_test, "image_id", lambda image, commit, version: IMAGE_ID)
    monkeypatch.setattr(
        political_test.test, "install_model", lambda path: installed.append(path.name)
    )

    def compose(path, *args):
        if args[:1] == ("config",):
            return json.dumps(LIVE) if "--format" in args else ""
        command = args[5] + (" --apply" if "--apply" in args else "")
        calls.append(command)
        if command == fail_on:
            raise RuntimeError("job failed")
        return json.dumps(reports[command].pop(0))

    monkeypatch.setattr(political_test.test, "compose", compose)
    root = tmp_path / "tests" / "political-aaaaaaaaaaaa"
    return root, calls, installed


def reports(retired=150, relinked=340, stale=433):
    return {
        "dedupe-political-trades": [
            {"stale_rows": stale, "ambiguous": 0},
            {"stale_rows": 0, "ambiguous": 0},
        ],
        "dedupe-political-trades --apply": [{"stale_rows": stale, "applied": True}],
        "retire-duplicate-listings": [{"retired_listings": retired}, {"retired_listings": 0}],
        "reresolve-political-trades": [{"relinked": relinked}, {"relinked": 0}],
        "retire-duplicate-listings --apply": [{"retired_listings": retired, "applied": True}],
        "reresolve-political-trades --apply": [{"relinked": relinked, "applied": True}],
    }


def test_apply_runs_dry_runs_then_repair_then_verification(monkeypatch, tmp_path, capsys):
    root, calls, installed = harness(monkeypatch, tmp_path, reports())
    political_test.apply(root, COMMIT, "unused", 200, 500)
    assert calls == [
        "dedupe-political-trades",
        "retire-duplicate-listings",
        "reresolve-political-trades",
        "dedupe-political-trades --apply",
        "retire-duplicate-listings --apply",
        "reresolve-political-trades --apply",
        "dedupe-political-trades",
        "retire-duplicate-listings",
        "reresolve-political-trades",
    ]
    assert installed == ["candidate.json"]
    candidate = json.loads((root / "candidate.json").read_text())
    assert candidate["services"]["ingest-political"]["image"] == IMAGE_ID
    assert candidate["services"]["ingest-us"] == LIVE["services"]["ingest-us"]
    out = capsys.readouterr().out
    assert "FACTORLAB_RESULT_VERIFICATION=passed" in out
    assert "FACTORLAB_RESULT_ROLLBACK=not-needed" in out


def test_a_dry_run_over_the_limit_restores_the_model_and_writes_nothing(
    monkeypatch, tmp_path, capsys
):
    root, calls, installed = harness(monkeypatch, tmp_path, reports(retired=500))
    with pytest.raises(RuntimeError, match="limit is 200"):
        political_test.apply(root, COMMIT, "unused", 200, 500)
    assert calls == ["dedupe-political-trades", "retire-duplicate-listings"]
    assert installed == ["candidate.json", "before.json"]
    assert "FACTORLAB_RESULT_ROLLBACK=restored" in capsys.readouterr().out


def test_a_failure_after_data_writes_does_not_swap_the_image_back(monkeypatch, tmp_path, capsys):
    root, _, installed = harness(
        monkeypatch, tmp_path, reports(), fail_on="reresolve-political-trades --apply"
    )
    with pytest.raises(RuntimeError, match="job failed"):
        political_test.apply(root, COMMIT, "unused", 200, 500)
    assert installed == ["candidate.json"]
    assert "not-possible-after-data-writes" in capsys.readouterr().out


@pytest.mark.parametrize(
    "image",
    [
        "ghcr.io/arjundoshi221/factorlab-ingest-political:latest",
        "ghcr.io/arjundoshi221/factorlab-api@sha256:" + "a" * 64,
    ],
)
def test_only_digest_pinned_political_images_are_accepted(image):
    with pytest.raises(RuntimeError, match="Invalid"):
        political_test.image_id(image, COMMIT, "0.0.0-trial.aaaaaaaaaaaa")


def test_too_many_superseded_rows_stops_before_any_write(monkeypatch, tmp_path):
    root, calls, installed = harness(monkeypatch, tmp_path, reports(stale=5000))
    with pytest.raises(RuntimeError, match="limit is 500"):
        political_test.apply(root, COMMIT, "unused", 200, 500)
    assert calls == ["dedupe-political-trades"]
    assert installed == ["candidate.json", "before.json"]
