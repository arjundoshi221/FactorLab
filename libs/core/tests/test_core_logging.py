"""factorlab.core.logging: JSON records, context, redaction, rotation-safe file sink."""

from __future__ import annotations

import io
import json
import logging

import pytest

from factorlab.core import logging as factorlab_logging
from factorlab.core.logging import bind, configure_logging, log_context, redact


@pytest.fixture(autouse=True)
def _restore_root_logger(monkeypatch):
    for name in ("FACTORLAB_COMPONENT", "FACTORLAB_SERVICE", "FACTORLAB_LOG_DIR",
                 "FACTORLAB_LOG_LEVEL", "FACTORLAB_LOG_FORMAT", "FACTORLAB_VERSION",
                 "FACTORLAB_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    context = factorlab_logging._CONTEXT.set({})  # isolate from runs other tests left open
    yield
    factorlab_logging._CONTEXT.reset(context)
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def _records(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


def test_records_are_json_with_release_metadata(monkeypatch):
    monkeypatch.setenv("FACTORLAB_VERSION", "1.4.0")
    monkeypatch.setenv("FACTORLAB_COMMIT", "0123456789abcdef")
    out = io.StringIO()
    configure_logging(component="ingest-us", service="universe-us", stream=out, fmt="json")
    logging.getLogger("factorlab.test").info("synced %d listings", 3, extra={"market": "USA"})
    [record] = _records(out)
    assert record["msg"] == "synced 3 listings"
    assert (record["component"], record["service"]) == ("ingest-us", "universe-us")
    assert (record["version"], record["commit"]) == ("1.4.0", "0123456789ab")
    assert record["level"] == "INFO" and record["logger"] == "factorlab.test"
    assert record["market"] == "USA" and record["ts"].endswith("Z")


def test_context_fields_attach_to_every_record_inside():
    out = io.StringIO()
    configure_logging(component="c", stream=out, fmt="json")
    log = logging.getLogger("factorlab.test")
    with log_context(run_id="r-1", pipeline="us_live"):
        log.info("inside")
    log.info("outside")
    inside, outside = _records(out)
    assert (inside["run_id"], inside["pipeline"]) == ("r-1", "us_live")
    assert "run_id" not in outside


def test_bind_persists_for_the_current_context():
    out = io.StringIO()
    configure_logging(component="c", stream=out, fmt="json")
    with log_context():
        bind(request_id="cf-123")
        logging.getLogger("t").warning("bound")
    assert _records(out)[0]["request_id"] == "cf-123"


def test_secrets_are_redacted_in_messages_extras_and_tracebacks():
    out = io.StringIO()
    configure_logging(component="c", stream=out, fmt="json")
    log = logging.getLogger("t")
    log.info("GET https://x/?api_key=abc123&q=1", extra={"access_token": "tok"})
    try:
        raise RuntimeError("Authorization: Bearer abc.def.ghi")
    except RuntimeError:
        log.exception("failed")
    first, second = _records(out)
    assert "abc123" not in first["msg"] and first["access_token"] == "[redacted]"
    assert "abc.def.ghi" not in second["exc"] and "Bearer [redacted]" in second["exc"]
    assert redact("password=hunter2 ok") == "password=[redacted] ok"


def test_file_sink_writes_jsonl_and_survives_rotation(tmp_path):
    out = io.StringIO()
    configure_logging(component="ingest-us", service="ingest-us", log_dir=tmp_path,
                      stream=out, fmt="json")
    log = logging.getLogger("t")
    log.info("before rotation")
    current = tmp_path / "ingest-us.jsonl"
    rotated = tmp_path / "ingest-us.jsonl-20260929-02"
    for handler in logging.getLogger().handlers:  # Windows cannot rename an open file
        if isinstance(handler, logging.FileHandler) and handler.stream:
            handler.stream.close()
            handler.stream = None
    current.rename(rotated)                        # what logrotate does
    log.info("after rotation")
    assert json.loads(rotated.read_text(encoding="utf-8"))["msg"] == "before rotation"
    assert json.loads(current.read_text(encoding="utf-8"))["msg"] == "after rotation"


def test_unwritable_log_dir_falls_back_to_the_console(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("x", encoding="utf-8")
    out = io.StringIO()
    configure_logging(component="c", log_dir=blocker / "logs", stream=out, fmt="json")
    logging.getLogger("t").info("still logging")
    messages = [r["msg"] for r in _records(out)]
    assert any("file logging disabled" in m for m in messages)
    assert "still logging" in messages


def test_configure_is_idempotent_and_keeps_foreign_handlers():
    foreign = logging.NullHandler()
    logging.getLogger().addHandler(foreign)
    out = io.StringIO()
    configure_logging(component="c", stream=out, fmt="json")
    configure_logging(component="c", stream=out, fmt="json")
    logging.getLogger("t").info("once")
    assert len(_records(out)) == 1
    assert foreign in logging.getLogger().handlers
    logging.getLogger().removeHandler(foreign)


def test_text_format_for_terminals():
    out = io.StringIO()
    configure_logging(component="c", stream=out, fmt="text")
    with log_context(run_id="r-9"):
        logging.getLogger("factorlab.x").warning("careful token=abc")
    line = out.getvalue()
    assert "WARNING" in line and "token=[redacted]" in line and "r-9" in line
