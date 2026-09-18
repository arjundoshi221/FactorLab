"""Tests for factorlab.shared.ingest — Phase 5."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from factorlab.shared.ingest import (
    BackfillPlan,
    BackfillRegistry,
    BackfillReport,
    HTTPClient,
    RateLimitDeferred,
    State,
    assert_under_root,
    list_backfillers,
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


# ── State ────────────────────────────────────────────────────────────────────


def test_state_round_trip(monkeypatch, tmp_path):
    """Patch the state_dir lookup inside the state module — no env reload."""
    import factorlab.shared.ingest.state as _state

    def fake_state_dir(name: str) -> Path:
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(_state, "state_dir", fake_state_dir)

    s = _state.State(source="zz_test_source")
    assert len(s) == 0
    s.mark_done("a", flush=True)
    s.mark_done("b", flush=True)

    s2 = _state.State(source="zz_test_source")
    assert "a" in s2
    assert "b" in s2
    assert len(s2) == 2


def test_state_namespace_nests_under_parent(monkeypatch, tmp_path):
    """``namespace='political'`` → file lives at <root>/political/<source>.json."""
    import factorlab.shared.ingest.state as _state

    def fake_state_dir(name: str) -> Path:
        d = tmp_path / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    monkeypatch.setattr(_state, "state_dir", fake_state_dir)

    s = _state.State(source="house_clerk_ptr", namespace="political")
    s.mark_done("2024/abc", flush=True)
    expected = tmp_path / "political" / "house_clerk_ptr.json"
    assert expected.exists(), f"missing {expected}"
    data = json.loads(expected.read_text(encoding="utf-8"))
    assert "2024/abc" in data


# ── HTTPClient (no real network) ─────────────────────────────────────────────


def test_http_client_cache_hit_reuses_disk_bytes(tmp_path):
    """Plant a cache file at the save_as path; .get() must return it without
    hitting the network."""
    source = "zz_test_http"
    client = HTTPClient(source=source, data_root=tmp_path, engine=None)
    target = tmp_path / source / "prefilled.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b'{"hello": "world"}')

    body, hit_path = client.get(
        "https://example.invalid/data",
        save_as="prefilled.json",
    )
    assert body == b'{"hello": "world"}'
    assert hit_path == target.resolve()


def test_http_client_rejects_save_as_escape(tmp_path):
    client = HTTPClient(source="zz", data_root=tmp_path, engine=None)
    with pytest.raises(ValueError):
        # Escape the data_root via ../..
        client.get("https://example.invalid/x", save_as="../../etc/passwd")


def test_rate_limit_deferred_carries_source_and_retry_after():
    e = RateLimitDeferred(source="fec", retry_after_sec=900.0,
                          url_redacted="https://api.fec.gov/x?api_key=REDACTED")
    assert e.source == "fec"
    assert e.retry_after_sec == 900.0
    assert "fec" in str(e)
    assert "REDACTED" in str(e)


# ── Backfiller protocol + registry ──────────────────────────────────────────


class _DummyBackfiller:
    source = "zz_dummy"

    def plan(self, *, since=None, until=None, **kw):
        return BackfillPlan(source=self.source, units=[{"x": 1}])

    def run(self, plan, *, dry_run=False, notify_fn=None):
        return BackfillReport(
            source=self.source,
            units_done=plan.unit_count if not dry_run else 0,
            rows_written=42 if not dry_run else 0,
        )


def test_registry_register_and_lookup():
    reg = BackfillRegistry()
    bf = _DummyBackfiller()
    reg.register("zz_dummy", bf)
    assert reg.get("zz_dummy") is bf
    assert "zz_dummy" in reg.list()


def test_backfill_plan_unit_count():
    p = BackfillPlan(source="x", units=[{"a": 1}, {"a": 2}])
    assert p.unit_count == 2


def test_backfill_report_ok_property():
    r = BackfillReport(source="x", units_done=5, units_failed=0)
    assert r.ok is True
    r.units_failed = 1
    assert r.ok is False


def test_eodhd_backfiller_registered_globally():
    """The dispatcher imports eodhd.backfill which auto-registers."""
    import factorlab.countries.us.equities.eodhd.backfill  # noqa: F401
    assert "eodhd" in list_backfillers()


def test_eodhd_backfiller_dry_run_emits_no_writes():
    import factorlab.countries.us.equities.eodhd.backfill as bf_mod
    bf = bf_mod.EODHDBackfiller(symbols=["AAPL.US"])
    plan = bf.plan(since=date(2024, 1, 1), until=date(2024, 1, 31))
    assert plan.unit_count == 1
    report = bf.run(plan, dry_run=True)
    assert report.units_done == 0
    assert report.rows_written == 0
    assert report.ok is True
    assert any("dry-run" in n for n in report.notes)
