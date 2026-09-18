"""Tests for factorlab.shared.paths — Phase 1 layout contract.

These tests pin the *legacy* shape: Phase 1 must not change any on-disk
location. Phase 2 (NAS migration) will introduce new tests for the unified
``<RAW_ROOT>/<domain>/<country>/<vendor>/`` layout.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from factorlab.shared import paths


def test_repo_root_resolves_to_repository():
    assert (paths.REPO_ROOT / "pyproject.toml").exists(), (
        "REPO_ROOT must resolve to the repo root (where pyproject.toml lives)"
    )


@pytest.mark.parametrize(
    "source,expected_subpath",
    [
        ("political",          "political/raw"),
        ("fec",                "political/raw/fec"),
        ("senate_efd",         "political/raw/senate_efd"),
        ("senate_efd_ptrs",    "political/raw/senate_efd/ptrs"),
        ("senate_efd_llm",     "political/raw/senate_efd/_llm_extractions"),
        ("eodhd",              "eodhd"),
        ("upstox_instruments", "upstox/instruments"),
        ("upstox_live",        "in/live"),
    ],
)
def test_raw_dir_preserves_legacy_layout(source, expected_subpath):
    """Phase 1 contract: raw_dir(<source>) returns the existing on-disk path."""
    got = paths.raw_dir(source)
    expected = (paths.RAW_ROOT / expected_subpath).resolve()
    assert got == expected, f"raw_dir({source!r}) drifted: {got} != {expected}"


def test_raw_dir_creates_directory():
    p = paths.raw_dir("eodhd")
    assert p.is_dir()


def test_raw_dir_falls_back_to_source_name_for_unknown():
    """Unknown sources land at <RAW_ROOT>/<source> as a uniform default."""
    p = paths.raw_dir("zz_test_source")
    assert p == (paths.RAW_ROOT / "zz_test_source").resolve()


def test_state_dir_political_preserves_legacy_nested_location():
    got = paths.state_dir("political")
    expected = (paths.REPO_ROOT / "data" / "political" / "_state").resolve()
    assert got == expected


def test_state_dir_unknown_uses_unified_state_root():
    got = paths.state_dir("zz_test")
    assert got == (paths.STATE_ROOT / "zz_test").resolve()


def test_token_path_upstox_preserves_legacy_location():
    got = paths.token_path("upstox")
    expected = (paths.REPO_ROOT / "data" / "upstox" / ".token").resolve()
    assert got == expected


def test_token_path_schwab_preserves_legacy_location():
    got = paths.token_path("schwab")
    expected = (paths.REPO_ROOT / "data" / "schwab" / ".token").resolve()
    assert got == expected


def test_assert_under_root_accepts_safe_path():
    safe = paths.RAW_ROOT / "anything"
    safe.mkdir(parents=True, exist_ok=True)
    paths.assert_under_root(safe, paths.RAW_ROOT)  # must not raise


def test_assert_under_root_rejects_escape():
    bad = paths.RAW_ROOT / ".." / ".." / "etc"
    with pytest.raises(PermissionError):
        paths.assert_under_root(bad, paths.RAW_ROOT)


def test_log_dir_returns_repo_logs_by_default():
    got = paths.log_dir()
    expected = (paths.REPO_ROOT / "logs").resolve()
    assert got.resolve() == expected


def test_heartbeat_path_creates_parent_and_resolves_under_root():
    p = paths.heartbeat_path("test_service")
    assert p.parent == paths.HEARTBEAT_ROOT.resolve()


def test_env_override_for_raw_root(monkeypatch, tmp_path):
    """FACTORLAB_RAW_ROOT must be respected when set (Phase 2 will rely on this)."""
    monkeypatch.setenv("FACTORLAB_RAW_ROOT", str(tmp_path))
    # Reimport with the env var set to pick up the new default
    import importlib

    import factorlab.shared.paths as _paths
    importlib.reload(_paths)
    try:
        got = _paths.raw_dir("eodhd")
        assert str(got).startswith(str(tmp_path.resolve()))
    finally:
        # Restore module state for downstream tests
        monkeypatch.delenv("FACTORLAB_RAW_ROOT", raising=False)
        importlib.reload(_paths)
