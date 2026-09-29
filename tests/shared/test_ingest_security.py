"""Tests for the ingestion security helpers (URL redaction, path and id validation)."""

from __future__ import annotations

import pytest

from factorlab.ingest import (
    assert_under_root,
    redact_error,
    redact_url,
    validate_id_segment,
    validate_year_segment,
)

# ── security ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://api.fec.gov/x?api_key=ABC123",
         "https://api.fec.gov/x?api_key=REDACTED"),
        ("https://finnhub.io/x?token=XYZ&foo=1",
         "https://finnhub.io/x?token=REDACTED&foo=1"),
        ("https://example.com?Access_Token=secret&q=z",
         "https://example.com?Access_Token=REDACTED&q=z"),
        ("https://example.com/no-secret", "https://example.com/no-secret"),
    ],
)
def test_redact_url(url, expected):
    assert redact_url(url) == expected


def test_redact_error_scrubs_embedded_url():
    msg = "HTTPError 403 for https://api.fec.gov/x?api_key=ABC123"
    out = redact_error(msg)
    assert "ABC123" not in out
    assert "REDACTED" in out


@pytest.mark.parametrize("good", ["2024", "abc_123", "doc-id_42"])
def test_validate_id_segment_accepts_safe(good):
    assert validate_id_segment(good) == good


@pytest.mark.parametrize("bad", ["", "../etc", "has space", "x" * 100, "x/y"])
def test_validate_id_segment_rejects_unsafe(bad):
    with pytest.raises(ValueError):
        validate_id_segment(bad)


def test_validate_year_segment_accepts_4_digits():
    assert validate_year_segment(2024) == "2024"
    assert validate_year_segment("2026") == "2026"


@pytest.mark.parametrize("bad", ["202", "20245", "abcd", ""])
def test_validate_year_segment_rejects(bad):
    with pytest.raises(ValueError):
        validate_year_segment(bad)


def test_assert_under_root_rejects_escape(tmp_path):
    root = tmp_path
    bad = tmp_path / ".." / "outside.txt"
    with pytest.raises(ValueError):
        assert_under_root(bad, root)


def test_assert_under_root_accepts_safe(tmp_path):
    root = tmp_path
    safe = tmp_path / "ok" / "file.txt"
    safe.parent.mkdir(parents=True, exist_ok=True)
    safe.touch()
    out = assert_under_root(safe, root)
    assert out == safe.resolve()
