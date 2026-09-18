"""Host-side notifier daemon.

Runs on the Windows host as a small Flask service that owns the Outlook COM
handle. Container clients (when we get there) POST alerts here so that we
can keep using the locally-installed Outlook without exposing COM into the
container.

Endpoints:

  POST /alert    body: {subject, body, severity, source, occurred_at?}
                 header: X-Notify-Pin: <FACTORLAB_NOTIFY_PIN>
                 → 200 {ok: true}

  GET  /healthz  → 200 {status: "ok", last_send: <UTC ISO | null>}

Launch::

    python -m factorlab.shared.notify.daemon

The daemon binds to ``127.0.0.1:8765`` by default (localhost only — heimdall
sign-off requires this; never expose to 0.0.0.0 without auth review). PIN
defaults to whatever ``FACTORLAB_NOTIFY_PIN`` says; if unset, the daemon
refuses to start (fails closed).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from factorlab.shared.notify._backend import Notification, Severity
from factorlab.shared.notify.jsonl import JSONLBackend
from factorlab.shared.runtime import setup_logging

log = setup_logging("factorlab_notify_daemon")

_state = {"last_send_utc": None}


def _outlook_backend():
    try:
        from factorlab.shared.notify.outlook import OutlookBackend
        return OutlookBackend()
    except Exception as e:
        log.warning("Outlook backend unavailable: %s -- falling back to JSONL only", e)
        return None


def create_app():
    """Return a configured Flask app. Factored so tests can swap routes."""
    from flask import Flask, jsonify, request  # imported lazily

    app = Flask("factorlab_notify_daemon")
    outlook = _outlook_backend()
    jsonl = JSONLBackend()
    expected_pin = os.environ.get("FACTORLAB_NOTIFY_PIN", "").strip()

    @app.get("/healthz")
    def healthz():
        return jsonify({
            "status": "ok",
            "outlook_available": outlook is not None,
            "last_send": _state["last_send_utc"],
        }), 200

    @app.post("/alert")
    def alert():
        if not expected_pin:
            return jsonify({"error": "daemon misconfigured (no PIN)"}), 503
        given = request.headers.get("X-Notify-Pin", "")
        if given != expected_pin:
            return jsonify({"error": "unauthorized"}), 401
        data = request.get_json(silent=True) or {}
        try:
            sev_raw = str(data.get("severity", "warn")).lower()
            severity = Severity(sev_raw)
        except ValueError:
            return jsonify({"error": f"unknown severity {data.get('severity')!r}"}), 400
        occurred = data.get("occurred_at")
        try:
            occurred_dt = (
                datetime.fromisoformat(occurred) if occurred
                else datetime.now(timezone.utc)
            )
        except ValueError:
            occurred_dt = datetime.now(timezone.utc)

        n = Notification(
            subject=str(data.get("subject", "(no subject)")),
            body=str(data.get("body", "")),
            severity=severity,
            source=str(data.get("source", "remote")),
            occurred_at=occurred_dt,
        )
        results: dict[str, bool] = {}
        if outlook is not None:
            results["outlook"] = outlook.send(n)
        results["jsonl"] = jsonl.send(n)
        _state["last_send_utc"] = datetime.now(timezone.utc).isoformat()
        return jsonify({"ok": True, "results": results}), 200

    return app


def main():  # pragma: no cover — entrypoint, exercised manually
    if not os.environ.get("FACTORLAB_NOTIFY_PIN", "").strip():
        log.error(
            "FACTORLAB_NOTIFY_PIN is not set -- refusing to start (fails closed)."
        )
        return 1
    host = os.environ.get("FACTORLAB_NOTIFY_DAEMON_HOST", "127.0.0.1")
    port = int(os.environ.get("FACTORLAB_NOTIFY_DAEMON_PORT", "8765"))
    if host not in ("127.0.0.1", "::1", "localhost"):
        log.error(
            "Refusing non-localhost bind %s -- heimdall policy requires localhost only.",
            host,
        )
        return 1
    app = create_app()
    log.info("notifier daemon listening on http://%s:%d", host, port)
    app.run(host=host, port=port, debug=False)
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
