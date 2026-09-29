"""Tests for factorlab.runtime.notify — Phase 4."""

from __future__ import annotations

import json
import time

from factorlab.runtime.notify import notify
from factorlab.runtime.notify._backend import (
    Backend,
    DedupeRing,
    Notification,
    Severity,
)
from factorlab.runtime.notify.jsonl import JSONLBackend
from factorlab.runtime.notify.null import NullBackend

# ── Notification dataclass ──────────────────────────────────────────────────


def test_notification_auto_timestamps():
    n = Notification(subject="s", body="b", severity=Severity.WARN, source="x")
    assert n.occurred_at is not None
    assert n.occurred_at.tzinfo is not None  # must be tz-aware


def test_notification_html_body_contains_severity_label():
    n = Notification(subject="s", body="hello", severity=Severity.FAIL, source="x")
    html = n.html_body()
    assert "FAIL" in html
    assert "hello" in html


def test_notification_subject_with_tag():
    n = Notification(subject="boom", body="", severity=Severity.FATAL, source="x")
    assert n.subject_with_tag() == "[FactorLab FATAL] boom"


# ── DedupeRing ──────────────────────────────────────────────────────────────


def test_dedupe_ring_blocks_repeats_in_window():
    r = DedupeRing(window_seconds=60.0)
    assert r.already_sent("k1") is False
    assert r.already_sent("k1") is True
    assert r.already_sent("k2") is False


def test_dedupe_ring_releases_after_window():
    r = DedupeRing(window_seconds=0.01)
    r.already_sent("k1")
    time.sleep(0.02)
    assert r.already_sent("k1") is False


# ── JSONLBackend ────────────────────────────────────────────────────────────


def test_jsonl_backend_appends_line(tmp_path):
    p = tmp_path / "notify.jsonl"
    b = JSONLBackend(path=p)
    n = Notification(subject="s", body="b", severity=Severity.WARN, source="src")
    assert b.send(n) is True
    line = json.loads(p.read_text(encoding="utf-8").strip())
    assert line["severity"] == "warn"
    assert line["source"] == "src"
    assert line["subject"] == "s"


# ── NullBackend ─────────────────────────────────────────────────────────────


def test_null_backend_returns_true():
    n = Notification(subject="s", body="b", severity=Severity.INFO, source="src")
    assert NullBackend().send(n) is True


# ── notify() entry — dedupe, severity, backend fan-out ──────────────────────


class _RecordingBackend(Backend):
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> bool:
        self.sent.append(notification)
        return True


def test_notify_fans_out_to_explicit_backends():
    rec = _RecordingBackend()
    results = notify(
        "subj",
        "body",
        severity="warn",
        source="src",
        dedupe_key=False,
        backends=[rec],
    )
    assert results == [True]
    assert len(rec.sent) == 1
    assert rec.sent[0].subject == "subj"


def test_notify_dedupes_by_default():
    rec = _RecordingBackend()
    notify("subj", "body", severity="warn", source="src", backends=[rec])
    notify("subj", "body", severity="warn", source="src", backends=[rec])
    notify("subj", "body", severity="warn", source="src", backends=[rec])
    # First call sends; subsequent dedupe to nothing.
    assert len(rec.sent) == 1


def test_notify_fatal_bypasses_dedupe():
    rec = _RecordingBackend()
    notify("boom", "b", severity="fatal", source="src", backends=[rec])
    notify("boom", "b", severity="fatal", source="src", backends=[rec])
    # Both sent: fatal never dedupes.
    assert len(rec.sent) == 2


def test_notify_dedupe_key_false_disables_dedupe():
    rec = _RecordingBackend()
    notify("s", "b", severity="warn", source="x", dedupe_key=False, backends=[rec])
    notify("s", "b", severity="warn", source="x", dedupe_key=False, backends=[rec])
    assert len(rec.sent) == 2


def test_notify_dedupe_key_string_uses_provided_key():
    rec = _RecordingBackend()
    notify("s1", "b", severity="warn", source="x", dedupe_key="k", backends=[rec])
    notify("s2", "b", severity="warn", source="x", dedupe_key="k", backends=[rec])
    # Same dedupe key blocks the second send despite different subject.
    assert len(rec.sent) == 1


def test_notify_env_selection_includes_jsonl_always(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTORLAB_NOTIFY_BACKEND", "null")
    monkeypatch.setenv("FACTORLAB_LOG_ROOT", str(tmp_path))
    # Force fresh import path resolution for log_dir()
    import importlib

    import factorlab.core.paths as _paths

    importlib.reload(_paths)
    import factorlab.runtime.notify.jsonl as _jsonl

    importlib.reload(_jsonl)
    import factorlab.runtime.notify as _notify

    importlib.reload(_notify)

    # Send through env-driven backends
    _notify.notify("s", "b", severity="warn", source="x", dedupe_key=False)
    jsonl_path = tmp_path / "notify.jsonl"
    assert jsonl_path.exists(), "JSONL must always be appended"

    # Restore
    monkeypatch.delenv("FACTORLAB_NOTIFY_BACKEND", raising=False)
    monkeypatch.delenv("FACTORLAB_LOG_ROOT", raising=False)
    importlib.reload(_paths)
    importlib.reload(_jsonl)
    importlib.reload(_notify)
